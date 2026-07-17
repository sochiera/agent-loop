# Plan 3: faza weryfikacji celu (CI + real hardware) — projekt do implementacji

Data: 2026-07-17 (rewizja 3 — domknięte trzy słabe punkty rewizji 2:
rozjazd środowisk naprawy, brak zbieżności werdyktu, niebramkow ane
klasyfikacje i luka anty-osłabiania).

> **Stan implementacji (2026-07-17):** E-V0 + mechanika E-V1/E-V2
> ZAIMPLEMENTOWANE w TDD (167 testów zielonych): `verify_ledger.py`,
> `verify.py`, `shellrun.py`, profil w bootstrapie, faza `verify_goal`
> (PASS/stall/env/limity, wznawialność), zadania z bramką repro, chronione
> ścieżki, early-warn CI, `--mcp-config` weryfikatora, raport, README.
> Odstępstwa od tekstu planu: (1) artefakty weryfikacji żyją w
> `.forge/verification/` — katalog `.forge/` jest w `.gitignore` projektu
> (konwencja „repo czyste od metadanych narzędzia"), więc commit feedbacku
> jest best-effort; pamięć między cyklami działa z dysku + STATE.json;
> (2) walidacja profilu w `smoke --dry` — odłożona (preflight i bootstrap
> walidują twardo). Do zrobienia po dostępie do żywych agentów: E-V1 żywy
> test na repo z workflow, E-V2 na fizycznym targecie, kalibracja E-V3.
>
> **Po review (5 znalezisk, naprawione w TDD):** ochrona ścieżek nie wycofuje
> już tworzenia testów targetowych (wyjątki: pliki cyklu + nowe pliki
> `verify_test_globs`); `FORGE_VERIFY_TARGETS` działa też po bootstrapie
> (nadpisanie w miejscu użycia, w tym porzucenie wznowionej fazy);
> chroniony jest także skrypt wywoływany bezpośrednio (tokens[0]);
> weryfikator dostał kontrolę diffu (zmiany poza docs/BACKLOG wycofywane);
> zdegradowane/odrzucone problemy są terminalne w rejestrze, a postęp
> liczony na surowych statusach agenta (degradacja ≠ naprawa). Znane,
> świadomie odłożone: --legacy ze STATE w fazie verify_goal robi zwykły
> rollback (mieszanie trybów), mylący log przy DONE z wyczerpanym sufitem repro.

**Koncepcja:** weryfikacja NIE jest wpleciona w każde zadanie. Gdy planista
orzeknie `no_more_tasks`, do gry wchodzi **nowy agent (weryfikator-QA)**,
który sprawdza całość na CI i prawdziwym hardware. Porażka = ustrukturyzowany
raport + **rejestr problemów** → planista planuje zadania naprawcze → cały
cykl od nowa, aż PASS albo brak postępu.

```
pętla zadań (jak dziś) → planista: no_more_tasks
        │
        ▼
  WERYFIKACJA CELU (cykl N)
   ├─ orkiestrator zbiera dowody mechanicznie (CI/hardware/smoke, 0 tokenów)
   ├─ agent QA bada dowody i produkt (gh/MCP/serial przez zadeklarowane skrypty)
   └─ werdykt = REJESTR PROBLEMÓW (id, status, klasa, repro, dowód)
        PASS → koniec pracy 🎉        PASS-z-notatkami → koniec + wpisy do BACKLOG
        FAIL → feedback.md + repro-skrypty → planista → zadania naprawcze
               (bramkowane własnym repro_cmd, nie tylko lokalną suitą)
               → pętla zadań → weryfikacja (cykl N+1)
        stop: brak postępu 2 cykle z rzędu | potwierdzony env_issue | sufit cykli
```

Dlaczego ten krój (a nie weryfikacja per zadanie): jedna nowa faza zamiast
czterech wplecionych w każde zadanie; koszt CI/hardware płacony per cykl celu;
weryfikator to QA patrzący na całość świeżym okiem; feedback do planisty to
istniejący mechanizm (jak `failures.md`). Świadoma cena: późne wykrycie —
mitygowane wczesnym ostrzeganiem (sekcja 9) i bramkami per zadanie, które
zostają pierwszą linią obrony.

---

## 1. Filary (niezmienne zasady forge)

1. **Stack-agnostyczność przez deklarację** — sposób weryfikacji deklaruje
   bootstrap w STATE.json; człowiek nadpisuje env/flagami. Forge zna tylko
   kody wyjścia komend.
2. **Pamięć w repo** — feedback i repro-skrypty żyją w
   `.forge/verification/`, commitowane; kolejne cykle czytają poprzednie.
3. **Bramki mechaniczne poza modelem** — PASS, postęp między cyklami,
   klasyfikacje i ochrona plików weryfikacji są egzekwowane przez
   orkiestrator na parsowalnych strukturach, nie na dobrej woli promptu.
4. **Wypchnięta historia nienaruszalna** — weryfikacja startuje po pushu
   wszystkiego; naprawy to nowe zadania i commity naprzód, zero rollbacków.

## 2. Profil weryfikacji w STATE.json

Bootstrap (rozszerzony prompt) deklaruje obok `test_cmd`/`run_cmd`:

```json
"verify": {
  "targets": ["ci", "hardware", "smoke"],
  "smoke_cmd":  "<dymny bieg produktu; rc==0 = OK>",
  "flash_cmd":  "<hardware: wgranie na target>",
  "target_cmd": "<hardware: testy na targecie; rc==0 = OK, stdout = log>",
  "probe_cmd":  "<hardware, opcjonalne: czy urządzenie podpięte (preflight)>",
  "ci_status_cmd": "<ci: status checków dla {sha}; rc: 0=zielono,1=czerwono,2=trwa>",
  "ci_logs_cmd":   "<ci: log porażek dla {sha} na stdout>",
  "verify_test_globs": ["<globy testów wykonywanych na targecie/w CI,
                          np. tests/hil/**, .github/workflows/**>"]
}
```

- `targets` wybiera bootstrap (konfig CI w repo → `ci`; brief o
  płytce/firmware → `hardware`; `smoke` zawsze warto). Nadpisanie:
  `FORGE_VERIFY_TARGETS=ci,hardware` / `--verify-targets`; `none` = wyłącznik
  (zachowanie bit-w-bit dzisiejsze).
- Kontrakt komend jak `test_cmd`: pojedyncza komenda bez operatorów powłoki,
  `{sha}` rozwija orkiestrator. Provider CI wymienny.
- **`verify_test_globs`** (nowe względem rew. 2): ścieżki testów, które
  wykonują się w środowisku weryfikacji (na targecie, w CI), a NIE w lokalnej
  suicie — objęte ochroną anty-osłabiania (sekcja 8). Bootstrap deklaruje,
  planista może rozszerzać w zadaniach tworzących takie testy.
- Pola płasko w `State`; walidacja w `phase_bootstrap` (target bez swoich
  komend = błąd). Stare STATE.json migrują na `verify_targets=[]` + log.
- Preflight trybu `hardware` odpala `probe_cmd` — brak płytki wychodzi przy
  starcie pętli, nie w środku nocy.

## 3. Przebieg fazy weryfikacji

### 3.1. Wejście

Dziś `no_more_tasks` kończy pętlę. Po zmianie: przy niepustych
`verify_targets` → `state.phase = "verify_goal"`, `state.verify_cycle += 1`.
Pętlę kończy PASS albo warunki stopu (sekcja 6.3).

### 3.2. Dowody mechaniczne (0 tokenów)

Orkiestrator zbiera materiał zanim zawoła agenta:

- `ci`: polling `ci_status_cmd {sha}` dla HEAD z backoffem (start 30 s, sufit
  5 min, `FORGE_CI_TIMEOUT` domyślnie 45 min); czerwono → `ci_logs_cmd` do
  `.forge/verification/cycle-N/ci.log`.
- `hardware`: `flash_cmd` (retry `FORGE_FLASH_RETRIES`, start 1) →
  `target_cmd` z timeoutem; pełne wyjście do `cycle-N/hardware.log`.
- `smoke`: `smoke_cmd` → `cycle-N/smoke.log`.

Wyniki per target: `{"rc": int, "log": "<ścieżka>"}` — trafiają do promptu
agenta i do bramki PASS. Czekanie na CI dzieje się w orkiestratorze za darmo.

### 3.3. Agent weryfikator-QA

Rola konfigurowana jak pozostałe (`--verifier-agent/-model/-effort`,
`FORGE_VERIFIER_*`; domyślnie agent planisty — to zadanie mocnego modelu).
Zawsze **świeży kontekst**: ocenia produkt i dowody, nie intencje z budowy.

Prompt (`prompts.verify_goal_prompt`) dostaje: ścieżki briefu/DESIGN.md,
profil weryfikacji, wyniki mechaniczne (rc + ścieżki logów), ścieżkę
rejestru problemów z poprzedniego cyklu (`cycle-(N-1)/problems.json`).
Zadania agenta:

1. Zbadać dowody; w razie potrzeby drążyć samodzielnie: ponowić `target_cmd`,
   obejrzeć joby CI (`gh` albo MCP — sekcja 10), uruchomić produkt
   (`run_cmd`) i skonfrontować zachowanie z DESIGN.md.
2. **Zaktualizować rejestr problemów** (sekcja 4): każdy problem z cyklu N-1
   oznaczyć `resolved`/`persisting`; nowe dodać jako `new`.
3. Dla każdego problemu naprawialnego kodem napisać **skrypt reprodukcji**
   `cycle-N/repro/<id>.sh` (sekcja 5.1).
4. Przy porażce napisać obszerny `cycle-N/feedback.md` (narracja dla
   planisty: objawy, cytaty z logów, hipotezy, proponowany podział na
   zadania, porównanie z cyklem N-1).
5. Zwrócić werdykt JSON (sekcja 4) — rejestr jest częścią werdyktu.

## 4. Rejestr problemów — struktura werdyktu

Serce rewizji 3: werdykt to nie „pass/fail + esej", tylko **parsowalny
rejestr**, na którym orkiestrator egzekwuje postęp, klasy i ochronę plików:

```json
{"verdict": "pass | fail",
 "problems": [
   {"id": "P-007",
    "status": "new | persisting | resolved",
    "class": "code_bug | verify_defect | env_issue | flaky | design_gap",
    "title": "<1 zdanie>",
    "target": "ci | hardware | smoke | behavior",
    "evidence": "<ścieżka:linie w logu / komenda z rc>",
    "repro_cmd": "bash .forge/verification/cycle-N/repro/P-007.sh",
    "criterion": "<dla design_gap: DOSŁOWNY cytat kryterium z DESIGN.md>"}
 ]}
```

Reguły (bramki orkiestratora, `verify_ledger.py` — czysta logika, testowalna
bez agentów):

- **Trwałe id.** Problem raz nazwany zachowuje id między cyklami; rejestr
  poprzedniego cyklu jest wejściem, więc agent odhacza, a orkiestrator
  weryfikuje kompletność: każdy problem otwarty w N-1 MUSI wystąpić w N ze
  statusem `resolved` albo `persisting` (jak `criteria_fully_mapped` przy
  DONE). Rejestr niekompletny → werdykt odrzucony, jedno ponowienie wywołania.
- **Klasy o skutkach mechanicznych:**
  - `code_bug` — trafia do planisty jako zadanie naprawcze; wymaga
    `repro_cmd`.
  - `verify_defect` — usterka samej weryfikacji (zły workflow, kruchy skrypt
    flash); JEDYNA klasa odblokowująca edycję chronionych ścieżek (sekcja 8).
  - `env_issue` — świat fizyczny/sekrety; NIGDY nie staje się zadaniem
    kodowym; obsługa w sekcji 7.
  - `flaky` — powtórka za darmo; nawrót w kolejnym cyklu → problem
    pełnoprawny (stabilizacja jako zadanie).
  - `design_gap` — zastrzeżenie behawioralne przy zielonych rc; **ważne
    tylko z polem `criterion`, którego znormalizowany tekst występuje w
    docs/DESIGN.md** (podciąg po normalizacji whitespace/case — mechanicznie,
    jak `_norm_criterion`). Bez tego orkiestrator degraduje problem do
    nieblokującej notatki. To zamyka „ruchome bramki": świeży agent nie może
    blokować PASS ocennym widzimisię, tylko niespełnionym, zapisanym
    kryterium.

## 5. Naprawa: zadania z własną bramką reprodukcji

Domknięcie rozjazdu środowisk (słaby punkt nr 1): usterka znaleziona na
CI/targecie dostaje **wykonywalną reprodukcję**, która staje się bramką
zadania naprawczego — lokalna suita przestaje być jedynym sędzią.

### 5.1. Kontrakt repro-skryptu

- `bash .forge/verification/cycle-N/repro/<id>.sh` — rc≠0 = bug obecny,
  rc==0 = naprawiony. Pojedyncza komenda (uruchamiana przez `_run_shellfree`).
- Skrypt może używać zadeklarowanych komend profilu (flash+target z filtrem
  na konkretny test, `gh` dla CI, kontener imitujący runnera) — pisze go
  weryfikator, który właśnie diagnozował problem, więc wie, co odtwarza.
- **Walidacja przy odbiorze werdyktu:** orkiestrator uruchamia każdy nowy
  `repro_cmd` — MUSI być czerwony (bug wszak istnieje). Zielony repro =
  odrzucony problem (dowód nie odtwarza usterki) → notatka, nie zadanie.
  To samo „czerwone najpierw", które pilnuje testera.
- Repro bywa drogi (flash). Akceptowalne: walidacja przy odbiorze + bramka
  w zadaniu naprawczym to 2 wykonania minimum; sufit
  `FORGE_MAX_REPRO_RUNS_PER_TASK` (start 6) chroni sprzęt i czas.

### 5.2. Zadania naprawcze w pętli mikro-TDD

Planista, czytając feedback + rejestr, tworzy zadania z nowymi polami:

```json
{"id": "task-021", "fixes": "P-007",
 "repro_cmd": "bash .forge/verification/cycle-3/repro/P-007.sh", ...}
```

Zmiany w pętli zadania, gdy `repro_cmd` obecny:

- **Start zadania:** orkiestrator odpala repro — musi być czerwony (inaczej
  problem już nie występuje → zadanie zamykane jako bezprzedmiotowe, wpis do
  rejestru cyklu jako `resolved` z adnotacją).
- **Tester:** pisze lokalny test regresji, jeśli się DA (najlepszy wynik);
  gdy natura błędu na to nie pozwala, `no_test` w zadaniu z repro **nie
  nalicza smellu** — specyfikacją jest repro (flaga w `_run_micro_loop`).
- **Bramka zielona zadania:** `test_cmd` zielony **i** `repro_cmd` zielony
  (rozszerzenie `run_gate` o drugi człon dla zadań z repro). Koder dostaje
  ogon wyjścia repro przy czerwieni, jak dziś ogon testów.
- **DONE testera:** mapa kryteriów jak dziś; dla zadania z repro orkiestrator
  dodatkowo wymaga zielonego repro (mechanicznie, nie z mapy).

Efekt: potwierdzenie naprawy przychodzi w zadaniu (minuty), nie w następnym
pełnym cyklu (godziny). Pełna weryfikacja cyklu N+1 potwierdza integrację.

## 6. PASS, postęp i warunki stopu

### 6.1. Bramka PASS

Orkiestrator przyjmuje `pass` tylko gdy JEDNOCZEŚNIE:

- wszystkie targety z 3.2 mają rc==0 (agent nie przegłosuje czerwonego rc),
- rejestr nie zawiera problemów blokujących (`code_bug`/`verify_defect`/
  `design_gap`-z-kryterium o statusie ≠ `resolved`).

Agent może sfailować mimo zielonych rc — ale wyłącznie przez ważny
`design_gap` (4.). Problemy zdegradowane (design_gap bez kryterium) przy
zielonych rc → **PASS-z-notatkami**: koniec pracy, notatki dopisane do
BACKLOG.md jako propozycje, nie porażka.

### 6.2. Miara postępu (zamiast gołego limitu cykli)

Cykl liczy się jako **postępowy**, gdy ≥1 problem przeszedł na `resolved`
albo liczba otwartych problemów blokujących spadła. Liczniki w stanie:
`verify_cycle`, `verify_stall` (kolejne cykle bez postępu).

### 6.3. Warunki stopu (twarde, mechaniczne)

1. `verify_stall >= FORGE_MAX_STALL_CYCLES` (start: 2) — pętla kręci się bez
   postępu → stop, kod wyjścia ≠ 0, log wskazuje rejestr i feedback.
2. `verify_cycle >= FORGE_MAX_VERIFY_CYCLES` (start: 8 — bezpiecznik
   absolutny, nie główny mechanizm; projekt robiący postęp w 9 problemach po
   3 na cykl nie zostanie ubity limitem 3, jak groziło w rew. 2).
3. Potwierdzony `env_issue` (sekcja 7).

### 6.4. Wznawialność

`verify_cycle`, `verify_stall`, faza, SHA badanego HEAD i ścieżka bieżącego
rejestru w STATE.json. Restart w `verify_goal` → ponowne dowody mechaniczne
dla zapisanego SHA (odtwarzalne; logi nadpisywane per cykl). Checkpoint przed
wywołaniem agenta, jak we wszystkich fazach.

## 7. `env_issue`: potwierdzenie zamiast zaufania

Słaby punkt nr 3a: stop całego biegu nie może wisieć na słowie agenta.

- Zgłoszony `env_issue` → orkiestrator robi **mechaniczne potwierdzenie**:
  pełna powtórka dowodów dla wskazanego targetu (dla hardware wraz z
  `probe_cmd`, dla CI świeży `ci_status_cmd`). Zielono → klasyfikacja
  odrzucona, problem wraca jako `code_bug` do rozpatrzenia (agent się
  pomylił, bieg trwa).
- Czerwono → `env_issue` **potwierdzony**: target zablokowany, PASS
  niemożliwy → grzeczny stop z DEDYKOWANYM kodem wyjścia (rozróżnialnym od
  porażki merytorycznej) i plikiem `cycle-N/ENV-ISSUE.md` (co sprawdzić:
  sekret, kabel, toolchain). To celowe: agent nie naprawi fizycznego świata,
  a mielenie cykli pali tokeny.
- `env_issue` nigdy nie trafia do planisty jako zadanie — egzekwowane
  mechanicznie przy przekazywaniu rejestru (filtrowanie klas).

## 8. Anty-osłabianie domknięte (mechanicznie)

Słaby punkt nr 3b — dwie luki rewizji 2, obie zamknięte regułami na danych,
które orkiestrator sam kontroluje:

1. **Chronione ścieżki:** `.github/workflows/**` (i odpowiedniki innych CI),
   skrypty komend profilu weryfikacji, `verify_test_globs` (testy
   targetowe/CI — luka rew. 2: były zwykłymi plikami testowymi, więc
   najtańszą „naprawą" hardware było ich osłabienie), oraz
   `.forge/verification/**` (repro i rejestry — pisze je wyłącznie
   weryfikator; koder/tester mają tam zakaz diffu).
2. **Odblokowanie tylko przez rejestr, nie przez planistę:** zadanie może
   deklarować chronione ścieżki w `code_globs` TYLKO gdy jego pole `fixes`
   wskazuje problem o klasie `verify_defect` w rejestrze bieżącego cyklu.
   Warunek sprawdza orkiestrator przy starcie zadania (lookup id→klasa) —
   planista nie może już „sam sobie napisać code_globs" (luka rew. 2:
   warunek był niesprawdzalny). Naruszenie w diffie → `revert_paths` +
   ponowna bramka, jak dziś przy testach.
3. **Osłabienie testu targetowego jest wykrywalne skutkiem:** nawet gdy
   koder legalnie dotknie testu z `verify_test_globs` (zadanie
   `verify_defect`), bramką zadania pozostaje repro + pełna weryfikacja
   cyklu N+1 na prawdziwym targecie — skip/rozwodnienie testu nie zazieleni
   czerwonego `target_cmd`, jeśli usterka jest realna. Backstop: recenzja
   i człowiek, jak przy testach lokalnych.

## 9. Wczesne ostrzeganie (mitygacja późnego wykrycia)

Push jest per zadanie, więc CI i tak mieli przy każdym zadaniu — odroczone
jest tylko patrzenie. Darmowa mitygacja: przy KAŻDYM planowaniu wsadowym
orkiestrator odpala `ci_status_cmd HEAD` (bez czekania: rc==2 „trwa" jest OK)
i wynik dokleja do promptu planisty jako jedną linię
(`UWAGA: CI dla HEAD czerwone od zadania X`). Planista może wstawić zadanie
naprawcze wcześniej, zamiast budować dziesiątki zadań na złamanym CI.
Zero tokenów, zero nowych faz; pokrętło `FORGE_CI_EARLY_WARN=0` wyłącza.

## 10. Dostęp weryfikatora do CI i hardware

- **Warstwa mechaniczna** (orkiestrator): zadeklarowane komendy — polling,
  logi, flash. Deterministyczna, 0 tokenów, provider-agnostyczna.
- **Warstwa dochodzeniowa** (agent):
  - `claude` jako weryfikator: `--mcp-config` z serwerem MCP GitHuba — opcja
    `FORGE_VERIFIER_MCP_CONFIG` dokładana do argv w `run_claude` tylko dla
    tej roli;
  - `codex`: serwery MCP w `~/.codex/config.toml` — poza forge, wystarczy
    udokumentować;
  - fallback bez MCP: sandbox ma sieć, prompt wskazuje `gh run view
    --log-failed` itd.;
  - hardware zawsze przez zadeklarowane skrypty (przypięte do konkretnego
    urządzenia/portu), nigdy przez komendy wymyślone ad hoc.

  MCP to opcjonalne wzmocnienie diagnozy — nocny bieg nie może zależeć od
  jego konfiguracji.

## 11. Zmiany w kodzie (per plik)

| Plik | Zmiana |
|---|---|
| `config.py` | `verifier_agent/model/effort`, `verify_targets` (nadpisanie), `max_verify_cycles`, `max_stall_cycles`, `ci_timeout_s`, `ci_poll_start_s/max_s`, `verify_timeout_s`, `flash_retries`, `max_repro_runs_per_task`, `ci_early_warn`, `verifier_mcp_config`; `agents_in_use()` + weryfikator |
| `state.py` | profil (`verify_targets`, komendy, `verify_test_globs`), checkpoint (`verify_cycle`, `verify_stall`, `verify_sha`, `verify_ledger_path`); migracja starych STATE.json |
| **`verify_ledger.py`** (nowy) | czysta logika rejestru: walidacja kompletności (odhaczanie id z N-1), klasy i ich skutki, degradacja `design_gap` bez kryterium (podciąg w DESIGN.md), miara postępu, filtrowanie do planisty. Bez subprocessów — w pełni testowalne |
| **`verify.py`** (nowy) | dowody mechaniczne: `collect_evidence()` (smoke/flash+target/polling CI, zapis logów), `confirm_env_issue()`, `run_repro()`; wszystko na `_run_shellfree` (wydzielić do wspólnego modułu, by uniknąć importu z orchestrate) |
| `prompts.py` | `bootstrap_prompt` + profil; nowe `verify_goal_prompt` (z kontraktem rejestru i repro); `plan_batch_prompt` + akapit o feedbacku/rejestrze i polach `fixes`/`repro_cmd`; `write_test_prompt`/`code_and_refactor_prompt` + wariant zadania z repro |
| `orchestrate.py` | faza `verify_goal` w miejscu dzisiejszego `return False`; bramka PASS (6.1); stop-warunki (6.3); zadania z repro: czerwony-na-starcie, `run_gate`+repro, `no_test` bez smellu; egzekwowanie chronionych ścieżek (8.2) w kontroli diffu; early-warn przy planowaniu (9) |
| `agents.py` | `run_claude`: opcjonalny `--mcp-config` per rola; reszta bez zmian |
| `report.py` | grupa faz `verify`; podsumowanie cykli (problemy otwarte/rozwiązane per cykl) |
| `smoke.py` | `--dry`: walidacja profilu (parsowalność komend, `probe_cmd` dla hardware) |
| `README.md` | sekcja „Weryfikacja celu" + pokrętła + referencyjne skrypty `gh` + kontrakt repro |

## 12. Etapy wdrożenia

**E-V0 — rejestr + szkielet cyklu na trybie `smoke`.**
`verify_ledger.py` w całości (to rdzeń — czysta logika, pełne testy
jednostkowe), profil w bootstrapie, faza `verify_goal`, PASS/stall/limity,
feedback → planista, wznawialność. Repro w wersji minimalnej (repro dla
smoke = zwykły skrypt). Kryterium wyjścia: wszystkie testy + tryb `none`
regresyjnie bit-w-bit.

**E-V1 — target `ci` + zadania z repro w mikro-TDD.**
Polling, logi, early-warn (9), dochodzenie agenta (gh/MCP), pełny obieg
repro (5.2: czerwony-na-starcie, bramka złożona, `no_test` bez smellu),
potwierdzanie `env_issue`. Żywy test na repo z minimalnym workflow
(`python3 -m unittest` jako królik doświadczalny).

**E-V2 — target `hardware`.**
Flash/target/probe, retry, `verify_test_globs` w ochronie ścieżek, pełne
logi seriala. Do czasu fizycznego targetu — mock płytki jako skrypt
echo/exit (dokładnie jak testy `run_gate`).

**E-V3 — kalibracja po danych.**
Limity (`FORGE_MAX_STALL_CYCLES`, timeouty, sufit repro), pomiar: ile usterek
łapie weryfikacja, których nie złapały bramki zadań; ile problemów
degradowanych (design_gap bez kryterium) — dużo = DESIGN.md za chudy, sygnał
dla bootstrapu/planisty, by pisali kryteria weryfikowalne.

## 13. Ryzyka (świadomie przyjęte, po rewizji 3)

1. **Jakość repro-skryptów** — repro może odtwarzać objaw, nie przyczynę
   (zielony repro ≠ naprawiony bug). Backstopy: walidacja czerwony-na-starcie,
   pełna weryfikacja cyklu N+1 na realnym środowisku, recenzja.
2. **Koszt sprzętu przy repro** (flash per bieg bramki) — sufit
   `FORGE_MAX_REPRO_RUNS_PER_TASK`; zachęta w promptach: repro możliwie
   tani (filtr na jeden test).
3. **`design_gap` ograniczony do literalnych kryteriów** — cena zamknięcia
   ruchomych bramek: realny problem behawioralny nieopisany w DESIGN.md
   zostanie tylko notatką. Akceptowane: DESIGN.md jest żywy, notatki PASS
   trafiają do BACKLOG — człowiek/planista może je awansować.
4. **Późne wykrycie** — cecha kroju; mitygacje: early-warn (9) + bramki per
   zadanie jako pierwsza linia.
5. **Sekrety CI** — `gh`/MCP wymagają tokenu na maszynie pętli; forge tylko
   dokumentuje wymóg (jak `codex login`).
6. **Dwa wykonania drogiego repro w cyklu życia problemu** (walidacja +
   bramka) — świadomy koszt pewności „czerwone najpierw".

## 14. Kryteria akceptacji (definicja ukończenia E-V0/E-V1)

- [ ] `no_more_tasks` przy niepustych `verify_targets` NIE kończy pętli;
      przy `targets=[]`/`none` zachowanie bit-w-bit dzisiejsze (regresja).
- [ ] PASS niemożliwy przy czerwonym rc któregokolwiek targetu ani przy
      otwartym problemie blokującym (testy bramki 6.1).
- [ ] Rejestr niekompletny (nieodhaczony problem z N-1) → werdykt odrzucony.
- [ ] `design_gap` bez kryterium obecnego w DESIGN.md nie blokuje PASS
      (degradacja do notatki; test normalizacji podciągu).
- [ ] Nowy `repro_cmd` zielony przy odbiorze → problem odrzucony; zadanie
      z repro czerwonym na starcie przechodzi bramkę tylko przy zielonym
      repro na końcu (testy na mock-skryptach).
- [ ] `no_test` w zadaniu z repro nie nalicza smellu.
- [ ] Zgłoszony `env_issue` z zielonym potwierdzeniem mechanicznym wraca do
      obiegu; z czerwonym → stop z dedykowanym kodem wyjścia i ENV-ISSUE.md.
- [ ] Stop po `FORGE_MAX_STALL_CYCLES` cyklach bez postępu; cykle z postępem
      nie zużywają stall-licznika (testy miary postępu).
- [ ] Chronione ścieżki: diff dotykający workflow/skryptów
      weryfikacji/`verify_test_globs`/`.forge/verification/**` wycofywany,
      chyba że zadanie ma `fixes` → problem klasy `verify_defect` (testy
      lookupu i revertu).
- [ ] FAIL: feedback + rejestr zacommitowane i wypchnięte; prompt planisty
      zawiera ścieżki; następna iteracja rusza od planowania.
- [ ] Restart w `verify_goal` wznawia dowody dla zapisanego SHA.
- [ ] Wszystkie nowe bramki mają testy jednostkowe bez agentów.
