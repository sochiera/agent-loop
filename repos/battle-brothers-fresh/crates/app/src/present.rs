//! Uploads the CPU framebuffer to a GPU texture and draws it (letterboxed) into the window.

use std::sync::Arc;

use winit::window::Window;

use crate::gfx::Framebuffer;

const SHADER: &str = r#"
struct VsOut { @builtin(position) pos: vec4<f32>, @location(0) uv: vec2<f32> };

@vertex
fn vs(@builtin(vertex_index) i: u32) -> VsOut {
    var p = array<vec2<f32>, 3>(vec2<f32>(-1.0, -1.0), vec2<f32>(3.0, -1.0), vec2<f32>(-1.0, 3.0));
    var o: VsOut;
    let xy = p[i];
    o.pos = vec4<f32>(xy, 0.0, 1.0);
    o.uv = vec2<f32>((xy.x + 1.0) * 0.5, (1.0 - xy.y) * 0.5);
    return o;
}

@group(0) @binding(0) var tex: texture_2d<f32>;
@group(0) @binding(1) var samp: sampler;

@fragment
fn fs(in: VsOut) -> @location(0) vec4<f32> {
    return textureSample(tex, samp, in.uv);
}
"#;

/// Pipeline that draws the framebuffer texture into any colour target.
struct Blit {
    pipeline: wgpu::RenderPipeline,
    bind_group: wgpu::BindGroup,
    texture: wgpu::Texture,
    size: (u32, u32),
}

impl Blit {
    fn new(device: &wgpu::Device, target_format: wgpu::TextureFormat, fb_w: u32, fb_h: u32) -> Blit {
        let texture = device.create_texture(&wgpu::TextureDescriptor {
            label: Some("framebuffer"),
            size: wgpu::Extent3d { width: fb_w, height: fb_h, depth_or_array_layers: 1 },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format: wgpu::TextureFormat::Rgba8Unorm,
            usage: wgpu::TextureUsages::TEXTURE_BINDING | wgpu::TextureUsages::COPY_DST,
            view_formats: &[],
        });
        let view = texture.create_view(&wgpu::TextureViewDescriptor::default());
        let sampler = device.create_sampler(&wgpu::SamplerDescriptor {
            mag_filter: wgpu::FilterMode::Linear,
            min_filter: wgpu::FilterMode::Linear,
            ..Default::default()
        });
        let bgl = device.create_bind_group_layout(&wgpu::BindGroupLayoutDescriptor {
            label: None,
            entries: &[
                wgpu::BindGroupLayoutEntry {
                    binding: 0,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Texture {
                        sample_type: wgpu::TextureSampleType::Float { filterable: true },
                        view_dimension: wgpu::TextureViewDimension::D2,
                        multisampled: false,
                    },
                    count: None,
                },
                wgpu::BindGroupLayoutEntry {
                    binding: 1,
                    visibility: wgpu::ShaderStages::FRAGMENT,
                    ty: wgpu::BindingType::Sampler(wgpu::SamplerBindingType::Filtering),
                    count: None,
                },
            ],
        });
        let bind_group = device.create_bind_group(&wgpu::BindGroupDescriptor {
            label: None,
            layout: &bgl,
            entries: &[
                wgpu::BindGroupEntry { binding: 0, resource: wgpu::BindingResource::TextureView(&view) },
                wgpu::BindGroupEntry { binding: 1, resource: wgpu::BindingResource::Sampler(&sampler) },
            ],
        });
        let shader = device.create_shader_module(wgpu::ShaderModuleDescriptor {
            label: None,
            source: wgpu::ShaderSource::Wgsl(SHADER.into()),
        });
        let layout = device.create_pipeline_layout(&wgpu::PipelineLayoutDescriptor {
            label: None,
            bind_group_layouts: &[Some(&bgl)],
            immediate_size: 0,
        });
        let pipeline = device.create_render_pipeline(&wgpu::RenderPipelineDescriptor {
            label: None,
            layout: Some(&layout),
            vertex: wgpu::VertexState { module: &shader, entry_point: Some("vs"), compilation_options: Default::default(), buffers: &[] },
            fragment: Some(wgpu::FragmentState {
                module: &shader,
                entry_point: Some("fs"),
                compilation_options: Default::default(),
                targets: &[Some(target_format.into())],
            }),
            primitive: wgpu::PrimitiveState::default(),
            depth_stencil: None,
            multisample: wgpu::MultisampleState::default(),
            multiview_mask: None,
            cache: None,
        });
        Blit { pipeline, bind_group, texture, size: (fb_w, fb_h) }
    }

    fn upload(&self, queue: &wgpu::Queue, fb: &Framebuffer) {
        queue.write_texture(
            self.texture.as_image_copy(),
            fb.bytes(),
            wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(self.size.0 * 4), rows_per_image: Some(self.size.1) },
            wgpu::Extent3d { width: self.size.0, height: self.size.1, depth_or_array_layers: 1 },
        );
    }

    /// Clear `target` to black and draw the framebuffer into `viewport` (x, y, w, h).
    fn draw(&self, device: &wgpu::Device, queue: &wgpu::Queue, target: &wgpu::TextureView, viewport: (f32, f32, f32, f32)) {
        let mut enc = device.create_command_encoder(&wgpu::CommandEncoderDescriptor::default());
        {
            let mut pass = enc.begin_render_pass(&wgpu::RenderPassDescriptor {
                label: None,
                color_attachments: &[Some(wgpu::RenderPassColorAttachment {
                    view: target,
                    depth_slice: None,
                    resolve_target: None,
                    ops: wgpu::Operations { load: wgpu::LoadOp::Clear(wgpu::Color::BLACK), store: wgpu::StoreOp::Store },
                })],
                depth_stencil_attachment: None,
                timestamp_writes: None,
                occlusion_query_set: None,
                multiview_mask: None,
            });
            let (x, y, w, h) = viewport;
            pass.set_viewport(x, y, w, h, 0.0, 1.0);
            pass.set_pipeline(&self.pipeline);
            pass.set_bind_group(0, &self.bind_group, &[]);
            pass.draw(0..3, 0..1);
        }
        queue.submit(Some(enc.finish()));
    }
}

/// Outcome of one attempt to put a frame on screen.
#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum PresentStatus {
    /// The frame was submitted to the display.
    Presented,
    /// No frame was shown this time (timeout, occluded, surface reconfigured, ...).
    Skipped(SkipReason),
}

#[derive(Copy, Clone, Debug, PartialEq, Eq)]
pub enum SkipReason {
    Timeout,
    Occluded,
    SurfaceChanged,
}

/// Consecutive skipped frames tolerated before presentation is declared broken (~2 s at 60 Hz).
pub const MAX_CONSECUTIVE_SKIPS: u32 = 120;

/// Counts frames that were really presented and bounds repeated failures, so a window
/// that never shows anything can neither loop forever nor be reported as a success.
#[derive(Debug, Default)]
pub struct PresentTracker {
    presented: u64,
    consecutive_skips: u32,
}

impl PresentTracker {
    pub fn presented(&self) -> u64 {
        self.presented
    }

    /// Record one attempt. `strict` (smoke runs) also counts an occluded window as a
    /// failure; interactive runs tolerate it indefinitely (e.g. a minimised window).
    pub fn record(&mut self, result: Result<PresentStatus, String>, strict: bool) -> Result<(), String> {
        match result? {
            PresentStatus::Presented => {
                self.presented += 1;
                self.consecutive_skips = 0;
            }
            PresentStatus::Skipped(SkipReason::Occluded) if !strict => {}
            PresentStatus::Skipped(reason) => {
                self.consecutive_skips += 1;
                if self.consecutive_skips >= MAX_CONSECUTIVE_SKIPS {
                    return Err(format!(
                        "presentation failed: {} consecutive frames were not shown (last reason: {reason:?})",
                        self.consecutive_skips
                    ));
                }
            }
        }
        Ok(())
    }
}

pub struct Presenter {
    surface: wgpu::Surface<'static>,
    device: wgpu::Device,
    queue: wgpu::Queue,
    config: wgpu::SurfaceConfiguration,
    blit: Blit,
    pub adapter_info: String,
}

impl Presenter {
    pub fn new(window: Arc<Window>, fb_w: u32, fb_h: u32) -> Result<Presenter, String> {
        let size = window.inner_size();
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor::new_without_display_handle());
        let surface = instance.create_surface(window).map_err(|e| format!("cannot create surface: {e}"))?;
        let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::LowPower,
            compatible_surface: Some(&surface),
            force_fallback_adapter: false,
            apply_limit_buckets: false,
        }))
        .map_err(|e| format!("no usable GPU adapter: {e}"))?;
        let info = adapter.get_info();
        let adapter_info = format!("{} ({:?})", info.name, info.backend);
        let (device, queue) = pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor::default()))
            .map_err(|e| format!("cannot open GPU device: {e}"))?;

        let mut config = surface
            .get_default_config(&adapter, size.width.max(1), size.height.max(1))
            .ok_or("surface not supported by adapter")?;
        let caps = surface.get_capabilities(&adapter);
        // Prefer a non-sRGB surface so the framebuffer's bytes are shown unchanged.
        if let Some(f) = caps.formats.iter().copied().find(|f| !f.is_srgb()) {
            config.format = f;
        }
        config.present_mode = wgpu::PresentMode::Fifo;
        surface.configure(&device, &config);
        let blit = Blit::new(&device, config.format, fb_w, fb_h);
        Ok(Presenter { surface, device, queue, config, blit, adapter_info })
    }

    pub fn resize(&mut self, w: u32, h: u32) {
        if w > 0 && h > 0 {
            self.config.width = w;
            self.config.height = h;
            self.surface.configure(&self.device, &self.config);
        }
    }

    /// Viewport rectangle `(x, y, w, h)` in surface pixels (aspect-preserving letterbox).
    pub fn viewport(&self) -> (f32, f32, f32, f32) {
        letterbox(self.config.width as f32, self.config.height as f32, self.blit.size.0 as f32, self.blit.size.1 as f32)
    }

    pub fn present(&mut self, fb: &Framebuffer) -> Result<PresentStatus, String> {
        self.blit.upload(&self.queue, fb);
        let frame = match self.surface.get_current_texture() {
            wgpu::CurrentSurfaceTexture::Success(f) | wgpu::CurrentSurfaceTexture::Suboptimal(f) => f,
            wgpu::CurrentSurfaceTexture::Outdated | wgpu::CurrentSurfaceTexture::Lost => {
                self.surface.configure(&self.device, &self.config);
                return Ok(PresentStatus::Skipped(SkipReason::SurfaceChanged));
            }
            wgpu::CurrentSurfaceTexture::Timeout => return Ok(PresentStatus::Skipped(SkipReason::Timeout)),
            wgpu::CurrentSurfaceTexture::Occluded => return Ok(PresentStatus::Skipped(SkipReason::Occluded)),
            wgpu::CurrentSurfaceTexture::Validation => {
                return Err("GPU validation error while acquiring the next frame".to_string());
            }
        };
        let view = frame.texture.create_view(&wgpu::TextureViewDescriptor::default());
        self.blit.draw(&self.device, &self.queue, &view, self.viewport());
        self.queue.present(frame);
        Ok(PresentStatus::Presented)
    }
}

/// Largest rectangle with the framebuffer's aspect ratio centred in the surface.
pub fn letterbox(sw: f32, sh: f32, fw: f32, fh: f32) -> (f32, f32, f32, f32) {
    let scale = (sw / fw).min(sh / fh);
    let (w, h) = (fw * scale, fh * scale);
    ((sw - w) / 2.0, (sh - h) / 2.0, w, h)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn persistent_presentation_failure_is_an_error_not_a_success() {
        for reason in [SkipReason::Timeout, SkipReason::SurfaceChanged, SkipReason::Occluded] {
            let mut t = PresentTracker::default();
            let mut failed = None;
            for i in 0..(MAX_CONSECUTIVE_SKIPS * 2) {
                if let Err(e) = t.record(Ok(PresentStatus::Skipped(reason)), true) {
                    failed = Some((i, e));
                    break;
                }
            }
            let (i, msg) = failed.expect("persistent skips must become an error");
            assert_eq!(i + 1, MAX_CONSECUTIVE_SKIPS);
            assert!(msg.contains("presentation failed"));
            assert_eq!(t.presented(), 0, "skipped frames must not count as presented");
        }
    }

    #[test]
    fn successful_present_resets_the_failure_streak_and_counts() {
        let mut t = PresentTracker::default();
        for _ in 0..5 {
            for _ in 0..(MAX_CONSECUTIVE_SKIPS - 1) {
                t.record(Ok(PresentStatus::Skipped(SkipReason::Timeout)), true).unwrap();
            }
            t.record(Ok(PresentStatus::Presented), true).unwrap();
        }
        assert_eq!(t.presented(), 5);
    }

    #[test]
    fn occluded_is_tolerated_interactively_but_not_in_smoke_runs() {
        let mut t = PresentTracker::default();
        for _ in 0..(MAX_CONSECUTIVE_SKIPS * 3) {
            t.record(Ok(PresentStatus::Skipped(SkipReason::Occluded)), false).unwrap();
        }
        assert_eq!(t.presented(), 0);
    }

    #[test]
    fn fatal_errors_propagate_immediately() {
        let mut t = PresentTracker::default();
        assert_eq!(t.record(Err("boom".into()), false), Err("boom".to_string()));
    }

    #[test]
    fn letterbox_preserves_aspect_and_centres() {
        assert_eq!(letterbox(1400.0, 820.0, 1400.0, 820.0), (0.0, 0.0, 1400.0, 820.0));
        let (x, y, w, h) = letterbox(2800.0, 2000.0, 1400.0, 820.0);
        assert_eq!((x, w), (0.0, 2800.0));
        assert!((h - 1640.0).abs() < 0.01 && (y - 180.0).abs() < 0.01);
        let (x, y, w, h) = letterbox(700.0, 820.0, 1400.0, 820.0);
        assert!((w - 700.0).abs() < 0.01 && (h - 410.0).abs() < 0.01 && x == 0.0 && (y - 205.0).abs() < 0.01);
    }

    /// Renders a known framebuffer through the real wgpu pipeline into an offscreen
    /// texture (no window needed) and reads it back. Skipped if no adapter exists.
    #[test]
    fn offscreen_blit_reproduces_framebuffer() {
        let instance = wgpu::Instance::new(wgpu::InstanceDescriptor::new_without_display_handle());
        let adapter = pollster::block_on(instance.request_adapter(&wgpu::RequestAdapterOptions {
            power_preference: wgpu::PowerPreference::LowPower,
            compatible_surface: None,
            force_fallback_adapter: false,
            apply_limit_buckets: false,
        }));
        let Ok(adapter) = adapter else {
            eprintln!("SKIP: no wgpu adapter available");
            return;
        };
        eprintln!("offscreen blit test on {:?}", adapter.get_info().name);
        let (device, queue) = pollster::block_on(adapter.request_device(&wgpu::DeviceDescriptor::default())).unwrap();
        let (w, h) = (64u32, 32u32);
        let mut fb = Framebuffer::new(w as usize, h as usize);
        fb.fill_rect(0, 0, 32, 32, crate::gfx::rgb(200, 30, 40));
        fb.fill_rect(32, 0, 32, 16, crate::gfx::rgb(10, 220, 30));
        fb.fill_rect(32, 16, 32, 16, crate::gfx::rgb(20, 40, 250));
        let format = wgpu::TextureFormat::Rgba8Unorm;
        let blit = Blit::new(&device, format, w, h);
        blit.upload(&queue, &fb);
        let target = device.create_texture(&wgpu::TextureDescriptor {
            label: None,
            size: wgpu::Extent3d { width: w, height: h, depth_or_array_layers: 1 },
            mip_level_count: 1,
            sample_count: 1,
            dimension: wgpu::TextureDimension::D2,
            format,
            usage: wgpu::TextureUsages::RENDER_ATTACHMENT | wgpu::TextureUsages::COPY_SRC,
            view_formats: &[],
        });
        let view = target.create_view(&wgpu::TextureViewDescriptor::default());
        blit.draw(&device, &queue, &view, (0.0, 0.0, w as f32, h as f32));
        let readback = device.create_buffer(&wgpu::BufferDescriptor {
            label: None,
            size: (w * h * 4) as u64,
            usage: wgpu::BufferUsages::COPY_DST | wgpu::BufferUsages::MAP_READ,
            mapped_at_creation: false,
        });
        let mut enc = device.create_command_encoder(&wgpu::CommandEncoderDescriptor::default());
        enc.copy_texture_to_buffer(
            target.as_image_copy(),
            wgpu::TexelCopyBufferInfo {
                buffer: &readback,
                layout: wgpu::TexelCopyBufferLayout { offset: 0, bytes_per_row: Some(w * 4), rows_per_image: Some(h) },
            },
            wgpu::Extent3d { width: w, height: h, depth_or_array_layers: 1 },
        );
        queue.submit(Some(enc.finish()));
        readback.slice(..).map_async(wgpu::MapMode::Read, |r| r.unwrap());
        device.poll(wgpu::PollType::wait_indefinitely()).unwrap();
        let data = readback.slice(..).get_mapped_range().unwrap();
        let px = |x: u32, y: u32| {
            let i = ((y * w + x) * 4) as usize;
            [data[i], data[i + 1], data[i + 2]]
        };
        let close = |a: [u8; 3], b: [u8; 3]| a.iter().zip(b).all(|(x, y)| (*x as i32 - y as i32).abs() <= 2);
        assert!(close(px(5, 5), [200, 30, 40]), "{:?}", px(5, 5));
        assert!(close(px(50, 5), [10, 220, 30]), "{:?}", px(50, 5));
        assert!(close(px(50, 25), [20, 40, 250]), "{:?}", px(50, 25));
    }
}
