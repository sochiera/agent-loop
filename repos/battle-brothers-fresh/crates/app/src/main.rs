//! Hireling's Oath: native desktop front end.
//!
//! Usage: `hirelings-oath [--smoke-frames N] [--autoplay | --autoplay-campaign] [--seed S] [--headless]
//!                        [--screenshot FILE] [--shot VIEW=FILE]...`

mod font;
mod game;
mod gfx;
mod present;

use std::process::ExitCode;
use std::sync::Arc;
use std::time::Instant;

use winit::application::ApplicationHandler;
use winit::dpi::LogicalSize;
use winit::event::{ElementState, MouseButton, MouseScrollDelta, WindowEvent};
use winit::event_loop::{ActiveEventLoop, ControlFlow, EventLoop};
use winit::keyboard::{KeyCode, PhysicalKey};
use winit::window::{Window, WindowId};

use game::{Game, Input, Key, Options, W, H};
use gfx::Framebuffer;
use present::{PresentStatus, PresentTracker, Presenter};

const TITLE: &str = "Hireling's Oath";

#[derive(Debug, Default)]
struct Args {
    smoke_frames: Option<u64>,
    autoplay: bool,
    autoplay_campaign: bool,
    seed: Option<u64>,
    headless: bool,
    screenshot: Option<String>,
    /// `(view, file)` pairs: save a PNG the first time that view has been on screen for a moment.
    shots: Vec<(String, String)>,
}

const USAGE: &str = "Usage: hirelings-oath [--smoke-frames N] [--autoplay | --autoplay-campaign] [--seed S] [--headless]\n\
                       [--screenshot FILE.png] [--shot VIEW=FILE.png]...\n\
  --smoke-frames N   open the window, run N frames, exit 0 (non-interactive check)\n\
  --autoplay         the AI controls both sides and a quick battle starts by itself\n\
  --autoplay-campaign an autopilot plays a whole campaign (contracts, travel, AI battles, results)\n\
  --shot VIEW=FILE   save a PNG of a view once it is on screen; VIEW is one of menu, overworld,\n\
                     travel, encounter, notice, town, battle, result, gameover (repeatable)\n\
  --seed S           seed for the battle dice (default: from the clock)\n\
  --headless         no window: run the frames with the software renderer only\n\
  --screenshot FILE  write the last rendered frame to FILE (PNG)";

fn parse_args<I: Iterator<Item = String>>(mut it: I) -> Result<Args, String> {
    let mut a = Args::default();
    while let Some(arg) = it.next() {
        let mut value = |name: &str| it.next().ok_or_else(|| format!("{name} needs a value"));
        match arg.as_str() {
            "--smoke-frames" => a.smoke_frames = Some(value("--smoke-frames")?.parse().map_err(|_| "bad frame count")?),
            "--seed" => a.seed = Some(value("--seed")?.parse().map_err(|_| "bad seed")?),
            "--screenshot" => a.screenshot = Some(value("--screenshot")?),
            "--autoplay" => a.autoplay = true,
            "--autoplay-campaign" => a.autoplay_campaign = true,
            "--shot" => {
                let v = value("--shot")?;
                let (view, file) = v.split_once('=').ok_or("--shot needs VIEW=FILE")?;
                a.shots.push((view.to_string(), file.to_string()));
            }
            "--headless" => a.headless = true,
            "-h" | "--help" => return Err(String::new()),
            other => return Err(format!("unknown argument {other}")),
        }
    }
    Ok(a)
}

fn save_png(path: &str, fb: &Framebuffer) {
    match std::fs::write(path, gfx::encode_png(fb)) {
        Ok(()) => println!("wrote screenshot {path}"),
        Err(e) => eprintln!("cannot write screenshot {path}: {e}"),
    }
}

/// Saves `--shot VIEW=FILE` screenshots the first time each view has been visible long enough.
struct Shots {
    wanted: Vec<(String, String, bool)>,
}

impl Shots {
    fn new(list: &[(String, String)]) -> Shots {
        Shots { wanted: list.iter().map(|(v, f)| (v.clone(), f.clone(), false)).collect() }
    }

    fn check(&mut self, game: &Game, fb: &Framebuffer) {
        let (view, age) = game.view();
        let settle = match view {
            "battle" => 3.0,
            "travel" | "overworld" => 0.3,
            _ => 0.5,
        };
        if age < settle {
            return;
        }
        for (v, file, done) in &mut self.wanted {
            if !*done && v == view {
                *done = true;
                save_png(file, fb);
            }
        }
    }

    fn report(&self) {
        for (v, _, done) in &self.wanted {
            if !done {
                eprintln!("warning: view '{v}' never appeared, no screenshot written");
            }
        }
    }
}

fn main() -> ExitCode {
    let args = match parse_args(std::env::args().skip(1)) {
        Ok(a) => a,
        Err(msg) => {
            if !msg.is_empty() {
                eprintln!("error: {msg}");
            }
            eprintln!("{USAGE}");
            return ExitCode::from(if msg.is_empty() { 0 } else { 2 });
        }
    };
    let opts = Options { autoplay: args.autoplay, autoplay_campaign: args.autoplay_campaign, seed: args.seed };
    if args.headless {
        return run_headless(&args, opts);
    }
    run_windowed(args, opts)
}

fn run_headless(args: &Args, opts: Options) -> ExitCode {
    let frames = args.smoke_frames.unwrap_or(600);
    let mut game = Game::new(opts);
    let mut fb = Framebuffer::new(W, H);
    let mut shots = Shots::new(&args.shots);
    for _ in 0..frames {
        game.update(1.0 / 60.0);
        game.render(&mut fb);
        shots.check(&game, &fb);
    }
    shots.report();
    println!("headless: {frames} frames rendered (software renderer only; no window opened)");
    if let Some(p) = &args.screenshot {
        save_png(p, &fb);
    }
    ExitCode::SUCCESS
}

fn run_windowed(args: Args, opts: Options) -> ExitCode {
    let event_loop = match EventLoop::new() {
        Ok(l) => l,
        Err(e) => {
            eprintln!("error: cannot connect to a display ({e}). Is DISPLAY or WAYLAND_DISPLAY set?");
            return ExitCode::from(3);
        }
    };
    event_loop.set_control_flow(ControlFlow::Poll);
    let mut app = App::new(args, opts);
    if let Err(e) = event_loop.run_app(&mut app) {
        eprintln!("error: event loop failed: {e}");
        return ExitCode::from(1);
    }
    app.verify_smoke_completed();
    match app.error {
        Some(e) => {
            eprintln!("error: {e}");
            ExitCode::from(1)
        }
        None => ExitCode::SUCCESS,
    }
}

struct App {
    args: Args,
    game: Game,
    fb: Framebuffer,
    window: Option<Arc<Window>>,
    presenter: Option<Presenter>,
    last: Instant,
    tracker: PresentTracker,
    error: Option<String>,
    /// Surface pixels per framebuffer pixel and letterbox offset, for mouse mapping.
    scale: f32,
    offset: (f32, f32),
    cursor: (f32, f32),
    shots: Shots,
}

impl App {
    fn new(args: Args, opts: Options) -> App {
        let shots = Shots::new(&args.shots);
        App {
            args,
            game: Game::new(opts),
            fb: Framebuffer::new(W, H),
            window: None,
            presenter: None,
            last: Instant::now(),
            tracker: PresentTracker::default(),
            error: None,
            scale: 1.0,
            offset: (0.0, 0.0),
            cursor: (0.0, 0.0),
            shots,
        }
    }

    /// Record the result of presenting a frame. Returns `false` when the run must stop
    /// (fatal error, repeated failures, quit requested, or the smoke target was reached).
    fn after_present(&mut self, result: Result<PresentStatus, String>) -> bool {
        let smoke = self.args.smoke_frames;
        if let Err(e) = self.tracker.record(result, smoke.is_some()) {
            self.error = Some(e);
            return false;
        }
        if let Some(n) = smoke {
            if self.tracker.presented() >= n {
                println!("smoke: {} frames presented in a real window; exiting 0", self.tracker.presented());
                return false;
            }
        }
        !self.game.quit_requested()
    }

    /// A smoke run that stopped before presenting its frames must not report success.
    fn verify_smoke_completed(&mut self) {
        if let Some(n) = self.args.smoke_frames {
            if self.error.is_none() && self.tracker.presented() < n {
                self.error = Some(format!(
                    "smoke run ended after presenting only {} of {n} frames",
                    self.tracker.presented()
                ));
            }
        }
    }

    fn to_fb(&self, x: f64, y: f64) -> (f32, f32) {
        ((x as f32 - self.offset.0) / self.scale, (y as f32 - self.offset.1) / self.scale)
    }

    fn finish(&mut self, el: &ActiveEventLoop) {
        if let Some(p) = &self.args.screenshot {
            save_png(p, &self.fb);
        }
        self.shots.report();
        el.exit();
    }
}

impl ApplicationHandler for App {
    fn resumed(&mut self, el: &ActiveEventLoop) {
        if self.window.is_some() {
            return;
        }
        let attrs = Window::default_attributes()
            .with_title(TITLE)
            .with_inner_size(LogicalSize::new(W as f64, H as f64))
            .with_min_inner_size(LogicalSize::new(700.0, 410.0));
        let window = match el.create_window(attrs) {
            Ok(w) => Arc::new(w),
            Err(e) => {
                self.error = Some(format!("cannot create window: {e}"));
                el.exit();
                return;
            }
        };
        match Presenter::new(window.clone(), W as u32, H as u32) {
            Ok(p) => {
                println!("window opened; GPU: {}", p.adapter_info);
                self.presenter = Some(p);
            }
            Err(e) => {
                self.error = Some(e);
                el.exit();
                return;
            }
        }
        self.last = Instant::now();
        window.request_redraw();
        self.window = Some(window);
    }

    fn window_event(&mut self, el: &ActiveEventLoop, _id: WindowId, event: WindowEvent) {
        match event {
            WindowEvent::CloseRequested => self.finish(el),
            WindowEvent::Resized(size) => {
                if let Some(p) = &mut self.presenter {
                    p.resize(size.width, size.height);
                }
            }
            WindowEvent::CursorMoved { position, .. } => {
                self.cursor = self.to_fb(position.x, position.y);
                self.game.input(Input::MouseMove(self.cursor.0, self.cursor.1));
            }
            WindowEvent::MouseInput { state: ElementState::Pressed, button: MouseButton::Left, .. } => {
                self.game.input(Input::MouseDown(self.cursor.0, self.cursor.1));
            }
            WindowEvent::MouseWheel { delta, .. } => {
                let dy = match delta {
                    MouseScrollDelta::LineDelta(_, y) => y,
                    MouseScrollDelta::PixelDelta(p) => p.y as f32,
                };
                self.game.input(Input::Wheel(dy));
            }
            WindowEvent::KeyboardInput { event, .. } if event.state == ElementState::Pressed && !event.repeat => {
                let key = match event.physical_key {
                    PhysicalKey::Code(KeyCode::Enter | KeyCode::NumpadEnter) => Some(Key::Enter),
                    PhysicalKey::Code(KeyCode::Escape) => Some(Key::Escape),
                    PhysicalKey::Code(KeyCode::Space) => Some(Key::Space),
                    PhysicalKey::Code(KeyCode::ArrowUp) => Some(Key::Up),
                    PhysicalKey::Code(KeyCode::ArrowDown) => Some(Key::Down),
                    _ => None,
                };
                if let Some(k) = key {
                    self.game.input(Input::Key(k));
                }
            }
            WindowEvent::RedrawRequested => {
                let smoke = self.args.smoke_frames.is_some();
                let now = Instant::now();
                let dt = if smoke { 1.0 / 60.0 } else { now.duration_since(self.last).as_secs_f32().min(0.1) };
                self.last = now;
                self.game.update(dt);
                self.game.render(&mut self.fb);
                self.shots.check(&self.game, &self.fb);
                let result = match &mut self.presenter {
                    Some(p) => {
                        let (x, y, w, _) = p.viewport();
                        self.scale = w / W as f32;
                        self.offset = (x, y);
                        p.present(&self.fb)
                    }
                    None => Err("no presenter available".to_string()),
                };
                if !self.after_present(result) {
                    self.finish(el);
                    return;
                }
                if let Some(w) = &self.window {
                    w.request_redraw();
                }
            }
            _ => {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use present::{SkipReason, MAX_CONSECUTIVE_SKIPS};

    fn smoke_app(frames: u64) -> App {
        App::new(Args { smoke_frames: Some(frames), ..Args::default() }, Options::default())
    }

    fn status(app: &App) -> u8 {
        if app.error.is_some() { 1 } else { 0 }
    }

    #[test]
    fn smoke_exit_is_zero_only_after_n_presented_frames() {
        let mut app = smoke_app(3);
        assert!(app.after_present(Ok(PresentStatus::Presented)));
        assert!(app.after_present(Ok(PresentStatus::Presented)));
        assert!(!app.after_present(Ok(PresentStatus::Presented)), "stops at the target");
        app.verify_smoke_completed();
        assert_eq!(status(&app), 0);
    }

    #[test]
    fn persistent_presentation_failure_never_exits_zero() {
        let mut app = smoke_app(5);
        let mut stopped = false;
        for _ in 0..(MAX_CONSECUTIVE_SKIPS + 10) {
            if !app.after_present(Ok(PresentStatus::Skipped(SkipReason::Timeout))) {
                stopped = true;
                break;
            }
        }
        assert!(stopped, "retries must be bounded");
        app.verify_smoke_completed();
        assert_eq!(status(&app), 1);
        assert_eq!(app.tracker.presented(), 0);
    }

    #[test]
    fn fatal_present_error_is_reported() {
        let mut app = smoke_app(5);
        assert!(!app.after_present(Err("device lost".into())));
        assert_eq!(app.error.as_deref(), Some("device lost"));
        assert_eq!(status(&app), 1);
    }

    #[test]
    fn closing_the_window_before_the_target_is_a_smoke_failure() {
        let mut app = smoke_app(10);
        assert!(app.after_present(Ok(PresentStatus::Presented)));
        app.verify_smoke_completed(); // event loop ended early (e.g. window closed)
        assert_eq!(status(&app), 1);
    }

    #[test]
    fn interactive_runs_have_no_frame_target() {
        let mut app = App::new(Args::default(), Options::default());
        app.verify_smoke_completed();
        assert_eq!(status(&app), 0);
    }

    #[test]
    fn campaign_and_shot_flags_parse() {
        let args = ["--autoplay-campaign", "--shot", "battle=/tmp/b.png", "--shot", "town=t.png", "--seed", "9"].map(String::from);
        let a = parse_args(args.into_iter()).unwrap();
        assert!(a.autoplay_campaign && !a.autoplay);
        assert_eq!(a.seed, Some(9));
        assert_eq!(a.shots, vec![("battle".to_string(), "/tmp/b.png".to_string()), ("town".to_string(), "t.png".to_string())]);
        assert!(parse_args(["--shot".to_string(), "nofile".to_string()].into_iter()).is_err());
        assert!(parse_args(["--bogus".to_string()].into_iter()).is_err());
    }

    #[test]
    fn shots_are_saved_once_the_view_has_settled() {
        let dir = std::env::temp_dir().join(format!("ho-shot-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let file = dir.join("menu.png");
        let mut shots = Shots::new(&[("menu".to_string(), file.to_string_lossy().to_string())]);
        let mut game = Game::new(Options::default());
        let mut fb = Framebuffer::new(W, H);
        game.update(0.1);
        game.render(&mut fb);
        shots.check(&game, &fb);
        assert!(!file.exists(), "too early");
        for _ in 0..60 {
            game.update(1.0 / 60.0);
        }
        game.render(&mut fb);
        shots.check(&game, &fb);
        assert!(file.exists());
        std::fs::remove_dir_all(&dir).ok();
    }
}
