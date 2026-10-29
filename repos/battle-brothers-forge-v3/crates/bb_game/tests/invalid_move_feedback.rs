#[test]
fn invalid_move_emits_feedback() {
    let source = include_str!("../src/main.rs");
    let click = source
        .split("fn click(&mut self) {")
        .nth(1)
        .and_then(|body| body.split("fn key(&mut self,").next())
        .expect("click handler exists");

    assert!(
        click.contains("} else {\n            self.run(Command::Move { unit: me, to: h });"),
        "unhandled clicks must reach the rules error path so the GUI can show feedback"
    );
    assert!(source.contains("Err(e) => self.error = Some((format!(\"{e:?}\"), Instant::now())),"));
    assert!(source.contains("error: self.error.as_ref().map(|(s, _)| s.as_str()),"));
}
