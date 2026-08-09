from forge import backlog


EXAMPLE = """# Kolejka

## US-007 — Gracz widzi wynik potyczki  [nowa]

Jako gracz chcę zobaczyć wynik, żeby zdecydować, czy warto było ryzykować.

- Dlaczego teraz: PROJECT.md stawia decyzyjność gracza jako kryterium sukcesu.
- Sprawdzenie: uruchom `make demo` i przejdź potyczkę do końca.
- Poza zakresem: statystyki historyczne.
"""


def test_parse_reads_canonical_story() -> None:
    stories, orphans = backlog.parse(EXAMPLE)
    assert not orphans
    assert stories == [backlog.Story(
        id="US-007", title="Gracz widzi wynik potyczki", status="nowa",
        drop_reason="", why_now="PROJECT.md stawia decyzyjność gracza jako kryterium sukcesu.",
        check="uruchom `make demo` i przejdź potyczkę do końca.",
        out_of_scope="statystyki historyczne.",
        body="Jako gracz chcę zobaczyć wynik, żeby zdecydować, czy warto było ryzykować.",
        line=3)]


def test_parse_preserves_story_with_missing_optional_field_and_reports_junk() -> None:
    stories, orphans = backlog.parse(
        EXAMPLE + "\n## US-008 — Druga [porzucona: brak popytu]\n"
        "- Dlaczego teraz: dowód\n- Sprawdzenie: ekran\n\n"
        "## Nie jest historyjką\nśmieci\n")
    assert stories[-1].drop_reason == "brak popytu"
    assert stories[-1].out_of_scope == ""
    assert any("Nie jest historyjką" in orphan for orphan in orphans)


def test_validate_hard_reports_each_structural_violation() -> None:
    before, _ = backlog.parse(EXAMPLE)
    duplicate = before[0]
    after = [duplicate, duplicate]
    violations = backlog.validate_hard(before, after, [], ["junk"])
    assert any("więcej niż raz" in item for item in violations)
    assert any("nieparsowalne" in item for item in violations)

    missing = backlog.Story(
        "US-999", "x", "nieznany", "", "", "", "", "", 1)
    violations = backlog.validate_hard(before, [missing], [], [])
    assert any("niedozwolony status" in item for item in violations)
    assert any("brak niepustego pola Sprawdzenie" in item for item in violations)
    assert any("brak niepustego pola Dlaczego teraz" in item for item in violations)
    assert any("US-007 zniknęła" in item for item in violations)


def test_validate_hard_rejects_status_change_and_accepts_drop() -> None:
    before, _ = backlog.parse(EXAMPLE)
    changed = backlog.Story(**{**before[0].__dict__, "status": "w toku"})
    assert any("statusy należą do Forge" in item
               for item in backlog.validate_hard(before, [changed], [], []))
    assert backlog.validate_hard(before, [], [{"id": "US-007"}], []) == []


def test_validate_hard_rejects_new_story_born_with_forge_owned_status() -> None:
    before, _ = backlog.parse(EXAMPLE)
    smuggled = backlog.Story(
        "US-008", "Druga", "zrobiona", "", "dowód", "ekran", "", "", 1)
    violations = backlog.validate_hard(before, [*before, smuggled], [], [])
    assert any("nowa historyjka musi mieć status" in item for item in violations)

    dropped_on_arrival = backlog.Story(
        "US-009", "Trzecia", "porzucona", "z góry", "dowód", "ekran", "", "", 1)
    assert backlog.validate_hard(
        before, [*before, dropped_on_arrival], [], []) == []


def test_set_status_changes_only_one_header_line() -> None:
    original = EXAMPLE.replace("\n", "\r\n") + "\r\nkomentarz\r\n"
    updated = backlog.set_status(original, "US-007", "porzucona", "brak popytu")
    old_lines = original.splitlines()
    new_lines = updated.splitlines()
    assert len(old_lines) == len(new_lines)
    assert old_lines[0:2] == new_lines[0:2]
    assert old_lines[3:] == new_lines[3:]
    assert "[porzucona: brak popytu]" in new_lines[2]


def test_set_status_rejects_unknown_id_and_status() -> None:
    try:
        backlog.set_status(EXAMPLE, "US-999", "nowa")
    except KeyError:
        pass
    else:
        raise AssertionError("unknown story must raise KeyError")
    try:
        backlog.set_status(EXAMPLE, "US-007", "zrobiona-przez-po")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown status must raise ValueError")


def test_counts_load_and_legacy_detection(tmp_path) -> None:
    stories, _ = backlog.parse(EXAMPLE)
    assert backlog.count_open(stories) == 1
    assert backlog.ids_by_status(stories, "nowa") == ["US-007"]
    assert backlog.load(str(tmp_path)) == ([], [])
    assert backlog.is_legacy("- [ ] stara proza\n")
    assert not backlog.is_legacy(EXAMPLE)
    assert not backlog.is_legacy("\n")
