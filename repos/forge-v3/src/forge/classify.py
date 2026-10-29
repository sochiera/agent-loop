"""Total ordered failure classifier (§5.3). First matching row wins.

Diagnostic patterns are matched only against error_fields (stderr + native error
events/fields), never against the final answer text of a successful result.
"""

from dataclasses import dataclass
from datetime import datetime
import json
import re

QUOTA = re.compile(r'\b429\b|rate.?limit|usage limit|quota|usage_limit_reached|rate_limit_error', re.I)
AUTH = re.compile(r'\b401\b|unauthori[sz]ed|not logged in|login required|invalid.*token', re.I)
NETWORK = re.compile(r'ECONNRESET|ENOTFOUND|network is unreachable|\b503\b|overloaded|selected model is at capacity', re.I)
USAGE = re.compile(r'usage:|unexpected argument|invalid value', re.I)
MODEL_CONFIG = re.compile(r'model.{0,20}not.?found|model_not_found|unknown model|unsupported.{0,30}effort|'
                          r'invalid.{0,20}(reasoning.)?effort', re.I)
RESET_KEYS = ('resets_at', 'reset_at', 'resetAt', 'retry_at')
QUOTA_BASE, QUOTA_CAP = 15 * 60, 4 * 3600
ENV_BASE, ENV_CAP = 10 * 60, 2 * 3600
AUTH_WAIT = 3600


@dataclass(frozen=True)
class Facts:
    cancelled: bool = False
    interrupted: bool = False
    spawn_error: str | None = None          # ENOENT/EACCES text
    binary_mismatch: bool = False           # realpath/version differs from smoke record
    sandbox_error: str | None = None        # validation/revalidation failure before spawn
    timed_out: bool = False
    rc: int | None = None
    stdout_bytes: int = 0
    native_status: str = 'absent'           # success | failure | absent
    final_valid: bool = False               # schema-valid final JSON
    model_match: bool | None = None
    turn_limit: bool = False
    error_fields: dict | None = None
    stderr: str = ''


@dataclass(frozen=True)
class Outcome:
    row: int
    outcome: str
    coding_budget: bool = False
    profile_strike: bool = False
    disable: str | None = None              # profile | sandbox | None
    account_wait: bool = False
    recheck_at: float | None = None


def _diagnostic(facts: Facts) -> str:
    fields = dict(facts.error_fields or {})
    fields.setdefault('stderr', facts.stderr)
    return json.dumps(fields, sort_keys=True, default=str)


def _reset_time(facts: Facts):
    def walk(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in RESET_KEYS:
                    yield item
                yield from walk(item)
        elif isinstance(value, list):
            for item in value:
                yield from walk(item)
    for value in walk(facts.error_fields or {}):
        if isinstance(value, (int, float)) and value > 1e9:
            return float(value)
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
            except ValueError:
                continue
    return None


def classify(facts: Facts, now: float, k: int = 0) -> Outcome:
    """Map one finished (or never-started) call to exactly one §5.3 outcome."""
    if facts.cancelled:
        return Outcome(1, 'cancelled')
    if facts.interrupted:
        return Outcome(2, 'interrupted')
    if facts.spawn_error or facts.binary_mismatch:
        return Outcome(3, 'env_block', disable='profile')
    if facts.sandbox_error or (facts.rc not in (0, None) and facts.stdout_bytes == 0
                               and facts.stderr.lstrip().startswith('bwrap:')):
        return Outcome(4, 'env_block', disable='sandbox')
    if facts.timed_out:
        return Outcome(5, 'timeout', coding_budget=True)
    if facts.rc == 0 and facts.native_status == 'success' and facts.final_valid and facts.model_match is not False:
        return Outcome(6, 'ok')
    if facts.native_status == 'success' and facts.model_match is False:
        return Outcome(7, 'model_mismatch', disable='profile')
    diagnostic = _diagnostic(facts)
    if QUOTA.search(diagnostic):
        reset = _reset_time(facts)
        delay = min(QUOTA_BASE * 2 ** k, QUOTA_CAP)
        return Outcome(8, 'quota_wait', account_wait=True,
                       recheck_at=reset if reset and reset > now else now + delay)
    if AUTH.search(diagnostic):
        return Outcome(9, 'auth_wait', account_wait=True, recheck_at=now + AUTH_WAIT)
    if NETWORK.search(diagnostic):
        return Outcome(10, 'env_wait', account_wait=True, recheck_at=now + min(ENV_BASE * 2 ** k, ENV_CAP))
    if (facts.rc == 2 and USAGE.search(diagnostic)) or MODEL_CONFIG.search(diagnostic):
        return Outcome(11, 'config_block', disable='profile')
    if facts.turn_limit:
        return Outcome(12, 'turn_limit', coding_budget=True)
    if facts.rc == 0 and (facts.native_status == 'absent' or
                          (facts.native_status == 'success' and not facts.final_valid)):
        return Outcome(13, 'protocol')
    return Outcome(14, 'unknown_fail', coding_budget=True, profile_strike=True)


def task_fail() -> Outcome:
    """Row 15: native call ok, but deterministic gate or independent review failed."""
    return Outcome(15, 'task_fail', coding_budget=True)
