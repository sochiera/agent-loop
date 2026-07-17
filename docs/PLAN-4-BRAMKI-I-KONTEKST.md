# Plan 4: uszczelnienie bramek i higiena kontekstu — projekt do implementacji

Data: 2026-07-17. Kontynuacja PLAN-2/PLAN-3.

> **Stan implementacji (2026-07-17):** E1–E3 ZAIMPLEMENTOWANE w TDD
> (214 testów zielonych; nowe w `tests/test_plan4.py`). Z1: heurystyka
> toolchainu + `test_toolchain_globs` (bootstrap/State/env), anty-osłabianie
> v2 (zbiór mechaniczny z diffu, kopiowanie toolchainu, baseline fail-open
> z logiem), reguła no_test (toolchain tylko w Ścieżkach kodu), testy cyklu
> read-only na turę kodera. Z2: `validate_criteria_map` z powodami odrzucenia
> wracającymi do testera, rola `reviewer` w świeżym kontekście z kontekstem
> mechanicznym (tag startu, pliki, toolchain, justified). Z3: rotacja sesji
> co K cykli ze startem na dzienniku, dziennik z plikami cyklu i sufitem
> z konfiguracji, czyszczenie wsadu po porażce zadania. Odstępstwa od tekstu
> planu: (1) pomiar anty-osłabiania pomijany, gdy w kopii nie ma żadnego
> pliku testowego (sam toolchain na zielonym HEAD dawałby fałszywy alarm) —
> nietknięte testy cyklu wchodzą do kopii przy zmianach toolchainu;
> (2) test blokady zapisu sprawdza bity trybu, nie `os.access` (root pisze
> mimo chmod). Do zrobienia po dostępie do żywych agentów: E4 (żywy test
> nerfu `package.json` i kryterium `justified` w prompcie recenzenta). Punkt wyjścia: przegląd repo
skonfrontowany z badaniami 2025–2026 o pętlach agentowych wskazał trzy słabe
punkty do naprawy (dwa pozostałe — sandbox/bezpieczeństwo i budżety tokenów —
świadomie odłożone, poza zakresem tego planu):

- **Z1 (reward-hacking):** bramki chronią PLIKI testowe, ale nie ŁAŃCUCH
  NARZĘDZI testowych — koder może wykastrować suitę edytując `package.json`,
  `Makefile` czy `pytest.ini`, bez dotykania żadnego testu. Bramka
  anty-osłabiania działa tylko na zmianach *zadeklarowanych* przez kodera.
  Badania: [ImpossibleBench](https://arxiv.org/abs/2510.20270) (modele
  oszukują głównie manipulując mechanizmem oceny; ukrycie/zablokowanie
  testów zbija oszustwa niemal do zera),
  [over-mocked tests](https://arxiv.org/pdf/2602.00409).
- **Z2 (samocertyfikacja DONE):** mapa kryterium→test jest przyjmowana na
  słowo testera — status `"justified"` nie wymaga treści, pole `"test"` nie
  jest sprawdzane z rzeczywistością. Recenzja całości to TA SAMA wznowiona
  sesja testera, która pisała testy — recenzent ocenia własną pracę z pełnym
  kontekstem współautora. Badania:
  [LLMs Gaming Verifiers](https://arxiv.org/pdf/2604.15149), sycophancy i
  fałszywy konsensus w
  [analizach porażek multi-agentów](https://www.augmentcode.com/guides/why-multi-agent-llm-systems-fail-and-how-to-fix-them).
- **Z3 (context rot + propagacja wsadu):** sesje testera/kodera rosną przez
  całe zadanie (do 12 mikro-cykli × dogrywki + rundy poprawek) bez kompakcji,
  a fallbackowy dziennik jest ucięty do 3000 znaków — dwie ścieżki ciągłości
  są skrajnie asymetryczne. Plan wsadowy 5 zadań w przód nie ma ścieżki
  unieważnienia: porażka zadania 1 nie kasuje zadań 2–5 zbudowanych na jego
  założeniach. Badania: [context rot / krzywa U uwagi](https://www.morphllm.com/context-rot),
  propagacja błędów wczesnych artefaktów,
  [Cognition: Don't Build Multi-Agents](https://cognition.com/blog/dont-build-multi-agents).

**Zasada nadrzędna (filar forge, bez zmian):** naprawy są MECHANICZNE —
egzekwowane przez orkiestrator na diffach, plikach i kodach wyjścia, nie przez
dopisywanie próśb do promptów. Prompt może pomagać, ale bramka nie może na nim
polegać.

```
Z1  koder nerfuje toolchain ──▶ globy toolchainu + rozszerzona bramka
                               anty-osłabiania (testy ∪ toolchain, z baseline)
Z2  tester sam się odhacza ──▶ walidator criteria_map (istnienie, ścieżka,
                               nazwa testu, niepuste why) + recenzent w świeżym
                               kontekście bez sesji i dziennika
Z3  sesje puchną, wsad żyje ─▶ rotacja sesji co K cykli + bogatszy dziennik
    własnym życiem              + kasowanie kolejki wsadu po porażce zadania
```

---

## 1. Z1: ochrona łańcucha narzędzi testowych

### 1.1. Definicja toolchainu (nowe pojęcie w bramkach)

**Plik toolchainu** = plik konfigurujący, CO i JAK uruchamia `test_cmd`
(runner, lista testów, skrypty). Dwa źródła prawdy, sumowane:

1. **Deklaracja bootstrapu** — nowe pole profilu w JSON bootstrapu i State:
   `test_toolchain_globs` (jak `verify_test_globs`). Bootstrap zna stack,
   więc wie, że np. `npm test` czyta `package.json`, a `ctest` —
   `CMakeLists.txt`. Prompt bootstrapu dostaje akapit z prośbą o deklarację.
2. **Wbudowana heurystyka** `_looks_like_toolchain(path)` — konserwatywna
   lista znanych nazw, działająca też na starych STATE.json bez deklaracji:
   `package.json`, `pyproject.toml`, `setup.cfg`, `pytest.ini`, `tox.ini`,
   `Makefile`, `CMakeLists.txt`, `jest.config.*`, `vitest.config.*`,
   `build.gradle*`, `pom.xml`, `Cargo.toml`, `noxfile.py`.

Uwaga rozgraniczająca: `conftest.py` i pliki fixture pozostają plikami
TESTOWYMI (własność testera), nie toolchainem — zawierają specyfikację,
nie konfigurację runnera.

### 1.2. Zasady per rola

- **TESTER**: pliki toolchainu NIE są plikami testowymi → edycja podpada pod
  `tester_path_violations` (wycofanie jak dziś dla nie-testów). Wymaga
  poprawki: heurystyka `_looks_like_test` musi jawnie WYKLUCZAĆ dopasowania
  toolchainowe, zanim zadziała dopasowanie po nazwie.
- **KODER**: edycja toolchainu jest LEGALNA (dodanie zależności to normalna
  praca), ale każda zmiana pliku toolchainu — wykryta mechanicznie po
  globach, bez wymogu deklaracji — wchodzi do rozszerzonej bramki
  anty-osłabiania (1.3). W cyklu **bez testu** (`no_test`) zmiany toolchainu
  są wycofywane, chyba że plik pasuje do `code_globs` zadania (jawna,
  widoczna w recenzji decyzja planisty) — w kroku bez czerwonego testu nie
  ma mechanicznego sposobu odróżnienia nerfu od legalnej zmiany.
- **WERYFIKATOR/recenzent**: bez zmian (kontrola diffu już istnieje).

### 1.3. Rozszerzona bramka anty-osłabiania (`anti_weakening_ok` v2)

Dziś: kopiuje ZADEKLAROWANE pliki testowe do worktree na HEAD; zieleń =
rozwodnienie. Trzy zmiany:

1. **Zbiór wejściowy liczony mechanicznie**, nie z deklaracji kodera:
   `(zmienione pliki testowe wg _is_test_path) ∪ (zmienione pliki toolchainu)`
   względem HEAD. Deklaracja `test_changes` zostaje — jako kontekst dla
   recenzji, nie jako bramka.
2. **Do worktree na HEAD kopiowane są testy ORAZ toolchain** w bieżących
   wersjach. Bramka zielona na kodzie sprzed cyklu ⇒ testy rozwodnione ALBO
   toolchain znerfowany (np. `package.json` z `"test": "true"` skopiowany do
   worktree sprawia, że suita „przechodzi" bez implementacji — złapane).
   Legalny przypadek przechodzi: nowa zależność w `package.json` + czerwony
   test nadal failuje na HEAD (implementacji brak) ⇒ bramka czerwona ⇒ OK.
3. **Baseline sanity-check** (naprawa istniejącej luki fail-open): przed
   właściwym pomiarem bramka w worktree uruchamiana jest RAZ bez żadnych
   kopii. Jeśli baseline nie jest zielony (worktree nie ma `node_modules`,
   artefaktów builda itd.), pomiar jest ŚRODOWISKOWO niemiarodajny — dziś
   taka czerwień cicho przepuszcza check. v2: baseline czerwony → check
   pominięty Z LOGIEM (świadomy fail-open, widoczny w historii), a decyzja
   spada na recenzję, która dostaje listę zmian toolchainu (1.4).

Werdykt „osłabiono": wycofanie jak dziś — testy cyklu ze snapshotu,
toolchain przez `revert_paths` (checkout z HEAD), ponowna bramka, czerwień =
porażka zadania.

### 1.4. Zmiany toolchainu jawnie w recenzji

Orkiestrator liczy diff toolchainu za CAŁE zadanie
(`git diff --name-only <task_start_tag>` przefiltrowany globami) i wstrzykuje
listę do promptu recenzenta z twardym pytaniem: „czy te zmiany konfiguracji
testów są uzasadnione zadaniem i nie zawężają suity?". Recenzent (po Z2 —
świeży kontekst) jest drugą linią; pierwszą jest 1.3.

### 1.5. Testy read-only na czas tury kodera (tani deterrent)

Zgodnie z wynikiem ImpossibleBench (ukrycie/zablokowanie testów niemal
zeruje oszustwa): przed wywołaniem kodera pliki testowe cyklu (znane ze
snapshotu) dostają `chmod a-w`; po turze uprawnienia wracają. To NIE jest
bariera bezpieczeństwa (agent może zdjąć atrybut) — to redukcja odruchowych
edycji; właściwą bramką pozostaje kontrola diffu. Pokrętło
`FORGE_LOCK_TESTS` (domyślnie włączone). Windows: best-effort, błąd chmod
ignorowany.

### 1.6. Świadomie POZA zakresem Z1

- Special-casing w kodzie produkcyjnym (hardkod pod konkretne asercje) —
  mechanicznie wykrywalny tylko testami held-out/randomizowanymi
  ([Capped Evaluation](https://arxiv.org/pdf/2606.07379)); kandydat na
  PLAN-5, dziś łapie to wyłącznie recenzja.
- Over-mocking — wykrywanie atrap to zadanie recenzenta (prompt już pyta);
  mechanizacja wymagałaby analizy AST per stack, wbrew stack-agnostyczności.

---

## 2. Z2: koniec samocertyfikacji DONE + niezależna recenzja

### 2.1. Walidator mapy kryteriów (`validate_criteria_map` — czysta funkcja)

Zastępuje boolowskie `criteria_fully_mapped` funkcją zwracającą **listę
powodów odrzucenia** (pusta = OK). Dla każdego wpisu:

- `status == "covered"`: pole `test` w formacie `ścieżka` albo
  `ścieżka::nazwa`. Mechanicznie: (a) plik ISTNIEJE w projekcie, (b) jest
  ścieżką testową zadania (`_is_test_path` — te same globy co bramki),
  (c) jeśli podano `::nazwa` — nazwa występuje w treści pliku (substring;
  tanie, stack-agnostyczne). Nazwa zmyślonego testu przestaje przechodzić.
- `status == "justified"`: pole `why` niepuste i ≥ 20 znaków. Wpisy
  justified NIE znikają: są zbierane i przekazywane recenzentowi (2.3) do
  rozstrzygnięcia — mechanika sprawdza formę, człowiek-zastępca (recenzent
  w świeżym kontekście) merytorykę.
- Inne statusy / brak kryterium z listy zadania: odrzucenie jak dziś
  (odhaczamy kryteria, nie liczymy wpisów — bez zmian).

### 2.2. Odrzucenie DONE z feedbackiem, nie w ciemno

Dziś tester dostaje kolejny cykl bez informacji, CZEMU mapa padła. Zmiana:
powody z walidatora trafiają do nowego stanu `done_reject_reasons`
(State, czyszczone po następnym werdykcie) i są doklejane do kolejnego
`write_test_prompt` („poprzednia mapa odrzucona z powodów: …"). Bez tego
bounded-retry pali cykle na zgadywanie.

### 2.3. Recenzent w świeżym kontekście (rozdzielenie autora od sędziego)

Zmiana w `_run_review_loop`: recenzja przestaje być wywołaniem
`_session_call(..., "tester", ...)`. Nowa rola w `Config.role("reviewer")`:

- **Agent/model/effort**: `FORGE_REVIEWER_AGENT/MODEL/EFFORT`; domyślnie
  agent testera (koszt bez zmian), ale wywołanie przez `run_agent` —
  BEZ sesji i BEZ dziennika zadania. Dziennik to narracja testera/kodera
  („uważam, że done") — podawanie go recenzentowi to wektor sugestii,
  dokładnie ten mechanizm sycophancy, który opisują badania.
- **Kontekst recenzenta budowany mechanicznie przez orkiestrator**, nie
  przez współautorów: plik zadania, NAZWA TAGA startu zadania (dziś prompt
  każe robić „diff względem punktu startu", ale nie mówi, jaki to ref —
  naprawiamy: `git diff {task_start_tag}`), lista plików zmienionych w
  zadaniu, lista zmian toolchainu (1.4), lista kryteriów `justified` z ich
  `why` (2.1) z wymogiem jawnego odniesienia się do każdego.
- **Poprawki po recenzji bez zmian**: `fix_review` zostaje w sesji kodera.
- README: zalecenie dywersyfikacji (`FORGE_REVIEWER_AGENT=claude` przy
  koderze-codeksie) — skorelowane ślepe punkty tego samego modelu to
  udokumentowany problem; default zostaje ekonomiczny, decyzja u człowieka.

Konsekwencja dla pętli: `_run_review_loop` traci parametr sesyjny recenzji;
`gate_green` z DONE działa jak dziś (bramka jest orkiestratora, nie
recenzenta).

---

## 3. Z3: higiena kontekstu i unieważnianie wsadu

### 3.1. Rotacja sesji ról co K mikro-cykli

Nowe pokrętło `FORGE_SESSION_ROTATE_CYCLES` (domyślnie 6 — połowa sufitu
mikro-cykli; 0 = wyłączone). Po K ukończonych cyklach zadania orkiestrator
zeruje `tester_session`/`coder_session`; następne wywołanie roli startuje
świeżą sesję **z dziennikiem zadania jako kontekstem**. Wymaga drobnej
zmiany w `_session_call`: dziś dziennik jest doklejany tylko agentom bez
resume albo po UTRACIE sesji — po zmianie także przy świadomym starcie
świeżej sesji w zadaniu w toku (`micro_cycle > 0`).

Uzasadnienie: degradacja jakości zaczyna się na długo przed zapełnieniem
okna (krzywa U); rotacja wymienia „spuchnięty, sprzeczny" kontekst na zwięzły
zapis stanu. Koszt: utrata cache sesji raz na K cykli — kompensowana
krótszym wejściem.

### 3.2. Dziennik zadania: bogatszy i dłuższy (symetria ścieżek ciągłości)

- Sufit `journal_tail`: 3000 → pokrętło `FORGE_JOURNAL_TAIL_CHARS`,
  domyślnie 8000. Pełny dziennik i tak żyje na dysku.
- Wpisy wzbogacone MECHANICZNIE (zero tokenów): przy commicie każdego cyklu
  orkiestrator dopisuje listę plików zmienionych w cyklu (zna ją z
  `changed_files`); przy DONE — skrót mapy kryteriów. Dziennik przestaje
  być tylko „cykl 3, koder: zielony" i staje się faktycznym zapisem stanu,
  zdatnym do odtworzenia kontekstu po rotacji/utracie.

### 3.3. Porażka zadania unieważnia resztę wsadu

Wsad planowany jest przy założeniu, że zadania wykonują się po kolei —
porażka zadania N falsyfikuje wejście zadań N+1… Zmiana w `_fail_task`:
przy `FORGE_REPLAN_ON_FAILURE` (domyślnie włączone) kolejka `task_queue`
jest czyszczona, a do `failures.md` trafia wpis z listą id porzuconych
zadań. Następna iteracja planuje od nowa — planista widzi failures.md,
BACKLOG i pliki `.forge/tasks/` porzuconych zadań, więc tanio je
przeplanowuje (zwykle: pocięcie padłego zadania + korekta następnych).
Sukces zadania NICZEGO nie unieważnia (założenia się potwierdziły) —
pełny re-plan po każdym zadaniu zjadłby cały zysk wsadu.

### 3.4. Świadomie POZA zakresem Z3

- Kompakcja sesji przez podsumowania LLM (koszt tokenowy; dziennik
  mechaniczny 3.2 daje większość zysku za darmo).
- Zmniejszenie domyślnego wsadu (5 zostaje; 3.3 czyni koszt błędnego wsadu
  ograniczonym).
- Mid-batch reprioritization na sygnałach innych niż porażka (np. wynik
  recenzji) — do rozważenia po żywych danych.

---

## 4. Zmiany w kodzie (mapa)

| Moduł | Zmiana |
|---|---|
| `orchestrate.py` | `_looks_like_toolchain`, wykluczenie toolchainu z `_looks_like_test`; `anti_weakening_ok` v2 (zbiór mechaniczny, kopiowanie toolchainu, baseline); reguła no_test dla toolchainu w `_run_micro_loop`; lock/unlock testów wokół tury kodera; `validate_criteria_map` zamiast `criteria_fully_mapped` + `done_reject_reasons`; `_run_review_loop` → `run_agent` dla recenzji + mechaniczny kontekst; rotacja sesji w `_run_micro_loop`; czyszczenie kolejki w `_fail_task`; wzbogacone wpisy dziennika |
| `state.py` | `test_toolchain_globs`, `done_reject_reasons`, `justified_criteria` (czyszczone w `_clear_task`) |
| `config.py` | rola `reviewer` w `role()` (+ `agents_in_use`), `FORGE_REVIEWER_*`, `FORGE_SESSION_ROTATE_CYCLES`, `FORGE_JOURNAL_TAIL_CHARS`, `FORGE_LOCK_TESTS`, `FORGE_REPLAN_ON_FAILURE` |
| `prompts.py` | bootstrap: deklaracja `test_toolchain_globs`; `write_test_prompt`: powody odrzucenia DONE; `code_and_refactor_prompt`: zasada o toolchainie; `review_task_prompt`: tag startu, listy zmian/toolchainu/justified |
| `agents.py` | bez zmian (rola reviewer przechodzi istniejącym `run_agent`) |
| `README.md` | nowe pokrętła + zalecenie dywersyfikacji modelu recenzenta |

Migracja STATE.json: wszystkie nowe pola mają defaulty — stare stany
wczytają się bez zmian (istniejący mechanizm `State.load`).

## 5. Pokrętła (nowe)

| Co | Domyślnie | Env |
|---|---|---|
| Globy toolchainu (dodatkowe, poza deklaracją bootstrapu) | heurystyka wbudowana | `FORGE_TOOLCHAIN_GLOBS` (CSV, doklejane) |
| Blokada zapisu testów na turę kodera | włączona | `FORGE_LOCK_TESTS=0` |
| Agent/model/effort recenzenta | agent testera, świeży kontekst | `FORGE_REVIEWER_AGENT/MODEL/EFFORT` |
| Rotacja sesji ról co K cykli | 6 (0 = wył.) | `FORGE_SESSION_ROTATE_CYCLES` |
| Sufit skrótu dziennika | 8000 znaków | `FORGE_JOURNAL_TAIL_CHARS` |
| Re-plan po porażce zadania | włączony | `FORGE_REPLAN_ON_FAILURE=0` |

## 6. Etapy implementacji (TDD, jak PLAN-3)

- **E1 — czyste bramki (bez agentów):** `_looks_like_toolchain` + poprawka
  `_looks_like_test`; `anti_weakening_ok` v2 z baseline;
  `validate_criteria_map`. Testy: nerf `package.json` łapany, legalna
  zależność przechodzi, baseline czerwony = pominięcie z logiem, zmyślona
  nazwa testu odrzucona, `justified` bez `why` odrzucone.
- **E2 — przewiązanie pętli:** rola reviewer (Config + `_run_review_loop`),
  `done_reject_reasons`, rotacja sesji, dziennik (sufit + wpisy), reguła
  no_test dla toolchainu, czyszczenie kolejki w `_fail_task`, lock testów.
  Testy: pętla recenzji nie używa sesji testera (mock), rotacja zeruje
  sesje i dokleja dziennik, porażka czyści kolejkę, wznawialność faz
  nienaruszona (istniejąca suita `test_new_model` musi przejść po
  aktualizacji oczekiwań).
- **E3 — prompty + README:** deklaracja toolchainu w bootstrapie, feedback
  odrzuconego DONE, kontekst recenzenta, dokumentacja pokręteł.
- **E4 — żywy test:** pełne zadanie z próbą nerfu (ręcznie podłożony
  `package.json` z `"test": "true"` w środku cyklu) — bramka musi wycofać;
  zadanie z kryterium `justified` — recenzent musi je dostać w prompcie.

## 7. Ryzyka szczątkowe (uczciwie)

1. Koder nadal może hardkodować implementację pod asercje — łapie to tylko
   recenzja (mocniejsza po Z2, ale to wciąż LLM). Właściwa mechanika
   (testy held-out/randomizowane) to osobny plan.
2. `chmod` nie powstrzyma agenta, który świadomie chce edytować testy —
   bramką pozostaje diff; lock redukuje tylko przypadki odruchowe.
3. Baseline anty-osłabiania czerwony (środowisko niepowtarzalne w worktree)
   = świadomy fail-open — ale teraz logowany i widoczny, z recenzją jako
   drugą linią; wcześniej był cichy.
4. Domyślny recenzent to wciąż ten sam MODEL co tester (świeży kontekst
   usuwa wspólną sesję, nie wspólne ślepe punkty) — dywersyfikacja modelu
   pozostaje decyzją kosztową użytkownika.
5. Heurystyka toolchainu nie pokryje egzotycznych stacków — dlatego jest
   sumowana z deklaracją bootstrapu i nadpisywalna env.
