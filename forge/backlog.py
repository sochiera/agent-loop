"""Parser i deterministyczne operacje na backlogu historyjek.

Ten moduł celowo nie ocenia jakości produktu. Sprawdza wyłącznie kontrakt
strukturalny, żeby Product Owner i jego recenzent nie mogli przepchnąć pliku,
którego kolejne fazy nie potrafią jednoznacznie odczytać.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


STATUSES = ("nowa", "w toku", "do weryfikacji", "zrobiona", "porzucona")
_ID = r"US-\d{3}"
_HEADING = re.compile(
    rf"^##\s+(?P<id>{_ID})\s+—\s+(?P<title>.+?)\s+"
    r"\[(?P<status>[^\]]+)\]\s*$"
)
_ANY_STORY_HEADING = re.compile(rf"^##\s+(?P<id>{_ID})(?:\s|$)")
_FIELD = re.compile(
    r"^\s*-\s*(?P<name>Dlaczego teraz|Sprawdzenie|Poza zakresem):\s*"
    r"(?P<value>.*)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Story:
    id: str
    title: str
    status: str
    drop_reason: str
    why_now: str
    check: str
    out_of_scope: str
    body: str
    line: int


def _status_parts(raw: str) -> tuple[str, str]:
    value = raw.strip()
    if value.startswith("porzucona:"):
        return "porzucona", value.split(":", 1)[1].strip()
    return value, ""


def _story_from_block(lines: list[str], line: int) -> Story | None:
    if not lines:
        return None
    match = _HEADING.match(lines[0].rstrip("\r\n"))
    if not match:
        return None

    fields = {"Dlaczego teraz": [], "Sprawdzenie": [], "Poza zakresem": []}
    narrative: list[str] = []
    for raw in lines[1:]:
        clean = raw.rstrip("\r\n")
        field = _FIELD.match(clean)
        if field:
            # Zapisywanie kolejnych linii pola jest celowo konserwatywne:
            # kanoniczny format wymaga jednej linii, a walidacja ma móc
            # zgłosić pustą wartość bez utraty reszty bloku.
            name = field.group("name")
            key = {
                "Dlaczego teraz": "Dlaczego teraz",
                "Sprawdzenie": "Sprawdzenie",
                "Poza zakresem": "Poza zakresem",
            }[name[:1].upper() + name[1:].lower()]
            fields[key].append(field.group("value").strip())
        elif clean.strip():
            narrative.append(clean)
    status, reason = _status_parts(match.group("status"))
    return Story(
        id=match.group("id"),
        title=match.group("title").strip(),
        status=status,
        drop_reason=reason,
        why_now="\n".join(fields["Dlaczego teraz"]).strip(),
        check="\n".join(fields["Sprawdzenie"]).strip(),
        out_of_scope="\n".join(fields["Poza zakresem"]).strip(),
        body="\n".join(narrative).strip(),
        line=line,
    )


def parse(text: str) -> tuple[list[Story], list[str]]:
    """Zwróć historyjki w kolejności oraz bloki spoza formatu.

    Nagłówki `US-NNN` są granicami bloków nawet wtedy, gdy ich status lub
    zapis tytułu jest niepoprawny. Dzięki temu walidator może zgłosić błąd
    strukturalny, zamiast cicho uznać istniejącą historyjkę za usuniętą.
    """
    lines = text.splitlines()
    stories: list[Story] = []
    orphans: list[str] = []
    outside: list[str] = []
    block: list[str] = []
    block_line = 0

    def flush_outside() -> None:
        meaningful = [line for line in outside if line.strip()]
        # Dokumentowy tytuł/preambuła nie jest sierocym story-blockiem.
        # Wszystko bardziej konkretnego (np. zwykła proza lub lista) pozostaje
        # sygnałem dla twardego walidatora.
        if meaningful and not all(line.lstrip().startswith("#") for line in meaningful):
            orphans.append("\n".join(outside).strip())
        outside.clear()

    def flush_block() -> None:
        nonlocal block
        story = _story_from_block(block, block_line)
        if story is None:
            if any(line.strip() for line in block):
                orphans.append("\n".join(block).strip())
        else:
            stories.append(story)
        block = []

    for number, line in enumerate(lines, 1):
        if line.startswith("## "):
            flush_block()
            flush_outside()
            block = [line]
            block_line = number
        elif block:
            block.append(line)
        else:
            outside.append(line)
    flush_block()
    flush_outside()
    return stories, orphans


def validate_hard(
    before: list[Story], after: list[Story], dropped: list[dict],
    orphans: list[str],
) -> list[str]:
    """Zwróć naruszenia sześciu twardych invariantów backlogu."""
    violations: list[str] = []
    ids: set[str] = set()
    for story in after:
        if not re.fullmatch(_ID, story.id):
            violations.append(f"historyjka ma niepoprawne ID: {story.id!r}")
        elif story.id in ids:
            violations.append(f"ID historyjki występuje więcej niż raz: {story.id}")
        ids.add(story.id)
        if story.status not in STATUSES:
            violations.append(f"{story.id}: niedozwolony status {story.status!r}")
        if not story.check.strip():
            violations.append(f"{story.id}: brak niepustego pola Sprawdzenie")
        if not story.why_now.strip():
            violations.append(f"{story.id}: brak niepustego pola Dlaczego teraz")

    dropped_ids = {
        str(item.get("id", "")) for item in dropped
        if isinstance(item, dict)
    }
    for story in before:
        if story.id not in ids and story.id not in dropped_ids:
            violations.append(
                f"historyjka {story.id} zniknęła bez wpisu w stories_dropped")

    before_ids = {story.id for story in before}
    after_by_id = {story.id: story for story in after}
    for old in before:
        new = after_by_id.get(old.id)
        if new and new.status != old.status and new.status != "porzucona":
            violations.append(
                f"{old.id}: PO nie może zmienić statusu {old.status!r} "
                f"na {new.status!r}; statusy należą do Forge")
    for story in after:
        if story.id not in before_ids and story.status not in ("nowa", "porzucona"):
            violations.append(
                f"{story.id}: nowa historyjka musi mieć status 'nowa', "
                f"a ma {story.status!r}; statusy należą do Forge")

    if orphans:
        violations.append("backlog zawiera nieparsowalne bloki poza historyjkami")
    return violations


def set_status(text: str, story_id: str, status: str, reason: str = "") -> str:
    """Zmień wyłącznie nawias statusu w nagłówku wskazanej historyjki."""
    if status not in STATUSES:
        raise ValueError(f"niedozwolony status: {status}")
    lines = text.splitlines(keepends=True)
    found = False
    output: list[str] = []
    heading = re.compile(rf"^(##\s+{re.escape(story_id)}\s+—\s+.+?\s+)\[[^\]]*\](?P<tail>\r?\n)?$")
    for line in lines:
        match = heading.match(line)
        if match:
            if found:
                raise ValueError(f"ID historyjki występuje więcej niż raz: {story_id}")
            label = status
            if status == "porzucona" and reason.strip():
                label += f": {reason.strip()}"
            newline = match.group("tail") or ""
            output.append(f"{match.group(1)}[{label}]{newline}")
            found = True
        else:
            output.append(line)
    if not found:
        raise KeyError(story_id)
    return "".join(output)


def count_open(stories: list[Story]) -> int:
    return sum(story.status == "nowa" for story in stories)


def ids_by_status(stories: list[Story], *statuses: str) -> list[str]:
    wanted = set(statuses)
    return [story.id for story in stories if story.status in wanted]


def load(project: str) -> tuple[list[Story], list[str]]:
    path = Path(project, "BACKLOG.md")
    if not path.is_file():
        return [], []
    return parse(path.read_text(encoding="utf-8"))


def is_legacy(text: str) -> bool:
    return bool(text.strip()) and not re.search(rf"^##\s+{_ID}(?:\s|$)", text, re.MULTILINE)
