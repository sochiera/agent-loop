from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from forge import orchestrate, prompts
from forge.agents import AgentError
from forge.config import Config
from forge.state import State
from forge.verify import (collect_evidence, confirm_env_issue, expand_sha,
                          run_repro)


class VerifierConfigTest(unittest.TestCase):
    def test_verifier_defaults_to_planner_role(self) -> None:
        cfg = Config()
        cfg.planner_agent, cfg.planner_model, cfg.planner_effort = "claude", "opus", "high"
        cfg.verifier_agent = ""

        self.assertEqual(cfg.role("verifier"), ("claude", "opus", "high"))

    def test_explicit_verifier_uses_fixed_role_matrix(self) -> None:
        cfg = Config()
        cfg.planner_agent, cfg.planner_model = "claude", "opus"
        cfg.verifier_agent, cfg.verifier_model, cfg.verifier_effort = "codex", "", "low"
        cfg.codex_model = "gpt-test"

        self.assertEqual(cfg.role("verifier"),
                         ("codex", "gpt-5.6-terra", "medium"))

    def test_agents_in_use_includes_explicit_verifier(self) -> None:
        cfg = Config()
        cfg.legacy_mode = False
        cfg.planner_agent = "claude"
        cfg.tester_agent = cfg.coder_agent = "codex"
        cfg.verifier_agent = "grok"
        cfg.reviewer_agent = ""  # izoluj od domyślnego opencode — dziedziczy testera (codex)

        self.assertEqual(cfg.agents_in_use(), ["claude", "codex", "grok"])

    def test_agents_in_use_deduplicates_default_verifier(self) -> None:
        cfg = Config()
        cfg.legacy_mode = False
        cfg.planner_agent = "claude"
        cfg.tester_agent = cfg.coder_agent = "codex"
        cfg.verifier_agent = ""
        cfg.reviewer_agent = ""  # izoluj od domyślnego opencode — dziedziczy testera (codex)

        self.assertEqual(cfg.agents_in_use(), ["claude", "codex"])

    def test_targets_override_none_disables_and_csv_replaces(self) -> None:
        cfg = Config()
        cfg.verify_targets_override = "none"
        self.assertEqual(cfg.effective_verify_targets(["ci", "smoke"]), [])

        cfg.verify_targets_override = "ci, hardware"
        self.assertEqual(cfg.effective_verify_targets(["smoke"]), ["ci", "hardware"])

        cfg.verify_targets_override = ""
        self.assertEqual(cfg.effective_verify_targets(["smoke"]), ["smoke"])

    def test_verify_knobs_have_sane_defaults(self) -> None:
        cfg = Config()
        self.assertGreaterEqual(cfg.max_verify_cycles, cfg.max_stall_cycles)
        self.assertGreater(cfg.ci_timeout_s, cfg.ci_poll_max_s)
        self.assertGreaterEqual(cfg.flash_retries, 1)
        self.assertGreater(cfg.max_repro_runs_per_task, 1)
        self.assertTrue(cfg.ci_early_warn)




def _script(tmp: Path, name: str, body: str) -> str:
    """Zapisz wykonywalny skrypt i zwróć komendę uruchamiającą go."""
    path = tmp / name
    path.write_text("#!/usr/bin/env bash\n" + body + "\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return f"bash {path}"


class ExpandShaTest(unittest.TestCase):
    def test_placeholder_is_replaced_everywhere(self) -> None:
        self.assertEqual(expand_sha("ci.sh {sha} --ref {sha}", "abc"),
                         "ci.sh abc --ref abc")
        self.assertEqual(expand_sha("ci.sh", "abc"), "ci.sh")


class EvidenceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.project = self.tmp / "proj"
        self.project.mkdir()
        self.cycle_dir = str(self.tmp / "cycle-1")
        self.cfg = Config()
        self.cfg.verify_timeout_s = 30
        self.cfg.flash_retries = 1

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_smoke_evidence_records_rc_and_full_log(self) -> None:
        state = State(verify_targets=["smoke"],
                      smoke_cmd=_script(self.tmp, "smoke.sh",
                                        "echo dziala; echo blad >&2; exit 3"))

        results = collect_evidence(str(self.project), state, self.cfg,
                                   self.cycle_dir, sha="", sleep=lambda s: None)

        self.assertEqual(results["smoke"]["rc"], 3)
        log = Path(results["smoke"]["log"]).read_text(encoding="utf-8")
        self.assertIn("dziala", log)
        self.assertIn("blad", log)

    def test_hardware_flash_gets_free_retry_then_runs_target(self) -> None:
        marker = self.tmp / "flash_count"
        flash = _script(self.tmp, "flash.sh",
                        f'n=$(cat {marker} 2>/dev/null || echo 0); '
                        f'echo $((n+1)) > {marker}; '
                        f'[ "$n" -ge 1 ] && exit 0 || exit 1')
        target = _script(self.tmp, "target.sh", "echo SERIAL-OK; exit 0")
        state = State(verify_targets=["hardware"], flash_cmd=flash, target_cmd=target)

        results = collect_evidence(str(self.project), state, self.cfg,
                                   self.cycle_dir, sha="", sleep=lambda s: None)

        self.assertEqual(results["hardware"]["rc"], 0)
        self.assertEqual(marker.read_text().strip(), "2")  # 1 porażka + 1 retry
        self.assertIn("SERIAL-OK",
                      Path(results["hardware"]["log"]).read_text(encoding="utf-8"))

    def test_hardware_flash_exhausted_skips_target(self) -> None:
        flash = _script(self.tmp, "flash.sh", "exit 1")
        sentinel = self.tmp / "target_ran"
        target = _script(self.tmp, "target.sh", f"touch {sentinel}; exit 0")
        state = State(verify_targets=["hardware"], flash_cmd=flash, target_cmd=target)

        results = collect_evidence(str(self.project), state, self.cfg,
                                   self.cycle_dir, sha="", sleep=lambda s: None)

        self.assertEqual(results["hardware"]["rc"], 1)
        self.assertFalse(sentinel.exists())

    def test_ci_polls_until_verdict_and_fetches_failure_logs(self) -> None:
        marker = self.tmp / "polls"
        status = _script(self.tmp, "status.sh",
                         f'n=$(cat {marker} 2>/dev/null || echo 0); '
                         f'echo $((n+1)) > {marker}; '
                         f'[ "$1" = "abc123" ] || exit 9; '
                         f'[ "$n" -ge 2 ] && exit 1 || exit 2')
        logs = _script(self.tmp, "logs.sh", "echo CI-FAILURE-LOG")
        state = State(verify_targets=["ci"],
                      ci_status_cmd=status + " {sha}", ci_logs_cmd=logs + " {sha}")
        delays: list[float] = []

        results = collect_evidence(str(self.project), state, self.cfg,
                                   self.cycle_dir, sha="abc123",
                                   sleep=delays.append)

        self.assertEqual(results["ci"]["rc"], 1)
        self.assertEqual(marker.read_text().strip(), "3")  # 2× "trwa" + werdykt
        self.assertEqual(len(delays), 2)
        self.assertLessEqual(delays[0], delays[1])  # backoff nie maleje
        self.assertIn("CI-FAILURE-LOG",
                      Path(results["ci"]["log"]).read_text(encoding="utf-8"))

    def test_ci_timeout_returns_none_not_green(self) -> None:
        status = _script(self.tmp, "status.sh", "exit 2")  # wiecznie "trwa"
        state = State(verify_targets=["ci"], ci_status_cmd=status + " {sha}",
                      ci_logs_cmd="")
        self.cfg.ci_timeout_s = 0  # natychmiastowy deadline

        results = collect_evidence(str(self.project), state, self.cfg,
                                   self.cycle_dir, sha="abc", sleep=lambda s: None)

        self.assertIsNone(results["ci"]["rc"])


class ReproAndEnvConfirmTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.project = str(self.tmp / "proj")
        os.makedirs(self.project)
        self.cfg = Config()
        self.cfg.verify_timeout_s = 30

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_repro_red_and_green_with_tail(self) -> None:
        red = _script(self.tmp, "red.sh", "echo objaw-bledu; exit 1")
        green, tail = run_repro(self.project, red, 30)
        self.assertFalse(green)
        self.assertIn("objaw-bledu", tail)

        ok = _script(self.tmp, "ok.sh", "exit 0")
        green, tail = run_repro(self.project, ok, 30)
        self.assertTrue(green)
        self.assertEqual(tail, "")

    def test_env_issue_confirmed_only_when_reproducibly_red(self) -> None:
        state = State(verify_targets=["smoke"],
                      smoke_cmd=_script(self.tmp, "bad.sh", "exit 1"))
        self.assertTrue(confirm_env_issue(self.project, state, self.cfg,
                                          "smoke", str(self.tmp / "confirm"),
                                          sha="", sleep=lambda s: None))

        state.smoke_cmd = _script(self.tmp, "good.sh", "exit 0")
        self.assertFalse(confirm_env_issue(self.project, state, self.cfg,
                                           "smoke", str(self.tmp / "confirm2"),
                                           sha="", sleep=lambda s: None))

    def test_hardware_env_confirmation_uses_probe_when_declared(self) -> None:
        # Odpięta płytka: probe czerwony → potwierdzone bez flashowania.
        sentinel = self.tmp / "flashed"
        state = State(verify_targets=["hardware"],
                      probe_cmd=_script(self.tmp, "probe.sh", "exit 1"),
                      flash_cmd=_script(self.tmp, "flash.sh", f"touch {sentinel}; exit 0"),
                      target_cmd=_script(self.tmp, "t.sh", "exit 0"))

        confirmed = confirm_env_issue(self.project, state, self.cfg, "hardware",
                                      str(self.tmp / "confirm"), sha="",
                                      sleep=lambda s: None)

        self.assertTrue(confirmed)
        self.assertFalse(sentinel.exists())


class VerifyPromptsTest(unittest.TestCase):
    def test_bootstrap_prompt_demands_verification_profile(self) -> None:
        prompt = prompts.bootstrap_prompt("brief")
        self.assertIn('"verify"', prompt)
        self.assertIn('"targets"', prompt)
        self.assertIn("ci_status_cmd", prompt)
        self.assertIn("{sha}", prompt)  # placeholder komend CI, rozwijany przez expand_sha
        self.assertIn("verify_test_globs", prompt)

    def test_verify_goal_prompt_carries_evidence_and_ledger_contract(self) -> None:
        evidence = {"ci": {"rc": 1, "log": ".forge/verification/cycle-2/ci.log"},
                    "smoke": {"rc": 0, "log": ".forge/verification/cycle-2/smoke.log"}}
        prompt = prompts.verify_goal_prompt(
            cycle=2, evidence=evidence,
            cycle_dir=".forge/verification/cycle-2",
            prev_problems_path=".forge/verification/cycle-1/problems.json",
            run_cmd="python game.py")

        self.assertIn("cycle-2/ci.log", prompt)
        self.assertIn("rc=1", prompt)
        self.assertIn("rc=0", prompt)
        self.assertIn("cycle-1/problems.json", prompt)   # rejestr do odhaczenia
        self.assertIn("feedback.md", prompt)
        for token in ("resolved", "persisting", "new", "code_bug",
                      "verify_defect", "env_issue", "flaky", "design_gap",
                      "repro", "criterion"):
            self.assertIn(token, prompt)

    def test_verify_goal_prompt_first_cycle_has_no_previous_ledger(self) -> None:
        prompt = prompts.verify_goal_prompt(
            cycle=1, evidence={"smoke": {"rc": 0, "log": "s.log"}},
            cycle_dir=".forge/verification/cycle-1",
            prev_problems_path="", run_cmd="")
        self.assertNotIn("poprzedniego cyklu: ", prompt)

    def test_plan_batch_prompt_relays_verification_feedback_and_ci_warning(self) -> None:
        plain = prompts.plan_batch_prompt(5, 1, "app")
        self.assertNotIn('"fixes"', plain)

        with_feedback = prompts.plan_batch_prompt(
            5, 8, "app",
            verify_feedback_path=".forge/verification/cycle-3/feedback.md",
            ci_warning="CI dla HEAD czerwone")
        self.assertIn("cycle-3/feedback.md", with_feedback)
        self.assertIn('"fixes"', with_feedback)
        self.assertIn('"repro_cmd"', with_feedback)
        self.assertIn("CI dla HEAD czerwone", with_feedback)


class BootstrapVerifyProfileTest(unittest.TestCase):
    BASE = ('{"kind":"app","stack":"Python","test_cmd":"python -m unittest",'
            '"build_cmd":"","run_cmd":"python app.py"')

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.brief = Path(self._tmp.name) / "brief.md"
        self.brief.write_text("brief", encoding="utf-8")
        self.cfg = Config(brief_path=str(self.brief), agent_timeout_s=10,
                          max_bootstrap_arch_reviews=0)
        self.state = State()

    def _bootstrap(self, verify_json: str) -> None:
        payload = self.BASE + (f',"verify":{verify_json}' if verify_json else "") + "}"
        with mock.patch("forge.orchestrate.run_planner", return_value=payload), \
             mock.patch("forge.orchestrate.build_then_test", return_value=True), \
             mock.patch("forge.orchestrate.commit_all"):
            orchestrate.phase_bootstrap(self.cfg, self._tmp.name, self.state,
                                        lambda _: "agent.log")

    def test_declared_profile_lands_in_state(self) -> None:
        self._bootstrap('{"targets":["smoke","ci"],"smoke_cmd":"bash s.sh",'
                        '"ci_status_cmd":"bash c.sh {sha}","ci_logs_cmd":"bash l.sh {sha}",'
                        '"verify_test_globs":["tests/hil/**"]}')
        self.assertEqual(self.state.verify_targets, ["smoke", "ci"])
        self.assertEqual(self.state.smoke_cmd, "bash s.sh")
        self.assertEqual(self.state.ci_status_cmd, "bash c.sh {sha}")
        self.assertEqual(self.state.verify_test_globs, ["tests/hil/**"])

    def test_target_without_its_commands_is_a_bootstrap_error(self) -> None:
        with self.assertRaisesRegex(AgentError, "hardware"):
            self._bootstrap('{"targets":["hardware"],"flash_cmd":"bash f.sh"}')
        self.assertFalse(self.state.bootstrapped)

    def test_missing_verify_object_disables_verification(self) -> None:
        self._bootstrap("")
        self.assertEqual(self.state.verify_targets, [])
        self.assertTrue(self.state.bootstrapped)

    def test_user_override_none_wins_over_declaration(self) -> None:
        self.cfg.verify_targets_override = "none"
        self._bootstrap('{"targets":["smoke"],"smoke_cmd":"bash s.sh"}')
        self.assertEqual(self.state.verify_targets, [])

    def test_unknown_target_is_a_bootstrap_error(self) -> None:
        with self.assertRaisesRegex(AgentError, "produkcja"):
            self._bootstrap('{"targets":["produkcja"],"smoke_cmd":"x"}')


def _verdict(problems: list | None = None, verdict: str = "fail") -> str:
    import json
    return json.dumps({"verdict": verdict, "problems": problems or []})


class VerifyGoalPhaseTest(unittest.TestCase):
    """phase_verify_goal na mockach dowodów/agenta — testuje wyłącznie logikę
    orkiestracji (bramka PASS, odbiór rejestru, stall, env, wznowienie)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = self._tmp.name
        os.makedirs(os.path.join(self.project, "docs"))
        Path(self.project, "docs", "DESIGN.md").write_text(
            "Gracz może zapisać stan gry.", encoding="utf-8")
        Path(self.project, "BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")
        import subprocess as sp
        sp.run(["git", "init", "-q"], cwd=self.project, check=True)
        sp.run(["git", "add", "-A"], cwd=self.project, check=True)
        sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                "commit", "-q", "-m", "init"], cwd=self.project, check=True)
        self.cfg = Config()
        self.cfg.max_stall_cycles = 2
        self.cfg.max_verify_cycles = 8
        self.state = State(bootstrapped=True, verify_targets=["smoke"],
                           smoke_cmd="bash s.sh", test_cmd="t")
        self.logf = lambda ph: os.path.join(self.project, ".forge", "logs", f"{ph}.log")

    def _run(self, evidence_rc: int, agent_outputs: list[str],
             repro_green: bool = False, env_confirmed: bool = False):
        evidence = {"smoke": {"rc": evidence_rc,
                              "log": os.path.join(self.project, "s.log")}}
        patches = [
            mock.patch("forge.orchestrate.verify.collect_evidence",
                       return_value=evidence),
            mock.patch("forge.orchestrate.verify.run_repro",
                       return_value=(repro_green, "" if repro_green else "ogon")),
            mock.patch("forge.orchestrate.verify.confirm_env_issue",
                       return_value=env_confirmed),
            mock.patch("forge.orchestrate.run_agent", side_effect=agent_outputs),
            mock.patch("forge.orchestrate.commit_all"),
            mock.patch("forge.orchestrate._head_sha", return_value="sha123"),
        ]
        mocks = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        result = orchestrate.phase_verify_goal(self.cfg, self.project,
                                               self.state, self.logf)
        return result, mocks

    def test_green_evidence_and_clean_ledger_end_the_loop(self) -> None:
        cont, _ = self._run(0, [_verdict([], "pass")])
        self.assertFalse(cont)
        self.assertEqual(self.state.phase, "idle")
        self.assertEqual(self.state.verify_cycle, 1)

    def test_stray_product_edits_of_verifier_are_reverted(self) -> None:
        def naughty_agent(*_a, **_k):
            os.makedirs(os.path.join(self.project, "src"), exist_ok=True)
            Path(self.project, "src", "hack.py").write_text("x", encoding="utf-8")
            with open(os.path.join(self.project, "BACKLOG.md"), "a",
                      encoding="utf-8") as f:
                f.write("- notatka weryfikatora\n")
            return _verdict([], "pass")

        cont, _ = self._run(0, naughty_agent)

        self.assertFalse(cont)
        self.assertFalse(Path(self.project, "src", "hack.py").exists())
        backlog = Path(self.project, "BACKLOG.md").read_text(encoding="utf-8")
        self.assertIn("notatka weryfikatora", backlog)  # docs/BACKLOG dozwolone

    def test_agent_pass_cannot_override_red_evidence(self) -> None:
        cont, _ = self._run(1, [_verdict([], "pass")])
        self.assertTrue(cont)  # porażka → wracamy do planowania

    def test_fail_with_red_repro_stores_ledger_and_returns_to_planning(self) -> None:
        problem = {"id": "P-001", "status": "new", "class": "code_bug",
                   "title": "pad", "target": "smoke", "repro_cmd": "bash r.sh"}
        cont, _ = self._run(1, [_verdict([problem])])
        self.assertTrue(cont)
        self.assertEqual(self.state.phase, "idle")
        self.assertEqual(self.state.verify_problems[0]["id"], "P-001")
        problems_json = Path(self.project, ".forge", "verification",
                             "cycle-1", "problems.json")
        self.assertTrue(problems_json.exists())
        feedback = Path(self.project, ".forge", "verification",
                        "cycle-1", "feedback.md")
        self.assertTrue(feedback.exists())  # fallback, gdy agent nie napisał

    def test_green_repro_drops_problem_and_allows_pass_with_notes(self) -> None:
        problem = {"id": "P-001", "status": "new", "class": "code_bug",
                   "title": "nie odtwarza się", "target": "smoke",
                   "repro_cmd": "bash r.sh"}
        cont, _ = self._run(0, [_verdict([problem])], repro_green=True)
        self.assertFalse(cont)  # zieleń + brak ważnych blokerów = PASS-z-notatkami
        backlog = Path(self.project, "BACKLOG.md").read_text(encoding="utf-8")
        self.assertIn("P-001", backlog)
        # odrzucony problem NIE zostaje otwartym wpisem rejestru (nie wymusza
        # odhaczania w następnym cyklu, nie liczy się jako otwarty bloker)
        stored = self.state.verify_problems[0]
        self.assertEqual(stored["status"], "resolved")
        self.assertTrue(stored.get("resolution"))

    def test_design_gap_with_real_criterion_blocks_pass(self) -> None:
        gap = {"id": "P-002", "status": "new", "class": "design_gap",
               "title": "zapis nie działa", "target": "behavior",
               "criterion": "gracz może zapisać stan gry"}
        cont, _ = self._run(0, [_verdict([gap])])
        self.assertTrue(cont)

    def test_design_gap_without_criterion_degrades_to_note(self) -> None:
        gap = {"id": "P-002", "status": "new", "class": "design_gap",
               "title": "nie podoba mi się", "target": "behavior",
               "criterion": "zmyślone kryterium"}
        cont, _ = self._run(0, [_verdict([gap])])
        self.assertFalse(cont)
        self.assertIn("P-002", Path(self.project, "BACKLOG.md").read_text("utf-8"))

    def test_degraded_gap_is_terminal_and_does_not_fake_progress(self) -> None:
        self.state.verify_problems = [{"id": "P-001", "status": "new",
                                       "class": "code_bug", "title": "pad",
                                       "repro_cmd": "bash r.sh"}]
        self.state.verify_cycle = 1
        verdict = _verdict([
            {"id": "P-001", "status": "persisting", "class": "code_bug",
             "title": "pad", "target": "smoke", "repro_cmd": "bash r.sh"},
            {"id": "P-002", "status": "new", "class": "design_gap",
             "title": "widzimisię", "target": "behavior",
             "criterion": "zmyślone kryterium"},
        ])

        cont, _ = self._run(1, [verdict])

        self.assertTrue(cont)
        # degradacja nie jest postępem: nic realnie nie rozwiązano → stall
        self.assertEqual(self.state.verify_stall, 1)
        by_id = {p["id"]: p for p in self.state.verify_problems}
        self.assertEqual(by_id["P-002"]["status"], "resolved")  # terminalny
        self.assertTrue(by_id["P-002"].get("resolution"))
        self.assertEqual(by_id["P-001"]["status"], "persisting")

    def test_incomplete_ledger_gets_one_retry_then_agent_error(self) -> None:
        self.state.verify_problems = [{"id": "P-009", "status": "new",
                                       "class": "code_bug", "title": "stary"}]
        with self.assertRaisesRegex(AgentError, "P-009"):
            self._run(1, [_verdict([]), _verdict([])])

    def test_confirmed_env_issue_stops_the_run(self) -> None:
        env = {"id": "P-003", "status": "new", "class": "env_issue",
               "title": "brak płytki", "target": "smoke"}
        with self.assertRaises(orchestrate.VerificationStop):
            self._run(1, [_verdict([env])], env_confirmed=True)
        self.assertTrue(Path(self.project, ".forge", "verification",
                             "cycle-1", "ENV-ISSUE.md").exists())

    def test_unconfirmed_env_issue_is_reclassified_and_run_continues(self) -> None:
        env = {"id": "P-003", "status": "new", "class": "env_issue",
               "title": "rzekomo brak płytki", "target": "smoke"}
        cont, _ = self._run(1, [_verdict([env])], env_confirmed=False)
        self.assertTrue(cont)
        stored = self.state.verify_problems[0]
        self.assertEqual(stored["class"], "code_bug")

    def test_two_cycles_without_progress_stop_the_run(self) -> None:
        problem = {"id": "P-001", "status": "persisting", "class": "code_bug",
                   "title": "pad", "target": "smoke", "repro_cmd": "bash r.sh"}
        self.state.verify_problems = [dict(problem, status="new")]
        self.state.verify_cycle = 1
        cont, _ = self._run(1, [_verdict([problem])])
        self.assertTrue(cont)  # stall=1 — jeszcze wolno
        self.assertEqual(self.state.verify_stall, 1)

        self.state.phase = "idle"
        with self.assertRaises(orchestrate.VerificationStop):
            self._run(1, [_verdict([problem])])  # stall=2 → stop


class VerifyGoalWiringTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.project = self._tmp.name
        self.cfg = Config()

    def test_no_more_tasks_with_targets_enters_verification(self) -> None:
        state = State(bootstrapped=True, verify_targets=["smoke"],
                      smoke_cmd="bash s.sh", test_cmd="t")
        with mock.patch("forge.orchestrate.phase_plan_batch",
                        return_value={"no_more_tasks": True}), \
             mock.patch("forge.orchestrate.phase_verify_goal",
                        return_value=False) as verify_phase:
            cont = orchestrate._task_iteration(self.cfg, self.project, state)
        verify_phase.assert_called_once()
        self.assertFalse(cont)

    def test_no_more_tasks_without_targets_ends_loop_as_before(self) -> None:
        state = State(bootstrapped=True, test_cmd="t")
        with mock.patch("forge.orchestrate.phase_plan_batch",
                        return_value={"no_more_tasks": True}), \
             mock.patch("forge.orchestrate.phase_verify_goal") as verify_phase:
            cont = orchestrate._task_iteration(self.cfg, self.project, state)
        verify_phase.assert_not_called()
        self.assertFalse(cont)

    def test_override_none_disables_verification_on_existing_project(self) -> None:
        # Projekt już zbootstrapowany z targetami — użytkownik wyłącza
        # weryfikację env-em bez edycji STATE.json.
        self.cfg.verify_targets_override = "none"
        state = State(bootstrapped=True, verify_targets=["smoke"],
                      smoke_cmd="bash s.sh", test_cmd="t")
        with mock.patch("forge.orchestrate.phase_plan_batch",
                        return_value={"no_more_tasks": True}), \
             mock.patch("forge.orchestrate.phase_verify_goal") as verify_phase:
            cont = orchestrate._task_iteration(self.cfg, self.project, state)
        verify_phase.assert_not_called()
        self.assertFalse(cont)

    def test_override_none_abandons_resumed_verify_goal_phase(self) -> None:
        self.cfg.verify_targets_override = "none"
        state = State(bootstrapped=True, verify_targets=["smoke"],
                      smoke_cmd="bash s.sh", test_cmd="t", phase="verify_goal")
        with mock.patch("forge.orchestrate.phase_plan_batch",
                        return_value={"no_more_tasks": True}), \
             mock.patch("forge.orchestrate.phase_verify_goal") as verify_phase:
            cont = orchestrate._task_iteration(self.cfg, self.project, state)
        verify_phase.assert_not_called()
        self.assertFalse(cont)
        self.assertEqual(state.phase, "idle")

    def test_override_narrows_targets_used_for_evidence(self) -> None:
        # collect_evidence honoruje jawną listę targetów (nadpisanie CSV).
        with tempfile.TemporaryDirectory() as tmp:
            state = State(verify_targets=["smoke", "ci"], smoke_cmd="true",
                          ci_status_cmd="false", ci_logs_cmd="false")
            cfg = Config()
            cfg.verify_timeout_s = 10
            results = collect_evidence(tmp, state, cfg,
                                       os.path.join(tmp, "c"), sha="",
                                       sleep=lambda s: None, targets=["smoke"])
        self.assertEqual(sorted(results), ["smoke"])

    def test_restart_in_verify_goal_resumes_verification_not_planning(self) -> None:
        state = State(bootstrapped=True, verify_targets=["smoke"],
                      smoke_cmd="bash s.sh", test_cmd="t",
                      phase="verify_goal", verify_cycle=2, verify_sha="abc")
        with mock.patch("forge.orchestrate.phase_plan_batch") as plan, \
             mock.patch("forge.orchestrate.phase_verify_goal",
                        return_value=True) as verify_phase:
            cont = orchestrate._task_iteration(self.cfg, self.project, state)
        plan.assert_not_called()
        verify_phase.assert_called_once()
        self.assertTrue(cont)


class TaskGateWithReproTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = Config()
        self.cfg.max_repro_runs_per_task = 3
        self.state = State(test_cmd="t", current_task={"repro_cmd": "bash r.sh"})

    def test_green_suite_but_red_repro_is_red_with_repro_tail(self) -> None:
        with mock.patch("forge.orchestrate.run_gate", return_value=(True, "")), \
             mock.patch("forge.orchestrate.verify.run_repro",
                        return_value=(False, "objaw")):
            green, tail = orchestrate._task_gate(self.cfg, "/p", self.state)
        self.assertFalse(green)
        self.assertIn("REPRO", tail)
        self.assertEqual(self.state.repro_runs, 1)

    def test_green_suite_and_green_repro_is_green(self) -> None:
        with mock.patch("forge.orchestrate.run_gate", return_value=(True, "")), \
             mock.patch("forge.orchestrate.verify.run_repro",
                        return_value=(True, "")):
            self.assertEqual(orchestrate._task_gate(self.cfg, "/p", self.state),
                             (True, ""))

    def test_red_suite_skips_repro(self) -> None:
        with mock.patch("forge.orchestrate.run_gate", return_value=(False, "boom")), \
             mock.patch("forge.orchestrate.verify.run_repro") as repro:
            green, tail = orchestrate._task_gate(self.cfg, "/p", self.state)
        repro.assert_not_called()
        self.assertEqual((green, tail), (False, "boom"))

    def test_repro_run_cap_fails_closed_without_touching_hardware(self) -> None:
        self.state.repro_runs = 3
        with mock.patch("forge.orchestrate.run_gate", return_value=(True, "")), \
             mock.patch("forge.orchestrate.verify.run_repro") as repro:
            green, tail = orchestrate._task_gate(self.cfg, "/p", self.state)
        repro.assert_not_called()
        self.assertFalse(green)
        self.assertIn("limit", tail)

    def test_task_without_repro_passes_suite_result_through(self) -> None:
        self.state.current_task = {}
        with mock.patch("forge.orchestrate.run_gate", return_value=(True, "")), \
             mock.patch("forge.orchestrate.verify.run_repro") as repro:
            self.assertEqual(orchestrate._task_gate(self.cfg, "/p", self.state),
                             (True, ""))
        repro.assert_not_called()


class NoTestWithReproTest(unittest.TestCase):
    def test_no_test_in_repro_task_does_not_count_towards_smell(self) -> None:
        import subprocess as sp
        with tempfile.TemporaryDirectory() as project:
            sp.run(["git", "init", "-q"], cwd=project, check=True)
            cfg = Config(max_micro_cycles=6, git_push=False)
            cfg.max_green_retries = 0
            state = State(test_cmd="pytest", current_task_title="T",
                          current_task={"file": "f", "criteria": [],
                                        "test_globs": ["tests/**"],
                                        "repro_cmd": "bash r.sh"},
                          phase="micro", micro_sub="test", no_test_count=2)

            with mock.patch("forge.orchestrate._session_call",
                            return_value='{"action":"no_test","reason":"strukturalny"}'), \
                 mock.patch("forge.orchestrate._task_gate",
                            return_value=(False, "czerwono")):
                reached = orchestrate._run_micro_loop(
                    cfg, project, state, lambda ph: os.path.join(project, "log"))

            # bez smellu (licznik stoi), zadanie pada na bramce, nie na recenzji
            self.assertEqual(state.no_test_count, 2)
            self.assertFalse(reached)


class ProtectedVerifyPathsTest(unittest.TestCase):
    def test_violations_only_when_not_allowed(self) -> None:
        changed = [".github/workflows/ci.yml", "src/a.py", "tests/hil/test_x.py"]
        globs = [".github/workflows/**", "tests/hil/**"]
        self.assertEqual(
            orchestrate.verify_protected_violations(changed, globs, allowed=False),
            [".github/workflows/ci.yml", "tests/hil/test_x.py"])
        self.assertEqual(
            orchestrate.verify_protected_violations(changed, globs, allowed=True), [])

    def test_exempt_files_are_not_violations(self) -> None:
        changed = ["tests/hil/test_new.py", "tests/hil/test_old.py"]
        globs = ["tests/hil/**"]
        self.assertEqual(
            orchestrate.verify_protected_violations(
                changed, globs, allowed=False, exempt={"tests/hil/test_new.py"}),
            ["tests/hil/test_old.py"])

    def test_protected_exempt_covers_new_target_tests_and_cycle_files(self) -> None:
        import subprocess as sp
        with tempfile.TemporaryDirectory() as project:
            sp.run(["git", "init", "-q"], cwd=project, check=True)
            os.makedirs(os.path.join(project, "tests", "hil"))
            os.makedirs(os.path.join(project, ".github", "workflows"))
            # śledzony, istniejący test targetowy (jego edycja = osłabianie)
            Path(project, "tests", "hil", "test_old.py").write_text("x", encoding="utf-8")
            sp.run(["git", "add", "-A"], cwd=project, check=True)
            sp.run(["git", "-c", "user.name=t", "-c", "user.email=t@t",
                    "commit", "-q", "-m", "init"], cwd=project, check=True)
            # NOWY test targetowy (tworzenie specyfikacji — legalne)
            Path(project, "tests", "hil", "test_new.py").write_text("y", encoding="utf-8")
            # NOWY workflow — konfiguracja CI zostaje chroniona także jako nowa
            Path(project, ".github", "workflows", "new.yml").write_text("z", encoding="utf-8")
            state = State(verify_test_globs=["tests/hil/**"],
                          cycle_test_files=["tests/cycle_test.py"])

            exempt = orchestrate._protected_exempt(project, state)

        self.assertIn("tests/hil/test_new.py", exempt)
        self.assertIn("tests/cycle_test.py", exempt)
        self.assertNotIn("tests/hil/test_old.py", exempt)
        self.assertNotIn(".github/workflows/new.yml", exempt)

    def test_task_may_touch_verify_only_for_verify_defect_fix(self) -> None:
        state = State(verify_problems=[
            {"id": "P-001", "status": "new", "class": "verify_defect", "title": "t"},
            {"id": "P-002", "status": "new", "class": "code_bug", "title": "t"}])
        self.assertTrue(orchestrate._task_may_touch_verify(state, {"fixes": "P-001"}))
        self.assertFalse(orchestrate._task_may_touch_verify(state, {"fixes": "P-002"}))
        self.assertFalse(orchestrate._task_may_touch_verify(state, {}))

    def test_protected_globs_include_declared_script_paths(self) -> None:
        with tempfile.TemporaryDirectory() as project:
            os.makedirs(os.path.join(project, "scripts"))
            Path(project, "scripts", "smoke.sh").write_text("exit 0", encoding="utf-8")
            state = State(smoke_cmd="bash scripts/smoke.sh",
                          verify_test_globs=["tests/hil/**"])
            cfg = Config()

            globs = orchestrate._verify_protected_globs(project, cfg, state)

        self.assertIn(".github/workflows/**", globs)
        self.assertIn("tests/hil/**", globs)
        self.assertIn("scripts/smoke.sh", globs)

    def test_directly_invoked_script_is_protected_too(self) -> None:
        # smoke_cmd bez prefiksu 'bash' — ścieżka skryptu jest w tokens[0].
        with tempfile.TemporaryDirectory() as project:
            os.makedirs(os.path.join(project, "scripts"))
            Path(project, "scripts", "smoke.sh").write_text("exit 0", encoding="utf-8")
            state = State(smoke_cmd="./scripts/smoke.sh")

            from forge.verify import verify_script_paths
            paths = verify_script_paths(project, state)

        self.assertEqual(paths, ["./scripts/smoke.sh"])


class PointlessFixTaskTest(unittest.TestCase):
    def test_green_repro_at_task_start_closes_task_without_micro_tdd(self) -> None:
        import subprocess as sp
        with tempfile.TemporaryDirectory() as project:
            sp.run(["git", "init", "-q"], cwd=project, check=True)
            os.makedirs(os.path.join(project, ".forge", "tasks"))
            Path(project, ".forge", "tasks", "task-001.md").write_text(
                "# Zadanie", encoding="utf-8")
            cfg = Config(git_push=False)
            state = State(bootstrapped=True, test_cmd="t",
                          task_queue=[{"id": "task-001", "title": "Naprawa P-001",
                                       "file": ".forge/tasks/task-001.md",
                                       "criteria": [], "test_globs": [],
                                       "code_globs": [], "fixes": "P-001",
                                       "repro_cmd": "bash r.sh"}])

            with mock.patch("forge.orchestrate.verify.run_repro",
                            return_value=(True, "")), \
                 mock.patch("forge.orchestrate._run_micro_loop") as micro, \
                 mock.patch("forge.orchestrate.commit_all"):
                cont = orchestrate._task_iteration(cfg, project, state)

        micro.assert_not_called()
        self.assertTrue(cont)
        self.assertEqual(state.last_done, "Naprawa P-001")


class CiEarlyWarnTest(unittest.TestCase):
    def _plan(self, status_rc: int, early_warn: bool = True) -> str:
        with tempfile.TemporaryDirectory() as project:
            cfg = Config()
            cfg.ci_early_warn = early_warn
            state = State(verify_targets=["ci"],
                          ci_status_cmd="bash ci.sh {sha}", ci_logs_cmd="bash l.sh")
            with mock.patch("forge.orchestrate._run_shellfree",
                            return_value=(status_rc, "")), \
                 mock.patch("forge.orchestrate._head_sha", return_value="abc"), \
                 mock.patch("forge.orchestrate.commit_all"), \
                 mock.patch("forge.orchestrate.run_planner",
                            return_value='{"no_more_tasks": false, "tasks": []}')\
                 as planner:
                orchestrate.phase_plan_batch(cfg, project, state,
                                             lambda ph: os.path.join(project, "log"))
            return planner.call_args.args[0]

    def test_red_head_ci_warns_the_planner(self) -> None:
        self.assertIn("CZERWONE", self._plan(1))

    def test_green_or_running_ci_stays_silent(self) -> None:
        self.assertNotIn("CZERWONE", self._plan(0))
        self.assertNotIn("CZERWONE", self._plan(2))

    def test_early_warn_can_be_disabled(self) -> None:
        with mock.patch("forge.orchestrate._run_shellfree") as run:
            self.assertNotIn("CZERWONE", self._plan(1, early_warn=False))


class VerifierMcpConfigTest(unittest.TestCase):
    def test_run_claude_appends_mcp_config_only_when_given(self) -> None:
        from forge import agents
        cfg = Config()
        with mock.patch("forge.agents._run_with_backoff",
                        return_value='{"result": "ok"}') as run:
            agents.run_claude("p", cfg, "/proj", "/log", model="opus",
                              effort="high", mcp_config="/mcp.json")
            argv = run.call_args.args[0]
            self.assertIn("--mcp-config", argv)
            self.assertIn("/mcp.json", argv)

            agents.run_claude("p", cfg, "/proj", "/log", model="opus", effort="high")
            argv = run.call_args.args[0]
            self.assertNotIn("--mcp-config", argv)

    def test_verifier_role_passes_mcp_config_from_cfg(self) -> None:
        cfg = Config()
        cfg.planner_agent, cfg.planner_model, cfg.planner_effort = "claude", "opus", "high"
        cfg.verifier_mcp_config = "/mcp.json"
        state = State(verify_targets=["smoke"], smoke_cmd="s", verify_cycle=1)
        evidence = {"smoke": {"rc": 0, "log": "s.log"}}
        with mock.patch("forge.orchestrate.run_agent",
                        return_value='{"verdict":"pass","problems":[]}') as run:
            orchestrate._accept_verdict(cfg, "/proj", state, evidence,
                                        "/cdir", lambda ph: "/log")
        self.assertEqual(run.call_args.kwargs.get("mcp_config"), "/mcp.json")


if __name__ == "__main__":
    unittest.main()
