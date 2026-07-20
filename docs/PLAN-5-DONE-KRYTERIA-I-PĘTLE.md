# Plan 5: naprawa bramki DONE, kryteriów i pętli mikro-TDD

Data: 2026-07-20 (rev. 2 — poprawki po przeglądzie designu). Kontynuacja PLAN-4
(Z2: `validate_criteria_map` + feedback odrzuceń). Punkt wyjścia: analiza żywych
runów na `total-battle-brothers-for-wesnoth` (m.in. V13.1, R12.1, G10.2, A7.2b3)
— porażki `mikro-TDD nieukończone (cykli=12)` przy **już zielonej implementacji**.

> **Stan:** **zaimplementowane** (T5.1–T5.6) w TDD — `tests/test_plan5.py` +
> zmiany w `orchestrate` / `prompts` / `config` / `state` / README. Diagnoza:
> `.forge/failures.md`, logi V13.1/R12.1.

---

## 0. Diagnoza (skrót z runów)

### 0.1. Objawy

| Zadanie | Objaw | Realna przyczyna |
|---------|--------|------------------|
| **V13.1** (pakiet `tbbui` + layout) | 12 cykli, NIEPOWODZENIE | c01: test+kod OK; c02–c12: same `done` odrzucane |
| **R12.1** (refaktor `_owned_settlements`) | 12 cykli, NIEPOWODZENIE | c01: `no_test` + koder + 552 passed; c02–c12: `done` reject |
| G10.2, A7.2b3, A7.2b3b | 12 cykli | ta sama klasa (DONE loop) + kruchość `test::node` |
| G10.5 | cykli=1 | **inna klasa**: koder nie zazielenił bramki (grube zadanie) |

Po failu: `_fail_task` robi **rollback do taga startu** (traci zielone commity)
i **porzuca resztę wsadu** (V13.2…, task-052…).

### 0.2. Root cause #1 — dwa źródła prawdy dla kryteriów

| Źródło | Używane przez |
|--------|----------------|
| Checkboxy w `.forge/tasks/task-NNN.md` | Prompt testera: „przepisz DOKŁADNY tekst **z pliku zadania**” |
| `task["criteria"]` z JSON planisty | `validate_criteria_map(task.get("criteria"), …)` |

Planista w JSON skraca/parafrazuje AC. Tester mapuje pełne zdania z pliku.
`_norm_criterion` wymaga równości → **0 trafień** mimo poprawnej merytorycznie
mapy. Przykład R12.1 (cykl 12): 4 justified z pliku vs 3 skróty planisty =
3× `kryterium bez ważnego pokrycia` + spalenie budżetu.

### 0.3. Root cause #2 — krucha walidacja pola `test`

- Wiele testów w jednym stringu (`a; b; c`) → nazwa „nie występuje w pliku”.
- Node-id pytest z parametrami: `test_foo[BattleResult.X]` nie ma literału w źródle.
- Feedback mylący („napisz test”), gdy AC zabrania testów (refaktor).
- Opis narracyjny w polu `test` (np. „bash scripts/test.sh → 552 passed…”) —
  „plik nie istnieje”; T5.3 leczy multi-ref i `[param]`, nie narrację (to
  nadal błąd formy mapy → reject / eskalacja).

### 0.4. Root cause #3 — brak eskalacji przy powtarzalnym reject DONE

Odrzucenie mapy zużywa cykl (`micro_cycle = c`) i wraca do testera ze
`done_reject_reasons`. **Brak** limitu serii rejectów, auto-eskalacji przy
zielonej bramce ani failu „mapa” bez 12 pełnych tur.

### 0.5. Root cause #4 — koszt failu nieproporcjonalny do błędu

Błąd formatu mapy JSON → utrata implementacji + unieważnienie wsadu.
`micro_cycle` w komunikacie bywa mylący (rośnie przy reject DONE; przy failu
kodera zostaje na ostatnim udanym commicie). Rodzic `_task_iteration` ma na
sztywno powód `mikro-TDD nieukończone (cykli=N)` — bez kanału na szczegółowy
powód z micro-pętli.

### 0.6. Zasada nadrzędna (bez zmian względem PLAN-4)

Naprawy są **mechaniczne** — egzekwowane przez orkiestrator na plikach,
stringach i kodach wyjścia. Prompt pomaga, ale bramka **nie polega** na
„proszę cytuj te same stringi”.

**Świadomy kompromis z PLAN-4 Z2:** eskalacja `review_if_green` (T5.2) to
**bezpiecznik budżetu**, nie zamiennik zaakceptowanej mapy. Poluzowuje
samocertyfikację tylko po N nieudanych mapach przy zielonej suitie; recenzent
dostaje kanon z pliku i musi się do niego odnieść (nie „approve w ciemno”).

```
P0  dwa źródła kryteriów ──▶ kanon: plik zadania (+ fallback JSON) + twardy błąd przy braku obu
P0  pętla DONE reject ─────▶ limit + eskalacja z fail_reason / outcome escalate
P1  kruchy ref testu ──────▶ multi-ref, strip [param], komunikaty (nie narracja w test=)
P1  zadania refactor ────▶ kind w kolejce + markery w body + prompt justified-first
P2  koszt failu ───────────▶ failed-ref (commit residual) + prefiksy powodów
P2  grube zadania ────────▶ prompt planisty anti-monolit + README
```

---

## 1. Design — P0

### 1.1. Jedno źródło prawdy: kryteria z pliku zadania

**Decyzja:** preferowany kanon to checkboxy z pliku `.forge/tasks/task-NNN.md`.
JSON planisty jest **fallbackiem** i diagnostyką rozjazdu — nie jedynym
źródłem przy udanym parse pliku.

#### 1.1.1. Parser checkboxów (`parse_task_criteria`)

Nowa czysta funkcja (np. w `orchestrate.py` lub `forge/taskfile.py`):

```text
Wejście: treść pliku zadania (markdown)
Wyjście: list[str] — teksty kryteriów w kolejności pliku
```

Reguły:

1. Szukaj **pierwszej** sekcji nagłówka poziomu 2 pasującej (case-insensitive)
   do: `Kryteria akceptacji`, `Kryteria`, `Acceptance criteria`.
2. Zbieraj pozycje listy zaczynające się od `- [ ]`, `- [x]`, `* [ ]`,
   `* [x]` (dopuszczalne spacje w nawiasie: `[ x ]`).
3. Kontynuacja: każda kolejna linia, która **nie** jest nowym checkboxem
   (w tym zagnieżdżone `  - …` bez `[ ]`, kontynuacje wcięte) **doklejana**
   do bieżącego kryterium; whitespace finalnie jak `_norm_criterion` przy
   porównaniu, w liście kanonicznej: `" ".join(fragment.split())` per
   kryterium (jedna linia logiczna).
4. Koniec sekcji: następny nagłówek `## ` (poziom 2) lub EOF.
   Nagłówek `###` **wewnątrz** sekcji **nie** kończy jej (traktowany jak tekst
   kontynuacji / ignorowany jeśli pusty) — w praktyce planista nie wstawia
   `###` w AC; test regresyjny na to nie jest obowiązkowy.
5. Checkboxy **poza** sekcją kryteriów — ignorowane.
6. Brak sekcji lub zero checkboxów → `[]` (obsługa: §1.1.2, nie „ciche DONE”).

**Testy obowiązkowe na żywych kształtach** (skopiowane / uproszczone z TBB):

- `task-046` (V13.1): wieloliniowe checkboxy z backticks.
- `task-033` (G10.5b): checkbox + zagnieżdżone `  - po pierwszym…`.
- `task-051` (R12.1): marker „zadanie refaktoryzacyjne” w treści AC.

#### 1.1.2. Rozwiązanie kanonu (`resolve_task_criteria`)

```text
file_crit = parse_task_criteria(body) if plik istnieje else []
json_crit = list z task["criteria"] / planisty (już w dict)

if file_crit:
    canon = file_crit
    source = "file"
    if json_crit and norms(file_crit) != norms(json_crit):
        log("PLAN/TASK: kryteria JSON ≠ plik — używam pliku")
elif json_crit:
    canon = json_crit
    source = "planner_fallback"
    log("TASK: brak checkboxów w pliku — fallback na criteria JSON planisty")
else:
    canon = []
    source = "empty"
```

Zapis w zadaniu (state):

- `task["criteria"] = canon` — zawsze to, czego używa walidator.
- `task["criteria_source"] = "file" | "planner_fallback" | "empty"` — diagnostyka;
  nie musi być w JSON planisty.

**Pusta lista (`source == "empty"`) — NIE wolno traktować jako sukces mapy.**

Obecny `validate_criteria_map([], …)` zwraca `[]` (zero kryteriów do odhaczenia
= „wszystko pokryte”). To **bug semantyki przy pustym kanonie**. Zmiana:

1. Na starcie zadania (`_start_task`): jeśli po resolve `empty` → log błędu;
   wolno kontynuować mikro-TDD (kod może powstać), ale…
2. Przy `action == "done"`: **przed** lub **wewnątrz** walidacji, jeśli
   `not criteria`: zawsze błąd  
   `"brak kryteriów akceptacji (plik bez checkboxów i pusty JSON planisty)"`  
   → mapa nie przechodzi (DONE niemożliwe bez kanonu).
3. Opcja twardsza (ta sama T5.1): natychmiast `_fail_task` / skip zadania przy
   `empty` na starcie — **nie** wybieramy jej jako default (zbyt ostra przy
   chwilowo złym markdownie); default = blokada tylko na DONE + log na starcie.
   Pokrętło opcjonalne: `FORGE_FAIL_ON_EMPTY_CRITERIA=1`.

Implementacja (1)+(2) w `validate_criteria_map` **lub** w wołającym przed
wywołaniem — preferowane w wołającym / na początku walidatora:

```python
if not criteria:
    return ["brak kryteriów akceptacji w zadaniu (kanon pusty)"]
```

Dzięki temu „pusta lista → DONE niemożliwe” staje się **prawdą w kodzie**,
nie tylko w życzeniu.

#### 1.1.3. Kiedy odczytywać

| Moment | Zachowanie |
|--------|------------|
| `_start_task` | Wczytaj plik → `resolve_task_criteria` → zapisz w `current_task` |
| Przed `validate_criteria_map` przy DONE | **Odśwież** z dysku (ten sam resolve) — plik mógł się zmienić |
| `phase_plan` po JSON | Dla każdego taska z istniejącym plikiem: resolve; skopiuj też `kind` (§2.2) |

#### 1.1.4. Spójność promptu

`write_test_prompt`:

- Mapuj **każde** kryterium = checkbox z sekcji Kryteria akceptacji w pliku.
- Orkiestrator waliduje te teksty (po znormalizowaniu spacji), nie skróty
  planisty — **o ile** parser coś znalazł; przy fallbacku JSON — teksty z JSON.
- W feedbacku rejectów: pokazywać pierwsze ~80 znaków **kanonicznego**
  kryterium, którego brakuje w mapie (nie tylko parafrazy z błędu).

`plan_batch_prompt`:

- Pole `criteria`: skopiuj **dosłownie** teksty checkboxów z pliku, który
  właśnie zapisałeś (best-effort; i tak nadpisujemy przy `source=file`).

#### 1.1.5. Testy (TDD) — skrót

- Parser: jedno-/wieloliniowe, `[x]`/`[ ]`, zagnieżdżone `  -`, brak sekcji → `[]`.
- `resolve`: plik wygrywa z JSON; JSON fallback; empty.
- `validate_criteria_map([], …)` → **niepusty** błąd (regresja semantyki).
- Fixture R12.1: justified z pełnymi tekstami z pliku → 0 błędów.
- Fixture: mapa tylko pod skróty planisty przy kanonie z pliku → MISS.

---

### 1.2. Limit i eskalacja pętli DONE-reject

**Problem:** feedback rejectów (PLAN-4) nie wystarcza, gdy źródło kryteriów
było rozjechane albo model uparcie psuje formę mapy — pali cały
`max_micro_cycles`.

#### 1.2.1. Stan i powody failu (kanał do rodzica)

| Pole State | Znaczenie |
|------------|-----------|
| `done_reject_count: int` | Liczba **kolejnych** odrzuceń DONE (reset: start zadania, `wrote_test`, **udanym code-cycle** po commit, zaakceptowana mapa DONE) |
| `done_reject_reasons: list[str]` | Powody ostatniego rejectu (PLAN-4, bez zmian) |
| `fail_reason: str` | Ustawiane w micro-pętli przed `return False` / przy eskalacji fail; **rodzic** `_task_iteration` używa go zamiast sztywnego stringa, potem czyści |

Rodzic (wymagana zmiana w T5.2):

```text
outcome = _run_micro_loop(...)
if not outcome:
    reason = state.fail_reason or f"mikro-TDD nieukończone (cykli={state.micro_cycle})"
    state.fail_reason = ""
    _fail_task(..., reason)
```

Bez `fail_reason` prefiksy `done_map:` / `coder_red:` z T5.5 są niemożliwe.

**Zwracane wartości `_run_micro_loop` (rozszerzenie):**

| Wartość | Znaczenie | `fresh_from_done` / `gate_green` |
|---------|-----------|----------------------------------|
| `"done"` | Mapa OK + bramka zielona | `True` |
| `"escalate"` | Limit rejectów mapy, policy `review_if_green`, bramka **zmierzona zielona** w eskalacji | `True` (nie marnuj drugiego full suite) |
| `"smell"` | Próg `no_test` (bez zmian) | `False` |
| `False` | Porażka micro (coder red, policy fail, micro_cap, …) | — fail |

```text
fresh_from_done = outcome in {"done", "escalate"}
# nazwa historyczna; sens: „mam świeżą zieleń bramki”
```

#### 1.2.2. Pokrętła Config

| Env | Default | Opis |
|-----|---------|------|
| `FORGE_MAX_DONE_REJECTS` | `3` | Po tylu **kolejnych** rejectach DONE → eskalacja policy |
| `FORGE_DONE_REJECT_POLICY` | `review_if_green` | `review_if_green` \| `fail` \| `continue` |
| `FORGE_FAIL_ON_EMPTY_CRITERIA` | `0` | `1` = fail zadania już na starcie przy `criteria_source=empty` |

Semantyka limitu: po inkrementacji `count >= max_done_rejects` → eskalacja.
Czyli przy default 3: reject #1, #2 jeszcze retry; reject #3 → policy.
**Każdy reject zużywa mikro-cykl** (`micro_cycle = c`) — 3 pełne tury
testera na mapę to świadomy koszt (bez osobnego „sub-cyklu mapy” w tym planie).

#### 1.2.3. Polityki eskalacji

| `FORGE_DONE_REJECT_POLICY` | Zachowanie |
|----------------------------|------------|
| `review_if_green` **(domyślna)** | Zmierz `_task_gate`. **Zielona** → `phase=review`, journal `DONE_ESCALATE after N rejects`, ustaw kontekst recenzji eskalacyjnej (§1.2.5), `return "escalate"`. **Czerwona** → `fail_reason = "done_map: mapa DONE ×N + bramka czerwona: …"`, `return False`. |
| `fail` | `fail_reason = "done_map: DONE odrzucone N razy: <skrót powodów>"`, `return False`. |
| `continue` | Jak dziś: tylko zużywaj cykle do `max_micro_cycles` (debug); bez eskalacji po limicie rejectów. |

#### 1.2.4. Przepływ przy `action == done` (fragment)

```text
canon = resolve_task_criteria(...)   # odśwież z dysku
map_errors = validate_criteria_map(canon, map, ...)
if map_errors:
    done_reject_count += 1
    done_reject_reasons = map_errors
    micro_cycle = c
    journal: "cykl N, tester: done ODRZUCONY (k=count): ..."
    if count >= max_done_rejects and policy != continue:
        apply policy (§1.2.3)
    else:
        micro_sub = test; save; continue
else:
    done_reject_count = 0
    justified_criteria = ... z mapy (jak dziś)
    gate...
    return "done" | micro_sub=code
```

Reset `done_reject_count`:

- `_start_task` / `_clear_task` → 0  
- zaakceptowana mapa DONE → 0  
- `wrote_test` (przejście do code z nowym testem) → 0  
- udany code-cycle (commit tdd) → 0  
- **`no_test` nie resetuje** (nadal utknięcie w mapowaniu / dryfie)

#### 1.2.5. Eskalacja a PLAN-4 Z2 (obowiązkowe wzmocnienie recenzji)

Eskalacja **nie** udaje zaakceptowanej mapy. Przy `outcome == "escalate"`:

1. `justified_criteria` **nie** bierze ślepo wpisów z odrzuconej mapy jako
   „pokrycie”. Zamiast tego state niesie kontekst eskalacji, np.:
   - `state.escalation_notes: list[str]` = ostatnie `map_errors` + lista
     kanonicznych AC (pierwsze ~100 znaków każdego),
   - albo rozszerzenie promptu recenzenta polami z wołającego.
2. `review_task_prompt` (zmiana w T5.2): sekcja **ESKALACJA DONE** gdy
   eskalacja aktywna:
   - kanoniczna lista kryteriów z pliku (pełne teksty lub ścieżka pliku +
     „przeczytaj sekcję Kryteria”),
   - powody odrzuceń mapy,
   - wymóg: **jawnie odnieś się do każdego kryterium z kanonu**
     (spełnione / niespełnione / wymaga zmian) — bez tego nie `approve`.
3. Journal:  
   `DONE_ESCALATE after {N} rejects; criteria_source={…}; gate=green`.
4. Po zakończeniu zadania (sukces lub fail) czyść flagę eskalacji w
   `_clear_task`.

To jest **jawne, ograniczone** poluzowanie Z2: suitę i merytorykę ocenia
recenzent w świeżym kontekście, nie tester-autor mapy po 12. próbie JSON.

#### 1.2.6. Testy

- 3 rejecty + zielona bramka + `review_if_green` → outcome `"escalate"`,
  `phase=="review"`, `fail_reason` pusty, journal z `DONE_ESCALATE`.
- 3 rejecty + czerwona → `False`, `fail_reason` z prefiksem `done_map:`.
- Policy `fail` po 2 → `False` bez review.
- Reject, potem `wrote_test` → `done_reject_count == 0`.
- Rodzic używa `state.fail_reason` w `_fail_task`.
- Prompt recenzenta przy eskalacji zawiera kanon / wymóg odniesienia do AC.

---

## 2. Design — P1

### 2.1. Walidator `test`: multi-ref, parametry pytest, komunikaty

#### 2.1.1. Format pola `test`

Akceptowane formy (`status == "covered"`):

1. `"tests/foo.py::test_bar"` — jak dziś.
2. `"tests/foo.py"` — plik bez nazwy — jak dziś.
3. **Lista JSON:** `"test": ["tests/foo.py::test_a", "tests/foo.py::test_b"]`  
   — każdy element osobno; covered tylko jeśli **wszystkie** OK.
4. **String z separatorem (awaryjnie):** split po `;` lub newline, trim,
   drop empty; każdy fragment jak (1)/(2). **Nie** szukaj całego sklejonego
   stringu w pliku.

**Komunikaty:** po udanym splocie nie ma błędu „użyj tablicy” — split **jest**
naprawą kompatybilności. Prompt i tak prosi o tablicę / osobne wpisy mapy
(lepszy styl). Jeśli **po** splocie któryś fragment nie jest ścieżką
(nie istnieje plik, wygląda na narrację) — zwykły błąd refu dla tego fragmentu.

Narracja w `test` (cały opis zamiast path) **pozostaje błędem** — nie zgadujemy.

#### 2.1.2. Strip parametrów pytest

Przed szukaniem segmentu nazwy w treści pliku:

```text
test_foo[BattleResult.DEFENDER_WIN]  →  test_foo
TestClass::test_foo[param]           →  segmenty: TestClass, test_foo
```

Dla każdego segmentu po `::`: obetnij suffix `\[[^\]]*\]` (parametry na końcu
segmentu). Nie ruszamy `[]` w środku nazwy (poza zakresem).

Istniejące zachowanie `path::Class::method` (segmenty osobno) — bez regresji
(testy PLAN-4).

#### 2.1.3. Lepsze komunikaty błędów

| Sytuacja | Komunikat |
|----------|-----------|
| Kryterium z kanonu bez wpisu | `kryterium bez pokrycia: '<pierwsze 60 znaków kanonu>…'` |
| Plik z refu nie istnieje | jak dziś + podpowiedź gdy string nie wygląda na ścieżkę |
| Nazwa nie w pliku | jak dziś (po strip param) |
| Kanon pusty | `brak kryteriów akceptacji w zadaniu (kanon pusty)` |

`write_test_prompt` przy rejectach: rozróżnij w preambule  
„błędy **formy** mapy (ref/test)” vs „brak wpisu dla kryterium z pliku”  
(na podstawie prefiksów / klasyfikacji powodów — prosta heurystyka stringowa
wystarczy).

#### 2.1.4. Testy

- G10.2-like: `path::test_name[Param.X]` + `def test_name` → OK.
- Multi-ref: lista i string z `;` → OK gdy każdy ref OK.
- Jeden zły fragment w multi → błąd.
- Sklejone bez separatora (jedna zła nazwa) → błąd jak dziś.
- Regresja Class::method z PLAN-4.

---

### 2.2. Zadania refaktoryzacyjne / „bez nowych testów”

**Uwaga priorytetu:** po T5.1 sam R12.1 (justified + teksty z pliku) przechodzi
walidację **bez** T5.4. T5.4 to higiena promptu i detekcja `kind`, nie warunek
konieczny domknięcia R12.1.

#### 2.2.1. Sygnał w zadaniu

1. Pole JSON planisty `"kind": "refactor"` — **musi** być kopiowane w
   `phase_plan` do dict zadania (dziś whitelist bez `kind` → pole ginie;
   to część T5.4 / obowiązkowy fix w orchestrate).
2. Detekcja markerów w **całym body pliku zadania** (nie tylko w criteria),
   casefold po normalizacji spacji:

```text
markers = (
  "nie dodaje nowych testów",
  "bez nowych testów",
  "zadanie refaktoryzacyjne",
)
refactor_task = (
  task.get("kind") == "refactor"
  or any(m in _norm_criterion(body) for m in markers)
)
```

Dzięki temu markery w „Poza zakresem” / „Cel” też działają; R12.1 ma marker
w checkboxie — pokryte podwójnie.

#### 2.2.2. Zachowanie

Gdy `refactor_task`:

1. Prompt testera: DONE → **preferuj `justified`** dla kryteriów strukturalnych;
   `covered` tylko na **istniejących** testach regresji; nie dodawaj testów
   prywatnego API jeśli AC tego zabrania.
2. **Bez twardego bana** na `wrote_test` w orkiestratorze (follow-up opcjonalny):
   mechaniczny revert nowych `def test_` bywa zbyt ostry (characterization
   test bywa legalny). Zostajemy przy prompcie + recenzji.
3. Walidacja mapy: bez zmian reguł justified (why ≥ 20) — kanon z pliku (T5.1)
   wystarcza do przejścia R12.1.
4. `no_test` + zielony koder: normalna ścieżka refaktoru; próg smell bez zmian.

#### 2.2.3. Testy

- `phase_plan` / budowa kolejki zachowuje `kind`.
- Detekcja markera z body „Poza zakresem”.
- Prompt z flagą refactor zawiera justified-first; zwykłe zadanie — nie.

---

## 3. Design — P2

### 3.1. Koszt porażki: artefakt failed-ref + czytelne powody

#### 3.1.1. Failed-ref (committed only)

W `_fail_task` **przed** `_reset_to_tag`:

1. Jeśli są niezacommitowane zmiany: `commit_all(project, f"wip: failed {title}")`
   (best-effort; ten sam helper co przy sukcesie cyklu). Dzięki temu failed-ref
   obejmuje pracę z `coder_red`, nie tylko ostatni zielony commit.
2. `git branch -f forge/failed/<task-id-or-slug> HEAD`  
   (slug z `task["id"]` lub skrót tytułu; `-f` nadpisuje poprzedni fail tego id).
3. `failures.md`: linia z powodem + `ref=forge/failed/…` + short sha.
4. Dopiero potem reset do `task_start_tag`.

Pokrętło: `FORGE_KEEP_FAILED_REF=1` (default on). Bez pusha (jak dotychczas
push tylko przy sukcesie zadania).

**Limitacja (udokumentować w README):** ref lokalny; nie chroni przed
`git clean` ręcznym; branch per id, nie historia wszystkich faili.

#### 3.1.2. Prefiksy powodów (`fail_reason`)

Ustawiane w miejscach failu micro/review (przez `state.fail_reason` lub
argument `_fail_task`):

| Prefiks | Kiedy |
|---------|--------|
| `done_map:` | limit mapy / policy fail / escalate przy czerwonej bramce |
| `micro_cap:` | `micro_cycle >= max_micro_cycles` bez innej przyczyny |
| `coder_red:` | koder nie zazielenił w limicie prób |
| `review:` | recenzja nie zaakceptowała / limit fix |

Log przy failu zawsze dopisuje metryki:

```text
cykli={micro_cycle} done_reject={done_reject_count} sub={micro_sub} criteria_source={…}
```

#### 3.1.3. Planista anti-monolit (prompt)

Dopisek w `plan_batch_prompt` (T5.6):

- Nie łącz w jednym zadaniu: nowej powierzchni API + testu E2E headless + docs
  „ROZSTRZYGNIĘTE” — rozbij (wzorzec G10.5 → a/b/c).
- Po `failures.md` z `coder_red:` / `micro_cap:` — następny wsad **musi** pociąć
  to zadanie drobniej.

Bez automatycznego parsera „rozmiaru” zadania.

#### 3.1.4. Metryki (opcjonalnie, niski priorytet w T5.5/T5.6)

Journal już ma wpisy reject/escalate. Opcja: wiersz w `usage` nie jest
potrzebny. Raport ręczny z `failures.md` po 2–3 biegach wystarczy do oceny P0.

---

## 4. Świadomie poza zakresem

| Temat | Dlaczego nie teraz |
|-------|---------------------|
| Zmiana domyślnego `max_micro_cycles` | Objaw; po P0 sufit chroni realne TDD |
| Wyłączenie `replan_on_failure` | Wsad oparty na sukcesie poprzednika; failed-ref taniej |
| LLM fuzzy match kryteriów | Łamie zasadę mechaniczną |
| AST / coverage jako covered | Stack-specific |
| Twardy ban `wrote_test` na refactor | Zbyt ostre; prompt + review |
| Osobny „sub-cykl mapy” bez zużycia micro_cycle | Kiedyś; dziś 3× tura to OK vs 12 |
| Fingerprint identycznych rejectów | Usunięte z designu (zbędne przy prostym limicie kolejnych) |
| Naprawa stanu repo TBB (dowiezienie V13.1) | Osobny bieg po wdrożeniu Planu 5 |
| Codex `--output-schema` dla DONE | PLAN-2 E2 |

---

## 5. Mapa zmian w kodzie

| Moduł | Zmiana |
|-------|--------|
| `forge/orchestrate.py` (+ ewent. `taskfile.py`) | `parse_task_criteria`, `resolve_task_criteria`; pusta lista = błąd przy DONE; limit DONE + outcome `escalate`; `fail_reason`; multi-ref + strip param; `kind` w kolejce; failed-ref + residual commit; prefiksy failu; rodzic czyta `fail_reason` |
| `forge/state.py` | `done_reject_count`, `fail_reason`, ewent. `escalation_notes` / flaga eskalacji; czyszczenie w `_clear_task` |
| `forge/config.py` | `max_done_rejects`, `done_reject_policy`, `keep_failed_ref`, `fail_on_empty_criteria` |
| `forge/prompts.py` | tester: kanon pliku, multi-ref, refactor justified-first; recenzent: sekcja ESKALACJA DONE; planista: dosłowne criteria, `kind`, anti-monolit |
| `tests/test_plan5.py` (+ regresja `test_plan4.py`) | parser, resolve, empty criteria, eskalacja, multi-ref, kind, fail_reason |
| `README.md` | env, kanon kryteriów, failed-ref, kompromis Z2 |

---

## 6. Kolejność implementacji

```text
T5.1  parse + resolve + puste criteria = błąd DONE     ← P0
T5.2  limit reject + escalate + fail_reason + review   ← P0
T5.3  multi-ref + strip [param] + komunikaty           ← P1
T5.4  kind w phase_plan + markery body + prompt        ← P1 (nice-to-have dla R12.1)
T5.5  keep_failed_ref + residual commit + prefiksy     ← P2
T5.6  planista anti-monolit + README (bez dublowania  ← P2
      fragmentów już dodanych w T5.4 — tylko brakujące)
```

T5.1 **przed** T5.2 w merge. T5.4 nie blokuje domknięcia R12.1 po T5.1.
T5.6 nie powtarza edycji `kind` / dosłownych criteria jeśli T5.4 już wylądował
— tylko anti-monolit + tabela env + akapit kanonu jeśli brakuje.

---

## 7. Taski implementacyjne

**Repo:** `agent-loop`.  
**Weryfikacja:** `python -m pytest tests/`.

---

### T5.1 — Kanon kryteriów z pliku + semantyka pustej listy

**Priorytet:** P0  

**Cel:** walidator i stan zadania używają `resolve_task_criteria` (plik >
JSON > empty); puste kryteria nie przechodzą DONE.

**Kryteria akceptacji:**

- [ ] `parse_task_criteria(markdown) -> list[str]` zgodnie z §1.1.1.
- [ ] `resolve_task_criteria` ustawia `criteria` + `criteria_source`
      (`file` / `planner_fallback` / `empty`) jak w §1.1.2.
- [ ] `_start_task` i odświeżenie przed DONE wołają resolve.
- [ ] `phase_plan`: po złożeniu kolejki resolve dla tasków z plikiem na dysku.
- [ ] `validate_criteria_map` (lub wołający): przy pustym `criteria` zwraca
      niepusty błąd — DONE niemożliwe; test regresji na `[], []`.
- [ ] Fixture R12.1-like: justified z pełnymi tekstami pliku → 0 błędów.
- [ ] Testy parsera na kształtach task-046, task-033, task-051 (zagnieżdżone
      bulletu, wielolinijkowe AC).
- [ ] Log przy rozjeździe JSON vs plik oraz przy fallbacku.
- [ ] Cały pakiet testów zielony.

**Ścieżki kodu:** `forge/orchestrate.py` i/lub `forge/taskfile.py`.  
**Ścieżki testów:** `tests/test_plan5.py`, ewent. `tests/test_plan4.py`.  
**Poza zakresem:** limit rejectów (T5.2), multi-ref (T5.3), `kind` (T5.4).

---

### T5.2 — Limit odrzuceń DONE, eskalacja, `fail_reason`

**Priorytet:** P0  

**Cel:** max 3 kolejne rejecty mapy; eskalacja do review przy zieleni albo
fail z prefiksem `done_map:`; rodzic nie gubi powodu.

**Kryteria akceptacji:**

- [ ] `State.done_reject_count`, `State.fail_reason` (+ ewent. kontekst
      eskalacji); reset wg §1.2.4.
- [ ] `Config.max_done_rejects` (`FORGE_MAX_DONE_REJECTS`, default 3),
      `Config.done_reject_policy` (`FORGE_DONE_REJECT_POLICY`).
- [ ] Outcome `"escalate"`; `_task_iteration` ustawia
      `gate_green` dla `done` **i** `escalate`.
- [ ] Policy `review_if_green` / `fail` / `continue` jak §1.2.3.
- [ ] `review_task_prompt`: sekcja ESKALACJA DONE z kanonem AC i wymogiem
      odniesienia do każdego kryterium (§1.2.5).
- [ ] Journal: `DONE_ESCALATE after N rejects…`.
- [ ] `_task_iteration`: `_fail_task(..., state.fail_reason or default)`.
- [ ] Testy mock-agent: eskalacja, fail przy czerwieni, policy fail, reset
      licznika po `wrote_test`.
- [ ] Cały pakiet testów zielony.

**Ścieżki kodu:** `forge/orchestrate.py`, `forge/state.py`, `forge/config.py`,
`forge/prompts.py`.  
**Ścieżki testów:** `tests/test_plan5.py`.  
**Zależności:** T5.1 (merge earlier).  
**Poza zakresem:** failed-ref (T5.5).

---

### T5.3 — Multi-ref, parametry pytest, komunikaty

**Priorytet:** P1  

**Cel:** mapy z listą testów / node-id z `[param]` przechodzą formę.

**Kryteria akceptacji:**

- [ ] `test` jako string lub lista; split `;` / newline (§2.1.1).
- [ ] Strip `[...]` na segmentach nazwy (§2.1.2).
- [ ] Komunikaty §2.1.3; prompt: tablica lub osobne wpisy, bez sklejania.
- [ ] Testy G10.2-like i multi-ref; regresja Class::method.
- [ ] Cały pakiet testów zielony.

**Ścieżki kodu:** `forge/orchestrate.py`, `forge/prompts.py`.  
**Ścieżki testów:** `tests/test_plan5.py`, `tests/test_plan4.py`.  
**Zależności:** T5.1 zalecane (kanon w komunikatach).

---

### T5.4 — `kind: refactor` + prompt justified-first

**Priorytet:** P1 (nice-to-have względem R12.1 po T5.1)  

**Cel:** planista i tester mają spójną ścieżkę refaktoru; `kind` nie ginie
z kolejki.

**Kryteria akceptacji:**

- [ ] `phase_plan` kopiuje `kind` (i nie gubi go przy resolve criteria).
- [ ] Detekcja `refactor_task` z `kind` **lub** markerów w całym body pliku
      (§2.2.1).
- [ ] `write_test_prompt` przy refactor: justified-first (§2.2.2) — **bez**
      twardego banu `wrote_test`.
- [ ] `plan_batch_prompt`: dokumentuje `"kind": "refactor"` + dosłowne
      criteria (jeśli nie zrobione wcześniej).
- [ ] Testy: kind w kolejce; marker w Poza zakresem; prompt zwykły vs refactor.
- [ ] Cały pakiet testów zielony.

**Ścieżki kodu:** `forge/orchestrate.py`, `forge/prompts.py`.  
**Ścieżki testów:** `tests/test_plan5.py`.  
**Zależności:** T5.1.

---

### T5.5 — Failed-ref, residual commit, prefiksy failu

**Priorytet:** P2  

**Cel:** po NIEPOWODZENIU lokalny branch z pracą (także WIP) + czytelne powody.

**Kryteria akceptacji:**

- [ ] Przed resetem: optional residual `commit_all`, potem
      `forge/failed/<id>` na HEAD gdy `keep_failed_ref`.
- [ ] `failures.md`: prefiks + ref + short sha.
- [ ] Miejsca failu ustawiają prefiksy `done_map:` / `coder_red:` /
      `micro_cap:` / `review:` (przez `fail_reason`).
- [ ] Log metryk: cykli, done_reject, sub, criteria_source.
- [ ] Test z mockiem git / tymczasowym repo.
- [ ] Cały pakiet testów zielony.

**Ścieżki kodu:** `forge/orchestrate.py`, `forge/config.py`.  
**Ścieżki testów:** `tests/test_plan5.py`.  
**Zależności:** T5.2 (`fail_reason`, `done_map:`).

---

### T5.6 — Planista anti-monolit + README

**Priorytet:** P2  

**Cel:** mniej monolitów typu G10.5; operator zna pokrętła.

**Kryteria akceptacji:**

- [ ] `plan_batch_prompt`: anti-monolit (§3.1.3); **nie duplikować** akapitów
      `kind`/criteria jeśli T5.4 już je dodał — tylko brakujące.
- [ ] README: `FORGE_MAX_DONE_REJECTS`, `FORGE_DONE_REJECT_POLICY`,
      `FORGE_KEEP_FAILED_REF`, `FORGE_FAIL_ON_EMPTY_CRITERIA`; akapit
      „kryteria = checkboxy w pliku (+ fallback JSON)”; nota o failed-ref
      i o eskalacji jako bezpieczniku Z2.
- [ ] Nagłówek tego PLAN-5: stan „do implementacji” do pierwszego merge kodu.
- [ ] Lekkie asercje promptu w testach jeśli dodajemy nowe stałe frazy.
- [ ] Cały pakiet testów zielony.

**Ścieżki kodu:** `forge/prompts.py`, `README.md`.  
**Ścieżki testów:** `tests/test_plan5.py` (opcjonalnie).  
**Zależności:** brak twardej; po T5.2–T5.4 sensowniej.

---

## 8. Macierz śledzenia: problem → task

| Problem z runu | Task | Uwagi |
|----------------|------|--------|
| Kryteria plik ≠ JSON (V13.1, R12.1) | **T5.1** | wystarczy na R12.1 justified |
| Puste criteria = fałszywe DONE | **T5.1** | semantyka walidatora |
| 11× done reject przy zielonej suitie | **T5.2** (+ T5.1) | escalate, nie 12 cykli |
| Multi-test / `[param]` (V13.1, G10.2) | **T5.3** | |
| Refaktor bez presji nowych testów | **T5.4** | nice-to-have po T5.1 |
| Rollback kasuje pracę; brudne drzewo | **T5.5** | residual commit + branch |
| G10.5 monolit; brak docs pokręteł | **T5.6** | |
| Ogólny `mikro-TDD nieukończone` bez klasy | **T5.2** + **T5.5** | `fail_reason` + prefiksy |

---

## 9. Kryteria sukcesu Planu 5 (po wdrożeniu)

1. **R12.1 (mentalny replay):** po **T5.1** (sam) DONE z justified w ≤2
   próbach mapy (zwykle 1). T5.4 nie jest wymagane do przejścia.
2. **V13.1:** po T5.1+T5.3 covered z osobnymi refami przechodzi; przy sklejaniu
   refów — T5.2 po 3 rejectach → `"escalate"` + review, nie 12 cykli i rollback
   bez śladu (po T5.5: jest `forge/failed/…`).
3. **Pusty plik zadania / brak AC:** DONE nie przechodzi (niepusty błąd kanonu).
4. **`failures.md`:** widać klasę (`done_map:` / `coder_red:` / …) — po T5.5;
   już po T5.2 powód failu z limitu mapy jest odróżnialny od czystego micro_cap.
5. **Regresja:** `pytest` w `agent-loop` zielone; happy-path
   test→code→done→review bez zmian przy zgodnym kanonie i poprawnej mapie.
6. **Recenzja eskalacyjna:** prompt zawiera kanon AC; approve bez odniesienia
   do kryteriów jest naruszeniem kontraktu promptu (egzekucja miękka przez
   model; nie parser werdyktu w tym planie).

---

## 10. Ryzyka i mitigacje

| Ryzyko | Mitigacja |
|--------|-----------|
| Parser nie łapie markdownu planisty | Fallback JSON + log; empty → błąd na DONE; testy na żywych plikach TBB |
| Eskalacja = backdoor self-cert | Prompt recenzenta: kanon + obowiązek odniesienia; journal DONE_ESCALATE; nie udajemy `"done"` |
| `fail_reason` zapomniany w jakimś `return False` | Default string w rodzicu; testy na głównych ścieżkach; code review checklist |
| Residual commit śmieci na failu | Jedna wiadomość `wip: failed…`; branch `-f` per id; README |
| Agent nadal wkleja narrację w `test=` | Reject formy + eskalacja; nie zgadujemy ścieżki |
| T5.6 dubluje T5.4 w promptach | T5.6 tylko brakujące akapity |
| Nadpisanie criteria z pliku ukrywa błąd planisty | Log przy rozjeździe JSON vs plik |

---

## 11. Checklist implementacji

- [x] T5.1 + testy (w tym empty criteria + żywe kształty plików)
- [x] T5.2 + testy (escalate, fail_reason, review prompt)
- [x] T5.3 + testy
- [x] T5.4 + testy
- [x] T5.5 + testy
- [x] T5.6 + README
- [x] Pełne `pytest` zielone
- [ ] (Opcja) suchy bieg / smoke na kopii TBB

---

## 12. Historia

| Data | Notatka |
|------|---------|
| 2026-07-20 | Utworzenie na podstawie logów V13.1, R12.1 i failures.md |
| 2026-07-20 | **rev. 2:** poprawki po przeglądzie — pusta lista kryteriów (fallback + błąd DONE); outcome `escalate` + `fail_reason`; eskalacja vs Z2 (kanon w review); `kind` w phase_plan; markery w całym body; residual commit przed failed-ref; usunięty fingerprint; T5.4 jako nice-to-have dla R12.1; spójność split `;` / komunikatów; testy na task-033/046/051; T5.6 bez dublowania T5.4 |
| 2026-07-20 | **rev. 3:** implementacja T5.1–T5.6 w TDD (`tests/test_plan5.py`, 265 testów zielonych) |
