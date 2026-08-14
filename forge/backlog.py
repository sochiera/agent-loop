"""Parser i deterministyczne operacje na backlogu historyjek.

Ten moduł celowo nie ocenia jakości produktu. Sprawdza wyłącznie kontrakt
strukturalny, żeby Product Owner i jego recenzent nie mogli przepchnąć pliku,
którego kolejne fazy nie potrafią jednoznacznie odczytać.
"""
from __future__ import annotations

import difflib
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
    r"^\s*-\s*(?P<name>Dlaczego teraz|Sprawdzenie|Poza zakresem|Sekcja briefu):"
    r"\s*(?P<value>.*)\s*$",
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
    # Nazwa sekcji briefu, którą ta historyjka otwiera lub pogłębia. Wiąże
    # backlog z mapą pokrycia (patrz `coverage.py`); pusta w projektach sprzed
    # tego mechanizmu i w briefach bez nagłówków `##`.
    brief_section: str
    body: str
    line: int


def section_key(name: str) -> str:
    """Klucz porównania nazw sekcji briefu: bez wielkości liter i odstępów.

    Dopasowanie musi być tolerancyjne, bo nazwę sekcji przepisuje ręcznie model.
    Twarde porównanie zamieniałoby każdą literówkę i podwójną spację w
    naruszenie kontraktu, czyli w kolejną pełnopłatną turę za zero wartości.
    """
    return " ".join(str(name).split()).casefold()


def closest_section(name: str, known: list[str]) -> str:
    """Najbliższa istniejąca nazwa sekcji albo pusty string."""
    keys = [section_key(item) for item in known]
    match = difflib.get_close_matches(section_key(name), keys, n=1, cutoff=0.6)
    if not match:
        return ""
    return known[keys.index(match[0])]


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

    fields = {"Dlaczego teraz": [], "Sprawdzenie": [], "Poza zakresem": [],
              "Sekcja briefu": []}
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
                "Sekcja briefu": "Sekcja briefu",
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
        brief_section="\n".join(fields["Sekcja briefu"]).strip(),
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


def coerce_statuses(text: str, before: list[Story]) -> tuple[str, list[str]]:
    """Przepisz kolumnę statusu na prawdę Forge; zwróć tekst i listę korekt.

    Status nie jest opinią Product Ownera, tylko stanem cyklu życia, który w
    całości zna Forge. Regułę, którą Forge umie wymusić zapisem, wymuszamy
    zapisem — zgłoszenie kosztuje pełną turę agenta i, w odróżnieniu od pary
    reguł zastąpionych tą funkcją, potrafi postawić PO przed wyborem między
    dwoma naruszeniami naraz (zostaw nielegalny status / nie zmieniaj statusu).
    Ta pętla nie umie zakleszczyć się z definicji: zawsze kończy się plikiem
    zgodnym z kontraktem, niezależnie od tego, co zastała.
    """
    known = {story.id: story for story in before}
    changes: list[str] = []
    result = text
    for story in parse(text)[0]:
        old = known.get(story.id)
        target, reason = (old.status, old.drop_reason) if old else ("nowa", "")
        if target not in STATUSES:
            # Status spoza kontraktu (stary plik, ręczna edycja, tura roli
            # zadaniowej) nie jest dowodem na nic — historyjka wraca do
            # kolejki weryfikacji, jedynej fazy, która umie go rozstrzygnąć.
            target, reason = "do weryfikacji", ""
        if story.status == target and story.drop_reason == reason:
            continue
        try:
            result = set_status(result, story.id, target, reason)
        except (KeyError, ValueError):
            # Zduplikowane albo nieparsowalne ID zgłasza walidator; tutaj nie
            # ma czego jednoznacznie przepisać, a wywrócenie tury kosztowałoby
            # znacznie więcej niż jedna pominięta korekta.
            continue
        changes.append(f"{story.id}: {story.status!r}→{target!r}")
    return result, changes


def validate_hard(
    before: list[Story], after: list[Story], dropped: list[dict],
    orphans: list[str], reopened: list[dict] | None = None,
    brief_sections: list[str] | None = None,
) -> list[str]:
    """Zwróć naruszenia twardych invariantów, których Forge nie umie naprawić.

    Świadomie nie ma tu żadnej reguły o statusach: należą do ``coerce_statuses``.
    Zostaje wyłącznie to, czego nie da się wymusić zapisem, bo prawdę zna sam
    Product Owner — a więc każde zgłoszenie stąd jest dla niego wykonalne.
    """
    violations: list[str] = []
    ids: set[str] = set()
    known_ids = {story.id for story in before}
    for story in after:
        if not re.fullmatch(_ID, story.id):
            violations.append(f"historyjka ma niepoprawne ID: {story.id!r}")
        elif story.id in ids:
            violations.append(f"ID historyjki występuje więcej niż raz: {story.id}")
        ids.add(story.id)
        if not story.check.strip():
            violations.append(f"{story.id}: brak niepustego pola Sprawdzenie")
        if not story.why_now.strip():
            violations.append(f"{story.id}: brak niepustego pola Dlaczego teraz")
        # Wiązanie z sekcją briefu egzekwujemy pod dwoma warunkami. Po pierwsze
        # brief musi mieć sekcje: brief bez nagłówków `##` jest legalny, a wymóg
        # pola, którego nie da się poprawnie wypełnić, zakleszczyłby turę
        # Product Ownera na własnym walidatorze — dokładnie tak, jak kiedyś
        # zrobiła to para reguł o statusach.
        #
        # Po drugie egzekwujemy go tylko dla historyjek NOWYCH w tej turze.
        # Backlog sprzed tego mechanizmu ma kilkanaście pozycji bez pola i
        # zażądanie ich wszystkich naraz zamieniłoby pierwszą turę PO w
        # zderzenie z budżetem korekt, czyli w twardą awarię biegu. Zaległość
        # jest widoczna w mapie pokrycia jako „MAPA NIEPEŁNA" i tam wyłącza
        # regułę blokującą, więc migracja rozkłada się na kilka tur, zamiast
        # zabijać pierwszą.
        if brief_sections and story.status != "porzucona" and story.id not in known_ids:
            value = story.brief_section.strip()
            if not value:
                violations.append(
                    f"{story.id}: brak pola Sekcja briefu; wpisz nazwę jednej "
                    "z sekcji briefu")
            elif not any(section_key(value) == section_key(name)
                         for name in brief_sections):
                hint = closest_section(value, brief_sections)
                suffix = f"; czy chodziło o {hint!r}?" if hint else ""
                violations.append(
                    f"{story.id}: Sekcja briefu {value!r} nie jest sekcją "
                    f"briefu{suffix}")

    dropped_ids = {
        str(item.get("id", "")) for item in dropped
        if isinstance(item, dict)
    }
    # Wznowienie każe Forge ogłosić fakt o historyjce i wysłać powód planiście,
    # więc ID musi istnieć NAPRAWDĘ. Zmyślone przeszłoby przez `set_status`
    # jako brak trafienia i zostawiło proces z wznowieniem bez historyjki.
    for item in reopened or []:
        story_id = str(item.get("id", "")) if isinstance(item, dict) else ""
        if story_id not in ids:
            violations.append(
                f"stories_reopened wskazuje nieznane ID: {story_id!r}")
        elif story_id in dropped_ids:
            violations.append(
                f"{story_id} jest jednocześnie w stories_dropped i "
                "stories_reopened; wybierz jedno")
    for story in before:
        if story.id not in ids and story.id not in dropped_ids:
            violations.append(
                f"historyjka {story.id} zniknęła bez wpisu w stories_dropped")

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


# Historyjka niedomknięta: praca nad nią jest zaczęta albo jeszcze nie ruszyła,
# ale na pewno nie jest rozliczona dowodem. Tylko `zrobiona` i `porzucona` są
# stanami końcowymi.
OPEN_STATUSES = ("nowa", "w toku", "do weryfikacji")


def count_open(stories: list[Story]) -> int:
    """Ile historyjek jest niedomkniętych.

    Liczyło kiedyś wyłącznie `nowa` — i to była przyczyna zacisku, w którym
    maszyna sama się zatrzymywała. Domknięte zadanie przestawia historyjkę na
    `do weryfikacji`, więc licznik `nowa` spadał do zera przy KAŻDYM domknięciu
    i wyzwalacz refilla odpalał bez przerwy. Product Owner widział tymczasem
    cały plik — dziesiątki historyjek `do weryfikacji` przy miękkim sufcie —
    więc czytał ten sam stan jako zakaz poszerzania i dokładał po jednej.
    Wyzwalacz i sufit mierzyły dwie różne rzeczy, a bieg kręcił się w miejscu.

    Jedna definicja „otwartej" historyjki usuwa tę sprzeczność: zaległość
    niezweryfikowanych jest zatorem do rozładowania weryfikacją, a nie brakiem
    pracy do dobrania.
    """
    return sum(story.status in OPEN_STATUSES for story in stories)


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
