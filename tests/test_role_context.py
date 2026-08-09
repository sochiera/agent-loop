from pathlib import Path

from forge import prompts
from forge.state import State


def test_every_json_contract_template_carries_quote_rule() -> None:
    templates = Path(prompts.__file__).with_name("templates")
    for template in templates.glob("*.md"):
        text = template.read_text(encoding="utf-8")
        if template.name != "json-rules.md" and "JSON" in text:
            assert "{{JSON_RULES}}" in text, template.name


def _flat(text: str) -> str:
    """Prompt jest zawijany dla czytelności — asercje mają pilnować treści,
    nie miejsca łamania linii."""
    return " ".join(text.split())


def test_private_role_prompts_use_capsule_without_record_or_ledger() -> None:
    state = State(
        current_task={"id": "task-001", "file": "task.md"},
        tdd_round=1,
        tester_decision={
            "status": "red", "reason": "brakuje walidacji",
            "command": "pytest tests/test_app.py",
        },
        coder_summary="dodano walidację",
    )
    tester_capsule = prompts.context_capsule(
        state, "tester",
        notebook_text="- r1: bramka pytest tests/test_app.py → 2 passed",
        changed_files=["app.py", "tests/test_app.py"],
        handoff="dodano walidację",
        confirmation=True,
    )
    coder_capsule = prompts.context_capsule(
        state, "coder",
        notebook_text="- r1: most żyje w bridge_client.gd:158",
        changed_files=["tests/test_app.py"],
    )
    tester = prompts.tester_task_prompt(
        "task.md", "pytest", capsule=tester_capsule,
    )
    coder = prompts.coder_task_prompt(
        "task.md", "pytest",
        decision=state.tester_decision, capsule=coder_capsule)
    assert "dodano walidację" in tester
    assert "Ostatnia decyzja testera: red — brakuje walidacji" in tester
    assert "app.py" in tester and "tests/test_app.py" in tester
    assert "- r1: bramka pytest tests/test_app.py → 2 passed" in tester
    assert ".forge/notebooks/" not in tester
    assert "brakuje walidacji" in coder
    assert "pytest tests/test_app.py" in coder
    # Koder nie dotyka pliku notatnika, więc ścieżka byłaby dla niego tylko
    # zaproszeniem do zbędnej tury narzędziowej.
    assert ".forge/notebooks/" not in coder
    assert "most żyje w bridge_client.gd:158" in coder
    assert "ostatnie wpisy dziennika" not in tester
    assert "Prywatny, ograniczony zapis" not in tester + coder
    assert "zachować, poprawić albo przywrócić" in tester
    assert "Uwagi review rozpoczynają nowy cykl TDD" in tester
    assert "kolekcjonuje się" in tester
    assert "pada na asercji kontraktu" in tester
    assert "błędzie składni/importu/nazwy" in tester
    assert "napraw natychmiast" in tester
    assert "blocked" in tester
    assert "Decyzja testera" in coder and "transcript" not in coder.lower()
    assert "summary" in coder and "testerowi" in coder
    assert "tester_input_needed" in coder


def test_notebooks_reach_roles_inline_instead_of_through_a_tool_turn() -> None:
    state = State(current_task={"id": "task-001", "file": "task.md"})
    filled = [
        prompts.context_capsule(
            state, role, notebook_text="- r1: sonda wymaga --headless")
        for role in ("tester", "coder")
    ]
    empty = [prompts.context_capsule(state, role)
             for role in ("tester", "coder")]

    for capsule in filled:
        assert "- r1: sonda wymaga --headless" in capsule
        # Ścieżka byłaby jedynym powodem, żeby mimo wszystko sięgnąć na dysk.
        assert ".forge" not in capsule
    for capsule in empty:
        # Pusty notatnik nie zostawia nagłówka sugerującego pamięć, której nie
        # ma, ani ścieżki zapraszającej do tury narzędziowej.
        assert "notat" not in capsule.lower()
        assert ".forge" not in capsule


def test_both_roles_return_their_notebook_line_inside_the_decision() -> None:
    coder = prompts.coder_task_prompt(
        "task.md", "pytest", decision={"status": "red", "reason": "missing"})
    tester = prompts.tester_task_prompt("task.md", "pytest")

    for prompt in (coder, tester):
        assert '"notebook":"..."' in prompt
        assert "Notatnika nie zapisujesz sama i nie czytasz z dysku" in prompt
    assert "gdzie leży kod" in coder
    assert "Nie powtarzaj `summary`" in coder
    assert "dokładną komendę wartą ponownego użycia" in tester
    assert "Nie zapisuj bieżącego `passed`/`failed`" in tester
    assert "Nie powtarzaj `reason`" in tester
    # Kontrakt append-only musi być jawny, bo tester traci możliwość rewizji.
    assert "nie poprawisz nimi wcześniejszej rundy" in tester


def test_bootstrap_creates_informational_project_instructions() -> None:
    prompt = prompts.bootstrap_prompt("brief")

    assert "AGENTS.md" in prompt
    assert "CLAUDE.md" in prompt
    assert ".forge/" in prompt
    assert "runtime orkiestratora" in prompt
    assert "Prywatny notatnik roli dostajesz\nw kapsule" in prompt
    assert "wpisy oddajesz polem" in prompt
    assert "Nie czytaj notatników innych ról" in prompt


def test_bootstrap_creates_indexed_documentation_layout() -> None:
    prompt = prompts.bootstrap_prompt("brief")

    assert "docs/ARCHITECTURE/00-INDEX.md" in prompt
    assert "docs/DESIGN/00-INDEX.md" in prompt
    assert "docs/DECISIONS/" in prompt


def test_planner_declares_explicit_task_dependencies() -> None:
    prompt = prompts.plan_batch_prompt(4, 1)

    assert "depends_on" in prompt
    assert "identyfikator" in prompt


def test_planner_contract_owns_outcomes_not_test_design() -> None:
    prompt = prompts.plan_batch_prompt(4, 1)

    assert "Kryteria akceptacji" in prompt
    assert "Publiczny kontrakt" in prompt
    assert "Ścieżki testów" not in prompt
    assert "Test ukierunkowany" not in prompt
    assert "test_globs" not in prompt
    assert "code_globs" not in prompt
    assert '"criteria"' not in prompt
    assert "najwęższy wiarygodny dobór należy do testera" in prompt
    assert "prywatnych helperów" in prompt
    assert "unikalnego ryzyka na granicy systemów" in prompt
    assert "zweryfikuj" in prompt
    assert "uruchomieniem" in prompt
    assert "wynik" in prompt


def test_confirmation_prompt_checks_targeted_gate_criteria_and_test_quality() -> None:
    prompt = prompts.tester_task_prompt(
        "task.md", "pytest -q", confirmation=True,
        suggested_test_cmd="pytest -q tests/test_app.py")

    assert "TURA POTWIERDZAJĄCA" in prompt
    assert "pytest -q tests/test_app.py" in prompt
    assert "czy jest zielona" in prompt
    assert "pozostały nieprzetestowane kryteria akceptacji" in prompt
    assert "mały refaktor bez osłabiania pokrycia" in prompt
    assert "należy do Forge przed commitem" in prompt
    assert "Nie oceniaj jakości implementacji" in prompt
    assert "świeżego reviewera" in prompt


def test_first_red_gate_maps_the_criteria_and_covers_more_than_one() -> None:
    """Runda, nie test, jest w tej pętli jednostką kosztu: bez mapy kryteriów
    tester odkrywa kolejne dopiero po zielonym i każde kupuje własną rundę."""
    prompt = _flat(prompts.tester_task_prompt("task.md", "pytest -q"))

    assert "KRYTERIA AKCEPTACJI z pliku zadania" in prompt
    assert "Bramka ma pokrywać 2–3 kryteria naraz, nie jedno i nie wszystkie" in prompt
    assert "świadomie odłożone wymień jawnie razem z powodem" in prompt
    # Warunku czerwonej bramki nie wolno rozluźnić razem z jej poszerzeniem.
    assert "KAŻDY test bramki kolekcjonuje się i pada na asercji kontraktu" in prompt
    assert "minimalny czerwony test" not in prompt


def test_confirmation_extends_the_gate_instead_of_opening_a_new_cycle() -> None:
    prompt = _flat(prompts.tester_task_prompt(
        "task.md", "pytest -q", confirmation=True))

    assert "ROZSZERZ o nie bieżącą bramkę w TEJ rundzie" in prompt
    assert "zamiast otwierać nowy cykl `red`" in prompt
    assert "wymaga osobnej bramki" in prompt


def test_confirmation_wins_over_sticky_regression_from_legacy_checkpoint() -> None:
    prompt = prompts.tester_task_prompt(
        "task.md", "pytest -q", confirmation=True, suite_regression=True)

    assert "TURA POTWIERDZAJĄCA" in prompt
    assert "PEŁNA BRAMKA wykryła regresję" not in prompt


def test_tester_owns_targeted_command_and_test_refactor() -> None:
    prompt = prompts.tester_task_prompt("task.md", "pytest -q")

    assert "Samodzielnie wybierz" in prompt
    assert "najwęższą wiarygodną komendę" in prompt
    assert "fallbackiem, nie domyślną komendą" in prompt
    assert "realistyczny defekt" in prompt
    assert "change-detectorów" in prompt
    assert "refaktorować testy i ich wspólną infrastrukturę" in prompt


def test_reviewer_prompt_is_plain_code_review() -> None:
    prompt = prompts.review_task_prompt_kiss(
        "task.md", start_tag="forge/task-start", changed=["app.py"])

    for expected in (
        "błędów zachowania", "SOLID/KISS", "design smells", "duplikacji",
        "nazw, które nie opisują", "testy sprawdzają wartościowe zachowanie",
        "Nie streszczaj diffu",
    ):
        assert expected in prompt
    assert "macierz" not in prompt
    assert "Zwróć wyłącznie JSON" in prompt
    assert '"verdict":"approve"' in prompt
    assert '"verdict":"suggestions"' in prompt
    assert '"verdict":"request_changes"' in prompt
    assert "można bezpiecznie zacommitować" in prompt
    assert "użyj `suggestions`" in prompt
    assert "użyj `request_changes`" in prompt
    assert "umieść ją w `nits`" in prompt


def test_suggestions_prompt_can_finalize_or_escalate() -> None:
    prompt = prompts.tester_task_prompt(
        "task.md",
        "pytest -q",
        capsule=(
            "KAPSUŁA KONTEKSTU\n"
            "Handoff do testera: uprość helper\n"
            "Aktywne uwagi review: usuń duplikację; skróć nazwę"
        ),
        review_suggestions=True,
        review_notes=["usuń duplikację", "skróć nazwę"],
    )

    assert "zaakceptował bieżący diff z sugestiami" in prompt.lower()
    assert "zastosuj" in prompt and "odrzuć" in prompt
    assert "usuń duplikację; skróć nazwę" in prompt
    assert "finalize" in prompt
    assert "bez ponownego review" in prompt
    assert "świadoma eskalacja" in prompt


def test_agent_prompt_bodies_live_in_separate_files() -> None:
    template_dir = (
        Path(prompts.__file__).parent / "templates"
    )

    expected = {
        "bootstrap.md",
        "planner.md",
        "tester.md",
        "coder.md",
        "reviewer.md",
        "master-system.md",
        "verify-goal.md",
    }
    assert expected <= {path.name for path in template_dir.iterdir()}


def test_planner_reads_small_indexes_and_archive_only_on_demand() -> None:
    prompt = prompts.plan_batch_prompt(4, 1)

    assert "docs/ARCHITECTURE/00-INDEX.md" in prompt
    assert "docs/DESIGN/00-INDEX.md" in prompt
    assert "BACKLOG-ARCHIVE.md" in prompt
    assert "tylko do wglądu na żądanie" in prompt


def test_coder_updates_documentation_through_indexes() -> None:
    prompt = prompts.coder_task_prompt(
        "task.md", "pytest", decision={"status": "red", "reason": "missing"})

    assert "docs/ARCHITECTURE/00-INDEX.md" in prompt
    assert "właściwego pliku wskazanego przez indeks" in prompt
    assert "nowy plik" in prompt and "wpisem w indeksie" in prompt
    assert "bramkę testera" in prompt
    assert "pełną suitę przed commitem uruchamia Forge" in prompt
    assert "tautologiczny" in prompt


def test_every_fifth_batch_requires_one_technical_debt_task() -> None:
    normal = prompts.plan_batch_prompt(4, 1, require_debt=False)
    fifth = prompts.plan_batch_prompt(4, 1, require_debt=True)

    assert "jedno zadanie w tym wsadzie ma być zadaniem długu technicznego" \
        not in normal
    assert "jedno zadanie w tym wsadzie ma być zadaniem długu technicznego" \
        in fifth
