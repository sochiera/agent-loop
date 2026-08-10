from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge import routing
from forge.config import Config, TASK_DIFFICULTIES


def _routing(payload: dict) -> routing.Routing:
    return routing.parse(payload, TASK_DIFFICULTIES)


class TestParsing:
    def test_unknown_roles_and_difficulties_are_skipped(self) -> None:
        parsed = _routing({"roles": {
            "nie_ma_takiej": {"agent": "claude"},
            "coder": {"slots": {"simple": {"model": "a"}, "wat": {"model": "b"}}},
        }})

        assert "nie_ma_takiej" not in parsed.roles
        assert set(parsed.roles["coder"].slots) == {"simple"}

    def test_values_impossible_to_pass_in_argv_are_dropped(self) -> None:
        parsed = _routing({"roles": {"coder": {
            "agent": "cla\nude",
            "slots": {"simple": {"model": "x" * 400}},
        }}})

        assert parsed.roles == {}

    def test_broken_file_means_project_policy(self, tmp_path: Path) -> None:
        path = tmp_path / "routing.json"
        path.write_text("{to nie jest json", encoding="utf-8")

        assert routing.load(path, TASK_DIFFICULTIES).roles == {}

    def test_round_trip_through_disk(self, tmp_path: Path) -> None:
        original = _routing({"roles": {"coder": {
            "agent": "opencode",
            "slots": {"complex": {"model": "zai-coding-plan/glm-5.2",
                                  "effort": "high"}},
            "fallbacks": [{"agent": "claude"}],
        }}})
        path = tmp_path / "sub" / "routing.json"

        routing.save(original, path)

        assert routing.load(path, TASK_DIFFICULTIES) == original
        assert not path.with_suffix(".json.tmp").exists()
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == 1


class TestSlots:
    def test_role_blind_to_difficulty_uses_the_shared_slot(self) -> None:
        parsed = _routing({"roles": {"planner": {
            "slots": {routing.ANY_DIFFICULTY: {"model": "opus"}}}}})

        for difficulty in TASK_DIFFICULTIES:
            assert parsed.slot("planner", difficulty).model == "opus"

    def test_shared_slot_covers_difficulties_left_empty(self) -> None:
        parsed = _routing({"roles": {"coder": {"slots": {
            routing.ANY_DIFFICULTY: {"model": "tani"},
            "complex": {"model": "mocny"},
        }}}})

        assert parsed.slot("coder", "simple").model == "tani"
        assert parsed.slot("coder", "complex").model == "mocny"

    def test_slot_carries_its_own_tool(self) -> None:
        # Wybór modelu przesądza o narzędziu, a model wybiera się per trudność:
        # zadanie proste bywa modelem lokalnym, złożone — najmocniejszym.
        parsed = _routing({"roles": {"coder": {"slots": {
            "simple": {"agent": "opencode", "model": "openai/gpt-5.6-luna"},
            "complex": {"agent": "claude", "model": "opus"},
        }}}})

        assert parsed.slot("coder", "simple").agent == "opencode"
        assert parsed.slot("coder", "complex").agent == "claude"


class TestConfigIntegration:
    def test_tool_of_the_slot_wins_over_the_tool_of_the_role(self) -> None:
        cfg = Config(routing=_routing({"roles": {"coder": {
            "agent": "opencode",
            "slots": {"complex": {"agent": "claude", "model": "opus",
                                  "effort": "high"}},
        }}}))

        assert cfg.role("coder", "complex") == ("claude", "opus", "high")
        # Trudności bez własnego slotu zostają przy narzędziu roli.
        assert cfg.role("coder", "simple")[0] == "opencode"

    def test_tool_chosen_in_the_slot_falls_back_to_its_own_level_policy(self) -> None:
        # Sam agent w slocie = „ten sam poziom, inne narzędzie": model ma
        # wyznaczyć polityka poziomu WYBRANEGO narzędzia, a nie pole roli
        # opisujące przestrzeń nazw innego CLI.
        cfg = Config(routing=_routing({"roles": {"tester": {
            "slots": {"standard": {"agent": "claude"}}}}}))

        assert cfg.role("tester", "standard") == ("claude", "sonnet", "medium")

    def test_master_refuses_codex_smuggled_into_a_slot(self) -> None:
        with pytest.raises(ValueError, match="Codex nie jest dostępny"):
            Config(routing=_routing({"roles": {"master": {
                "slots": {"complex": {"agent": "codex", "model": "gpt-5.6-sol"}}}}}))

    def test_chosen_model_wins_over_level_policy(self) -> None:
        cfg = Config(routing=_routing({"roles": {"coder": {
            "agent": "opencode",
            "slots": {"simple": {"model": "openai/gpt-5.6-luna",
                                 "effort": "low"}},
        }}}))

        assert cfg.role("coder", "simple") == (
            "opencode", "openai/gpt-5.6-luna", "low")
        # Trudność bez wyboru zostaje przy polityce projektu.
        assert cfg.role("coder", "complex")[1] == "zai-coding-plan/glm-5.2"

    def test_switching_the_tool_does_not_carry_over_a_foreign_model(self) -> None:
        # tester_model to nazwa z przestrzeni OpenCode — Claude by jej nie znał.
        cfg = Config(routing=_routing({"roles": {"tester": {"agent": "claude"}}}))

        assert cfg.role("tester", "standard") == ("claude", "sonnet", "medium")

    def test_product_owner_is_configurable_on_its_own(self) -> None:
        cfg = Config(routing=_routing({"roles": {
            "product_owner": {"agent": "claude",
                              "slots": {routing.ANY_DIFFICULTY: {"model": "opus"}}}}}))

        assert cfg.role("product_owner")[0:2] == ("claude", "opus")
        # Planista zostaje tam, gdzie był — role przestały być sklejone.
        assert cfg.role("planner")[0] == Config().planner_agent

    def test_effort_alone_refines_the_policy_model(self) -> None:
        cfg = Config(routing=_routing({"roles": {"reviewer": {
            "agent": "claude", "slots": {"complex": {"effort": "high"}}}}}))

        assert cfg.role("reviewer", "complex") == ("claude", "opus", "high")


class TestFallbackChain:
    def test_fallback_without_a_model_follows_the_level_policy(self) -> None:
        cfg = Config(routing=_routing({"roles": {"coder": {
            "agent": "opencode", "fallbacks": [{"agent": "claude"}]}}}))

        chain = cfg.role_chain("coder", "complex")

        assert chain[0][0] == "opencode"
        # balanced dla kodera przy zadaniu złożonym → sonnet/medium u Claude'a.
        assert chain[1] == ("claude", "sonnet", "medium")

    def test_fallback_may_switch_only_the_model(self) -> None:
        cfg = Config(routing=_routing({"roles": {"tester": {
            "agent": "opencode",
            "slots": {"standard": {"model": "zai-coding-plan/glm-5.2"}},
            "fallbacks": [{"model": "qwencloud-token-plan/qwen3.8-max"}],
        }}}))

        assert cfg.role_chain("tester", "standard") == [
            ("opencode", "zai-coding-plan/glm-5.2", ""),
            ("opencode", "qwencloud-token-plan/qwen3.8-max", ""),
        ]

    def test_duplicate_entries_are_dropped(self) -> None:
        # Zapas identyczny z pierwszym wyborem tylko powtórzyłby tę samą awarię.
        cfg = Config(routing=_routing({"roles": {"master": {
            "agent": "opencode",
            "slots": {"standard": {"model": "m"}},
            "fallbacks": [{"agent": "opencode", "model": "m"}, {"agent": "grok"}],
        }}}))

        chain = cfg.role_chain("master", "standard")

        assert [entry[0] for entry in chain] == ["opencode", "grok"]

    def test_preflight_sees_agents_hidden_in_the_chain(self) -> None:
        cfg = Config(routing=_routing({"roles": {"coder": {
            "agent": "opencode", "fallbacks": [{"agent": "grok"}]}}}))

        assert "grok" in cfg.agents_in_use()

    def test_master_still_refuses_codex(self) -> None:
        with pytest.raises(ValueError, match="Codex nie jest dostępny"):
            Config(routing=_routing({"roles": {"master": {"agent": "codex"}}}))

    def test_master_refuses_codex_hidden_in_the_chain(self) -> None:
        # Zakaz, który kończy się na pierwszej awarii, nie jest zakazem.
        with pytest.raises(ValueError, match="Codex nie jest dostępny"):
            Config(routing=_routing({"roles": {"master": {
                "agent": "opencode", "fallbacks": [{"agent": "gpt"}]}}}))

    def test_role_without_a_key_and_without_a_fallback_is_blocked(self) -> None:
        # Preflight ma zatrzymać przebieg tylko wtedy, gdy roli nie ma czym
        # wykonać — tu cały łańcuch prowadzi do dostawcy bez klucza.
        cfg = Config(routing=_routing({"roles": {"coder": {
            "agent": "opencode",
            "slots": {"complex": {"model": "platny/duzy"}}}}}))

        assert "coder/complex" in cfg.roles_blocked_by({"platny"})

    def test_working_fallback_keeps_the_role_usable(self) -> None:
        cfg = Config(routing=_routing({"roles": {"coder": {
            "agent": "opencode",
            "slots": {"complex": {"model": "platny/duzy"}},
            "fallbacks": [{"agent": "grok"}]}}}))

        assert "coder/complex" not in cfg.roles_blocked_by({"platny"})

    def test_models_in_use_lists_only_opencode_endpoints(self) -> None:
        cfg = Config(routing=_routing({"roles": {"coder": {
            "agent": "opencode",
            "slots": {"complex": {"model": "platny/duzy"}},
            "fallbacks": [{"agent": "grok", "model": "grok-4"}]}}}))

        models = cfg.opencode_models_in_use()

        assert "platny/duzy" in models and "grok-4" not in models
