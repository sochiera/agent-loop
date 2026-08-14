"""Mapa pokrycia briefu: które sekcje wymagań mają już wynik, a które stoją puste.

Product Owner nie widział dotąd briefu wcale. Czytał ``docs/PROJECT.md`` i
``BACKLOG.md``, a sam brief docierał do niego wyłącznie przy triggerze
``brief``, i to jako diff. Skutek był systematyczny: kolejne historyjki
pogłębiały to, co już działało, bo tylko to było widać w backlogu. W mierzonym
biegu dało to dwunasty przyrost porównania wariantów przy ośmiu sekcjach
briefu, do których nie powstała ANI JEDNA linia kodu — i był to ruch całkowicie
legalny, bo żadna rola nie miała jak zauważyć, że reszta mapy nie istnieje.

Stan sekcji liczy się DETERMINISTYCZNIE z backlogu, a nie opinią modelu. Powód
jest ten sam, dla którego statusy historyjek przepisuje Forge, a nie PO: mapa
ma być punktem odniesienia dla recenzji, więc nie może być wytworem tej samej
tury, którą ocenia. Wiązanie historyjki z sekcją niesie pole ``Sekcja briefu:``
w BACKLOG.md — jedno zdanie od PO zamiast dodatkowego wywołania modelu.

Moduł jest czysty: bez repozytorium, bez ``git`` i bez LLM-a.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .backlog import Story, closest_section, section_key

# Sekcją briefu jest nagłówek drugiego poziomu. Pierwszy poziom to tytuł
# dokumentu, a głębsze nagłówki należą już do wnętrza sekcji.
_SECTION_HEADING = re.compile(r"^##\s+(?P<name>.+?)\s*$", re.MULTILINE)

# Stany sekcji.
DONE = "jest"
SKELETON = "szkielet"
EMPTY = "brak"
# Sekcja świadomie odpuszczona: brief bywa dokumentem, w którym część
# nagłówków to kontekst (podsumowanie, kryterium ukończenia), a nie wymaganie.
# Bez tego stanu kontrakt byłby sprzeczny: prompt startu pozwalałby pominąć
# taką sekcję, a recenzentka MUSIAŁABY zablokować turę za jej pustkę.
WAIVED = "pominięta"

# Kolejność otwierania sekcji. Sam `brak` to za mało: po turze startowej żadna
# sekcja nie jest już pusta, więc reguła „otwórz największą pustą" milkłaby i
# wolno byłoby znowu szlifować to, co działa, obok ledwie zaczętej reszty mapy.
_PRIORITY = {EMPTY: 0, SKELETON: 1, DONE: 2, WAIVED: 3}


@dataclass(frozen=True)
class Section:
    """Jedna sekcja briefu razem z jej pokryciem."""

    name: str
    # Waga to liczba niepustych linii sekcji w briefie. Jest przybliżeniem, ale
    # przybliżeniem UCZCIWYM: pochodzi wyłącznie z tego, ile miejsca poświęcił
    # danej rzeczy autor briefu, a nie z oceny modelu. Bez niej „największa
    # pusta sekcja" byłaby zwrotem retorycznym, którego recenzentka nie ma jak
    # sprawdzić.
    weight: int
    state: str
    stories: tuple[str, ...]


# Normalizacja i dopasowanie nazw sekcji mieszkają w `backlog`, bo używa ich
# też twardy walidator. Jedno źródło reguły, żeby walidator i mapa nie mogły
# uznać tej samej nazwy za dwie różne sekcje.
_key = section_key


def sections(brief_text: str) -> list[str]:
    """Nazwy sekcji briefu, w kolejności występowania."""
    return [match.group("name").strip()
            for match in _SECTION_HEADING.finditer(brief_text or "")]


def resolve(name: str, brief_text: str) -> str:
    """Kanoniczna nazwa sekcji dla wpisu z historyjki albo pusty string."""
    if not str(name).strip():
        return ""
    known = {_key(item): item for item in sections(brief_text)}
    return known.get(_key(name), "")


def suggest(name: str, brief_text: str) -> str:
    """Najbliższa istniejąca nazwa sekcji — podpowiedź do treści naruszenia."""
    return closest_section(name, sections(brief_text))


def _weights(brief_text: str) -> dict[str, int]:
    text = brief_text or ""
    found = list(_SECTION_HEADING.finditer(text))
    weights: dict[str, int] = {}
    for index, match in enumerate(found):
        end = found[index + 1].start() if index + 1 < len(found) else len(text)
        body = text[match.end():end]
        weights[match.group("name").strip()] = sum(
            1 for line in body.splitlines() if line.strip())
    return weights


def _state(statuses: list[str]) -> str:
    if not statuses:
        return EMPTY
    if "zrobiona" in statuses:
        return DONE
    # Sama `porzucona` nie jest pokryciem: potrzeba została odrzucona, więc
    # sekcja stoi tak samo pusta jak przed nią.
    if all(status == "porzucona" for status in statuses):
        return EMPTY
    return SKELETON


def build(brief_text: str, stories: list[Story],
          waived: list[str] | None = None) -> list[Section]:
    """Mapa pokrycia, posortowana w kolejności otwierania sekcji.

    Kolejność jest częścią kontraktu z recenzentką: reguła „następna historyjka
    otwiera wskazaną sekcję" jest sprawdzalna tylko wtedy, gdy to Forge, a nie
    model, rozstrzyga, która sekcja jest następna.
    """
    weights = _weights(brief_text)
    waived_keys = {_key(item) for item in (waived or [])}
    by_section: dict[str, list[Story]] = {name: [] for name in weights}
    canonical = {_key(name): name for name in weights}
    for story in stories:
        name = canonical.get(_key(story.brief_section), "")
        if name:
            by_section[name].append(story)
    result = []
    for name, found in by_section.items():
        state = _state([story.status for story in found])
        # Odpuszczenie nie kasuje dowodu: sekcja, która mimo wszystko dostała
        # rozliczoną historyjkę, jest po prostu zrobiona.
        if state != DONE and _key(name) in waived_keys:
            state = WAIVED
        result.append(Section(
            name=name, weight=weights[name], state=state,
            stories=tuple(f"{story.id} [{story.status}]" for story in found)))
    return sorted(result, key=lambda item: (_PRIORITY[item.state], -item.weight,
                                            item.name))


def unmapped(stories: list[Story], brief_text: str) -> list[str]:
    """Historyjki wskazujące sekcję, której brief nie zna (albo żadnej)."""
    known = {_key(item) for item in sections(brief_text)}
    return [story.id for story in stories
            if story.status != "porzucona"
            and _key(story.brief_section) not in known]


def next_target(coverage: list[Section]) -> Section | None:
    """Sekcja, którą ma otworzyć następna historyjka.

    Najpierw największa pusta, a gdy pustych nie ma — największa rozgrzebana.
    Drugi człon jest istotny: po turze startowej każda sekcja ma już swoją
    historyjkę, więc sama reguła „największa pusta" milknie i przestaje bronić
    czegokolwiek dokładnie w chwili, w której zaczyna się pogłębianie.
    """
    for item in coverage:
        if item.state in (EMPTY, SKELETON):
            return item
    return None


def settled(coverage: list[Section]) -> bool:
    """Czy mapa MVP jest rozliczona: każda sekcja zrobiona albo odpuszczona."""
    return all(item.state in (DONE, WAIVED) for item in coverage)


def render(coverage: list[Section], unmapped_ids: list[str] | None = None) -> str:
    """Tabela dla promptu. Pusty brief bez sekcji nie udaje mapy."""
    if not coverage:
        return ("(brief nie ma sekcji `##` — mapa pokrycia niedostępna, "
                "nie egzekwuj reguły następnej sekcji)")
    lines = ["| sekcja briefu | waga | stan | historyjki |",
             "|---|---|---|---|"]
    for item in coverage:
        stories = ", ".join(item.stories) or "—"
        lines.append(f"| {item.name} | {item.weight} | {item.state} | {stories} |")
    lines.append("")
    target = next_target(coverage)
    if target:
        lines.append(f"NASTĘPNA SEKCJA DO OTWARCIA: {target.name} "
                     f"(stan: {target.state}, waga: {target.weight})")
    else:
        lines.append("Każda sekcja briefu jest zrobiona albo świadomie pominięta.")
    if unmapped_ids:
        # Mapa liczona z niepełnych danych nie może uzbrajać reguły blokującej:
        # historyjka bez rozpoznanej sekcji wygląda jak dziura w produkcie,
        # choć jest wyłącznie dziurą w metadanych.
        lines.append(
            "MAPA NIEPEŁNA — historyjki bez rozpoznanej sekcji: "
            + ", ".join(unmapped_ids)
            + ". Dopóki ta lista nie jest pusta, reguła blokująca NIE obowiązuje; "
            "uzupełnij `Sekcja briefu:` w tych historyjkach.")
    return "\n".join(lines)
