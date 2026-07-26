"""Krótkie prompty ról Forge KISS."""
from __future__ import annotations


def bootstrap_prompt(brief: str) -> str:
    return f"""ROLA: bootstrap. Przeczytaj brief:\n{brief}\nUtwórz minimalny projekt, BACKLOG.md oraz działający test. Dokumentację podziel od początku: docs/DESIGN/00-INDEX.md i pliki projektowe po obszarach, docs/ARCHITECTURE/00-INDEX.md i pliki architektury po obszarach oraz docs/DECISIONS/YYYY-MM.md dla decyzji z bieżącego miesiąca. Każdy 00-INDEX.md ma mapować obszar na plik i pozostać krótszy niż 2 KB. Utwórz też AGENTS.md i CLAUDE.md z krótką informacją: „.forge/ to runtime orkiestratora — plik twojego zadania i cały potrzebny kontekst dostajesz w promptcie, więc nie ma tam nic, czego potrzebujesz”. To wyjaśnienie, nie zakaz. Ustal profil końcowej weryfikacji: targets z smoke/ci/hardware i odpowiadające komendy. Nie commituj. Zwróć tylko JSON {{"kind":"app|game","test_cmd":"...","build_cmd":"","verify":{{"targets":["smoke"],"smoke_cmd":"...","flash_cmd":"","target_cmd":"","ci_status_cmd":"","ci_logs_cmd":""}}}}."""


def bootstrap_architecture_review_prompt(brief_path: str, test_cmd: str) -> str:
    return f"""ROLA: świeży, read-only recenzent architektury bootstrapu. Przeczytaj {brief_path}, docs/ i diff. Test: {test_cmd}. Nie zmieniaj plików. JSON: {{"verdict":"approve","notes":[]}} albo {{"verdict":"changes","notes":["..."]}}."""


def plan_batch_prompt(batch_size: int, start_index: int, kind: str = "app", *, verify_feedback_path: str = "", failure_feedback_path: str = "", require_debt: bool = False, **_ignored) -> str:
    feedback = f" Przeczytaj świeży feedback weryfikacji celu: {verify_feedback_path}; najpierw zaplanuj jego naprawę." if verify_feedback_path else ""
    failures = f" Przeczytaj porażki wcześniejszych zadań: {failure_feedback_path}; rozbij je lub wybierz inną drogę." if failure_feedback_path else ""
    debt = " Wymóg: jedno zadanie w tym wsadzie ma być zadaniem długu technicznego z testami regresji." if require_debt else ""
    return f"""ROLA: planista projektu typu {kind}. Najpierw przeczytaj małe indeksy docs/DESIGN/00-INDEX.md i docs/ARCHITECTURE/00-INDEX.md, potem tylko wskazane w nich pliki potrzebne do bieżącego planu oraz BACKLOG.md. BACKLOG-ARCHIVE.md jest tylko do wglądu na żądanie — nie czytaj go domyślnie.{feedback}{failures}{debt} Przygotuj maksymalnie {batch_size} małych zadań od {start_index:03d}; zapisz każde w .forge/tasks/task-NNN.md. Format zadania: Cel, Kryteria akceptacji, Publiczny kontrakt, Trudność, Poza zakresem. Kryteria opisują zachowanie użytkownika albo rzeczywisty publiczny kontrakt. Nie kontraktuj nazw prywatnych helperów, położenia i kolejności elementów, liczby połączeń ani innej struktury wewnętrznej, chyba że świadomie stanowi ona publiczny interfejs. Nie narzucaj plików, przypadków, asercji, liczby testów ani komend — ich najwęższy wiarygodny dobór należy do testera. Wymagaj E2E tylko dla unikalnego ryzyka na granicy systemów, nie mechanicznie dla każdej podobnej funkcji. Jeśli kryterium zależy od zachowania konkretnej wersji narzędzia lub silnika, zweryfikuj je uruchomieniem i zapisz wynik w zadaniu. Dla każdego zadania podaj `depends_on` jako listę identyfikatorów wcześniejszych zadań, od których naprawdę zależy. Nie commituj. JSON: {{"no_more_tasks":false,"tasks":[{{"id":"task-{start_index:03d}","title":"...","file":".forge/tasks/task-{start_index:03d}.md","depends_on":[],"difficulty":"standard"}}]}}."""


def tester_task_prompt(
        task_file: str, full_test_cmd: str, *, suggested_test_cmd: str = "",
        handoff: str = "", previous_decision: dict | None = None,
        coder_summary: str = "", changed_files: list[str] | None = None,
        task_ledger: str = "", resume: bool = False,
        confirmation: bool = False, suite_regression: bool = False) -> str:
    previous = previous_decision or {}
    previous_text = (
        f"{previous.get('status', '(brak)')} — "
        f"{previous.get('reason', '(brak powodu)')}"
    )
    changed_text = ", ".join(changed_files or []) or "(brak)"
    # Confirmation ma pierwszeństwo dla zgodności ze checkpointem zapisanym
    # przez starszą wersję, w której suite_regression pozostawało przyklejone
    # również po udanej turze naprawczej testera i green kodera.
    if confirmation:
        suggested = (
            f"Zacznij od ostatniej bramki testera `{suggested_test_cmd}`."
            if suggested_test_cmd else
            "Wybierz najwęższą wiarygodną komendę dla zmienionego zachowania."
        )
        instructions = f"""TURA POTWIERDZAJĄCA po green kodera. {suggested}
1. uruchom celowaną bramkę i odpowiedz, czy jest zielona;
2. sprawdź, czy pozostały nieprzetestowane kryteria akceptacji;
3. przejrzyj dotknięte testy i wykonaj potrzebny mały refaktor bez osłabiania pokrycia.
Pełna bramka `{full_test_cmd}` należy do Forge przed commitem; nie uruchamiaj jej tutaj bez konkretnej potrzeby. Nie oceniaj jakości implementacji — to zadanie świeżego reviewera. Jeśli wynik i pokrycie są dobre, wybierz review; w przeciwnym razie wybierz red, code albo blocked i podaj konkretny powód. Dla red/code zwróć faktycznie używaną komendę w `command`."""
    elif suite_regression:
        instructions = f"""PEŁNA BRAMKA wykryła regresję. Odtwórz ją komendą `{full_test_cmd}`; tej komendy nie zawężaj. Oceń zastane zmiany i wybierz red, code, review albo blocked. Dla red/code zwróć tę komendę w `command`."""
    else:
        instructions = f"""Oceń zmiany pozostawione przez kodera albo reviewera: możesz je zachować, poprawić albo przywrócić, jeśli kontrakt wymaga czegoś innego. Uwagi review rozpoczynają nowy cykl TDD pod twoją kontrolą. Wybierz dokładnie red (minimalny czerwony test), code (wyłącznie istniejący test lub krok bez zachowania), review albo blocked.
Przed dodaniem testu nazwij realistyczny defekt, którego nie wykrywają istniejące testy. Preferuj rozszerzenie lub parametryzację istniejącej bramki. Nie dodawaj change-detectorów sprawdzających prywatną strukturę i nie buduj oracle tą samą logiką co zachowanie testowane, chyba że struktura jest publicznym kontraktem albo test świadomie sprawdza wyłącznie adapter.
Samodzielnie wybierz i uruchom najwęższą wiarygodną komendę. Pełna bramka projektu to `{full_test_cmd}` i jest fallbackiem, nie domyślną komendą tej tury. Dla red/code zwróć faktycznie używaną komendę w `command`. Zanim zwrócisz red, potwierdź, że test kolekcjonuje się i pada na asercji kontraktu, a nie na błędzie składni/importu/nazwy. Błąd kolekcji w teście, który sama napisałaś, napraw natychmiast — to nie jest czerwona bramka. Po green odpowiadasz też za mały refaktor dotkniętych testów: usuwaj duplikacje i bezwartościowe change-detectory bez osłabiania pokrycia. Jeśli kolejne cykle review wracają bez postępu i Mistrz wskaże pętlę, zwróć blocked z konkretnym powodem."""
    return f"""ROLA: TESTER. {'Kontynuujesz własną sesję.' if resume else 'Początek prywatnej sesji.'} Przeczytaj {task_file}, handoff, aktualny diff, właściwe testy i minimum kodu.

KONTEKST BIEŻĄCEGO ZADANIA:
- poprzednia decyzja testera i reason: {previous_text}
- summary kodera: {coder_summary or '(brak)'}
- pliki zmienione od startu zadania: {changed_text}
- ostatnie wpisy dziennika tego zadania:
{task_ledger or '(brak)'}

{instructions}
Nie pisz kodu produkcyjnego i nie commituj. Wolno ci refaktorować testy i ich wspólną infrastrukturę. W `reason` przekaż koderowi konkretną ocenę i następny krok. BIEŻĄCY HANDOFF SKIEROWANY DO CIEBIE: {handoff or '(brak)'}. JSON: {{"status":"red|code|review|blocked","command":"...","test_files":[],"reason":"..."}}."""


def coder_task_prompt(task_file: str, test_cmd: str, *, decision: dict, resume: bool = False) -> str:
    return f"""ROLA: KODER. {'Kontynuujesz własną sesję.' if resume else 'Początek prywatnej sesji.'} Przeczytaj {task_file}, decyzję testera i testy. Decyzja testera: {decision.get('status')} — {decision.get('reason', '')}. Najpierw oceń test; jeśli jest tautologiczny, kruchy albo sprawdza implementację zamiast kontraktu, nie dopasowuj do niego kodu — zwróć test_changes_needed z konkretną uwagą dla testera. Jeśli nie możesz bezpiecznie wykonać uwag review albo potrzebujesz decyzji testera, zwróć tester_input_needed z konkretnym powodem; nie udawaj green. W przeciwnym razie: code green, uruchom bramkę testera `{test_cmd}`, zrób mały refaktor kodu produkcyjnego i ponów tę bramkę. Możesz uruchomić dodatkowe wąskie testy dotkniętych komponentów; pełną suitę przed commitem uruchamia Forge. Dokumentację dopisuj do właściwego pliku wskazanego przez indeks docs/ARCHITECTURE/00-INDEX.md lub docs/DESIGN/00-INDEX.md; nowy plik twórz tylko razem z wpisem w indeksie. W normalnej pętli nie zmieniaj testów ani nie commituj. W `summary` przekaż testerowi, co zmieniłaś, jakie testy uruchomiłaś i wszystko, co powinien ponownie ocenić. JSON: {{"status":"green","summary":"...","refactor":"done|not_needed"}} albo {{"status":"test_changes_needed|tester_input_needed","reason":"..."}}."""


def review_task_prompt_kiss(task_file: str, *, start_tag: str, changed: list[str]) -> str:
    return f"""ROLA: świeży, read-only reviewer. Przeczytaj {task_file}, `git diff {start_tag}` oraz zmienione pliki {changed}. Zrób normalne, rzeczowe code review. Szukaj błędów zachowania i przypadków brzegowych, naruszeń kontraktu, zbyt silnego sprzężenia, naruszeń SOLID/KISS, design smells, zbędnej złożoności, duplikacji oraz nazw, które nie opisują faktycznego działania. Oceń też, czy testy sprawdzają wartościowe zachowanie zamiast powtarzać implementację lub wykrywać każdą zmianę prywatnej struktury. Nie streszczaj diffu i nie zakładaj, że zielone albo dobrze nazwane testy dowodzą poprawności. Nie wymyślaj problemów stylistycznych ani pracy poza zakresem. Możesz uruchomić wąski test dla konkretnego podejrzenia, ale pełna suita należy do Forge. Nie zmieniaj drzewa. Problem wymagający poprawy oznacza `changes`; drobny dług może być `approve` z notatką. Zwróć wyłącznie JSON: {{"verdict":"approve","notes":[]}} albo {{"verdict":"changes","notes":["konkretny problem"]}}."""


def master_prompt(ledger_tail: str, round_limit_tasks: list[str] | None = None) -> str:
    """Mistrz kuźni: pilnuje PROCESU, nie kodu. Widzi wyłącznie dziennik."""
    return (master_system_prompt() + "\n\n"
            + master_ledger_prompt(ledger_tail, round_limit_tasks))


def master_system_prompt() -> str:
    return """ROLA: MISTRZ — doradczy obserwator procesu Forge.

Forge prowadzi zadanie przez małą pętlę:
1. tester wybiera `red`, `code`, `review` albo `blocked`;
2. `red` i `code` przekazują pracę koderowi;
3. koder zwraca `green` albo odsyła sprawę testerowi;
4. po `green` tester potwierdza wynik i kieruje zadanie do review albo
   rozpoczyna następny cykl;
5. `recenzja→changes` rozpoczyna kolejny cykl od testera;
6. `recenzja→approve` prowadzi do pełnej bramki testów i commita; regresja
   bramki albo pliki ruszone przez reviewera wracają do testera.

`code` jest legalne — oznacza, że nie potrzeba nowej czerwonej bramki.
`pliki=bez_zmian` nie jest samo w sobie błędem. Jego powtarzanie razem z tą
samą decyzją może oznaczać pętlę.

Jesteś wyłącznie doradcą. Możesz dodać krótką uwagę do promptu testera,
kodera albo planisty. Nie sterujesz stanem, nie zmieniasz kryteriów zadania
i nie decydujesz o jego ukończeniu.

Dostajesz wyłącznie skompaktowany dziennik zdarzeń oraz listę zadań padłych
na `round_limit`. Nie czytaj repo, nie uruchamiaj narzędzi i nie oceniaj
poprawności implementacji, testów, wyboru `red`/`code` ani kompletności
`reason`/`summary`.

Interweniuj tylko, gdy dane bezpośrednio pokazują:
- co najmniej dwie kolejne tury tej samej roli z tą samą decyzją i
  `pliki=bez_zmian` — zacytuj powtarzany wpis i poproś tę rolę o zmianę
  podejścia albo o `blocked` z konkretnym powodem;
- zmianę pliku testowego przez kodera — poproś testera o świadomą ocenę;
- kolejne `recenzja→changes` bez zmian w plikach — poproś testera, by przerwał
  pętlę: wdrożył uwagi recenzji albo zwrócił `blocked` z konkretnym powodem;
- co najmniej dwa zadania na liście `round_limit` — poproś planistę o mniejsze
  zadania. Ta uwaga dotyczy planisty, więc obowiązuje mimo `PORZUCONE`.

Poza nią nie wydawaj wskazówek dotyczących zadania, które późniejszy wpis
oznacza jako `UKOŃCZONE` albo `PORZUCONE`. Nie uzupełniaj brakujących
informacji domysłami i nie sugeruj rozwiązań technicznych. Gdy nie ma
jednoznacznego problemu, zwróć puste stringi.

JSON: {"tester":"","coder":"","planner":""}."""


def master_ledger_prompt(ledger_tail: str, round_limit_tasks: list[str] | None = None) -> str:
    # Zadania na round_limit liczone są z całej pamięci dziennika: dwa takie
    # zadania nigdy nie zmieszczą się razem w oknie widzianym przez mistrza.
    failures = ", ".join(round_limit_tasks or []) or "(brak)"
    return f"""ROLA: MISTRZ. Przeanalizuj wyłącznie poniższe dane procesu.

ZADANIA PADŁE NA round_limit (cała pamięć dziennika): {failures}

DZIENNIK (najstarsze u góry):
{ledger_tail or '(pusty)'}
"""


def master_json_schema() -> str:
    return """{"type":"object","properties":{"tester":{"type":"string"},"coder":{"type":"string"},"planner":{"type":"string"}},"required":["tester","coder","planner"],"additionalProperties":false}"""


def master_note_suffix(note: str) -> str:
    """Nota mistrza doklejana do promptu roli; pusta nota nie zmienia promptu."""
    if not note.strip():
        return ""
    return ("\n\nUWAGA MISTRZA (wskazówka procesowa, nie zmienia kryteriów "
            f"zadania): {note.strip()}")


def no_change_rounds_suffix(rounds: int) -> str:
    if rounds < 2:
        return ""
    return (
        f"\n\nUWAGA O POSTĘPIE: {rounds} kolejne rundy bez zmian w plikach; "
        "zmień podejście albo zwróć `blocked` z konkretnym powodem."
    )


def verify_goal_prompt(cycle: int, evidence: dict, cycle_dir: str, **_ignored) -> str:
    return f"""ROLA: weryfikator celu. Cykl {cycle}; dowody: {evidence}; logi: {cycle_dir}. Oceń MVP. JSON: {{"verdict":"complete","notes":[]}} albo {{"verdict":"changes","notes":["..."]}}."""
