# Slice runtime notes

`target/debug/headless_smoke --seed 1` exited 0. Stdout: `OUTCOME=Victory REASON=Elimination ROUNDS=6 HASH=69898ecd416a3e66`. That is the rules binary, not original-game fidelity.

Private Xvfb `:99` was started only after `DISPLAY` was unset. The user `:0` socket was not used. Two grabs with ffmpeg, after about 2s and about 5s, each wrote a 4493-byte 1280x720 PNG. The opened frame is black with an X cursor. Game stderr was empty and the process was still alive when killed. `docs/evidence/slice-runtime-frame.png` is that miss. It does not prove the window rendered a battle.

Store frame `docs/source/reference-images/steam-ss-0.jpg` was opened separately and shows the official tactical aftermath. That open is not this black capture and not the doctor synthetic image test.
