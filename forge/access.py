"""Optional WWW access gate for the control room.

Forge is a single-user laptop controller. When the UI is deliberately
published through a tunnel and a reverse proxy (the /forge/ entrance on
sochiera.pl), the process can enforce Jan's access gate itself. The gate is
off by default: a plain local install keeps its historical anonymous surface.
The expected value is supplied by the operator's local secret file, never via
command-line options, and the match is constant-time rather than literal.
"""

from __future__ import annotations

import hmac
import os
import stat
from pathlib import Path

GATE_ENV = "FORGE_UI_PASSWORD_FILE"


class GateMisconfigured(RuntimeError):
    """The gate is enabled but the configured secret file is unusable."""


def read_expected_value(path_str: str | None) -> bytes:
    expanded = os.path.expandvars(str(path_str or "")) if path_str else ""
    expanded = expanded.strip()
    if not expanded:
        raise GateMisconfigured("no access gate file configured")
    path = Path(expanded)
    if not path.is_absolute():
        raise GateMisconfigured("access gate file must be an absolute path")
    if path.is_symlink():
        raise GateMisconfigured("access gate file must not be a symlink")
    if not path.is_file():
        raise GateMisconfigured("access gate file does not exist")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise GateMisconfigured("access gate file permissions are too open (need 0600)")
    value = path.read_bytes().strip()
    if not value:
        raise GateMisconfigured("access gate file is empty")
    return value


class AccessGate:
    """Rejects every request until the WWW entrance sends the secret."""

    def __init__(self, expected: bytes):
        self._expected = expected

    def allows(self, value: str | None) -> bool:
        if not value:
            return False
        offered = value.strip().encode("utf-8")
        return hmac.compare_digest(offered, self._expected)


def gate_from_env(environ: dict[str, str] | None = None) -> AccessGate | None:
    env = os.environ if environ is None else environ
    path_str = env.get(GATE_ENV, "").strip()
    if not path_str:
        return None
    return AccessGate(read_expected_value(path_str))
