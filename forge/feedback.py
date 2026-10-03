"""Durable operator feedback and run suggestions."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .models import ROLE_NAMES


MAX_FEEDBACK_LENGTH = 8000
MAX_SUGGESTION_LENGTH = 4000
MAX_OPEN_SUGGESTIONS = 20
MAX_QUESTIONS_PER_RUN = 50
FEEDBACK_KINDS = frozenset({"guidance", "scope_change"})
SUGGESTION_KINDS = frozenset({"suggestion", "question", "blocker"})
SUGGESTION_ACTIONS = frozenset({"accept", "reject", "defer", "answer"})
FEEDBACK_DECISIONS = frozenset({"use_as_guidance", "schedule_replan", "dismiss", "defer"})
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _normalized(text: str) -> str:
    return " ".join(text.strip().split())


def _digest(*parts: str) -> str:
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()


class RunConversationStore:
    """Atomically store one run's feedback queue and operator suggestions.

    Every read and update holds an OS file lock. That serializes HTTP threads,
    CLI commands, and separate Forge processes without racing the run state
    snapshot. Writes use fsync + replace, so an acknowledged message survives
    process recovery as a complete record.
    """

    def __init__(self, repo: Path, run_id: str):
        if not _RUN_ID.fullmatch(run_id) or run_id in {".", ".."}:
            raise ValueError("invalid Forge run id")
        self.root = Path(repo).expanduser().resolve() / ".forge" / "runs" / run_id
        self.path = self.root / "conversation.json"
        self.lock_path = self.root / "conversation.lock"

    @contextmanager
    def _locked(self) -> Iterator[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if self.path.exists():
                try:
                    state = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                    raise RuntimeError(f"run conversation state is unreadable: {self.path}") from exc
                if not isinstance(state, dict):
                    raise RuntimeError("run conversation state must be an object")
                state.setdefault("schema_version", 1)
                state.setdefault("feedback", [])
                state.setdefault("suggestions", [])
                state.setdefault("suppressed_suggestion_keys", [])
            else:
                state = {
                    "schema_version": 1,
                    "feedback": [],
                    "suggestions": [],
                    "suppressed_suggestion_keys": [],
                }
            for name in ("feedback", "suggestions"):
                records = state[name]
                if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
                    raise RuntimeError(f"run conversation {name} must be a list of objects")
            feedback_fields = {"id", "message", "kind", "target_role", "status", "deliveries", "history"}
            for item in state["feedback"]:
                item.setdefault("idempotency_keys", [])
                if (
                    not feedback_fields.issubset(item)
                    or not isinstance(item["id"], str)
                    or not isinstance(item["message"], str)
                    or not isinstance(item["kind"], str)
                    or item["kind"] not in FEEDBACK_KINDS
                    or not isinstance(item["target_role"], str)
                    or item["target_role"] not in {"auto", *ROLE_NAMES}
                    or not isinstance(item["status"], str)
                    or item["status"] not in {"received", "pending", "needs_decision", "applied", "dismissed", "not_applied"}
                    or not isinstance(item["deliveries"], list)
                    or any(not isinstance(entry, dict) for entry in item["deliveries"])
                    or not isinstance(item["idempotency_keys"], list)
                    or any(not isinstance(key, str) for key in item["idempotency_keys"])
                    or any(
                        not {"relative", "role"}.issubset(entry)
                        or not isinstance(entry["relative"], str)
                        or not isinstance(entry["role"], str)
                        for entry in item["deliveries"]
                    )
                    or not isinstance(item["history"], list)
                    or any(not isinstance(entry, dict) for entry in item["history"])
                ):
                    raise RuntimeError("run conversation contains an invalid feedback record")
            suggestion_fields = {
                "id", "title", "kind", "status", "dedupe_key", "history",
                "target_role", "feedback_kind", "recommendation", "requires_decision",
            }
            for item in state["suggestions"]:
                if (
                    not suggestion_fields.issubset(item)
                    or not isinstance(item["id"], str)
                    or not isinstance(item["title"], str)
                    or not isinstance(item["kind"], str)
                    or item["kind"] not in SUGGESTION_KINDS
                    or not isinstance(item["status"], str)
                    or item["status"] not in {"open", "deferred", "accepted", "answered", "rejected", "superseded"}
                    or not isinstance(item["dedupe_key"], str)
                    or not isinstance(item["target_role"], str)
                    or item["target_role"] not in {"auto", *ROLE_NAMES}
                    or not isinstance(item["feedback_kind"], str)
                    or item["feedback_kind"] not in FEEDBACK_KINDS
                    or not isinstance(item["recommendation"], str)
                    or not isinstance(item["requires_decision"], bool)
                    or not isinstance(item.get("truncated_fields", []), list)
                    or any(not isinstance(field, str) for field in item.get("truncated_fields", []))
                    or not isinstance(item["history"], list)
                    or any(not isinstance(entry, dict) for entry in item["history"])
                ):
                    raise RuntimeError("run conversation contains an invalid suggestion record")
            suppressed = state.get("suppressed_suggestions", 0)
            if not isinstance(suppressed, int) or suppressed < 0:
                raise RuntimeError("run conversation suppressed_suggestions must be a non-negative integer")
            suppressed_keys = state.get("suppressed_suggestion_keys", [])
            if not isinstance(suppressed_keys, list) or any(not isinstance(key, str) for key in suppressed_keys):
                raise RuntimeError("run conversation suppressed_suggestion_keys must be a list of strings")
            yield state
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _write(self, state: dict[str, Any]) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".conversation.", suffix=".tmp", dir=self.root
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def snapshot(self) -> dict[str, Any]:
        with self._locked() as state:
            return {
                "feedback": [dict(item) for item in state["feedback"]],
                "suggestions": [dict(item) for item in state["suggestions"]],
                "suppressed_suggestions": int(state.get("suppressed_suggestions", 0)),
            }

    @staticmethod
    def _has_idempotency_key(item: dict[str, Any], key: str) -> bool:
        return item.get("idempotency_key") == key or key in item.get("idempotency_keys", [])

    @staticmethod
    def _remember_idempotency_key(item: dict[str, Any], key: str) -> bool:
        if not key or RunConversationStore._has_idempotency_key(item, key):
            return False
        keys = list(item.get("idempotency_keys") or [])
        keys.append(key)
        item["idempotency_keys"] = keys
        return True

    def mark_unapplied_coder_feedback(self, iteration_id: str) -> int:
        if not iteration_id:
            return 0
        explanation = "The iteration was accepted without confirmed application of this feedback. "
        with self._locked() as state:
            changed = 0
            for item in state["feedback"]:
                if (
                    item["status"] not in {"received", "pending"}
                    or not item["target_role"].startswith("coder_")
                    or item.get("iteration_received") != iteration_id
                    or any(delivery.get("state") == "applied" for delivery in item["deliveries"])
                ):
                    continue
                had_prepared_delivery = bool(item["deliveries"])
                detail = (
                    "Forge could not confirm a prepared delivery after recovery; it will not replay "
                    "automatically. Submit it again if it is still needed."
                    if had_prepared_delivery
                    else explanation + " Submit it again if it is still needed."
                )
                self._mark_not_applied(item, detail)
                changed += 1
            if changed:
                self._write(state)
            return changed

    @staticmethod
    def _mark_not_applied(item: dict[str, Any], explanation: str) -> None:
        now = utc_now()
        item.update(status="not_applied", updated_at=now, explanation=explanation)
        item["history"].append(
            {"at": now, "status": "not_applied", "explanation": explanation}
        )

    def add_feedback(
        self,
        message: str,
        *,
        kind: str = "guidance",
        target_role: str = "auto",
        phase: str = "",
        iteration_id: str = "",
        source: str = "user",
        idempotency_key: str = "",
    ) -> tuple[dict[str, Any], bool]:
        text = _normalized(str(message))
        if not text:
            raise ValueError("feedback message must not be empty")
        if len(text) > MAX_FEEDBACK_LENGTH:
            raise ValueError(f"feedback message exceeds {MAX_FEEDBACK_LENGTH} characters")
        if kind not in FEEDBACK_KINDS:
            raise ValueError("feedback kind must be guidance or scope_change")
        if target_role not in {"auto", *ROLE_NAMES}:
            raise ValueError("unknown feedback target role")
        key = _normalized(idempotency_key) or "content:" + _digest(text.casefold(), kind, target_role)
        if len(key) > 200:
            raise ValueError("idempotency key exceeds 200 characters")
        now = utc_now()
        initial_status = "needs_decision" if kind == "scope_change" else "received"
        explanation = (
            "Scope changes wait for an explicit decision; the active run continues."
            if kind == "scope_change"
            else "Saved durably; waiting for the next safe agent boundary."
        )
        with self._locked() as state:
            for existing in state["feedback"]:
                if self._has_idempotency_key(existing, key):
                    if (
                        existing.get("message") != text
                        or existing.get("kind") != kind
                        or existing.get("target_role") != target_role
                    ):
                        raise ValueError("idempotency key was already used for different feedback")
                    return dict(existing), False
            for existing in state["feedback"]:
                if (
                    existing.get("status") in {"received", "pending", "needs_decision"}
                    and existing.get("message", "").casefold() == text.casefold()
                    and existing.get("kind") == kind
                    and existing.get("target_role") == target_role
                ):
                    if self._remember_idempotency_key(existing, key):
                        self._write(state)
                    return dict(existing), False
            item = {
                "id": uuid.uuid4().hex,
                "message": text,
                "kind": kind,
                "target_role": target_role,
                "source": source,
                "idempotency_key": key,
                "idempotency_keys": [],
                "created_at": now,
                "updated_at": now,
                "status": initial_status,
                "explanation": explanation,
                "phase_received": phase,
                "iteration_received": iteration_id,
                "decision": "",
                "deferred": False,
                "wait_for_iteration": "",
                "deliveries": [],
                "history": [
                    {"at": now, "status": initial_status, "explanation": explanation}
                ],
            }
            state["feedback"].append(item)
            self._write(state)
            return dict(item), True

    def decide_feedback(
        self,
        feedback_id: str,
        action: str,
        *,
        active_iteration_id: str = "",
    ) -> dict[str, Any]:
        if action not in FEEDBACK_DECISIONS:
            raise ValueError("decision must be use_as_guidance, schedule_replan, dismiss, or defer")
        with self._locked() as state:
            item = self._find(state["feedback"], feedback_id)
            if item["status"] != "needs_decision":
                raise ValueError("feedback does not need a decision")
            now = utc_now()
            if action == "dismiss":
                item.update(status="dismissed", deferred=False, decision=action)
                explanation = "Dismissed by the user; it will not be sent to an agent."
            elif action == "defer":
                item.update(deferred=True, decision=action)
                explanation = "Deferred by the user; it remains visible and will not block the run."
            elif action == "schedule_replan":
                item.update(
                    status="pending",
                    kind="scope_change",
                    target_role="brain",
                    decision=action,
                    deferred=False,
                    wait_for_iteration=active_iteration_id or item.get("iteration_received", ""),
                )
                explanation = (
                    "Approved for the next Product Owner boundary; current sprint work continues unchanged."
                )
            else:
                item.update(
                    status="received",
                    kind="guidance",
                    decision=action,
                    deferred=False,
                    wait_for_iteration="",
                    iteration_received="",
                )
                explanation = "Approved as guidance; waiting for the next safe agent boundary."
            item.update(updated_at=now, explanation=explanation)
            item["history"].append(
                {"at": now, "status": item["status"], "decision": action, "explanation": explanation}
            )
            self._write(state)
            return dict(item)

    def prepare_feedback(
        self,
        *,
        role: str,
        phase: str,
        iteration_id: str,
        relative: str,
        active_roles: set[str] | None = None,
        code_started: bool = False,
        winner_role: str = "",
    ) -> list[dict[str, Any]]:
        active_roles = active_roles or set()
        selected: list[dict[str, Any]] = []
        with self._locked() as state:
            changed = False
            for item in state["feedback"]:
                if item["status"] not in {"received", "pending"} or item.get("deferred"):
                    continue
                is_coder_feedback = item["target_role"].startswith("coder_")
                # Feedback submitted in the Product Owner gap has no active
                # iteration yet. Bind coder-directed feedback to the first
                # iteration boundary it can actually affect.
                if (
                    iteration_id
                    and is_coder_feedback
                    and not item.get("iteration_received")
                ):
                    item["iteration_received"] = iteration_id
                    item["updated_at"] = utc_now()
                    item["explanation"] = (
                        f"Associated with {iteration_id}; waiting for a safe coder boundary."
                    )
                    item["history"].append(
                        {
                            "at": item["updated_at"],
                            "status": item["status"],
                            "explanation": item["explanation"],
                        }
                    )
                    changed = True
                elif (
                    iteration_id
                    and is_coder_feedback
                    and item.get("iteration_received")
                    and item["iteration_received"] != iteration_id
                ):
                    self._mark_not_applied(
                        item,
                        f"Feedback was bound to {item['iteration_received']} and was not delivered "
                        f"before {iteration_id}; it will not be replayed automatically. Submit it "
                        "again if it is still needed.",
                    )
                    changed = True
                    continue
                if not self._targets(
                    item, role, phase, iteration_id, active_roles, code_started, winner_role
                ):
                    explanation = self._held_explanation(
                        item, phase, active_roles, code_started, winner_role
                    )
                    if explanation and item.get("explanation") != explanation:
                        item.update(updated_at=utc_now(), explanation=explanation)
                        item["history"].append(
                            {
                                "at": item["updated_at"],
                                "status": item["status"],
                                "explanation": explanation,
                            }
                        )
                        changed = True
                    continue
                delivery = next(
                    (
                        entry
                        for entry in item["deliveries"]
                        if entry.get("relative") == relative and entry.get("role") == role
                    ),
                    None,
                )
                if delivery is None:
                    delivery = {
                        "relative": relative,
                        "role": role,
                        "phase": phase,
                        "at": utc_now(),
                        "state": "prepared",
                    }
                    item["deliveries"].append(delivery)
                    changed = True
                if item["status"] != "pending":
                    item["status"] = "pending"
                    item["updated_at"] = utc_now()
                    item["explanation"] = f"Assigned to {role} at the {phase} boundary."
                    item["history"].append(
                        {
                            "at": item["updated_at"],
                            "status": "pending",
                            "explanation": item["explanation"],
                        }
                    )
                    changed = True
                selected.append(dict(item))
            if changed:
                self._write(state)
        return selected

    @staticmethod
    def _held_explanation(
        item: dict[str, Any],
        phase: str,
        active_roles: set[str],
        code_started: bool,
        winner_role: str,
    ) -> str:
        target = item.get("target_role", "auto")
        if not target.startswith("coder_"):
            return ""
        tournament_running = code_started or any(
            role.startswith("coder_") for role in active_roles
        )
        if tournament_running or phase == "selection":
            return "Held until the parallel coder tournament selects a winner."
        if winner_role:
            return "Waiting for a post-selection review, test, or single-writer fix boundary."
        return "Held until Forge knows which candidate won the tournament."

    def complete_delivery(self, feedback_ids: list[str], *, relative: str, response_path: str) -> None:
        if not feedback_ids:
            return
        with self._locked() as state:
            changed = False
            for item in state["feedback"]:
                if item["id"] not in feedback_ids:
                    continue
                for delivery in item["deliveries"]:
                    if delivery.get("relative") == relative and delivery.get("state") != "applied":
                        delivery.update(state="applied", response_path=response_path, applied_at=utc_now())
                        item.update(
                            status="applied",
                            updated_at=utc_now(),
                            explanation=f"Delivered to {delivery['role']} in a durable agent prompt.",
                        )
                        item["history"].append(
                            {
                                "at": item["updated_at"],
                                "status": "applied",
                                "explanation": item["explanation"],
                            }
                        )
                        changed = True
                        break
            if changed:
                self._write(state)

    def publish_suggestion(
        self,
        *,
        kind: str,
        title: str,
        context: str,
        rationale: str,
        expected_impact: str,
        recommendation: str,
        source: str,
        target_role: str = "auto",
        feedback_kind: str = "guidance",
        requires_decision: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        if kind not in SUGGESTION_KINDS:
            raise ValueError("suggestion kind must be suggestion, question, or blocker")
        if target_role not in {"auto", *ROLE_NAMES}:
            raise ValueError("unknown suggestion target role")
        if feedback_kind not in FEEDBACK_KINDS:
            raise ValueError("unknown suggestion feedback kind")
        values = {
            "title": _normalized(title),
            "context": _normalized(context),
            "rationale": _normalized(rationale),
            "expected_impact": _normalized(expected_impact),
            "recommendation": _normalized(recommendation),
        }
        if any(not value for value in values.values()):
            raise ValueError("suggestions require a title, context, rationale, impact, and recommendation")
        truncated_fields = []
        for field, value in values.items():
            if len(value) > MAX_SUGGESTION_LENGTH:
                values[field] = value[: MAX_SUGGESTION_LENGTH - 1] + "…"
                truncated_fields.append(field)
        dedupe_scope = values["title"].casefold()
        if kind == "blocker":
            # A changed blocker rationale needs a new card even within one
            # iteration. Identical reports remain deduplicated by stage/source.
            dedupe_scope += "\0" + _normalized(source).casefold()
            dedupe_scope += "\0" + values["rationale"].casefold()
        dedupe_key = _digest(kind, dedupe_scope)
        with self._locked() as state:
            state.setdefault("suppressed_suggestions", 0)
            state.setdefault("suppressed_suggestion_keys", [])
            for existing in state["suggestions"]:
                if existing.get("dedupe_key") == dedupe_key:
                    if kind == "blocker" and existing.get("status") not in {"open", "deferred"}:
                        continue
                    return dict(existing), False
            now = utc_now()
            if kind == "blocker":
                for existing in state["suggestions"]:
                    if (
                        existing.get("kind") == "blocker"
                        and existing.get("title", "").casefold() == values["title"].casefold()
                        and existing.get("status") in {"open", "deferred"}
                    ):
                        existing.update(status="superseded", updated_at=now)
                        existing["history"].append(
                            {
                                "at": now,
                                "status": "superseded",
                                "explanation": "A newer blocker with this title replaced this report.",
                            }
                        )
            active_count = sum(
                item.get("status") in {"open", "deferred"} for item in state["suggestions"]
            )
            question_count = sum(
                item.get("kind") == "question" for item in state["suggestions"]
            )
            suppress = (
                kind == "suggestion" and active_count >= MAX_OPEN_SUGGESTIONS
            ) or (kind == "question" and question_count >= MAX_QUESTIONS_PER_RUN)
            if suppress:
                if dedupe_key not in state["suppressed_suggestion_keys"]:
                    state["suppressed_suggestion_keys"].append(dedupe_key)
                    state["suppressed_suggestions"] += 1
                    self._write(state)
                return {
                    "id": "",
                    **values,
                    "kind": kind,
                    "status": "suppressed",
                    "source": source,
                }, False
            item = {
                "id": uuid.uuid4().hex,
                **values,
                "kind": kind,
                "source": source,
                "target_role": target_role,
                "feedback_kind": feedback_kind,
                "requires_decision": bool(requires_decision),
                "dedupe_key": dedupe_key,
                "truncated_fields": truncated_fields,
                "status": "open",
                "created_at": now,
                "updated_at": now,
                "answer": "",
                "history": [{"at": now, "status": "open", "explanation": "Published by Forge."}],
            }
            state["suggestions"].append(item)
            self._write(state)
            return dict(item), True

    def answer_suggestion(
        self,
        suggestion_id: str,
        action: str,
        *,
        answer: str = "",
        phase: str = "",
        iteration_id: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any] | None]:
        if action not in SUGGESTION_ACTIONS:
            raise ValueError("suggestion action must be accept, reject, defer, or answer")
        answer_text = _normalized(answer)
        if len(answer_text) > MAX_FEEDBACK_LENGTH:
            raise ValueError(f"suggestion answer exceeds {MAX_FEEDBACK_LENGTH} characters")
        if action == "answer" and not answer_text:
            raise ValueError("an answer is required for this question")
        with self._locked() as state:
            suggestion = self._find(state["suggestions"], suggestion_id)
            if suggestion["status"] not in {"open", "deferred"}:
                completed_actions = {"accepted": "accept", "answered": "answer", "rejected": "reject"}
                if (
                    completed_actions.get(suggestion["status"]) == action
                    and (action != "answer" or suggestion.get("answer") == answer_text)
                ):
                    feedback = next(
                        (
                            item
                            for item in state["feedback"]
                            if self._has_idempotency_key(
                                item, f"suggestion:{suggestion_id}:{action}"
                            )
                        ),
                        None,
                    )
                    return dict(suggestion), dict(feedback) if feedback else None
                raise ValueError("suggestion is no longer open")
            now = utc_now()
            feedback: dict[str, Any] | None = None
            if action in {"accept", "answer"}:
                message = answer_text if action == "answer" else suggestion["recommendation"]
                kind = "scope_change" if (
                    suggestion["requires_decision"]
                    or suggestion["feedback_kind"] == "scope_change"
                ) else "guidance"
                feedback = self._add_feedback_in_state(
                    state,
                    message,
                    kind=kind,
                    target_role=suggestion["target_role"],
                    phase=phase,
                    iteration_id=iteration_id,
                    source=f"suggestion:{suggestion_id}",
                    idempotency_key=f"suggestion:{suggestion_id}:{action}",
                )
                new_status = "accepted" if action == "accept" else "answered"
                suggestion["answer"] = answer_text
                explanation = (
                    "Accepted; the recommendation entered the durable feedback path."
                    if action == "accept"
                    else "Answer saved in the durable feedback path for the relevant agent."
                )
            elif action == "reject":
                new_status = "rejected"
                explanation = "Rejected by the user; no run action was taken."
            else:
                new_status = "deferred"
                explanation = "Deferred by the user; the active run continues."
            suggestion.update(status=new_status, updated_at=now)
            suggestion["history"].append(
                {"at": now, "status": new_status, "explanation": explanation}
            )
            self._write(state)
            return dict(suggestion), feedback

    @staticmethod
    def _add_feedback_in_state(
        state: dict[str, Any],
        message: str,
        *,
        kind: str,
        target_role: str,
        phase: str,
        iteration_id: str,
        source: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        text = _normalized(message)
        if not text:
            raise ValueError("feedback message must not be empty")
        if len(text) > MAX_FEEDBACK_LENGTH:
            raise ValueError(f"feedback message exceeds {MAX_FEEDBACK_LENGTH} characters")
        for existing in state["feedback"]:
            if RunConversationStore._has_idempotency_key(existing, idempotency_key):
                return dict(existing)
            if (
                existing.get("status") in {"received", "pending", "needs_decision"}
                and existing.get("message", "").casefold() == text.casefold()
                and existing.get("kind") == kind
                and existing.get("target_role") == target_role
            ):
                RunConversationStore._remember_idempotency_key(existing, idempotency_key)
                return dict(existing)
        now = utc_now()
        status = "needs_decision" if kind == "scope_change" else "received"
        explanation = (
            "This suggestion may change scope; it waits for an explicit decision."
            if status == "needs_decision"
            else "Saved durably; waiting for the next safe agent boundary."
        )
        item = {
            "id": uuid.uuid4().hex,
            "message": text,
            "kind": kind,
            "target_role": target_role,
            "source": source,
            "idempotency_key": idempotency_key,
            "idempotency_keys": [],
            "created_at": now,
            "updated_at": now,
            "status": status,
            "explanation": explanation,
            "phase_received": phase,
            "iteration_received": iteration_id,
            "decision": "",
            "deferred": False,
            "wait_for_iteration": "",
            "deliveries": [],
            "history": [{"at": now, "status": status, "explanation": explanation}],
        }
        state["feedback"].append(item)
        return dict(item)

    @staticmethod
    def _targets(
        item: dict[str, Any],
        role: str,
        phase: str,
        iteration_id: str,
        active_roles: set[str],
        code_started: bool,
        winner_role: str,
    ) -> bool:
        if item["kind"] == "scope_change":
            if item.get("decision") != "schedule_replan":
                return False
            if phase != "product-owner" or role != "brain":
                return False
            if item.get("wait_for_iteration") and iteration_id == item["wait_for_iteration"]:
                return False
            return True
        target = item.get("target_role", "auto")
        tournament_running = code_started or any(
            active_role.startswith("coder_") for active_role in active_roles
        )
        if target.startswith("coder_"):
            received_iteration = str(item.get("iteration_received") or "")
            if received_iteration and received_iteration != iteration_id:
                return False
            # Keep candidate-targeted feedback out of the parallel tournament
            # and selection. Afterward, a current coder fix, reviewer, or tester
            # can carry it forward without interrupting work already in flight.
            if tournament_running or phase == "selection":
                return False
            if phase == "review":
                return role == "reviewer"
            if phase == "testing":
                return role == "tester"
            if winner_role and target == winner_role:
                return role == target and phase == "coding"
            return target == role
        if target != "auto":
            return target == role
        by_phase = {
            "product-owner": {"brain"},
            "planning": {"planner"},
            "test-authoring": {"test_author"},
            "coding": {"coder_tdd", "coder_explore", "coder_classic"},
            "selection": {"reviewer"},
            "review": {"reviewer"},
            "testing": {"tester"},
            "delivery": {"planner"},
            "finalizing": {"brain"},
        }
        targets = by_phase.get(phase, set())
        if role not in targets:
            return False
        # A message that arrives while tournament workers are already active is
        # held for the selection reviewer instead of being injected into only a
        # subset of independent candidate worktrees.
        if phase == "coding" and tournament_running:
            return False
        return True

    @staticmethod
    def _find(items: list[dict[str, Any]], item_id: str) -> dict[str, Any]:
        for item in items:
            if item.get("id") == item_id:
                return item
        raise KeyError(item_id)
