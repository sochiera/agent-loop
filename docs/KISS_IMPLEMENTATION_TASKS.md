# Forge KISS — taski implementacyjne

## 1. Decyzja implementacyjna

Silnik wykonywania pojedynczego zadania należy napisać od nowa. Nie warto
upraszczać `_run_micro_loop()` kolejnymi warunkami: obecna implementacja łączy
w jednej pętli decyzje agentów, bramki czerwieni, mapy `DONE`, liczniki smell,
snapshoty, chmod, anty-osłabianie, retry kodera, review i odzyskiwanie sesji.
Usuwanie tych elementów pojedynczo pozostawiłoby trudny do zweryfikowania
automat z nazwami i stanami starego modelu.

Należy zachować stabilną infrastrukturę:

- adaptery i uruchamianie agentów z pomiarem tokenów;
- routing modeli według roli i trudności;
- bootstrap wraz ze świeżą recenzją architektury;
- planowanie wsadowe i pliki zadań;
- bezpieczne uruchamianie komend bez shella;
- operacje git, checkpointy, artefakt porażki i push;
- weryfikację celu po wyczerpaniu backlogu;
- obsługę briefu i kompaktowanie dokumentacji.

Należy wymienić cały środek:

```text
START TASK
  → TESTER ↔ KODER
  → TESTY PRZED REVIEW
  → ŚWIEŻY REVIEWER
  → [POPRAWKI KODERA → FINALNE TESTY]
  → COMMIT
```

Nowy kod może tymczasowo powstawać w osobnym module, ale przed zakończeniem
migracji stary pipeline musi zostać fizycznie usunięty. Nie powstaje feature
flag, tryb zgodności ani możliwość powrotu do `legacy`/`micro`.

## 2. Docelowy podział kodu

Po refaktorze odpowiedzialności powinny być czytelne:

| Plik | Odpowiedzialność |
|---|---|
| `forge/orchestrate.py` | CLI, bootstrap, planowanie, weryfikacja celu i główna pętla |
| `forge/task_pipeline.py` | wyłącznie cykl życia jednego zadania |
| `forge/prompts.py` | krótkie prompty ról i planowania |
| `forge/state.py` | minimalny, wznawialny stan całego Forge |
| `forge/agents.py` | wywołania agentów, resume, użycie tokenów |
| `forge/config.py` | routing modeli i niewielka liczba potrzebnych limitów |

Nie należy tworzyć osobnych klas dla każdej fazy, frameworka workflow ani
generycznego silnika grafów. Jedna jawna funkcja sterująca i kilka małych
funkcji faz wystarczą.

Docelowe decyzje agentów:

```json
{"status": "red", "command": "...", "test_files": ["..."], "reason": "..."}
{"status": "code", "command": "...", "reason": "..."}
{"status": "review", "reason": "..."}
{"status": "blocked", "reason": "..."}
```

```json
{"status": "green", "summary": "...", "refactor": "done"}
{"status": "test_changes_needed", "reason": "..."}
```

```json
{"verdict": "approve", "notes": []}
{"verdict": "changes", "notes": ["..."]}
```

Forge waliduje wyłącznie poprawność JSON-a i dozwoloną nazwę decyzji.
Semantykę czerwieni, jakość testu, kompletność implementacji i sens refaktoru
oceniają agenci.

## 3. Kolejność prac

Taski są uporządkowane. Każdy kończy się własnym commitem i zieloną suitą
Forge. W każdym tasku praca przebiega lokalnie jako:

```text
TEST RED → CODE GREEN → REFACTOR
```

Nie wolno rozpoczynać taska KISS-09, dopóki nowy entrypoint nie przechodzi
testów end-to-end. Nie wolno zakończyć migracji przed KISS-10, ponieważ dopiero
ten task usuwa pozostałości starego projektu.

---

## KISS-01 — kontrakty decyzji i minimalny stan zadania

### Cel

Utworzyć mały, niezależny rdzeń danych nowego pipeline'u bez podłączania go
jeszcze do głównej pętli.

### TEST RED

Utworzyć `tests/test_task_pipeline.py` i najpierw pokryć:

1. tester może zwrócić tylko `red`, `code`, `review` albo `blocked`;
2. koder może zwrócić tylko `green` albo `test_changes_needed`;
3. reviewer może zwrócić tylko `approve` albo `changes`;
4. niepoprawny JSON daje błąd techniczny, a nie domyślną decyzję;
5. parser nie ocenia semantyki `reason`, nie sprawdza kodu wyjścia czerwonego
   testu i nie wymaga mapy kryteriów;
6. dozwolone fazy zadania to tylko:
   `tester`, `coder`, `review`, `corrections`, `commit`;
7. stan potrafi zapisać i odtworzyć bieżącą fazę, numer rundy, obie sesje,
   ostatnią decyzję testera, uwagi review i tag startowy.

### CODE GREEN

Utworzyć `forge/task_pipeline.py` z:

- małymi parserami decyzji;
- jednym wyjątkiem `InvalidDecision`;
- stałymi lub prostym enumem faz;
- pomocniczym typem wyniku fazy;
- bez zależności od starego `_run_micro_loop()`.

W `forge/state.py` dodać tylko pola konieczne nowemu silnikowi:

- `task_phase`;
- `tdd_round`;
- `tester_session`;
- `coder_session`;
- `tester_decision`;
- `review_notes`;
- `task_start_tag`.

Na tym etapie stare pola mogą jeszcze istnieć, ponieważ działający entrypoint
nie został przełączony.

### REFACTOR

Parsery mają wspólny mały helper do wydobycia JSON-a, ale nie wspólną
„konfigurowalną maszynę schematów”.

### Kryteria akceptacji

- Testy kontraktu są zielone.
- Nie ma map `criterion → test`, `justified`, smell counterów ani retry policy
  w nowym module.
- Nowy moduł nie importuje `_run_micro_loop`, `_run_review_loop` ani ich
  helperów.

---

## KISS-02 — krótkie prompty i izolowane konteksty ról

### Cel

Zapewnić ciągłość testera i kodera przez całe zadanie bez przecieku ich
rozmów między rolami.

### TEST RED

Dodać `tests/test_role_context.py`:

1. pierwsze wywołanie testera i kodera rozpoczyna oddzielne sesje;
2. kolejne wywołanie testera używa poprzedniego `tester_session`;
3. kolejne wywołanie kodera i poprawki review używają poprzedniego
   `coder_session`;
4. prompt kodera nie zawiera transkryptu ani dziennika testera;
5. prompt testera nie zawiera transkryptu ani dziennika kodera;
6. jawny, krótki handoff jest dozwolony:
   tester przekazuje koderowi `reason`, a koder testerowi powód
   `test_changes_needed`;
7. reviewer zawsze otrzymuje `session_id=None` i jego zwrócona sesja jest
   odrzucana;
8. sesje nie są rotowane w połowie zadania;
9. dla agenta bez `resume` odtwarzany jest wyłącznie ograniczony zapis jego
   własnej roli, nigdy wspólny dziennik zadania.

### CODE GREEN

- Zastąpić wspólny task journal dwoma prywatnymi, ograniczonymi zapisami:
  `tester` i `coder`.
- Wznowić sesję, jeśli adapter to obsługuje.
- Jeśli adapter nie obsługuje `resume`, przekazać mu krótki zapis tylko jego
  wcześniejszych działań.
- Utrata sesji może rozpocząć nową sesję tej samej roli z jej prywatnym
  zapisem.
- Utworzyć cztery krótkie prompty:
  `tester_task_prompt`, `coder_task_prompt`, `review_task_prompt`,
  `corrections_prompt`.

Prompt testera opisuje wybór `red|code|review|blocked`. Prompt kodera nakazuje
ocenić test, następnie wykonać `code green → refactor`, albo zwrócić
`test_changes_needed`. Prompt reviewera wymaga świeżej oceny całego diffu.
Prompt poprawek pozwala koderowi zmienić testy i kod zgodnie z uwagami.

### REFACTOR

Stałe zasady każdej roli występują raz w jej prompcie. Przy `resume` wysyłany
jest tylko nowy handoff i aktualne fakty, bez ponownego wklejania całego
manifestu.

### Kryteria akceptacji

- Tester pamięta test 1, gdy wraca napisać test 2.
- Koder pamięta poprzednią implementację, gdy wraca do kolejnej rundy lub
  poprawek review.
- Żaden test nie znajduje wspólnego dziennika przekazywanego obu autorom.
- `session_rotate_cycles` nie jest używane przez nowy moduł.

---

## KISS-03 — pętla TESTER ↔ KODER bez mechanicznych bramek czerwieni

### Cel

Zaimplementować najmniejszą pętlę TDD sterowaną wyłącznie decyzją testera.

### TEST RED

W `tests/test_task_pipeline.py` dodać scenariusze:

1. `red → green → tester`;
2. `red → test_changes_needed → ten sam tester`;
3. po poprawieniu testu wraca ten sam koder;
4. `code → green → tester`;
5. tylko `review` kończy pętlę;
6. `blocked` zatrzymuje zadanie z czytelnym powodem;
7. po `green` nie uruchamia się automatycznie review;
8. po `green` Forge nie wymusza pełnej suity;
9. Forge nie sprawdza mechanicznie, czy test testera zwrócił niezerowy kod;
10. Forge nie wycofuje testu, który przeszedł od razu;
11. Forge nie liczy `no_test`, `gate_not_red` ani odrzuceń `DONE`;
12. mały limit, domyślnie 4 rundy, kończy zadanie jako wymagające podziału.

### CODE GREEN

Zaimplementować w `forge/task_pipeline.py` jawny przebieg:

```python
while True:
    decision = run_tester()
    if decision.status == "review":
        break
    if decision.status == "blocked":
        return blocked(decision.reason)

    result = run_coder(decision)
    if result.status == "test_changes_needed":
        handoff_to_tester(result.reason)
        continue

    tdd_round += 1
```

Nie dodawać automatycznych retry kodera. Następną decyzję zawsze podejmuje
tester.

### Jedyna ochrona testów w normalnej pętli

Przed turą kodera zapisać deterministyczny hash plików pasujących do
`test_globs` zadania. Po turze porównać hash:

- brak zmiany — normalny powrót do testera;
- zmiana — zadanie przechodzi do `blocked`, a artefakt zachowuje diff.

Forge nie próbuje automatycznie odgadywać, co cofnąć, nie robi chmod i nie
tworzy worktree anty-osłabiania. Koder powinien użyć
`test_changes_needed`, zanim zmieni test.

### REFACTOR

Funkcja sterująca nie zna nazw agentów ani modeli. Dostaje małe zależności:
wywołanie roli, zapis checkpointu i obliczenie hasha.

### Kryteria akceptacji

- Kolejność faz odpowiada planowi KISS.
- Nie ma bramki RED w orkiestratorze.
- Nie ma bramki pełnej suity po każdej turze kodera.
- Tester jest jedyną rolą, która może skierować zadanie do review.

---

## KISS-04 — pojedyncza zielona granica przed REVIEW

### Cel

Uruchamiać test ukierunkowany i pełną suitę dokładnie wtedy, gdy tester
wybiera `review`.

### TEST RED

Pokryć:

1. decyzja `review` uruchamia najpierw test ukierunkowany, potem pełną suitę;
2. reviewer nie startuje, jeśli którakolwiek komenda jest czerwona;
3. przy czerwieni sterowanie wraca do tej samej sesji testera z krótkim
   wynikiem komendy;
4. tester może następnie wybrać `red`, `code` albo `blocked`;
5. przy dwóch zielonych wynikach reviewer dostaje ich krótkie podsumowanie;
6. brak osobnej komendy ukierunkowanej oznacza jedno uruchomienie pełnej suity,
   nie jej dwukrotne wykonanie;
7. komendy nadal są uruchamiane bez shella i z timeoutem.

### CODE GREEN

- Rozszerzyć format taska o `targeted_test_cmd`.
- Dla starszego zadania bez tego pola użyć pustej wartości.
- Po `status=review` wykonać granicę testową na niezmienionym drzewie.
- Zapisać wynik tylko jako krótki kod, nazwę komendy i ograniczony ogon logu.
- Nie zapisywać „zieleni” jako wiecznie ważnego booleana. Po restarcie lub
  zmianie drzewa granica musi zostać wykonana ponownie.

### REFACTOR

Użyć istniejącego bezpiecznego runnera komend. Nie budować drugiego systemu
uruchamiania procesów.

### Kryteria akceptacji

- Zdanie „test ukierunkowany i pełna suita są zielone przed wysłaniem do
  review” jest prawdziwe dla każdego wejścia do review.
- Te komendy nie są mechaniczną bramką każdej rundy CODE.

---

## KISS-05 — świeży REVIEW, poprawki tego samego KODERA i COMMIT

### Cel

Dokończyć ścieżkę sukcesu bez pętli wielu recenzji.

### TEST RED

Pokryć:

1. reviewer zawsze zaczyna w świeżym kontekście;
2. reviewer dostaje task, finalny diff od taga startowego, zmienione pliki
   oraz wyniki obu testów;
3. reviewer nie dostaje sesji, promptów ani prywatnych zapisów autorów;
4. `approve` przy niezmienionym przez review drzewie prowadzi bezpośrednio do
   commita, bez ponownego uruchamiania tej samej suity;
5. `changes` przekazuje konkretne uwagi do zachowanej sesji kodera;
6. w fazie poprawek koder może zmienić testy i kod;
7. poprawka zachowania ma w prompcie kolejność
   `test red → code green → refactor`;
8. po poprawkach test ukierunkowany i pełna suita są zielone przed commitem;
9. po poprawkach nie wracamy do testera i domyślnie nie uruchamiamy drugiej
   pełnej recenzji;
10. czerwone testy po poprawkach nie pozwalają na commit;
11. commit obejmuje dokładnie drzewo sprawdzone przed review albo, po
    poprawkach, przez finalne testy;
12. `tester_session` i `coder_session` są czyszczone dopiero po udanym
   commicie.

### CODE GREEN

Zaimplementować trzy małe fazy:

- `run_review()` — jedno świeże wywołanie, bez prawa zapisu;
- `run_corrections()` — jedna tura zachowanego kodera;
- `finish_task()` — jeśli były poprawki: finalne testy; następnie commit, push,
  usunięcie taga i dopiero potem wyczyszczenie kontekstów.

Jeżeli reviewer zmieni drzewo, zadanie ma się zatrzymać: reviewer jest rolą
read-only.

Przy `approve` Forge porównuje fingerprint drzewa z fingerprintem zmierzonym
przed review. Ponieważ reviewer jest read-only, zgodność pozwala wykorzystać
wynik testów sprzed review i nie uruchamiać pełnej suity drugi raz.

Przy `changes` do kodera trafiają wyłącznie:

- task;
- lista uwag;
- aktualny diff;
- komendy testów;
- jego własna zachowana sesja.

### REFACTOR

Usunąć pętlę `while` z fazy review. Jedna recenzja i najwyżej jedna faza
poprawek są zwykłym przepływem liniowym.

### Kryteria akceptacji

- Przepływ sukcesu ma dokładnie postać
  `REVIEW → [POPRAWKI KODERA] → TESTY → COMMIT`.
- Nie istnieje licznik rund review/fix dla nowego silnika.
- Context cleanup nie następuje pomiędzy rundami TDD ani przed poprawkami.

---

## KISS-06 — checkpointy, restart i porażka zadania

### Cel

Zachować odporność operacyjną bez rozbudowy automatu.

### TEST RED

Dodać test restartu osobno dla faz:

1. `tester`;
2. `coder`;
3. `review`;
4. `corrections`;
5. `commit`.

Ponadto pokryć:

6. restart nie tworzy drugiego taga startowego;
7. restart wznawia właściwą sesję roli;
8. po restarcie przed review testy są mierzone ponownie;
9. `blocked`, przekroczenie limitu rund, błąd agenta i czerwone finalne testy
   zachowują artefakt `forge/failed/<task-id>`;
10. rollback dotyczy wyłącznie bieżącego zadania;
11. pozostały wsad jest porzucany i planowany ponownie;
12. konteksty porzuconego zadania są usuwane dopiero po zachowaniu artefaktu
   i rollbacku;
13. nieznana lub stara aktywna faza w `STATE.json` nie jest zgadywana — start
   kończy się czytelnym komunikatem migracyjnym.

### CODE GREEN

- Zapisywać checkpoint po każdej decyzji zmieniającej fazę.
- Stan ma wskazywać następną czynność, aby restart jej nie powtarzał.
- Zachować obecny tag startowy, branch porażki, failure note i replan.
- Dla starego `STATE.json`:
  - stan bez aktywnego zadania może zachować konfigurację bootstrapu, kolejkę,
    licznik iteracji i pola weryfikacji;
  - aktywne stare `micro`, `fix_review` albo `legacy` nie jest automatycznie
    konwertowane, bo brak danych do wiarygodnego odtworzenia handoffu;
  - operator dostaje instrukcję dokończenia albo porzucenia starego zadania
    przed aktualizacją.

### REFACTOR

Jedna funkcja `checkpoint(next_phase)` ma zastąpić rozproszone ręczne ustawianie
kilku pól.

### Kryteria akceptacji

- Każda faza jest wznawialna.
- Nie ma snapshotów mikrocyklu ani journalowego odtwarzania wspólnego
  kontekstu.
- Porażka nie pozostawia zmienionego drzewa na głównym branchu.

---

## KISS-07 — podłączenie planisty, trudności i końcowej weryfikacji

### Cel

Podłączyć nowy silnik do zachowanej zewnętrznej pętli Forge.

### TEST RED

Pokryć end-to-end z atrapami agentów:

1. bootstrap → plan wsadowy → task KISS → commit/push;
2. planista nadal używa mocnego profilu;
3. tester, koder i reviewer dostają modele według trudności taska;
4. następny task z kolejki startuje po commicie poprzedniego;
5. porażka kasuje założenia pozostałego wsadu i wraca do planowania;
6. `no_more_tasks` nadal uruchamia weryfikację celu;
7. feedback weryfikacji nadal trafia do następnego planowania;
8. zadanie z `repro_cmd` używa repro jako części granicy przed review i
   finalnej granicy przed commitem, ale nie jako bramki RED testera;
9. recenzja architektury bootstrapu pozostaje świeża i obowiązkowa zgodnie z
   konfiguracją.

### CODE GREEN

- Przełączyć `one_iteration()` na nowy `run_task()`.
- Zachować `phase_plan_batch()` i `phase_verify_goal()`.
- Uaktualnić format taska planisty o `targeted_test_cmd`.
- Nie dodawać trybu wyboru starego pipeline'u.

### REFACTOR

`orchestrate.py` nie powinien znać decyzji `red`, `code` ani
`test_changes_needed`; te należą do `task_pipeline.py`.

### Kryteria akceptacji

- Główny entrypoint używa wyłącznie nowego silnika.
- Bootstrap, routing trudności i weryfikacja celu nie tracą pokrycia.
- Typowy task kończy się po czterech wywołaniach:
  tester → koder → tester → reviewer.

---

## KISS-08 — uproszczenie `State` i `Config`

### Cel

Po przełączeniu entrypointu usunąć wszystkie pola i przełączniki, których nowy
pipeline nie używa.

### TEST RED

Najpierw zmienić testy konfiguracji i roundtrip stanu tak, aby oczekiwały
wyłącznie nowego modelu.

Pokryć:

- domyślny `max_tdd_rounds=4`;
- brak `legacy_mode`;
- brak automatycznej rotacji sesji;
- dokładnie jedna faza poprawek review;
- roundtrip minimalnego stanu;
- stare, nieaktywne dodatkowe klucze JSON są ignorowane;
- aktywna nieznana faza daje błąd migracji.

### CODE GREEN

Usunąć z `Config`:

- `legacy_mode`;
- `max_micro_cycles`;
- `max_green_retries`;
- `session_rotate_cycles`;
- `lock_tests`;
- `toolchain_globs_extra`, jeśli nie wykorzystuje go końcowa weryfikacja;
- `max_done_rejects`;
- `done_reject_policy`;
- `fail_on_empty_criteria`;
- `max_fix_attempts`.

Dodać tylko `max_tdd_rounds`, domyślnie 4.

Usunąć ze `State` pola starego silnika:

- `micro_cycle`, `micro_sub`, `cycle_test_files`, `pending_no_test`;
- `no_test_count`, `gate_not_red_count`, `last_gate_not_red_attempt`;
- `done_reject_reasons`, `justified_criteria`, `done_reject_count`;
- `escalation_notes`, `escalation_map_errors`, `done_escalated`;
- `gate_not_red_escalated`;
- `tests_green` jako trwały stan;
- stare `phase`/`fix_attempt`, jeśli zostały zastąpione przez
  `task_phase`/`tdd_round`.

`test_toolchain_globs` usunąć ze stanu taska. Jeżeli pozostaje potrzebne
weryfikacji celu, przenieść je do jednoznacznie nazwanej konfiguracji
weryfikatora, a nie do mechaniki TDD.

### REFACTOR

Zgrupować pola w kolejności: bootstrap, kolejka, bieżący task, sesje/faza,
weryfikacja celu. Usunąć komentarze opisujące historyczne `PLAN-3/4/5`.

### Kryteria akceptacji

- `Config` nie oferuje gałek do nieistniejących mechanizmów.
- `State` nie przechowuje liczników polityk semantycznych.
- CLI i GUI nie odwołują się do usuniętych pól.

---

## KISS-09 — fizyczne usunięcie starego silnika

### Cel

Usunąć stary kod, a nie tylko przestać go wywoływać.

### TEST RED

Dodać test architektoniczny, który przegląda źródła `forge/` i nie pozwala
ponownie wprowadzić nazw usuniętego pipeline'u.

Test ma zabronić co najmniej:

```text
_legacy_iteration
_run_micro_loop
_apply_done_reject_policy
red_gate_ok
tester_path_violations
coder_test_violations
weakening_candidates
snapshot_cycle_tests
restore_test_changes
anti_weakening_ok
gate_not_red
done_reject
max_green_retries
legacy_mode
```

Wyjątkiem są dokumenty migracyjne w `docs/`, które mogą te nazwy opisywać.

### CODE GREEN

Usunąć z `forge/orchestrate.py`:

- `phase_plan`, `phase_implement`, `phase_review`, `phase_fix`;
- `_legacy_iteration`;
- stary `_run_micro_loop`;
- `_apply_done_reject_policy`;
- stary `_run_review_loop`;
- wszystkie helpery czerwonej bramki, map `DONE`, smell gates;
- chmod testów;
- snapshoty testów cyklu;
- worktree anty-osłabiania;
- selektywne auto-reverty zmian agentów;
- commit po każdym mikrocyklu;
- rotację sesji.

Usunąć z `forge/prompts.py`:

- `implement_prompt`, stary `review_prompt`, `fix_prompt`;
- `micro_principles`;
- `write_test_prompt`;
- `code_and_refactor_prompt`;
- stary `review_task_prompt`;
- `fix_review_prompt`;
- wszystkie teksty o mapie `DONE`, smellach, anty-osłabianiu, chmod,
  jednym teście na cykl i mechanicznych czerwonych bramkach.

Usunąć `--legacy`, `--max-micro-cycles` oraz odpowiadające zmienne środowiskowe.

Usunąć katalog runtime `.forge/cycle_tests` z obsługi i dokumentacji.

### REFACTOR

Po usunięciu kodu:

- poprawić importy;
- usunąć nieużywane `shutil`, `tempfile` i regexy, o ile nie są potrzebne
  innym zachowanym funkcjom;
- przenieść małe współdzielone helpery do właściwego modułu zamiast zostawiać
  ich kopie;
- uruchomić narzędzie wykrywające nieużywane importy lub wykonać równoważny
  ręczny przegląd.

### Kryteria akceptacji

- Nie istnieje żaden alternatywny pipeline wykonania taska.
- Nie da się uruchomić starego trybu flagą ani zmienną środowiskową.
- Test zakazanych symboli jest zielony.
- `rg` nie znajduje implementacji usuniętych mechanizmów.

---

## KISS-10 — wymiana starych testów i odchudzenie repozytorium

### Cel

Nie utrzymywać testów kodu, który już nie istnieje, ani atrap dawnej
architektury.

### TEST RED

Przed kasowaniem sporządzić krótką macierz zachowywanego pokrycia:

| Obszar | Docelowy plik testu |
|---|---|
| nowy task pipeline | `tests/test_task_pipeline.py` |
| sesje i izolacja | `tests/test_role_context.py` |
| restart i artefakty | `tests/test_task_lifecycle.py` |
| bootstrap i główna pętla | `tests/test_orchestrate.py` |
| routing/adaptery | `tests/test_adapters.py`, `tests/test_agents.py` |
| planowanie | istniejące testy briefu i kompaktowania |
| weryfikacja celu | `tests/test_goal_verification.py` |

Przenieść wartościowe przypadki do docelowych plików, najpierw widząc ich
porażkę na brakującym zachowaniu nowego silnika.

### CODE GREEN

Usunąć:

- `tests/test_plan4.py`;
- `tests/test_plan5.py`;
- testy smell gates, map `DONE`, rotacji sesji, chmod, snapshotów,
  anty-osłabiania i legacy z `tests/test_new_model.py`;
- testy legacy z `tests/test_orchestrate.py`;
- stare testy nazw faz raportu.

Jeżeli po przeniesieniu `tests/test_new_model.py` nie zawiera już spójnego
obszaru, usunąć cały plik. Nie zostawiać go jako zbioru przypadkowych testów.

Nie przepisywać testów 1:1. Zachować pokrycie zachowań produktu, nie
wewnętrznych helperów starego automatu.

### REFACTOR

- Preferować testy przejść faz i rezultatów zamiast patchowania kilkunastu
  prywatnych funkcji naraz.
- Wspólny fixture repozytorium i atrap agentów zdefiniować raz.
- Nie asertować pełnych promptów; sprawdzać granice kontekstu i wymagane
  kontrakty.

### Kryteria akceptacji

- Nie istnieje test, którego nazwa lub fixture opisuje stary mikro-pipeline.
- Testy nowego przebiegu obejmują wszystkie przypadki z sekcji 11
  `KISS_PIPELINE_PLAN.md`.
- Pełna suita Forge jest zielona i wyraźnie mniejsza.

---

## KISS-11 — CLI, GUI, raporty i dokumentacja tylko dla KISS

### Cel

Usunąć publiczne ślady starego modelu i uczynić nowy przepływ jedynym
udokumentowanym sposobem pracy.

### TEST RED

Pokryć:

1. parser CLI odrzuca `--legacy` i `--max-micro-cycles`;
2. komunikat startowy opisuje KISS TDD i `max_tdd_rounds`;
3. GUI przekazuje wyłącznie aktualne ustawienia ról;
4. raport grupuje fazy jako `tester`, `coder`, `review`, `corrections`;
5. README nie reklamuje usuniętych zmiennych środowiskowych.

### CODE GREEN

Zaktualizować:

- `README.md`;
- `docs/PIPELINE.md`;
- `forge/gui.py`;
- `forge/report.py`;
- pomoc CLI w `forge/orchestrate.py`.

`docs/PIPELINE.md` ma opisywać stan wdrożony, a
`docs/KISS_PIPELINE_PLAN.md` pozostaje dokumentem decyzji projektowej.

Usunąć z README:

- tryb legacy;
- mikrocykle i dogrywki zieleni;
- rotację sesji;
- blokowanie testów;
- toolchain anti-weakening;
- mapy `DONE` i ich polityki.

Dodać:

- cztery decyzje testera;
- wznowienie sesji w obrębie taska;
- jedną granicę testową przed review;
- poprawki wykonywane przez tego samego kodera;
- czyszczenie kontekstów po commicie.

### REFACTOR

Nie kopiować całego planu do README. README ma zawierać krótki opis
użytkowy, a szczegóły architektury są w `docs/PIPELINE.md`.

### Kryteria akceptacji

- Dokumentacja, CLI i kod opisują jeden pipeline.
- Raport nie używa nazw `micro-test`, `micro-code`, `implement (legacy)`.
- Wszystkie przykłady uruchomienia działają.

---

## KISS-12 — test akceptacyjny, pomiar i końcowy audyt usunięcia

### Cel

Potwierdzić na rzeczywistych przebiegach, że uproszczenie zmniejszyło liczbę
wywołań i nie zostawiło martwego kodu.

### Scenariusze

Uruchomić co najmniej:

1. małe zadanie:
   `red → green → review → approve → commit`;
2. zadanie z dwoma testami:
   `red → green → red → green → review`;
3. wadliwy test:
   `red → test_changes_needed → red → green`;
4. krok bez nowego testu:
   `code → green → review`;
5. review z poprawkami testu i kodu:
   `review → changes → corrections → commit`;
6. czerwoną suitę przy próbie wejścia do review;
7. restart w środku tury testera, kodera i poprawek;
8. porażkę z zachowaniem brancha `forge/failed/*`;
9. wyczerpany backlog i weryfikację celu.

### Pomiary

Dla każdego scenariusza zapisać:

- liczbę wywołań każdej roli;
- tokeny wejściowe i wyjściowe;
- czas całego taska;
- liczbę uruchomień pełnej suity;
- werdykt review;
- przyczynę ewentualnej porażki.

Porównać z ostatnimi przebiegami starego Forge. Oczekiwany zysk wynika z:

- braku mechanicznych retry czerwieni;
- braku pełnej bramki po każdej turze kodera;
- braku map `DONE`;
- braku rotacji i odbudowy kontekstu w połowie zadania;
- jednej recenzji;
- krótkich, prywatnych handoffów.

### Audyt końcowy

Przed uznaniem refaktoru za zakończony:

```bash
python3 -m pytest -q
rg -n "legacy_mode|_legacy_iteration|_run_micro_loop|gate_not_red|done_reject|anti_weakening|snapshot_cycle_tests|max_green_retries" forge tests README.md docs/PIPELINE.md
git status --short
```

Pierwsza komenda ma być zielona. Druga nie może znaleźć aktywnego kodu,
testów ani dokumentacji użytkowej starego pipeline'u. Dopuszczalne są tylko
historyczne wzmianki w dokumentach planu.

Następnie ręcznie przejrzeć:

- `forge/orchestrate.py` pod kątem nieosiągalnych gałęzi;
- `forge/config.py` pod kątem martwych opcji;
- `forge/state.py` pod kątem pól, które są tylko zapisywane;
- `forge/prompts.py` pod kątem sprzecznych zasad;
- `tests/` pod kątem testów prywatnych helperów bez wartości produktowej.

### Kryteria akceptacji

- Wszystkie scenariusze działają.
- Nie istnieje możliwość uruchomienia starego pipeline'u.
- Nie ma martwych pól stanu, flag ani promptów.
- Konteksty testera i kodera żyją przez cały task i są czyszczone po commicie.
- Reviewer zawsze ma świeży kontekst.
- Pełna suita jest uruchamiana na granicy przed review oraz po poprawkach
  przed commitem, nie po każdym CODE+REFACTOR.

## 4. Definicja ukończenia całej migracji

Migracja jest ukończona dopiero, gdy jednocześnie:

1. główny entrypoint korzysta tylko z nowego `task_pipeline.py`;
2. stary silnik i jego testy zostały usunięte;
3. `State` oraz `Config` nie zawierają pól starej polityki;
4. dokumentacja opisuje jeden przepływ;
5. pełna suita Forge jest zielona;
6. co najmniej jeden rzeczywisty task przeszedł przez nowy pipeline;
7. audyt `rg` nie wykazuje aktywnych pozostałości;
8. liczba wywołań i tokenów z pilota została porównana z przebiegami
   wyjściowymi.

Samo przełączenie entrypointu bez usunięcia starego kodu nie spełnia tej
definicji.
