# Taski implementacyjne — Product Owner, preflight i weryfikacja historyjek

Wykonawcza rozpiska do [PLAN-PRODUCT-OWNER.md](PLAN-PRODUCT-OWNER.md).
Tam jest uzasadnienie i design; tutaj kolejność, kontrakty i kryteria odbioru.

**Zasada odbioru dla każdego taska:** `python3 -m pytest -q` jest zielone przed
przejściem do następnego. Task bez własnego testu nie jest ukończony.

## Mapa bloków

| blok | strumień planu | taski | zależy od |
|---|---|---|---|
| B1 | S1 preflight | 1.1 – 1.6 | — |
| B2 | S3 parser backlogu | 2.1 – 2.5 | — |
| B3 | S2 + S9 rola PO i poziomy | 3.1 – 3.8 | B2 |
| B4 | S6 notatnik PO | 4.1 – 4.2 | B3 |
| B5 | S10 migracja | 5.1 – 5.3 | B2, B3 |
| B6 | S4 pole `story` | 6.1 – 6.4 | B2 |
| B7 | S4a automat statusów | 7.1 – 7.5 | B6 |
| B8 | S5 weryfikator historyjek | 8.1 – 8.6 | B7 |
| B9 | S7 wyzwalacze | 9.1 – 9.4 | B2, B8 |
| B10 | S8 recenzent PO | 10.1 – 10.3 | B3 |
| B11 | sprzątanie i dokumentacja | 11.1 – 11.3 | wszystkie |

B1 i B2 są niezależne i mogą iść równolegle. Reszta jest łańcuchem.

---

# B1. Preflight deterministyczny

Nowy moduł, zero wywołań modelu. Cel: Forge nigdy nie zatrzymuje się na starcie
przez zastaną, niezacommitowaną pracę.

### 1.1 Szkielet `forge/preflight.py`

**Pliki:** `forge/preflight.py` (nowy)

**Kontrakt:**
```python
@dataclass(frozen=True)
class PreflightResult:
    parked_branch: str = ""     # "" gdy nie parkowano
    parked_paths: list[str] = field(default_factory=list)
    dropped_tags: list[str] = field(default_factory=list)
    legacy_backlog: bool = False

def run(project: str, cfg: Config, state: State) -> PreflightResult: ...
```

Moduł importuje `git`, `has_changes`, `commit_all` z `orchestrate` **odwrotnie**
— to `orchestrate` importuje `preflight`, więc pomocniki gitowe wędrują do
`forge/gitutil.py` albo `preflight` dostaje je wstrzyknięte. Wybierz jedno i
zapisz w docstringu; cykl importów jest tu realnym ryzykiem.

**Gotowe gdy:** `run` na czystym repozytorium zwraca pusty `PreflightResult` i
nie dotyka drzewa.

**Testy:** `tests/test_preflight.py` — czyste repo, brak efektów ubocznych.

---

### 1.2 Rozpoznanie stanu HEAD

**Pliki:** `forge/preflight.py`

**Kontrakt:**
```python
def head_state(project: str) -> tuple[str, str]:
    """Zwraca ("branch", nazwa) | ("detached", sha) | ("unborn", "")."""
```
Implementacja: `git symbolic-ref --short HEAD` → gałąź; niepowodzenie +
`git rev-parse --verify HEAD` sukces → detached; oba niepowodzenia → unborn.

**Gotowe gdy:** trzy stany rozpoznawane bez wyjątku.

**Testy:** repo świeże po `git init` → `unborn`; po commicie → `branch`; po
`git checkout <sha>` → `detached`.

---

### 1.3 Parking brudnego drzewa

**Pliki:** `forge/preflight.py`

**Kontrakt:** `park_dirty_tree(project, cfg, state) -> tuple[str, list[str]]`

Tabela decyzyjna (§S1 planu) — implementuj dokładnie w tej kolejności:

1. `not has_changes(project)` → `("", [])`
2. `state.current_task` niepuste → `("", [])`, wpis do ledgera „preflight:
   zastane zmiany należą do aktywnego zadania"
3. `head_state == "unborn"` → `("", [])`, wpis do ledgera „preflight: repo bez
   commita bazowego, zmiany zostają dla bootstrapu"
4. w pozostałych przypadkach — parking:
   - zapamiętaj punkt powrotu z `head_state`
   - `git switch -c forge/parked/<YYYYMMDD-HHMMSS>`
   - `commit_all(project, "wip: zaparkowana praca sprzed startu Forge", cfg)`
   - powrót: `git switch <nazwa>` albo `git switch --detach <sha>`
   - potwierdź `not has_changes(project)`; niespełnione → `AgentError`

**Zakazane:** `git switch -`, `@{-1}`, `git checkout -`. Opierają się na
reflogu HEAD, którego świeże repozytorium nie ma; potwierdzone lokalnie —
kończy się `fatal: invalid reference: @{-1}` i procesem uwięzionym na gałęzi
parkingowej.

**Gotowe gdy:** po parkingu drzewo jest czyste, HEAD wrócił na punkt wyjścia, a
commit z pracą istnieje na gałęzi `forge/parked/*`.

**Testy:** cztery przypadki z tabeli osobno + odłączony HEAD + niepowodzenie
kroku parkingu (mock `git` zwracający kod ≠ 0) → `AgentError` i nietknięte
drzewo.

---

### 1.4 Notatka o parkingu

**Pliki:** `forge/preflight.py`, `forge/orchestrate.py`

**Kontrakt:** parking zapisuje `.forge/parked.md`:
```markdown
# Zaparkowana praca

- gałąź: forge/parked/20260809-2141
- punkt powrotu: main
- data: 2026-08-09 21:41
- pliki: src/a.py, tests/test_a.py

Powrót: `git switch forge/parked/20260809-2141`
```
plus wpis w ledgerze. Plik jest jednorazowym wejściem najbliższego PO — ten sam
wzorzec, co `.forge/steering.md`: doklejany do promptu i **kasowany po udanej
turze PO**, nie wcześniej.

**Gotowe gdy:** plik powstaje, jest czytelny dla człowieka i znika po
skonsumowaniu przez PO.

**Testy:** obecność pliku po parkingu; brak po udanej turze PO; obecność po
turze nieudanej.

---

### 1.5 Usuwanie osieroconych tagów zadań

**Pliki:** `forge/preflight.py`

**Kontrakt:** `drop_stale_task_tags(project, state) -> list[str]` — usuwa tagi
pasujące do `task-*-start`, których identyfikator nie odpowiada
`state.current_task["id"]`.

**Gotowe gdy:** tag aktywnego zadania przeżywa, pozostałe znikają, wynik trafia
do ledgera.

**Testy:** aktywne zadanie + trzy stare tagi → zostaje jeden.

---

### 1.6 Wpięcie preflightu w `main`

**Pliki:** `forge/orchestrate.py`

**Kontrakt:** wywołanie `preflight.run(...)` w `main`, **raz**, po wczytaniu
stanu i **przed** pętlą `while`. Nie w `one_iteration` — to nie jest operacja
per-iteracja.

`_require_clean` zostaje we wszystkich obecnych miejscach jako asercja
niezmiennika. Po preflighcie nie ma prawa się odpalić; jeśli się odpali, to jest
prawdziwy błąd, nie stan startowy — zaktualizuj jej komunikat, żeby to mówił.

**Gotowe gdy:** start Forge na brudnym repo bez aktywnego zadania dochodzi do
planowania zamiast kończyć się błędem.

**Testy:** `tests/test_preflight.py` — pełna ścieżka startu na brudnym repo.
Regresja: `git switch -` ani `@{-1}` nie występują w `forge/`.

---

# B2. Parser backlogu

Fundament dla B5, B7, B8, B9, B10. Zero wywołań modelu.

### 2.1 Model danych i `parse`

**Pliki:** `forge/backlog.py` (nowy)

**Kontrakt:**
```python
STATUSES = ("nowa", "w toku", "do weryfikacji", "zrobiona", "porzucona")

@dataclass(frozen=True)
class Story:
    id: str            # "US-007"
    title: str
    status: str
    drop_reason: str   # tylko dla "porzucona"
    why_now: str       # treść po "Dlaczego teraz:"
    check: str         # treść po "Sprawdzenie:"
    out_of_scope: str
    body: str          # narracja historyjki
    line: int          # numer linii nagłówka — do punktowej edycji

def parse(text: str) -> tuple[list[Story], list[str]]:
    """Historyjki w kolejności występowania + lista bloków nieparsowalnych."""
```

Nagłówek kanoniczny: `## US-NNN — <tytuł>  [<status>]`, przy `porzucona`
dopuszczalne `[porzucona: powód]`.

**Gotowe gdy:** parser czyta przykład z §S3 planu i zwraca komplet pól.

**Testy:** `tests/test_backlog.py` — przykład wzorcowy, historyjka bez pól
opcjonalnych, blok śmieciowy trafia do drugiej listy zamiast wywracać parsowanie.

---

### 2.2 Twarde invarianty

**Pliki:** `forge/backlog.py`

**Kontrakt:**
```python
def validate_hard(before: list[Story], after: list[Story],
                  dropped: list[dict], orphans: list[str]) -> list[str]:
    """Lista naruszeń w języku naturalnym; pusta = wolno commitować."""
```
Sprawdza dokładnie sześć rzeczy (§S3 planu):
1. format i unikalność `US-NNN`;
2. status w `STATUSES`;
3. niepuste `Sprawdzenie:` i `Dlaczego teraz:` w każdej historyjce;
4. żadne ID z `before` nie zniknęło bez wpisu w `dropped`;
5. status zmieniony między `before` a `after` wyłącznie na `porzucona`;
6. `orphans` puste.

**Czego NIE sprawdza:** sufitu `FORGE_MAX_BACKLOG_STORIES` ani niczego
semantycznego — to należy do recenzenta PO (task 10.2).

**Gotowe gdy:** każde z sześciu naruszeń daje osobny, konkretny komunikat.

**Testy:** po jednym teście na naruszenie + test „czysta tura → pusta lista".

---

### 2.3 Punktowa zmiana statusu

**Pliki:** `forge/backlog.py`

**Kontrakt:** `set_status(text: str, story_id: str, status: str, reason: str = "") -> str`

Modyfikuje **wyłącznie linię nagłówka** wskazanej historyjki. Reszta pliku musi
wyjść bajt w bajt identyczna — to jest kryterium odbioru, nie preferencja: ta
funkcja będzie wołana przez Forge na pliku, którego właścicielem jest PO, i
każda poboczna zmiana byłaby cichym nadpisaniem cudzej pracy.

**Gotowe gdy:** `set_status` na pliku z komentarzami, pustymi liniami i
nietypowym formatowaniem zmienia jeden znak w jednej linii.

**Testy:** porównanie bajt-w-bajt reszty pliku; nieznane ID → `KeyError`;
niedozwolony status → `ValueError`.

---

### 2.4 Liczniki i zbiory dla wyzwalaczy

**Pliki:** `forge/backlog.py`

**Kontrakt:**
```python
def count_open(stories: list[Story]) -> int          # status == "nowa"
def ids_by_status(stories, *statuses) -> list[str]
def load(project: str) -> tuple[list[Story], list[str]]   # czyta BACKLOG.md
```

**Gotowe gdy:** brak `BACKLOG.md` zwraca `([], [])`, a nie wyjątek.

**Testy:** liczniki na mieszanym backlogu; brakujący plik.

---

### 2.5 Wykrycie starego formatu

**Pliki:** `forge/backlog.py`

**Kontrakt:** `is_legacy(text: str) -> bool` — `True`, gdy plik jest niepusty i
nie zawiera ani jednego nagłówka `US-NNN`.

**Gotowe gdy:** proza z dzisiejszych projektów daje `True`, backlog po migracji
`False`, pusty plik `False` (nie ma czego migrować).

**Testy:** trzy powyższe przypadki.

---

# B3. Rola Product Owner

### 3.1 Poziomy modeli

**Pliki:** `forge/config.py`

```python
"product_owner":      {d: "max"    for d in TASK_DIFFICULTIES},
"po_reviewer":        {d: "strong" for d in TASK_DIFFICULTIES},
"verifier":           {d: "strong" for d in TASK_DIFFICULTIES},   # było economy/efficient/balanced
# "diff_bootstrap" — usunięty
```
`bootstrap` i `bootstrap_reviewer` bez zmian (`max`).

**Gotowe gdy:** `cfg.role("product_owner", d)` daje ten sam model dla każdej
trudności.

**Testy:** aktualizacja `tests/test_architecture.py` i testów routingu.

---

### 3.2 Nowe pokrętła konfiguracji

**Pliki:** `forge/config.py`

```python
backlog_low_water:   int = int(os.environ.get("FORGE_BACKLOG_LOW_WATER", "2"))
max_backlog_stories: int = int(os.environ.get("FORGE_MAX_BACKLOG_STORIES", "6"))
```
Komentarz przy każdym: pierwszy to próg wyzwalacza `refill`, drugi jest
**miękki** i trafia wyłącznie do recenzenta.

**Testy:** odczyt domyślnych i z env.

---

### 3.3 Nowe pola stanu

**Pliki:** `forge/state.py`

```python
po_refill_batch: int = 0
stories_verified_at_batch: int = 0
stories_verified_sha: str = ""
backlog_migrated: bool = False
```
Nazwy `steered_at_batch`, `steering_due`, `batch_drained` **zostają** — zmiana
kosztowałaby migrację `STATE.json` bez zysku. Dopisz komentarz, że „steering"
znaczy teraz „przegląd Product Ownera".

**Gotowe gdy:** stary `STATE.json` bez nowych pól wczytuje się z domyślnymi.

**Testy:** wczytanie stanu sprzed zmiany.

---

### 3.4 Szablon promptu PO

**Pliki:** `forge/prompts/templates/product-owner.md` (nowy) + warianty
wyzwalaczy `po-trigger-{refill,brief,cadence}.md`, `po-corrections.md`,
`po-parse-corrections.md`, `po-migration.md`, `po-parked.md`

Treść przenosi wartościową część `diff-bootstrap.md` i **dokłada** komplet zasad
z §S3 planu (osiem zasad + dwie anty-zasady + format zapisu). Sloty:
`{{TRIGGER}}`, `{{BRIEF_CHANGE}}`, `{{STORY_REPORT}}`, `{{QUEUED}}`,
`{{PARKED}}`, `{{MIGRATION}}`, `{{NOTEBOOK}}`, `{{CORRECTIONS}}`, `{{JSON_RULES}}`.

Kontrakt wyjścia (bez `stories_closed` — statusy należą do Forge):
```json
{"summary":"...","stories_added":["US-007"],
 "stories_dropped":[{"id":"US-005","reason":"..."}],
 "changes":["..."],"replan":false,"goal_reached":false,"notebook":"..."}
```

**Gotowe gdy:** `render` nie zgłasza brakujących slotów dla każdego z trzech
wyzwalaczy.

**Testy:** `tests/test_prompts.py` — render każdego wariantu; obecność
wszystkich ośmiu zasad w treści.

---

### 3.5 Funkcje promptu

**Pliki:** `forge/prompts/__init__.py`

```python
def product_owner_prompt(*, trigger, brief_diff="", story_report="",
                         queued_tasks=None, parked="", migration=False,
                         notebook_path="", review_notes=None) -> str
def po_review_prompt(result: dict) -> str
def po_parse_corrections_prompt(violations: list[str]) -> str
```
`diff_bootstrap_prompt` i `diff_bootstrap_review_prompt` usuwane w tasku 11.1.

**Testy:** render z minimalnym i pełnym zestawem argumentów.

---

### 3.6 `phase_product_owner`

**Pliki:** `forge/orchestrate.py`

Na bazie `phase_diff_bootstrap`. Zachować bez zmian: kotwiczenie na SHA sprzed
fazy, `_revert_out_of_scope`, `_restore_head`, `_dump_phase_work`, zapis
snapshotu briefu dopiero po zaakceptowanym werdykcie.

Nowa kolejność wewnątrz fazy:
```text
tura PO
  → parser: parse + validate_hard
      ├─ naruszenia → po_parse_corrections_prompt → ponowna tura PO
      └─ budżet FORGE_MAX_BOOTSTRAP_REVIEWS wyczerpany
           → _restore_head + AgentError + checkpoint
  → recenzent PO (task 10.1)
  → zapis snapshotu, notatki steering, statusów `porzucona`
  → commit
```
Parser stoi **przed** recenzentem: źle sformatowana tura nigdy nie dociera do
modelu recenzenta.

**Gotowe gdy:** tura z naruszeniem struktury nie commituje i nie woła recenzenta.

**Testy:** `tests/test_product_owner.py` — tura czysta; tura naprawiona po jednej
korekcie; wyczerpanie budżetu → cofnięcie i checkpoint; recenzent nie wołany przy
naruszeniu.

---

### 3.7 Zastosowanie `stories_dropped`

**Pliki:** `forge/orchestrate.py`

Po zaakceptowanej turze Forge ustawia `porzucona(powód)` przez
`backlog.set_status` dla każdego wpisu z `stories_dropped`. PO nie zapisuje tego
statusu sam — to jedyny status, o który *prosi*, a nie który *pisze*.

**Gotowe gdy:** porzucenie widoczne w `BACKLOG.md` i w ledgerze.

**Testy:** porzucenie nieistniejącego ID → ostrzeżenie w ledgerze, faza się nie
wywraca.

---

### 3.8 Ledger dla tury PO

**Pliki:** `forge/orchestrate.py`, `forge/ledger.py`

Wpisy: `po (<trigger>): +N historyjek, -M porzuconych, replan=…, goal=…`,
osobno naruszenia parsera i werdykt recenzenta.

**Testy:** obecność wpisów po turze.

---

# B4. Notatnik Product Ownera

### 4.1 Notatnik projektowy

**Pliki:** `forge/notebooks.py`

**Kontrakt:**
```python
PROJECT_NOTEBOOKS = {"product-owner": "# Notatnik Product Ownera\n\n## Obserwacje\n"}
def project_path(project: str, runtime_dir: str, role: str) -> Path
def ensure_project(project: str, runtime_dir: str) -> None
```
Ścieżka `.forge/notebooks/product-owner.md` — **poza** katalogami `<task-id>/`.

**Krytyczne:** `prune_orphans` musi ten plik pominąć. Dziś kasuje wszystko, co
nie jest katalogiem aktywnego zadania, więc bez tej poprawki housekeeping usunie
jedyną pamięć produktową projektu przy pierwszym planowaniu.

**Gotowe gdy:** `prune_orphans` z pustym `active_task_id` zostawia notatnik PO.

**Testy:** `tests/test_notebooks.py` — dokładnie ten przypadek.

---

### 4.2 Notatnik w kapsule PO

**Pliki:** `forge/orchestrate.py`, szablon PO

Prompt wskazuje ścieżkę notatnika i **nie wkleja treści** — tak samo jak u
testera i kodera. Wpis wraca polem `notebook` decyzji i dokleja go Forge; PO nie
czyta pliku z dysku ani nie zapisuje sam.

**Gotowe gdy:** wpis z pola `notebook` ląduje w pliku po turze.

**Testy:** doklejenie wpisu; pusty `notebook` nic nie zmienia.

---

# B5. Automatyczna migracja

### 5.1 Wykrycie w preflighcie

**Pliki:** `forge/preflight.py`, `forge/state.py`

`backlog.is_legacy(...)` → `state.backlog_migrated = False`. Backlog już w
formacie historyjek albo nieistniejący → `True`.

**Testy:** projekt z prozą; projekt po migracji; projekt bez backlogu.

---

### 5.2 Sekcja migracyjna w promptcie PO

**Pliki:** `forge/prompts/templates/po-migration.md`, `forge/orchestrate.py`

Doklejana wyłącznie przy `backlog_migrated == False`. Treść: przepisz istniejące
wpisy na historyjki wg zasad, **zachowaj treść i kolejność**, nie wymyślaj nowego
zakresu przy tej okazji, nadaj ID od `US-001`, wszystkim nadaj status `nowa`.

Nadmiar ponad `FORGE_MAX_BACKLOG_STORIES` **nie jest kasowany** — ogon idzie do
`docs/PROJECT.md` jako „kolejne prawdopodobne etapy". Sufit obowiązuje dopiero od
następnej tury.

Po zwalidowanej turze → `backlog_migrated = True`, sekcja znika z promptu.

**Testy:** sekcja obecna przy fladze `False`, nieobecna przy `True`.

---

### 5.3 Jednorazowa inwentaryzacja

**Pliki:** `forge/orchestrate.py`

Pusty `stories_verified_sha` znaczy, że nie ma od czego liczyć „ukończone od".
W tym jednym przebiegu weryfikator dostaje **wszystkie nieporzucone historyjki**
niezależnie od statusu, ograniczone do `FORGE_MAX_BACKLOG_STORIES` od góry
kolejki; reszta czeka na następną kadencję.

**Gotowe gdy:** migrowany projekt dostaje inwentaryzację dorobku zamiast pustego
raportu.

**Testy:** pusty `stories_verified_sha` + 10 historyjek → weryfikator dostaje 6.

---

# B6. Pole `story` w zadaniu

### 6.1 Kontrakt planisty

**Pliki:** `forge/prompts/templates/planner.md`

Planista czyta backlog w formacie historyjek i zwraca `"story":"US-007"` obok
`depends_on`. Puste `""` jest dozwolone i znaczy „dług techniczny lub
dokumentacyjny" — dokładnie zadania z `planner-debt-requirement.md`.

**Testy:** render zawiera pole w przykładzie JSON.

---

### 6.2 Przeniesienie pola

**Pliki:** `forge/orchestrate.py`

`build_task_from_plan` przenosi `story` (domyślnie `""`), `_write_current_task`
zapisuje je w pliku zadania.

**Testy:** `tests/test_task_flow.py` — pole przeżywa checkpoint i wznowienie.

---

### 6.3 Walidacja przynależności

**Pliki:** `forge/orchestrate.py`

Nieznane ID historyjki → wpis ostrzegawczy w logu i ledgerze, **zadanie
zostaje**. Nie kasujemy pracy za literówkę; sygnał trafia do PO i weryfikatora.
To świadomie inaczej niż przy niepoprawnym `task-NNN`, gdzie odsiew jest
konieczny, bo zgadnięty numer wskazałby cudzy plik.

**Testy:** nieznane ID → ostrzeżenie, zadanie w kolejce.

---

### 6.4 Grupowanie w raporcie

**Pliki:** `forge/report.py`

Zadania grupowane po historyjce; `$/historyjkę` obok `$/zadanie`.

**Testy:** `tests/test_report.py` — grupowanie i mianownik.

---

# B7. Automat statusów

**Musi wyprzedzić B8.** Bez niego weryfikator dostaje pustą listę i cała
kadencja jest bezwartościowa — to była główna uwaga z przeglądu planu.

### 7.1 `nowa → w toku`

**Pliki:** `forge/orchestrate.py`

Przy starcie zadania z niepustym `story` — przed pierwszą turą testera.

**Testy:** start zadania przestawia status; zadanie bez `story` nie rusza nic.

---

### 7.2 `w toku → do weryfikacji`

**Pliki:** `forge/orchestrate.py`

Po commicie zadania: jeśli żadne zadanie w `task_queue` ani `current_task` nie
wskazuje już tej historyjki → `do weryfikacji`.

Domknięcie liczone po **bieżącej kolejce i aktywnym zadaniu**, bo późniejszy wsad
może dołożyć kolejne zadania tej samej historyjki.

**Testy:** ostatnie zadanie historyjki zamyka ją; przedostatnie nie.

---

### 7.3 Powrót `do weryfikacji → w toku`

**Pliki:** `forge/orchestrate.py`

Po planowaniu: historyjka, do której nowy wsad dołożył zadanie, wraca do
`w toku`. To normalny stan, nie regres — zapisz to w komentarzu.

**Testy:** dołożenie zadania cofa status.

---

### 7.4 Zbiór „dotknięte od ostatniej weryfikacji"

**Pliki:** `forge/orchestrate.py`, `forge/ledger.py`

**Kontrakt:** `stories_touched_since(project, sha) -> set[str]` — historyjki
wskazane przez zadania ukończone od `stories_verified_sha`, czytane z ledgera.

To siatka bezpieczeństwa: automat sam nie wystarcza, bo wyjątek, ręczna edycja
backlogu albo zadanie bez `story` mogłyby dać pusty raport przy realnie
wykonanej pracy. Odczyt ledgera jest darmowy.

**Testy:** ręcznie zepsuty status i tak trafia do zbioru.

---

### 7.5 Test integracyjny automatu

**Pliki:** `tests/test_story_lifecycle.py` (nowy)

Pełny przebieg: plan 3 zadań dla `US-001` → start → commit → commit → commit →
status `do weryfikacji`; potwierdzenie weryfikatora → `zrobiona`;
`niepotwierdzona` → `w toku`.

---

# B8. Weryfikator historyjek

### 8.1 Szablon `verify-stories.md`

**Pliki:** `forge/prompts/templates/verify-stories.md` (nowy)

Wejście: historyjki z ich linią `Sprawdzenie:` + dowody mechaniczne z
`verify.collect_evidence` (kody wyjścia i ścieżki logów, **nie treści**).

Wyjście:
```json
{"stories":[{"id":"US-007","status":"potwierdzona|niepotwierdzona|częściowa",
             "evidence":"co zrobiłem i co zobaczyłem"}],
 "verdict":"complete|changes","notes":["..."]}
```
Prompt mówi wprost: **to nie jest code review**, nie czytaj diffu; tester i koder
już to zrobili. Twoim zadaniem jest wykonać `Sprawdzenie:` z zewnątrz.

**Testy:** render; obecność zakazu czytania diffu.

---

### 8.2 `phase_verify_stories`

**Pliki:** `forge/orchestrate.py`

Zbiór wejściowy = suma: status `do weryfikacji` **plus**
`stories_touched_since(project, state.stories_verified_sha)`.

**Pusty zbiór → rola nie jest wołana wcale**, kadencja idzie prosto do PO. Zero
tokenów za pytanie bez materiału.

**Testy:** pusty zbiór → brak wywołania; niepusty → wywołanie.

---

### 8.3 Ochrona drzewa

**Pliki:** `forge/orchestrate.py`

Weryfikator uruchamia produkt, więc dostaje narzędzia. Ochrona jak u recenzenta
kierunku: `_snapshot_tree` przed turą, `_restore_snapshot` po niej, cofnięte
ścieżki do ledgera.

**Testy:** zapis pliku przez weryfikatora zostaje cofnięty i odnotowany.

---

### 8.4 Zapis raportu i pól świeżości

**Pliki:** `forge/orchestrate.py`, `forge/state.py`

Raport → `.forge/verification/stories-latest.md` z nagłówkiem:
```markdown
<!-- verified_at_batch: 7 -->
<!-- verified_sha: a1b2c3d -->
```
Autorytatywne są `state.stories_verified_at_batch` i `state.stories_verified_sha`.
Nagłówek pliku służy ludziom i wykrywaniu podmiany: **niezgodność ze stanem
znaczy „raportu nie ma"**.

**Mtime nie jest używany nigdzie** — kłamie po skopiowaniu projektu, wznowieniu
z checkpointu i przy przestawionym zegarze.

**Testy:** raport z niezgodnym nagłówkiem jest odrzucany, nie reużywany.

---

### 8.5 Zastosowanie werdyktu

**Pliki:** `forge/orchestrate.py`

`potwierdzona` → `zrobiona`; `niepotwierdzona` / `częściowa` → `w toku`. Zapis
przez `backlog.set_status`.

**Testy:** oba przejścia.

---

### 8.6 Wpięcie w kadencję i `verify_goal`

**Pliki:** `forge/orchestrate.py`, `forge/prompts/templates/verify-goal.md`

Dwa miejsca: przed PO przy wyzwalaczu `cadence` oraz przed `verify_goal`.
`verify-goal.md` konsumuje raport zamiast pytać o „MVP" na sucho — dzisiejsze
cztery linijki z samymi kodami wyjścia znikają.

Przy wyzwalaczu `brief` raport jest reużywany, jeśli
`plan_batches - stories_verified_at_batch < FORGE_STEERING_BATCHES`; inaczej PO
dostaje informację, że świeżego raportu nie ma — nigdy starego udającego nowy.

**Testy:** kolejność faz przy `cadence`; reuse świeżego i odrzucenie starego.

---

# B9. Wyzwalacze PO

### 9.1 `_po_trigger`

**Pliki:** `forge/orchestrate.py`

Na bazie `_steering_trigger`, kolejność pierwszeństwa bez zmian:

| wyzwalacz | warunek | weryfikator przed PO |
|---|---|---|
| `brief` | skrót briefu ≠ snapshot | nie (reuse raportu) |
| `refill` | `backlog.count_open(...) < cfg.backlog_low_water` | nie |
| `cadence` | `plan_batches - steered_at_batch >= cfg.steering_batches` | tak |

**Zachować bez zmian warunek pustej kolejki** — komentarz w
`_steering_trigger` tłumaczy, dlaczego jego „uproszczenie" kasowało dopiero co
zaplanowany wsad wart ~520 tys. tokenów wejścia. Przenieś ten komentarz razem z
kodem.

**Testy:** `tests/test_steering.py` — każdy wyzwalacz osobno i pierwszeństwo.

---

### 9.2 Bezpiecznik `refill`

**Pliki:** `forge/orchestrate.py`, `forge/state.py`

`po_refill_batch` — `refill` nie odpala się dwa razy w tym samym wsadzie
planisty. Bez tego PO, który odmówi dołożenia historyjek, kręci `max`-model w
kółko.

**Testy:** dwa kolejne sprawdzenia w tym samym wsadzie → jedno odpalenie.

---

### 9.3 Skrócenie ścieżki wyczerpanego backlogu

**Pliki:** `forge/orchestrate.py`

Dzisiejsza sekwencja „planista zwraca `no_more_tasks` → `steering_due` →
przegląd" traci całe wywołanie planisty na `strong`. Przy działającym `refill`
backlog nie powinien dochodzić do zera; ścieżka `no_more_tasks` zostaje jako
bezpiecznik razem z licznikiem `empty_plans`.

**Testy:** `refill` uprzedza `no_more_tasks` przy niskim backlogu.

---

### 9.4 Konsumpcja notatki o parkingu

**Pliki:** `forge/orchestrate.py`

`.forge/parked.md` doklejany do promptu PO i kasowany po udanej turze.

**Testy:** patrz 1.4.

---

# B10. Recenzent PO

### 10.1 Parametr roli w pętli recenzji

**Pliki:** `forge/orchestrate.py`

`_reviewed_bootstrap` dostaje nazwę roli recenzenta. `bootstrap_reviewer`
(`max`) zostaje wyłącznie do jednorazowej recenzji architektury bootstrapu;
turę PO recenzuje `po_reviewer` (`strong`). Reszta maszynerii bez zmian: budżet
`FORGE_MAX_BOOTSTRAP_REVIEWS`, rewert, kotwica na SHA.

**Testy:** obie ścieżki wołają właściwą rolę.

---

### 10.2 Szablon recenzji PO

**Pliki:** `forge/prompts/templates/po-review.md`, `po-corrections.md`

Checklista **wyłącznie semantyczna** — struktury nie sprawdza wcale, bo do
recenzenta dociera backlog już przepuszczony przez twarde invarianty parsera:

1. czy historyjka opisuje wynik, a nie rozwiązanie;
2. czy `Sprawdzenie:` da się faktycznie wykonać z zewnątrz;
3. czy `Dlaczego teraz:` wiąże się z `PROJECT.md` albo z dowodem z raportu;
4. czy teza o kierunku wynika z raportu weryfikatora, a nie z domysłu;
5. czy nic nie zniknęło bez wpisu w `stories_dropped`;
6. czy `goal_reached` jest uczciwe wobec raportu;
7. czy przyrost jest najcieńszy sensowny;
8. czy sufit `FORGE_MAX_BACKLOG_STORIES` nie został przekroczony bez powodu
   (miękki — migracja może go legalnie przekroczyć).

**Testy:** render; obecność wszystkich ośmiu punktów.

---

### 10.3 Test pętli recenzji

**Pliki:** `tests/test_product_owner.py`

`request_changes` wraca do PO z uwagami; wyczerpanie budżetu cofa zmiany i
zostawia checkpoint.

---

# B11. Sprzątanie i dokumentacja

### 11.1 Usunięcie `diff_bootstrap`

**Pliki:** `forge/orchestrate.py`, `forge/config.py`, `forge/prompts/__init__.py`,
`forge/prompts/templates/diff-bootstrap*.md`, `tests/`

Usuń `phase_diff_bootstrap`, `diff_bootstrap_prompt`,
`diff_bootstrap_review_prompt`, wpis w `ROLE_MODEL_LEVELS` i wszystkie szablony
`diff-bootstrap*`. Bez feature flagi i bez trybu zgodności — ta sama zasada, co
przy migracji KISS.

**Gotowe gdy:** `grep -rn "diff_bootstrap\|diff-bootstrap" forge/ tests/` nic nie
zwraca.

---

### 11.2 Aktualizacja `docs/PIPELINE.md`

**Pliki:** `docs/PIPELINE.md`

Sekcja „Bootstrap i przegląd kierunku" → „Bootstrap, preflight i Product Owner".
Opisz: trzy wyzwalacze, automat statusów, weryfikator historyjek, dwa poziomy
walidacji backlogu, zakaz `git switch -`.

Rób to w trzech ratach — po B2, po B8 i po B10 — żeby dokument nigdy nie opisywał
stanu, którego nie ma w kodzie.

---

### 11.3 Aktualizacja `README.md`

**Pliki:** `README.md`

Diagram `planner → tester ↔ coder → reviewer` staje się:
```text
product owner → planner → tester ↔ coder → reviewer
                    ↑                          │
                    └── weryfikator historyjek ┘
```

---

## Kolejność wykonania

```text
B1 ─┐
    ├─ (równolegle)
B2 ─┘
     └─ B3 ─┬─ B4
            ├─ B5
            ├─ B10
            └─ B6 ─ B7 ─ B8 ─ B9
                                └─ B11
```

Punkty, w których łatwo się pomylić, w kolejności ryzyka:

1. **B7 przed B8.** Odwrotna kolejność daje weryfikator z pustą listą wejściową
   i test, który przechodzi, nie sprawdzając niczego.
2. **B2 przed B3.** Parser jest bramką w `phase_product_owner`; dorabianie go
   później znaczy przepisanie fazy.
3. **`set_status` bajt w bajt** (2.3). Ta funkcja pisze po pliku, którego
   właścicielem jest PO. Każda poboczna zmiana to ciche nadpisanie cudzej pracy.
4. **`prune_orphans` a notatnik PO** (4.1). Bez wyjątku housekeeping skasuje
   jedyną pamięć produktową projektu przy pierwszym planowaniu — i nikt tego nie
   zauważy, bo Forge będzie działał dalej, tylko głupiej.
