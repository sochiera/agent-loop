from pathlib import Path


def test_removed_pipeline_symbols_do_not_return() -> None:
    forbidden = (
        "_legacy_iteration",
        "_run_micro_loop",
        "_apply_done_reject_policy",
        "red_gate_ok",
        "tester_path_violations",
        "coder_test_violations",
        "weakening_candidates",
        "snapshot_cycle_tests",
        "restore_test_changes",
        "anti_weakening",
        "gate_not_red",
        "done_reject",
        "max_green_retries",
        "legacy_mode",
    )
    root = Path(__file__).parents[1] / "forge"
    found = {
        symbol: str(path.relative_to(root.parent))
        for path in root.rglob("*.py")
        for symbol in forbidden
        if symbol in path.read_text(encoding="utf-8")
    }
    assert not found, f"wróciły elementy usuniętego pipeline'u: {found}"
