# Store references

Official Steam store images for appid 365360 live only under `docs/source/reference-images/`. Access timestamp of the download is `2026-10-23T13:57:03Z`. Page: https://store.steampowered.com/app/365360 [2]. Metadata API: https://store.steampowered.com/api/appdetails?appids=365360&l=english [1]. Per-file URL, SHA256, and pixel size are in `docs/source/reference-images/download-manifest.json` and [05-authentic-reference.md](../../docs/source/05-authentic-reference.md).

These files are copyrighted official store assets kept for inspection and analysis. They are not a redistribution license, not crate assets, and not original-runtime captures. No original game DLL, script, or extracted art was copied.

Distributable builds and packages must exclude `docs/source/reference-images/` entirely, including the JPEG bytes and the manifest that restates CDN URLs. Do not copy those bytes into `crates/`.

[1] https://store.steampowered.com/api/appdetails?appids=365360&l=english
[2] https://store.steampowered.com/app/365360
