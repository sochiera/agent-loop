# Forge control-room access gate (deploy/)

The control room (`python3 -m forge ui`, `127.0.0.1:8787`) is a living UI for
Jan's laptop: starting runs needs file paths, working directories, and the
agent CLIs that exist only locally. This directory holds the pieces needed to
expose a hidden, gated WWW entrance for it at `https://sochiera.pl/forge/`
without moving Forge or its runners off the laptop.

## One password, UI and API

Every request through the entrance must present Jan's access-gate secret
(from the card description — never committed anywhere). The UI process itself
enforces it:

```bash
FORGE_UI_PASSWORD_FILE=/home/jan/.config/sochiera/forge-gate-secret \
  python3 -m forge ui --no-browser
```

The value is a `0600` file; anything unanswered — UI HTML, static assets
(`/`, `/app.js`, `/style.css`), and every `/api/…` endpoint — is rejected with
403 before the server does authentication work. POSTs are refused in the same
order (tests cover it in `tests/test_web.py`). `forge ui
--access-gate-file PATH` sets the same thing without exporting the variable.

## Reverse tunnel (laptop → VPS)

Forge must stay reachable from the VPS only over loopback. `tunnelforge.sh`
holds up an SSH reverse forward: VPS `127.0.0.1:8791` → laptop
`127.0.0.1:8787`. It requires the key `~/.ssh/pbn_vps` (same identity the
homepage and PBN runbooks already use) and `exit-on-forward-failure` so a lost
forward is never silently half-up.

```bash
deploy/tunnelforge.sh up    # keeps a single ssh -N in the foreground
deploy/tunnelforge.sh status
deploy/tunnelforge.sh down
```

`forge-tunnel.service` is an optional systemd user unit wrapping the same
command for a persistent laptop setup. The VPS side needs nothing: sshd binds
remote forwards to loopback by default, exactly like the PBN test tunnel.

## nginx fragment (VPS)

`nginx-forge.conf` mirrors the `/biblioteka/` snippet
(`/etc/nginx/snippets/ew-biblioteka.conf` → here `/etc/nginx/snippets/
forge.conf`): two location blocks inserted before the static catch-all, a
`/forge/healthz` reachability probe, `proxy_read_timeout 300s` (agent runs run
long), and `rm -rf`-grade durability is intentionally absent — this fragment
only routes; nothing writes to disk from the proxied UI.

Augmenting this with a second proxy-auth layer on the VPS (e.g. an
`auth_basic` htpasswd file holding the same card-description secret) is fine
and defense-in-depth, but the process-level gate above already covers UI and
API paths without any VPS-side secret material.

## Verification (no production touches)

This repo has no test slot for `sochiera.pl`; the runbook for the entrance
itself focuses on local-only checks so the production vhost stays untouched:

1. `python3 -m forge ui --no-browser --access-gate-file …` responds 403
   without the header and 200 with it (now also verifiable with `pytest
   tests/test_web.py`).
2. Local loopback SSH round-trip: `ssh -R 127.0.0.1:18999:127.0.0.1:8787
   localhost` then `curl http://127.0.0.1:18999/api/health` proves the
   forward mechanics without contacting the VPS.
3. VPS reachability probe (read-only, was exercised during the implementation
   run): `ssh ubuntu@51.83.199.206 true` succeeded; `sudo -n nginx -t` on the
   VPS reported success as it already stands.

Installing the VPS snippet, mixing proxy auth, and pointing the
`proxy_pass` at `127.0.0.1:8791` touch the production vhost and therefore
wait for an explicit ship-it decision — same as every homepage release.
