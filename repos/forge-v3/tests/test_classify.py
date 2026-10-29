"""§5.3 total ordered classifier: one row per case, first match wins."""

import pytest

from forge.classify import Facts, classify, task_fail

NOW = 1_000_000.0
OK = dict(rc=0, native_status='success', final_valid=True, stdout_bytes=10)


@pytest.mark.parametrize('facts, row, outcome', [
    (Facts(cancelled=True, timed_out=True), 1, 'cancelled'),
    (Facts(interrupted=True), 2, 'interrupted'),
    (Facts(spawn_error='FileNotFoundError: ENOENT'), 3, 'env_block'),
    (Facts(binary_mismatch=True, **OK), 3, 'env_block'),
    (Facts(sandbox_error='mount source replaced'), 4, 'env_block'),
    (Facts(rc=1, stderr='bwrap: Can\'t find source path /x'), 4, 'env_block'),
    (Facts(timed_out=True, rc=-9), 5, 'timeout'),
    (Facts(**OK), 6, 'ok'),
    (Facts(**OK, model_match=True), 6, 'ok'),
    (Facts(**OK, model_match=False), 7, 'model_mismatch'),
    (Facts(rc=1, error_fields={'native_errors': [{'message': 'usage_limit_reached'}]}), 8, 'quota_wait'),
    (Facts(rc=1, stderr='HTTP 429 Too Many Requests'), 8, 'quota_wait'),
    (Facts(rc=1, error_fields={'native_result': {'api_error_status': 'rate_limit_error'}}), 8, 'quota_wait'),
    (Facts(rc=1, stderr='Error: 401 Unauthorized'), 9, 'auth_wait'),
    (Facts(rc=1, stderr='not logged in, please run login'), 9, 'auth_wait'),
    (Facts(rc=1, stderr='connect ECONNRESET'), 10, 'env_wait'),
    (Facts(rc=1, stderr='503 service overloaded'), 10, 'env_wait'),
    (Facts(rc=2, stderr="error: unexpected argument '--bogus' found\nUsage: codex exec"), 11, 'config_block'),
    (Facts(rc=1, stderr='model_not_found: gpt-x'), 11, 'config_block'),
    (Facts(rc=1, stderr='unsupported reasoning effort xhigh for model'), 11, 'config_block'),
    (Facts(rc=1, native_status='failure', turn_limit=True), 12, 'turn_limit'),
    (Facts(rc=0, native_status='absent'), 13, 'protocol'),
    (Facts(rc=0, native_status='success', final_valid=False), 13, 'protocol'),
    (Facts(rc=0, native_status='failure'), 14, 'unknown_fail'),
    (Facts(rc=1, stderr='segfault'), 14, 'unknown_fail'),
    (Facts(rc=2, stderr='weird'), 14, 'unknown_fail'),
])
def test_classify_table(facts, row, outcome):
    result = classify(facts, NOW)
    assert (result.row, result.outcome) == (row, outcome)


def test_successful_prose_cannot_trigger_quota_or_auth():
    # Quota/auth words in the final answer of a success never reach the patterns (row 6 first;
    # final text is never part of error_fields).
    facts = Facts(**OK, error_fields={'stderr': ''})
    assert classify(facts, NOW).outcome == 'ok'
    # Even a stderr warning mentioning 429 cannot turn a structured success into a wait.
    assert classify(Facts(**OK, stderr='retrying after 429'), NOW).outcome == 'ok'


def test_effects_budget_and_disable():
    assert classify(Facts(timed_out=True), NOW).coding_budget
    for facts in (Facts(rc=1, stderr='429'), Facts(rc=1, stderr='401'), Facts(rc=0, native_status='absent'),
                  Facts(**OK, model_match=False), Facts(sandbox_error='x'), Facts(cancelled=True)):
        assert not classify(facts, NOW).coding_budget
    unknown = classify(Facts(rc=3), NOW)
    assert unknown.coding_budget and unknown.profile_strike
    assert classify(Facts(sandbox_error='x'), NOW).disable == 'sandbox'
    assert classify(Facts(rc=2, stderr='invalid value for --model'), NOW).disable == 'profile'
    assert task_fail().row == 15 and task_fail().coding_budget


def test_waits_have_finite_recheck_and_backoff():
    quota = [classify(Facts(rc=1, stderr='quota exceeded'), NOW, k).recheck_at - NOW for k in range(6)]
    assert quota == [900, 1800, 3600, 7200, 14400, 14400]
    assert classify(Facts(rc=1, stderr='401'), NOW).recheck_at == NOW + 3600
    env = [classify(Facts(rc=1, stderr='ENOTFOUND'), NOW, k).recheck_at - NOW for k in range(5)]
    assert env == [600, 1200, 2400, 4800, 7200]
    reset = classify(Facts(rc=1, error_fields={'error': {'message': 'rate limit', 'resets_at': '2030-01-01T00:00:00Z'}}),
                     NOW)
    assert reset.recheck_at == 1893456000.0
    past = classify(Facts(rc=1, error_fields={'e': {'msg': 'quota', 'reset_at': NOW - 5}}), NOW)
    assert past.recheck_at == NOW + 900


def test_real_selected_model_capacity_is_environment_wait_not_coding_failure():
    """Captured P5 native resource error, not a fabricated successful provider reply."""
    from forge.classify import Facts, classify
    facts = Facts(rc=1, native_status='failure', error_fields={'native_errors': [
        {'type':'error','message':'Selected model is at capacity. Please try a different model.'}]})
    outcome = classify(facts, 1000)
    assert outcome.outcome == 'env_wait'
    assert outcome.account_wait and outcome.recheck_at == 1600
    assert not outcome.coding_budget and not outcome.profile_strike
