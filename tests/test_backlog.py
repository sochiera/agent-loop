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
    assert any("brak niepustego pola Sprawdzenie" in item for item in violations)
    assert any("brak niepustego pola Dlaczego teraz" in item for item in violations)
    assert any("US-007 zniknęła" in item for item in violations)


def test_validate_hard_never_reports_status_because_forge_rewrites_it() -> None:
    """Statusy nie są opinią; walidator nie ma o nich nic do powiedzenia.

    Ta para reguł zakleszczała się na sobie: nielegalny status w pliku był
    naruszeniem, a naprawienie go — drugim naruszeniem. Właścicielem jest
    teraz ``coerce_statuses``, więc walidator ma o nich milczeć.
    """
    before, _ = backlog.parse(EXAMPLE)
    changed = backlog.Story(**{**before[0].__dict__, "status": "w toku"})
    assert backlog.validate_hard(before, [changed], [], []) == []
    illegal = backlog.Story(**{**before[0].__dict__, "status": "gotowe"})
    assert backlog.validate_hard(before, [illegal], [], []) == []
    smuggled = backlog.Story(
        "US-008", "Druga", "zrobiona", "", "dowód", "ekran", "", "", 1)
    assert backlog.validate_hard(before, [*before, smuggled], [], []) == []
    assert backlog.validate_hard(before, [], [{"id": "US-007"}], []) == []


def test_coerce_statuses_restores_forge_truth_and_heals_illegal_status() -> None:
    before, _ = backlog.parse(EXAMPLE)
    forge_truth = [backlog.Story(**{**before[0].__dict__, "status": "w toku"})]

    text, changes = backlog.coerce_statuses(
        EXAMPLE.replace("[nowa]", "[zrobiona]"), forge_truth)
    assert "## US-007 — Gracz widzi wynik potyczki  [w toku]" in text
    assert changes == ["US-007: 'zrobiona'→'w toku'"]

    # Sedno awarii: status spoza kontraktu (tu wpisany przez turę kodera) nie
    # ma jak zostać, ale i nie może zablokować tury — wraca do weryfikacji.
    legacy = [backlog.Story(**{**before[0].__dict__, "status": "gotowe"})]
    text, changes = backlog.coerce_statuses(
        EXAMPLE.replace("[nowa]", "[gotowe]"), legacy)
    assert "[do weryfikacji]" in text
    assert changes == ["US-007: 'gotowe'→'do weryfikacji'"]

    # Powtórzenie na własnym wyniku nic nie zmienia — dlatego ta pętla nie umie
    # się zapętlić, niezależnie od tego, co zastała w pliku.
    assert backlog.coerce_statuses(text, legacy) == (text, [])


def test_coerce_statuses_forces_new_story_to_nowa_and_keeps_drop_reason() -> None:
    before, _ = backlog.parse(EXAMPLE)
    smuggled = EXAMPLE + (
        "\n## US-008 — Druga  [zrobiona]\n\n"
        "- Dlaczego teraz: dowód\n- Sprawdzenie: ekran\n")
    text, changes = backlog.coerce_statuses(smuggled, before)
    assert "## US-008 — Druga  [nowa]" in text
    assert changes == ["US-008: 'zrobiona'→'nowa'"]

    dropped = [backlog.Story(**{**before[0].__dict__,
                                "status": "porzucona", "drop_reason": "brak popytu"})]
    text, _ = backlog.coerce_statuses(EXAMPLE, dropped)
    assert "[porzucona: brak popytu]" in text


def test_validate_hard_rejects_reopened_ghost_id_and_conflict_with_dropped() -> None:
    """Wznowienie jest ogłaszane procesowi jako fakt, więc ID musi istnieć.

    `set_status` na nieznanym ID jest cichym brakiem trafienia — bez tej reguły
    zmyślone US-999 nie zmieniłoby backlogu, ale planista dostałby pracę do
    wykonania na historyjce, której nie ma.
    """
    before, _ = backlog.parse(EXAMPLE)
    violations = backlog.validate_hard(
        before, before, [], [], [{"id": "US-999", "reason": "nie działa"}])
    assert any("nieznane ID" in item and "US-999" in item for item in violations)

    violations = backlog.validate_hard(
        before, before, [{"id": "US-007", "reason": "zbędna"}], [],
        [{"id": "US-007", "reason": "nie działa"}])
    assert any("jednocześnie w stories_dropped" in item for item in violations)

    assert backlog.validate_hard(
        before, before, [], [], [{"id": "US-007", "reason": "nie działa"}]) == []


def test_coerce_statuses_leaves_duplicate_ids_to_the_validator() -> None:
    before, _ = backlog.parse(EXAMPLE)
    duplicated = EXAMPLE + EXAMPLE.split("# Kolejka", 1)[1].replace("[nowa]", "[zrobiona]")
    text, changes = backlog.coerce_statuses(duplicated, before)
    assert text == duplicated and changes == []
    assert any("więcej niż raz" in item for item in backlog.validate_hard(
        before, backlog.parse(duplicated)[0], [], []))


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
