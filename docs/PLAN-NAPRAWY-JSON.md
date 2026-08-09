# Plan naprawy: werdykty JSON zabijane przez polskie cudzysłowy

**Status:** propozycja do wdrożenia
**Data:** 2026-08-09
**Dotyczy:** `forge/agents.py`, `forge/orchestrate.py`, `forge/prompts/`
**Powód:** trzy aborty biegu (`.forge/failed/_bez_zadania/invalid_json/`), ostatni
kosztował 1245 s modelu `max` i cofnięcie gotowej pracy nad `BACKLOG.md` /
`docs/PROJECT.md`.

---

## 0. Diagnoza — co dokładnie się psuje

Model (GLM-5.2, role bootstrap/steering/plan) pisze po polsku i przenosi
polską interpunkcję do **wartości** JSON-a. Dwa warianty awarii:

| wariant | przykład z zrzutu | błąd `json.loads` |
|---|---|---|
| A — niesparowany cudzysłów | `…w ramieniu „zaplanowany". Kolejka…` | `Expecting ',' delimiter` |
| A — jw. | `(„taktyczne bitwy na heksach" vs auto_resolve…` | `Expecting ',' delimiter` |
| B — eskejp znaku typograficznego | `a „grać patrząc\” stoi dla mapy` | `Invalid \escape` |

**Sprostowanie względem pierwszej diagnozy w rozmowie.** Kierunek naprawy jest
odwrotny, niż napisałam: znaki `„ ” « »` są **legalne** wewnątrz stringa JSON i
nie trzeba ich normalizować. Psuje dopiero:

* wariant A — **goły ASCII `"`** użyty jako cudzysłów zamykający; on kończy
  string i reszta zdania trafia do parsera jako składnia;
* wariant B — **`\` przed znakiem spoza `"\/bfnrtu`**, czyli nielegalna
  sekwencja eskejpowa.

Warstwa naprawcza musi więc **eskejpować zabłąkane ASCII `"`** i **usuwać
nielegalne backslashe**, a nie podmieniać cudzysłowy typograficzne.

### Dlaczego nic tego nie złapało

1. `extract_json` ([agents.py:336](../forge/agents.py#L336)) i
   `_scan_json_objects` ([agents.py:135](../forge/agents.py#L135)) są
   zahartowane wyłącznie na niesparowany cudzysłów **w prozie wokół** JSON-a.
   Tu uszkodzenie jest **w środku wartości**, więc obie ścieżki padają.
2. `_parse_json_object` ([orchestrate.py:871](../forge/orchestrate.py#L871))
   zgłasza `InvalidDecision("agent nie zwrócił obiektu JSON")` — **`JSONDecodeError`
   jest połknięty piętro niżej** i nigdy nie opuszcza `extract_json`.
3. `_decision_with_retry` ([orchestrate.py:826](../forge/orchestrate.py#L826))
   wkleja do retry ten bezużyteczny komunikat. Model nie wie, gdzie jest błąd
   ani że chodzi o cudzysłów, więc powtarza nawyk (próba 2: 46 s, ten sam błąd).
4. `phase_diff_bootstrap` na wyjątku robi `_restore_head` + `_revert_paths`
   ([orchestrate.py:517-524](../forge/orchestrate.py#L517)) — poprawna praca
   znika, bo nieczytelny był tylko werdykt.

Cztery poprawki poniżej są **niezależne**; każda skraca tę ścieżkę awarii w
innym miejscu. Kolejność wdrożenia jest w §5.

---

## 1. Parser — warstwa naprawcza w `extract_json`

Jedyna poprawka działająca **wstecz i bez zaufania do modelu**. Musi zachować
zasadę z docstringa `_scan_json_objects`: *lepiej `None` niż poprawnie
wyglądający dict bez pola, którego szuka wywołujący*.

### 1.1 Nowa funkcja `_repair_json_text(raw: str) -> str`

Plik: `forge/agents.py`, tuż nad `extract_json`.

Jeden przebieg znak po znaku, ze stanem „w stringu / poza stringiem”:

* **poza stringiem** — przepisujemy znak bez zmian; `"` otwiera string;
* **w stringu**:
  * `\` + znak z `"\/bfnrtu` → przepisz oba (legalny eskejp);
  * `\` + cokolwiek innego → **pomiń backslash**, przepisz sam znak *(wariant B)*;
  * `"` → rozstrzygnij lookaheadem, czy to domknięcie:
    * pomiń białe znaki; jeśli następny znak to `,` `}` `]` `:` albo koniec
      tekstu → to **domknięcie strukturalne**, przepisz `"` i wyjdź ze stringa;
    * w przeciwnym razie → to **treść**, wypisz `\"` i zostań w stringu *(wariant A)*.

Na obu obserwowanych przypadkach lookahead trafia: po `„zaplanowany"` stoi `.`,
po `…na heksach"` stoi spacja i `v` — oba to treść.

**Znany fałszywy negatyw, świadomie zaakceptowany:** treść kończąca się na
`",` (np. `…„zaplanowany", ale…`) zostanie uznana za domknięcie, tekst nadal nie
sparsuje i wrócimy do `None` — czyli do dzisiejszego zachowania. Naprawa jest
best-effort, nigdy nie zgaduje ponad to, co potwierdzi `json.loads`.

### 1.2 Rdzeń z diagnozą — `_extract_json_detail`

`extract_json` zwraca dziś sam `dict | None` i gubi powód porażki, którego
potrzebuje poprawka §2. Rozdzielamy to bez ruszania wywołujących:

```python
@dataclass(frozen=True)
class JsonExtraction:
    data: dict | None = None
    repaired: bool = False   # werdykt odzyskany warstwą naprawczą
    error: str = ""          # powód dla promptu retry, pusty gdy data is not None
```

`_extract_json_detail(text) -> JsonExtraction` — kolejność prób bez zmian, z
**trzecim** etapem i zapamiętaniem najlepszego błędu:

1. ogrodzenia ```` ```json ```` od ostatniego → `json.loads`;
2. `_scan_json_objects` na całym tekście;
3. **NOWE:** te same ogrodzenia od ostatniego, przepuszczone przez
   `_repair_json_text` → `json.loads`; przy sukcesie `repaired=True`. Gdy
   ogrodzeń nie ma — ten sam zabieg na wycinku od ostatniego `{` do ostatniego `}`.

`error` budujemy z `JSONDecodeError` **ostatniego ogrodzenia** (tam agent kładzie
werdykt w 100% obserwowanych przypadków), w formacie:

```
blok ```json``` nie parsuje: Expecting ',' delimiter (linia 1, kolumna 298);
kontekst: …a G118.1a–d jako [ ], nota zamykająca K117 wciąż w ramieniu „zaplanowany". Kolejka…
```

Kontekst = `raw[max(0, e.pos-60) : e.pos+60]`, znaki nowej linii zamienione na
spacje, żeby komunikat został jednolinijkowy w logu i w promptcie.

`extract_json(text)` zostaje **cienkim opakowaniem** `_extract_json_detail(...).data`
— żaden z 5 dzisiejszych wywołujących (`task_pipeline._decision`,
`orchestrate._parse_json_object`, `orchestrate:752`) nie wymaga zmiany.

### 1.3 Obserwowalność

`_parse_json_object` i `task_pipeline._decision` przechodzą na
`_extract_json_detail`. Gdy `repaired=True`:

```
log("  UWAGA: werdykt odzyskany po naprawie cudzysłowów — rola pisze niepoprawny JSON")
ledger.append(project, "json: werdykt odzyskany warstwą naprawczą")
```

Bez tego naprawa **ukryje** defekt modelu i nigdy się nie dowiemy, że warto
zmienić routing roli. To nie jest kosmetyka logu, tylko warunek, żeby §3 dało
się kiedykolwiek zweryfikować.

### 1.4 Testy (`tests/test_agents.py`)

Nowe, obok istniejących `test_extract_json_*`:

| test | wejście | oczekiwanie |
|---|---|---|
| `…repairs_ascii_quote_closing_polish_opener` | dosłowny fragment z `„zaplanowany".` | dict z pełnym `summary`, `replan is False` |
| `…repairs_invalid_escape_before_typographic_quote` | `„grać patrząc\”` | dict, brak wyjątku |
| `…repair_keeps_valid_json_untouched` | poprawny werdykt z `„…”` w treści | identyczny dict, `repaired is False` |
| `…repair_returns_none_when_ambiguous` | treść z `",` w środku | `None`, **nie** dict-połówka |
| `…detail_reports_position_and_context` | jw. wariant A | `error` zawiera `kolumna 298` i fragment `zaplanowany` |

Materiał wejściowy: `.forge/failed/_bez_zadania/invalid_json/*.txt` — trzy
prawdziwe awarie. Skopiować fragmenty do fixture'ów w teście, **nie** czytać
plików z cudzego repo.

---

## 2. Retry — konkretna diagnoza zamiast „nie zwrócił obiektu JSON”

Plik: `forge/orchestrate.py`.

### 2.1 Przekazanie powodu

`_parse_json_object` zgłasza dziś stałą. Po §1.2:

```python
def _parse_json_object(text: str) -> dict:
    found = _extract_json_detail(text)
    if not isinstance(found.data, dict):
        raise InvalidDecision("agent nie zwrócił obiektu JSON"
                              + (f" — {found.error}" if found.error else ""))
    return found.data
```

To samo w `task_pipeline._decision` (te same role, ta sama klasa błędu).

### 2.2 Poszerzenie limitu w `_decision_with_retry`

`reason = str(exc)[:500]` → **`[:800]`**. Komunikat rośnie o ~130 znaków
kontekstu; 500 zaczyna go ucinać w pół cytatu, a ucięty cytat jest gorszy niż
brak cytatu (model zobaczy urwany fragment i może go potraktować jako wzór).

### 2.3 Wzmocnienie `_JSON_RETRY`

Dziś ([orchestrate.py:28](../forge/orchestrate.py#L28)) mówi tylko „zwróć
poprawny JSON”. Dokładamy **regułę cudzysłowów** — model dostaje ją tu nawet
wtedy, gdy diagnoza jest ogólna, i nawet w rolach, których promptu §3 nie ruszy:

```
Poprzednia odpowiedź nie spełniła kontraktu. Nie wykonuj dalszych zmian.
W wartościach JSON nie używaj cudzysłowów: ani typograficznych („ ” « »),
ani prostego ". Cytuj apostrofami albo pisz bez cudzysłowu.
Zwróć teraz wyłącznie jeden poprawny obiekt JSON w formacie podanym wyżej.
```

### 2.4 Test (`tests/test_orchestrate.py`)

`test_json_retry_prompt_carries_decoder_position` — `invoke` jako fake
zwracający najpierw zepsuty werdykt, potem poprawny; asercja, że **drugi**
prompt zawiera `kolumna` oraz fragment treści z pierwszej próby. To pilnuje
całej ścieżki §1.2 → §2.1 → §2.2, a nie samego formatowania stringa.

---

## 3. Prompt — jedno zdanie, jedno miejsce

Plik: `forge/prompts/`.

### 3.1 Wspólna stała zamiast dziesięciu kopii

Kontrakt JSON występuje w **11 szablonach** (`bootstrap.md`,
`bootstrap-corrections.md`, `bootstrap-architecture-review.md`,
`diff-bootstrap.md`, `diff-bootstrap-review.md`, `planner.md`, `reviewer.md`,
`tester.md`, `coder.md`, `verify-goal.md`, `master-system.md`). Wklejenie zdania
do trzech z nich zostawia osiem ról z tą samą pułapką i gwarantuje rozjazd przy
następnej edycji.

Robimy to raz: nowy szablon `forge/prompts/templates/json-rules.md`

```
W wartościach JSON nie używaj cudzysłowów: ani typograficznych („ ” « »), ani
prostego ". Cytuj apostrofami albo pisz bez cudzysłowu.
```

i slot `{{JSON_RULES}}` w każdym szablonie z kontraktem JSON, tuż pod linią
formatu. Renderer wymusza komplet slotów
([render.py:22](../forge/prompts/render.py#L22)), więc pominięcie któregoś
szablonu wyjdzie od razu wyjątkiem, a nie po cichu.

Podstawienie w `forge/prompts/__init__.py` — jedna linia w każdej funkcji
`*_prompt`, albo (czyściej) domyślka w `render()`: jeśli szablon deklaruje
`JSON_RULES`, a wywołujący go nie podał, renderer wstawia treść
`json-rules.md`. Druga wersja jest o tyle lepsza, że nowy szablon z kontraktem
JSON dostaje regułę **bez** dotykania `__init__.py`.

### 3.2 Uwaga do `master-system.md`

Mistrz ma osobny tor: `json_schema` = `master-schema.json`
([config](../forge/prompts/templates/master-schema.json)) i tryb cienki. Schemat
jest kontraktem struktury, nie stylu — reguła cudzysłowów idzie do
`master-system.md`, plik `master-schema.json` **zostaje bez zmian**.

### 3.3 Test (`tests/test_role_context.py`)

`test_every_json_contract_template_carries_quote_rule` — iteracja po
`templates/*.md`; jeśli szablon zawiera `{"` (linia formatu JSON), musi zawierać
też `{{JSON_RULES}}`. Test rośnie sam wraz z nowymi szablonami; to jego cały
sens.

---

## 4. Ratowanie pracy przed rewertem

Plik: `forge/orchestrate.py`.

### 4.1 Dlaczego nie da się tego zrobić w `_dump_invalid_decision`

`_dump_invalid_decision` biegnie w handlerze `main()`
([orchestrate.py:1671](../forge/orchestrate.py#L1671)) — czyli **po** tym, jak
`phase_diff_bootstrap` już wyczyścił drzewo. Zrzut musi powstać **wewnątrz
fazy**, w bloku `except`, przed `_restore_head`.

### 4.2 Nowy helper `_dump_phase_work`

```python
def _dump_phase_work(project, cfg, label, base_sha, paths) -> str:
    """Zachowaj pracę fazy tuż przed rewertem — best-effort, nigdy nie przesłania
    pierwotnego wyjątku ani nie wstrzymuje sprzątania drzewa."""
```

Wzorowany na `_fail_task` ([orchestrate.py:911](../forge/orchestrate.py#L911)),
zawartość katalogu `.forge/failed/_steering/<YYYYmmdd-HHMMSS>/`:

* `diff.patch` — `git diff --no-ext-diff <base_sha> -- <paths>`;
* `changed.txt` — lista ścieżek z `_turn_changes`;
* `untracked/<ścieżka>` — kopie plików nieśledzonych (jak w `_fail_task`;
  `_revert_paths` je kasuje, więc `git diff` ich nie zobaczy);
* `reason.txt` — `str(exc)`.

Całość w `try/except OSError` z logiem ostrzeżenia — dokładnie kontrakt
`_dump_invalid_decision` ([orchestrate.py:844](../forge/orchestrate.py#L844)):
awaria zapisu nie ma prawa zjeść ani komunikatu o bezpiecznym zatrzymaniu, ani
kodu wyjścia.

### 4.3 Wpięcie

```python
except Exception:
    changed = _turn_changes(before, _tree_manifest(project))
    dest = _dump_phase_work(cfg, project, "Diff-bootstrap", base_sha, changed)
    if dest:
        log(f"Diff-bootstrap: praca przed rewertem zachowana w {dest}")
        ledger.append(project, f"diff-bootstrap: praca zachowana w {dest}")
    _restore_head(project, base_sha, "Diff-bootstrap")
    _revert_paths(project, base_sha, changed)
    raise
```

Rewert **zostaje bez zmian** — to jest własność bezpieczeństwa („niezaakceptowany
kierunek nie ma prawa zostać w drzewie”, komentarz przy
[orchestrate.py:515](../forge/orchestrate.py#L515)) i tego nie ruszamy. Zmienia
się tylko to, że praca daje się odzyskać `git apply` zamiast lądować w koszu.

Ten sam zabieg wchodzi do `phase_bootstrap` (ta sama para
`_restore_head`/`_revert_paths`) — jeden helper, dwa wywołania.

### 4.4 Test (`tests/test_steering.py`)

`test_failed_steering_saves_work_before_revert` — fake agent edytuje
`BACKLOG.md` i zwraca zepsuty JSON; asercje: (a) `BACKLOG.md` w drzewie wrócił
do stanu z `base_sha`, (b) `.forge/failed/_steering/*/diff.patch` istnieje i
zawiera nazwę pliku. Punkt (a) jest tu równie ważny jak (b) — pilnuje, że
ratowanie nie rozszczelniło rewertu.

---

## 5. Kolejność wdrożenia i kryterium „gotowe”

| # | zakres | pliki | ryzyko | efekt |
|---|---|---|---|---|
| 1 | §3 prompt | `prompts/templates/*`, `prompts/render.py`, `__init__.py` | zerowe | usuwa przyczynę u ~90% wywołań |
| 2 | §2 retry | `orchestrate.py` | zerowe | druga próba wreszcie wie, co poprawić |
| 3 | §1 parser | `agents.py`, `orchestrate.py`, `task_pipeline.py` | średnie — jedyna zmiana dotykająca ścieżki poprawnych werdyktów | ratuje bieg mimo złego modelu |
| 4 | §4 rewert | `orchestrate.py` | niskie | zamienia stratę pracy w plik do `git apply` |

Kolejność jest celowa: 1–2 są czystym dopisaniem tekstu i mogą wejść od razu,
3 wymaga kompletu testów z §1.4, zanim dotknie `extract_json`, którym płynie
**każdy** werdykt w systemie.

**Kryterium akceptacji całości:** trzy pliki z
`.forge/failed/_bez_zadania/invalid_json/` przepuszczone przez `extract_json`
zwracają poprawne dicty z niepustym `summary`, a `bash scripts/test.sh`
(w agent-loop: `python3 -m pytest tests/`) jest zielony.

---

## 6. Czego ten plan świadomie nie robi

* **Nie zmienia routingu ról.** Naprawa formatu jest ortogonalna do pytania,
  czy GLM-5.2 ma zostać planistą — po wdrożeniu §1.3 zobaczymy w dzienniku,
  jak często warstwa naprawcza ratuje bieg, i dopiero to jest podstawa do
  decyzji o modelu.
* **Nie rusza `master_gate`** ani progu `max_bootstrap_reviews`.
* **Nie wprowadza tolerancyjnego parsera JSON5 / `demjson`** — nowa zależność
  dla trzech znaków interpunkcyjnych, przy czym akceptowałaby też błędy, które
  dziś słusznie kończą bieg (urwany wsad planisty, patrz docstring
  `_scan_json_objects`).
* **Nie usuwa rewertu** w `phase_diff_bootstrap` — §4 tylko archiwizuje pracę
  przed nim.
