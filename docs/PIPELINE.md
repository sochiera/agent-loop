# Pipeline Forge: role, kolejność i routing modeli

Opis dotyczy domyślnego trybu mikro-TDD.
Źródłem prawdy dla routingu jest `forge/config.py`, a dla checkpointów
`STATE.json` w repozytorium projektu.

## 1. Pipeline w skrócie

```text
brief
  -> BOOTSTRAP (raz)
  -> PLAN WSADOWY (do 5 zadań)
  -> dla każdego zadania:
       START + checkpoint
       TESTER -> czerwona bramka
       KODER -> implementacja, zieleń, refaktor, commit
       ... aż TESTER zgłosi DONE
       świeża RECENZJA -> poprawki KODERA -> commit/push
  -> no_more_tasks -> WERYFIKACJA CELU
       PASS -> koniec
       FAIL -> feedback -> nowe zadania naprawcze
```

Agenci zapisują wiedzę w repozytorium, nie w przekazywanym transkrypcie.
Orkiestrator zapisuje checkpoint przed fazami i po nich, więc limit CLI lub
Ctrl-C pozwala wznowić pracę od `STATE.json`.

## 2. Fazy i odpowiedzialności

### 2.1. Bootstrap — architekt, raz

Domyślnie wykonuje go planista (`claude`). Czyta brief i:

- rozpoznaje produkt jako `game` albo `app`;
- wybiera stack i tworzy `docs/DESIGN.md`, `docs/ARCHITECTURE.md`,
  `docs/DECISIONS.md` oraz `BACKLOG.md`;
- tworzy minimalny szkielet i jeden przechodzący test;
- ustala pojedyncze komendy `test`, `build` i `run`;
- deklaruje profil weryfikacji (`smoke`, `ci`, `hardware`) oraz chronione
  globy testów i toolchainu.

Orkiestrator uruchamia build/test szkieletu, a potem świeży recenzent
architektury ocenia brief, dokumentację, scaffold i diff. Bootstrap jest z
definicji zmianą `complex`: tylko werdykt `approve` pozwala zapisać komendy i
profil w `STATE.json` oraz wykonać commit bazowy. Recenzent jest read-only;
wykryta zmiana pliku produktu kończy bootstrap błędem. Przy `changes` architekt
poprawia kandydat, zwraca pełny, ponownie walidowany kontrakt bootstrapu, testy
są uruchamiane ponownie, a recenzja powtarza się do limitu
`FORGE_MAX_BOOTSTRAP_ARCH_REVIEWS` (domyślnie 2). Czerwień lub brak aprobaty
kończy fazę bez commita.

### 2.2. Planowanie wsadowe

Planista czyta brief, dokumentację, backlog, ostatnie commity i
`.forge/failures.md`. Przygotowuje maksymalnie `batch_size` małych zadań
(domyślnie 5), a dla każdego tworzy `.forge/tasks/task-NNN.md`.

Zadanie zawiera cel, mierzalne kryteria akceptacji, ścieżki testów i kodu,
trudność, ryzyka oraz zakres wyłączony. Checkboxy z pliku zadania są
kanonicznym źródłem kryteriów; JSON planisty jest fallbackiem.

Trudność:

- `simple` — lokalna zmiana bez publicznego API, migracji, toolchainu,
  refaktoru, sprzętu ani naprawy po porażce;
- `standard` — typowy przyrost wymagający kilku kroków;
- `complex` — architektura/API, wiele modułów, bezpieczeństwo,
  współbieżność, migracja, CI/toolchain, sprzęt, refaktor lub zadanie
  naprawcze.

Domyślny profil to `standard`; orkiestrator mechanicznie podnosi go przy
oczywistym ryzyku.

### 2.3. Start zadania

Orkiestrator zdejmuje pierwsze zadanie z kolejki, odświeża kryteria z pliku,
tworzy tag `forge/<id>-start`, czyści sesje i tworzy
`.forge/current_task.md` oraz dziennik zadania.

Zadanie naprawcze z `repro_cmd` musi mieć repro czerwone na starcie. Jeśli
jest już zielone, zadanie zamyka się jako bezprzedmiotowe.

### 2.4. Mikro-TDD: TESTER

Tester działa pierwszy w każdym mikro-cyklu. Czyta zadanie, testy i kod, po
czym zwraca jedną z akcji:

- `wrote_test` — dodaje jeden test regresyjny w zadeklarowanej ścieżce;
- `no_test` — stwierdza, że dla kroku nie ma sensownego testu, np. przy
  refaktorze;
- `done` — zwraca mapę każdego kryterium na test albo uzasadnienie
  `justified`.

Po `wrote_test` orkiestrator wymaga czerwonej bramki całego build/test.
Niepoprawne ścieżki testera są wycofywane. Tester nie wykonuje commitów; jego sesja
może być wznawiana między cyklami.

Seria `no_test` albo testów przechodzących od razu uruchamia smell gate i
przekazuje zadanie do recenzji zamiast bez końca zużywać cykle.

### 2.5. Mikro-TDD: KODER

Po czerwonej bramce koder:

1. implementuje minimalny kod zazieleniający test;
2. wykonuje refaktor po uzyskaniu zieleni;
3. może dostać do dwóch dodatkowych prób zazielenienia w tym cyklu;
4. deklaruje ewentualne zmiany testów, ale nie wykonuje commitów.

Testy cyklu są domyślnie read-only na czas tury kodera. Orkiestrator
mechanicznie sprawdza zakres zmian, chroni testy/toolchain weryfikacyjny i
uruchamia anty-osłabianie na kodzie sprzed cyklu.

Po sukcesie wykonuje commit `tdd: ... (cykl N)`, aktualizuje dziennik i
checkpoint, a następny cykl zaczyna się od testera. Co 4 ukończone cykle
sesje testera i kodera są domyślnie rotowane.

### 2.6. DONE i bramka kryteriów

`DONE` nie jest akceptowane tylko na podstawie deklaracji testera.
Orkiestrator sprawdza:

- kompletność mapy kryterium -> test;
- istnienie pliku i nazwy testu;
- poprawność wpisów `justified`;
- zielony build/test oraz, dla zadań naprawczych, zielony repro.

Po 3 kolejnych odrzuceniach mapy domyślna polityka
`review_if_green` eskaluje do recenzenta, o ile bramka jest zielona.
Alternatywy to `fail` i `continue`.

### 2.7. Recenzja zadania

Recenzent jest niezależnym sędzią: dostaje świeży kontekst bez sesji i
dziennika testera/kodera. Orkiestrator przekazuje mu diff od taga startu,
listę zmienionych plików, zmiany toolchainu, kryteria `justified` oraz
ewentualny kontekst eskalacji.

Przy `approve` i zielonej bramce zadanie kończy się commitem końcowym i
pushem. Przy `changes` koder wykonuje poprawki, a orkiestrator ponawia
bramkę i recenzję. Domyślnie są maksymalnie 3 rundy poprawek.

Porażka zachowuje artefakt na `forge/failed/<id>`, wraca do taga startowego
i zapisuje powód w `.forge/failures.md`. Domyślnie pozostała kolejka jest
porzucana, bo jej założenia mogły się zdezaktualizować; następny planista
buduje nowy wsad.

### 2.8. Weryfikacja celu

`no_more_tasks` nie oznacza automatycznie sukcesu. Weryfikator-QA sprawdza:

- `smoke` — dymny bieg produktu;
- `ci` — polling statusu dla konkretnego SHA, z backoffem i logami;
- `hardware` — opcjonalny probe, flash i testy na urządzeniu.

Dowody i pełne logi zbiera najpierw orkiestrator; agent dostaje kody wyjścia
i ścieżki logów. Weryfikator prowadzi rejestr `code_bug`,
`verify_defect`, `env_issue`, `flaky` i `design_gap`.

`PASS` wymaga zielonych wszystkich targetów i braku aktywnych problemów.
`FAIL` tworzy `feedback.md` i `problems.json`, po czym planista tworzy
zadania naprawcze z czerwonym na starcie repro. Potwierdzony `env_issue`,
brak postępu przez 2 cykle albo 8 cykli łącznie kończy pipeline twardym
stopem dla człowieka.

## 3. Domyślne role, modele i efforty

Bez flag i zmiennych środowiskowych:

| Rola | Agent | Model | Effort | Moment |
|---|---|---|---|---|
| Planista / bootstrap | `claude` | `opus` | `high` | bootstrap i każdy plan |
| Tester | `opencode` | `glm-5.2-short-fast-flex` (simple), `glm-5.2-flex` (standard/complex) | low / medium / high | początek mikro-cyklu |
| Koder | `opencode` | `kimi-k2.7-code-flex` | pusty (wybiera agent) | implementacja i refaktor |
| Recenzent | `opencode` | `glm-5.2-flex` | medium / high / high | po DONE lub smell gate |
| Weryfikator | `opencode` | `qwen3.5-397b` | pusty (wybiera agent) | po wyczerpaniu backlogu |

Pełne nazwy modeli opencode mają prefiks `neuralwatt/`. W tabeli efforty są
w kolejności `simple / standard / complex`. Pusty effort oznacza brak
jawnej flagi CLI.

### Pełna macierz wbudowanych agentów

Format komórki: `model / effort`; kolumny to `simple / standard / complex`.

| Agent | Rola | simple | standard | complex |
|---|---|---|---|---|
| codex | planner | gpt-5.6-sol / high | gpt-5.6-sol / high | gpt-5.6-sol / high |
| codex | tester | gpt-5.6-terra / medium | gpt-5.6-terra / medium | gpt-5.6-sol / medium |
| codex | coder | gpt-5.6-luna / medium | gpt-5.6-terra / low | gpt-5.6-terra / medium |
| codex | reviewer | gpt-5.6-sol / medium | gpt-5.6-sol / medium | gpt-5.6-sol / medium |
| codex | verifier | gpt-5.6-terra / medium | gpt-5.6-terra / medium | gpt-5.6-terra / medium |
| claude | planner | opus / high | opus / high | opus / high |
| claude | tester | sonnet / medium | sonnet / high | opus / high |
| claude | coder | sonnet / low | sonnet / medium | opus / medium |
| claude | reviewer | sonnet / high | sonnet / high | opus / high |
| claude | verifier | sonnet / high | sonnet / high | sonnet / high |
| grok | planner | grok-4.5 / high | grok-4.5 / high | grok-4.5 / high |
| grok | tester | grok-4.5 / low | grok-4.5 / medium | grok-4.5 / high |
| grok | coder | grok-4.5 / low | grok-4.5 / medium | grok-4.5 / high |
| grok | reviewer | grok-4.5 / medium | grok-4.5 / high | grok-4.5 / high |
| grok | verifier | grok-4.5 / high | grok-4.5 / high | grok-4.5 / high |
| opencode | planner | qwen3.5-397b / — | qwen3.5-397b / — | qwen3.5-397b / — |
| opencode | tester | glm-5.2-short-fast-flex / low | glm-5.2-flex / medium | glm-5.2-flex / high |
| opencode | coder | kimi-k2.7-code-flex / — | kimi-k2.7-code-flex / — | kimi-k2.7-code-flex / — |
| opencode | reviewer | glm-5.2-flex / medium | glm-5.2-flex / high | glm-5.2-flex / high |
| opencode | verifier | qwen3.5-397b / — | qwen3.5-397b / — | qwen3.5-397b / — |
| kiro | każda rola | model wybiera Kiro | model wybiera Kiro | model wybiera Kiro |

Niestandardowy agent korzysta z własnego szablonu CLI. Bez osobnej macierzy
używa pól `FORGE_<ROLE>_MODEL` i `FORGE_<ROLE>_EFFORT`.

## 4. Limity i bramki

| Ustawienie | Domyślnie | Znaczenie |
|---|---:|---|
| `batch_size` | 5 | zadań w jednym planie |
| `max_micro_cycles` | 12 | cykli test -> kod na zadanie |
| `max_green_retries` | 2 | dodatkowe próby kodera w cyklu |
| `max_fix_attempts` | 3 | rund recenzja -> poprawka |
| `session_rotate_cycles` | 4 | rotacja sesji |
| `max_done_rejects` | 3 | odrzuceń mapy DONE przed eskalacją |
| `done_reject_policy` | review_if_green | polityka po limicie mapy |
| `max_verify_cycles` | 8 | absolutny sufit weryfikacji |
| `max_stall_cycles` | 2 | cykle bez postępu |
| `verify_timeout_s` | 1800 s | komenda weryfikacji/repro |
| `ci_timeout_s` | 2700 s | oczekiwanie na CI |
| push po sukcesie | włączony | bieżący branch do `origin` |

## 5. Konfiguracja ról

```bash
python3 -m forge.orchestrate --non-interactive \
  --planner-agent claude --tester-agent opencode --coder-agent codex \
  --reviewer-agent claude --verifier-agent opencode
```

Najważniejsze zmienne to `FORGE_PLANNER_AGENT`, `FORGE_TESTER_AGENT`,
`FORGE_CODER_AGENT`, `FORGE_REVIEWER_AGENT` i `FORGE_VERIFIER_AGENT`.
Modele i efforty można nadpisać odpowiednimi zmiennymi
`FORGE_*_MODEL` i `FORGE_*_EFFORT`. Recenzent pozostaje w świeżym
kontekście nawet przy tym samym narzędziu co autor.
