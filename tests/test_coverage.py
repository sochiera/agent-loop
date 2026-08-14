from forge import backlog, coverage


BRIEF = """# Projekt: Konfigurator

Wstęp, który nie należy do żadnej sekcji.

## Konfigurator

Wybór części i podpowiedzi.
Reguły zgodności.

## Porównywanie komponentów

Zestawienie dwóch wariantów.

## Import wszystkich produktów z x-kom

Pobranie katalogu.
Odświeżanie cen.
Identyfikacja produktów.
Obsługa braków w danych.
"""


def _story(story_id: str, status: str, section: str) -> str:
    return (f"\n## {story_id} — Zdolność  [{status}]\n\n"
            "Jako użytkownik chcę czegoś.\n\n"
            "- Dlaczego teraz: dowód.\n"
            f"- Sekcja briefu: {section}\n"
            "- Sprawdzenie: uruchom demo.\n")


def _stories(*blocks: str) -> list[backlog.Story]:
    return backlog.parse("# Backlog\n" + "".join(blocks))[0]


def test_sections_reads_second_level_headings_only() -> None:
    assert coverage.sections(BRIEF) == [
        "Konfigurator", "Porównywanie komponentów",
        "Import wszystkich produktów z x-kom"]


def test_section_state_comes_from_delivered_proof_not_from_intent() -> None:
    """`jest` wymaga historyjki rozliczonej dowodem, nie zaplanowanej.

    Gdyby sam plan liczył się jako pokrycie, mapa gasłaby w chwili napisania
    historyjki i przestałaby pokazywać, gdzie produkt naprawdę ma dziury.
    """
    stories = _stories(
        _story("US-001", "zrobiona", "Konfigurator"),
        _story("US-002", "do weryfikacji", "Porównywanie komponentów"),
    )
    by_name = {item.name: item for item in coverage.build(BRIEF, stories)}

    assert by_name["Konfigurator"].state == coverage.DONE
    assert by_name["Porównywanie komponentów"].state == coverage.SKELETON
    assert by_name["Import wszystkich produktów z x-kom"].state == coverage.EMPTY


def test_abandoned_story_does_not_cover_a_section() -> None:
    """Porzucenie potrzeby nie jest jej zaspokojeniem."""
    stories = _stories(_story("US-001", "porzucona", "Konfigurator"))
    by_name = {item.name: item for item in coverage.build(BRIEF, stories)}

    assert by_name["Konfigurator"].state == coverage.EMPTY


def test_empty_sections_come_first_ordered_by_weight_of_the_brief() -> None:
    """„Następna sekcja" musi być rozstrzygnięciem Forge, nie modelu.

    Waga to liczba niepustych linii sekcji w briefie — miara pochodząca
    wyłącznie z tego, ile miejsca poświęcił jej autor. Bez niej reguła
    „otwórz wskazaną sekcję" byłaby zwrotem, którego recenzentka nie ma jak
    sprawdzić.
    """
    stories = _stories(_story("US-001", "zrobiona", "Konfigurator"))
    result = coverage.build(BRIEF, stories)

    assert [item.name for item in result] == [
        "Import wszystkich produktów z x-kom",  # pusta i najcięższa
        "Porównywanie komponentów",             # pusta, lżejsza
        "Konfigurator",                         # pokryta
    ]
    assert coverage.next_target(result).name == "Import wszystkich produktów z x-kom"


def test_next_target_falls_back_to_the_largest_started_section() -> None:
    """Po turze startowej nie ma sekcji pustych — reguła nie może zamilknąć.

    Start zakłada po jednej historyjce na sekcję, więc `brak` znika z mapy
    natychmiast. Gdyby wskazówka opierała się wyłącznie na `brak`, przestałaby
    czegokolwiek bronić dokładnie w chwili, w której zaczyna się pogłębianie —
    i znowu wolno byłoby szlifować to, co działa, obok ledwie zaczętej reszty.
    """
    stories = _stories(
        _story("US-001", "zrobiona", "Konfigurator"),
        _story("US-002", "nowa", "Porównywanie komponentów"),
        _story("US-003", "nowa", "Import wszystkich produktów z x-kom"),
    )
    target = coverage.next_target(coverage.build(BRIEF, stories))

    assert target.state == coverage.SKELETON
    assert target.name == "Import wszystkich produktów z x-kom"


def test_waived_section_stops_being_a_hole_but_never_hides_a_real_one() -> None:
    """Odpuszczenie sekcji jest legalne i widoczne, a dowód je nadpisuje.

    Brief bywa dokumentem, w którym część nagłówków to kontekst, a nie
    wymaganie. Bez tego stanu kontrakt byłby sprzeczny: prompt startu pozwalał
    pominąć taką sekcję, a recenzentka musiałaby zablokować turę za jej pustkę.
    """
    result = coverage.build(BRIEF, [], waived=["Porównywanie komponentów"])
    by_name = {item.name: item for item in result}

    assert by_name["Porównywanie komponentów"].state == coverage.WAIVED
    # Pominięta sekcja schodzi na koniec kolejki i nie jest już celem.
    assert coverage.next_target(result).name == "Import wszystkich produktów z x-kom"

    # Odpuszczenie nie może zamaskować pracy, która jednak powstała.
    stories = _stories(_story("US-001", "zrobiona", "Porównywanie komponentów"))
    with_proof = {item.name: item
                  for item in coverage.build(BRIEF, stories,
                                             waived=["Porównywanie komponentów"])}
    assert with_proof["Porównywanie komponentów"].state == coverage.DONE


def test_settled_map_needs_every_section_done_or_waived() -> None:
    stories = _stories(
        _story("US-001", "zrobiona", "Konfigurator"),
        _story("US-002", "zrobiona", "Porównywanie komponentów"),
    )
    assert not coverage.settled(coverage.build(BRIEF, stories))
    assert coverage.settled(coverage.build(
        BRIEF, stories, waived=["Import wszystkich produktów z x-kom"]))


def test_section_names_match_tolerantly_so_a_typo_is_not_a_contract_breach() -> None:
    """Nazwę sekcji przepisuje model, więc porównanie nie może być dosłowne.

    Twarde porównanie zamieniałoby podwójną spację albo inną wielkość litery w
    naruszenie kontraktu, czyli w kolejną pełnopłatną turę za zero wartości.
    """
    stories = _stories(_story("US-001", "zrobiona", "  konfigurator  "))
    by_name = {item.name: item for item in coverage.build(BRIEF, stories)}

    assert by_name["Konfigurator"].state == coverage.DONE
    assert coverage.unmapped(stories, BRIEF) == []
    assert coverage.resolve("KONFIGURATOR", BRIEF) == "Konfigurator"
    # Nazwa naprawdę obca dostaje podpowiedź zamiast samego odrzucenia.
    assert coverage.suggest("Analiza zasilania", BRIEF) == ""
    assert coverage.suggest("Konfigurato", BRIEF) == "Konfigurator"


def test_story_pointing_at_an_unknown_section_is_reported_not_silently_dropped() -> None:
    stories = _stories(_story("US-001", "nowa", "Sekcja, której nie ma"))

    assert coverage.unmapped(stories, BRIEF) == ["US-001"]
    assert all(item.state == coverage.EMPTY
               for item in coverage.build(BRIEF, stories))


def test_incomplete_map_disarms_the_blocking_rule_instead_of_lying() -> None:
    """Mapa liczona z niepełnych danych nie może uzbrajać reguły blokującej.

    Backlog sprzed tego mechanizmu ma historyjki bez pola `Sekcja briefu`.
    Wyglądają one jak dziura w produkcie, choć są wyłącznie dziurą w
    metadanych — a recenzentka blokowałaby wtedy każdą turę za rzekomo pustą
    sekcję, którą tamte historyjki dawno pokryły.
    """
    stories = _stories(_story("US-001", "zrobiona", ""))
    table = coverage.render(coverage.build(BRIEF, stories),
                            coverage.unmapped(stories, BRIEF))

    assert "MAPA NIEPEŁNA" in table
    assert "reguła blokująca NIE obowiązuje" in table
    assert "US-001" in table


def test_render_names_the_next_section_for_the_reviewer() -> None:
    stories = _stories(_story("US-001", "zrobiona", "Konfigurator"))
    table = coverage.render(coverage.build(BRIEF, stories))

    assert ("NASTĘPNA SEKCJA DO OTWARCIA: Import wszystkich produktów z x-kom "
            "(stan: brak, waga: 4)") in table
    assert "| Konfigurator | 2 | jest | US-001 [zrobiona] |" in table
    assert "MAPA NIEPEŁNA" not in table


def test_brief_without_sections_disables_the_rule_instead_of_faking_a_map() -> None:
    """Brief bez nagłówków jest legalny i nie może zakleszczyć recenzji."""
    table = coverage.render(coverage.build("Zwykły tekst bez sekcji.", []))

    assert "nie egzekwuj reguły" in table
    assert coverage.sections("Zwykły tekst bez sekcji.") == []


def test_full_coverage_says_so_instead_of_pointing_nowhere() -> None:
    stories = _stories(
        _story("US-001", "zrobiona", "Konfigurator"),
        _story("US-002", "zrobiona", "Porównywanie komponentów"),
        _story("US-003", "zrobiona", "Import wszystkich produktów z x-kom"),
    )
    table = coverage.render(coverage.build(BRIEF, stories))

    assert "Każda sekcja briefu jest zrobiona albo świadomie pominięta." in table
    assert "NASTĘPNA SEKCJA" not in table
