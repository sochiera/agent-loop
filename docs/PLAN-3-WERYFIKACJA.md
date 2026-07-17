# Plan 3: faza weryfikacji celu (CI + real hardware) — projekt

Data: 2026-07-17 (rewizja 2 — zmiana koncepcji po decyzji użytkownika).
Kontynuacja `PLAN-2-HARDENING.md`.

**Koncepcja:** weryfikacja NIE jest wpleciona w każde zadanie. Pracuje na sam
koniec — gdy planista orzeknie, że cel jest osiągnięty (`no_more_tasks`),
do gry wchodzi **nowy agent (weryfikator-QA)**, który sprawdza, czy całość
naprawdę działa: na CI i na prawdziwym hardware. Jeśli nie — pisze **obszerny
feedback** i oddaje go planiście, a cały cykl rusza od nowa (plan → zadania →
mikro-TDD → ... → ponowna weryfikacja), aż weryfikacja przejdzie.

```
pętla zadań (jak dziś) → planista: no_more_tasks
        │
        ▼
  WERYFIKACJA CELU (nowy agent, świeży kontekst)
   ├─ CI: obserwuje/debuguje przebieg dla HEAD (gh / MCP)
   ├─ hardware: flash → testy na targecie → logi seriala
   └─ werdykt:
        PASS → koniec pracy 🎉
        FAIL → obszerny raport → .forge/verification/feedback.md
                 → cykl N+1: planista czyta feedback, planuje naprawy
                 → pętla zadań → ... → ponowna weryfikacja
        (limit cykli → czysty stop z raportem dla człowieka)
```

Dlaczego to lepszy krój niż weryfikacja per zadanie (rewizja 1):

- **Prostota maszyny stanów.** Jedna nowa faza na końcu cyklu zamiast czterech
  faz wplecionych między recenzję, push i rollback każdego zadania.
- **Koszt.** CI mieli minuty, flash+testy na targecie też — per zadanie
  zabiłoby to przepustowość nocnej pętli. Per cykl celu płacimy raz.
- **Naturalna rola.** Weryfikator to inżynier QA patrzący na CAŁOŚĆ świeżym
  okiem (bez historii intencji), a nie podprogram diagnostyczny. Ocenia
  produkt, nie diff.
- **Feedback → planista to istniejący mechanizm.** Dokładnie tak działa dziś
  `failures.md`: pamięć w repo, planista czyta i planuje. Zero nowych kanałów.

---

## 1. Zasady projektowe (niezmienne filary forge)

1. **Stack-agnostyczność przez deklarację.** Forge nie zna GitHub Actions ani
   ESP-IDF. Sposób weryfikacji deklaruje agent bootstrapu (profil w
   STATE.json), człowiek nadpisuje przez env/flagi.
2. **Pamięć w repo.** Feedback weryfikacji żyje w plikach
   (`.forge/verification/`), nie w kontekście modelu. Planista czyta plik,
   weryfikator kolejnego cyklu czyta poprzednie raporty (co się poprawiło,
   co nawraca).
3. **Bramki mechaniczne tam, gdzie się da.** Werdykt PASS nie może być gołą
   deklaracją agenta — orkiestrator żąda dowodów (patrz 4.3).
4. **Wypchnięta historia nienaruszalna.** Weryfikacja startuje, gdy wszystko
   jest już na remote; naprawy to nowe zadania i nowe commity. Zero rollbacków
   w tej fazie.

## 2. Profil weryfikacji w STATE.json

Bootstrap (rozszerzony prompt) deklaruje obok `test_cmd`/`run_cmd`:

```json
"verify": {
  "targets": ["ci", "hardware", "smoke"],
  "smoke_cmd":  "<dymny bieg produktu, rc==0 = OK — fallback, gdy brak ci/hw>",
  "flash_cmd":  "<hardware: wgranie na target, np. bash scripts/flash.sh>",
  "target_cmd": "<hardware: testy na targecie; rc==0 = OK, stdout = log seriala>",
  "probe_cmd":  "<hardware, opcjonalne: czy urządzenie podpięte (preflight)>",
  "ci_status_cmd": "<ci: status checków dla {sha}; rc: 0=zielono,1=czerwono,2=trwa>",
  "ci_logs_cmd":   "<ci: log porażek dla {sha} na stdout>"
}
```

- `targets` wybiera bootstrap z briefu i repo: `.github/workflows/` (czy inny
  konfig CI) → `ci`; brief mówi o płytce/firmware → `hardware`; zawsze warto
  dodać `smoke`. Człowiek nadpisuje: `FORGE_VERIFY_TARGETS=ci,hardware` /
  `--verify-targets` (`none` = wyłącznik, zachowanie jak dziś).
- Kontrakt komend ten sam co `test_cmd`: pojedyncza komenda bez operatorów
  powłoki (złożone kroki → skrypt), `{sha}` rozwijany przez orkiestrator.
  Provider CI jest wymienny — forge zna tylko kody wyjścia.
- Pola płasko w `State` (jak `test_cmd`); walidacja w `phase_bootstrap`:
  target zadeklarowany bez swoich komend = błąd bootstrapu. Stare STATE.json
  migrują na `verify_targets=[]` + log z podpowiedzią.
- Preflight: tryb `hardware` w `preflight()` odpala `probe_cmd` (jeśli jest) —
  brak płytki wychodzi przy starcie pętli, nie w środku nocy.

## 3. Przebieg fazy weryfikacji

### 3.1. Wejście

Dziś `no_more_tasks` kończy pętlę (`return False` w `_task_iteration`).
Po zmianie: jeśli `verify_targets` niepuste → `state.phase = "verify_goal"`,
`state.verify_cycle += 1` i wchodzimy w fazę weryfikacji. Pętlę kończy
dopiero PASS albo limit cykli.

### 3.2. Przygotowanie mechaniczne (0 tokenów)

Zanim orkiestrator zawoła agenta, sam zbiera tani materiał dowodowy:

- `ci`: polling `ci_status_cmd {sha}` dla HEAD z backoffem (start 30 s, sufit
  5 min, `FORGE_CI_TIMEOUT` domyślnie 45 min). Jeśli czerwono — ściąga
  `ci_logs_cmd` do `.forge/verification/cycle-N/ci.log`. CI zielone i brak
  innych targetów → można w ogóle nie budzić agenta (patrz 3.4).
- `hardware`: `flash_cmd` (z jednym darmowym retry — USB bywa flaky), potem
  `target_cmd` z timeoutem; pełny stdout/stderr do
  `.forge/verification/cycle-N/hardware.log`.
- `smoke`: `smoke_cmd`, wyjście do `.forge/verification/cycle-N/smoke.log`.

Wyniki (rc + ścieżki logów) trafiają do promptu weryfikatora. Czekanie na CI
odbywa się więc w orkiestratorze za darmo, a nie w wiszącym wywołaniu agenta.

### 3.3. Agent weryfikator-QA

Nowa rola obok planisty/testera/kodera: `--verifier-agent/-model/-effort`,
`FORGE_VERIFIER_*`; domyślnie agent planisty (to zadanie „mocnego modelu" —
ocena całości, nie mechaniczne kodowanie). Zawsze **świeży kontekst** (bez
sesji): ma oceniać produkt i dowody, nie pamiętać intencji z budowy.

Prompt (`prompts.verify_goal_prompt`) dostaje: brief/DESIGN.md (ścieżki, nie
treść — agent sam czyta), profil weryfikacji, wyniki mechaniczne z 3.2
(rc + ścieżki logów), ścieżki raportów z poprzednich cykli. Zadania agenta:

1. Zbadać dowody: przeczytać logi, w razie potrzeby samodzielnie drążyć —
   ponowić `target_cmd`, obejrzeć konkretny job CI (`gh` albo MCP, patrz 5),
   uruchomić produkt (`run_cmd`) i sprawdzić realne zachowania z DESIGN.md.
2. Przy porażce: napisać **obszerny raport** do
   `.forge/verification/cycle-N/feedback.md` (format w sekcji 4).
3. Zwrócić werdykt:

```json
{"verdict": "pass",
 "evidence": {"ci": "green", "hardware": "rc=0", "smoke": "rc=0"}}
```
```json
{"verdict": "fail",
 "report": ".forge/verification/cycle-N/feedback.md",
 "headline": "<1 zdanie: co jest zepsute>",
 "areas": ["ci", "hardware"]}
```

### 3.4. Rozstrzygnięcie orkiestratora

- **PASS wymaga zgody dowodów z deklaracją** (bramka anty-„ogłaszaniu
  zwycięstwa", jak przy DONE testera): orkiestrator przyjmuje `pass` tylko
  gdy jego WŁASNE wyniki z 3.2 są zielone dla każdego targetu. Agent nie może
  przegłosować czerwonego rc. Odwrotnie może: agent może sfailować mimo
  zielonych komend (np. produkt startuje, ale zachowanie niezgodne z DESIGN).
  Optymalizacja: gdy wszystkie targety zielone mechanicznie, wywołanie agenta
  można ograniczyć do taniego przeglądu (albo pominąć przy
  `FORGE_VERIFY_AGENT_ON_GREEN=0`) — pokrętło, decyzja po pomiarach.
- **PASS** → `log("CEL ZWERYFIKOWANY")`, koniec pętli (dzisiejsze „MVP
  ukończone 🎉" przenosi się tutaj).
- **FAIL** → orkiestrator:
  1. commituje raport (`docs`-commit, jak plan) i pushuje — feedback jest
     częścią historii projektu;
  2. czyści `no_more_tasks`-stan: `state.phase = "idle"`, kolejka pusta;
  3. `verify_cycle >= FORGE_MAX_VERIFY_CYCLES` (start: 3) → **czysty stop**
     z kodem wyjścia ≠ 0 i wskazaniem raportu — dalsze mielenie bez człowieka
     to palenie tokenów w kółko;
  4. inaczej → następna iteracja pętli: planista dostaje feedback (sekcja 4.2)
     i planuje zadania naprawcze; te przechodzą normalną pętlę mikro-TDD ze
     wszystkimi istniejącymi bramkami.

### 3.5. Wznawialność

`verify_cycle`, `phase="verify_goal"` i SHA badanego HEAD w STATE.json.
Restart w tej fazie: ponowny polling/flash dla zapisanego SHA (wyniki
mechaniczne są odtwarzalne, logi nadpisywane per cykl). Limit/Ctrl-C działa
jak w każdej fazie — checkpoint przed wywołaniem agenta.

## 4. Feedback: format i droga do planisty

### 4.1. Raport `feedback.md`

Obszerny, ale ustrukturyzowany — to kontrakt między weryfikatorem a planistą:

```markdown
# Weryfikacja celu — cykl N: FAIL
## Co sprawdzono i jak (target → komenda/ścieżka → wynik)
## Co działa (żeby planista tego nie ruszał)
## Problemy (per problem):
### P1: <tytuł>
- Objaw: <co widać, cytat z loga + ścieżka do pełnego loga>
- Dowód: .forge/verification/cycle-N/ci.log:123-160
- Podejrzana przyczyna: <hipoteza + pliki>
- Proponowany podział na zadania: <1-3 małe, testowalne kroki>
- Klasyfikacja: code_bug | env_issue | flaky
## Porównanie z cyklem N-1 (co naprawiono, co nawraca)
```

- Klasyfikacja `env_issue` (brak sekretu CI, odpięta płytka, brak toolchaina
  na runnerze) jest wyróżniona w werdykcie: to NIE wraca do planisty jako
  zadanie kodowe, tylko zatrzymuje pętlę z komunikatem dla człowieka —
  agent nie naprawi fizycznego świata, a cykle by się paliły.
- `flaky`: jedna darmowa powtórka targetu przed uznaniem porażki; nawracająca
  flakiness w kolejnych cyklach → jawny problem w raporcie (ustabilizowanie
  testu to legalne zadanie naprawcze).

### 4.2. Planista czyta feedback

Rozszerzenie `plan_batch_prompt` (ten sam mechanizm co `failures.md`):
jeśli istnieje `.forge/verification/cycle-N/feedback.md` z ostatniego cyklu —
przeczytaj, przełóż problemy na zadania naprawcze (sekcja „Proponowany
podział" to sugestia, nie rozkaz — planista może pociąć inaczej), zaktualizuj
BACKLOG. `no_more_tasks` wolno mu orzec dopiero, gdy problemy z raportu są
zaadresowane — wtedy pętla naturalnie wraca do weryfikacji (cykl N+1).

### 4.3. Guardraile anty-osłabiania

Zadania naprawcze przechodzą normalne bramki mikro-TDD, ale dochodzi ryzyko
specyficzne dla weryfikacji: najtańszą „naprawą" CI jest wyłączenie kroku w
workflow, a testu na targecie — skip. Mechanicznie (rozszerzenie istniejącej
kontroli diffu): w cyklach naprawczych (verify_cycle > 1) zmiany w
`.github/workflows/**` i w plikach skryptów zadeklarowanych w profilu
weryfikacji są dozwolone TYLKO, gdy zadanie z planu jawnie je wymienia w
`code_globs` — a takie zadanie planista może utworzyć wyłącznie, gdy raport
klasyfikuje problem jako usterkę samej weryfikacji. Backstop jak zawsze:
recenzja + człowiek rano.

## 5. Dostęp weryfikatora do CI i hardware

- **Warstwa mechaniczna** (orkiestrator): zadeklarowane komendy — polling,
  logi, flash. Deterministyczna, 0 tokenów, provider-agnostyczna.
- **Warstwa dochodzeniowa** (agent): gdy ogon loga nie wystarcza, agent drąży
  sam:
  - `claude` jako weryfikator: `--mcp-config` z serwerem MCP GitHuba —
    nowa opcja `FORGE_VERIFIER_MCP_CONFIG`, dokładana do argv w `run_claude`
    tylko dla tej roli;
  - `codex`: serwery MCP w `~/.codex/config.toml` (`[mcp_servers.*]`) — poza
    forge, wystarczy udokumentować;
  - fallback bez MCP: sandbox ma sieć (`danger-full-access`), prompt wskazuje
    `gh run view --log-failed` itd. jako narzędzia.
  - hardware: agent może ponawiać `flash_cmd`/`target_cmd` i czytać pełne
    logi z `.forge/verification/` — fizyczny dostęp zawsze przez zadeklarowane
    skrypty, nigdy przez komendy wymyślone ad hoc (skrypt przypina konkretne
    urządzenie/port).

  MCP jest opcjonalnym wzmocnieniem, nie rdzeniem — nocny bieg nie może
  zależeć od konfiguracji MCP.

## 6. Zmiany w kodzie (per plik)

| Plik | Zmiana |
|---|---|
| `config.py` | `verifier_agent/model/effort`, `verify_targets` (nadpisanie), `max_verify_cycles`, `ci_timeout_s`, `ci_poll_start_s/max_s`, `verify_timeout_s`, `flash_retries`, `verifier_mcp_config`; `agents_in_use()` + weryfikator gdy targets ≠ puste |
| `state.py` | profil (`verify_targets`, `smoke_cmd`, `flash_cmd`, `target_cmd`, `probe_cmd`, `ci_status_cmd`, `ci_logs_cmd`), checkpoint (`verify_cycle`, `verify_sha`); migracja starych STATE.json |
| `prompts.py` | `bootstrap_prompt` + profil weryfikacji; nowy `verify_goal_prompt`; `plan_batch_prompt` + akapit o feedbacku (analogiczny do failures.md) |
| `orchestrate.py` | czyste funkcje: `collect_verify_evidence()` (smoke/flash+target/polling CI, zapis logów, zwraca wyniki per target), `verdict_allowed()` (bramka PASS-wymaga-zielonych-rc), `verify_config_violations()` (4.3); faza `verify_goal` wpięta w miejsce dzisiejszego `return False` po `no_more_tasks`; limit cykli |
| `agents.py` | `run_claude`: opcjonalny `--mcp-config` per rola; reszta bez zmian (weryfikator = zwykłe `run_agent`) |
| `report.py` | grupa faz `verify`; podsumowanie cykli weryfikacji |
| `smoke.py` | `--dry`: walidacja profilu (parsowalność komend, `probe_cmd` dla hardware) |
| `README.md` | sekcja „Weryfikacja celu" + pokrętła + referencyjne skrypty `gh` |

Wszystkie nowe bramki na wzór `run_gate`/`_run_shellfree`: bez shella,
z timeoutem, testowalne bez agentów (komendy mockowane skryptami — jak testy
bramki anty-osłabiania).

## 7. Etapy wdrożenia

**E-V0 — szkielet cyklu na trybie `smoke`.** Profil w bootstrapie, faza
`verify_goal`, raport → feedback → planista → cykl N+1, limit cykli,
wznawialność. Najprostszy target przewierca CAŁĄ nową pętlę zewnętrzną.
Testy jednostkowe: bramka PASS/rc, limit cykli, migracja STATE, przepływ
feedbacku (planista dostaje ścieżkę raportu).

**E-V1 — target `ci`.** Polling + logi + dochodzenie agenta (gh/MCP),
klasyfikacja `env_issue`/`flaky`, referencyjne skrypty. Żywy test na repo
z minimalnym workflow (`python3 -m unittest` jako królik doświadczalny).

**E-V2 — target `hardware`.** Flash/target/probe, retry, pełne logi seriala,
preflight. Do czasu dostępu do fizycznego targetu — bramki na
skryptach-atrapach (mock płytki = skrypt echo/exit).

**E-V3 — hartowanie po danych.** Kalibracja limitów i pokręteł
(`FORGE_MAX_VERIFY_CYCLES`, timeouty, `FORGE_VERIFY_AGENT_ON_GREEN`), pomiar:
ile realnych usterek łapie weryfikacja, których nie złapały bramki zadań —
to uzasadnia jej koszt. Decyzja o 4.3 w wersji twardszej/miększej.

## 8. Ryzyka (świadomie przyjęte)

1. **Pętla cykli bez postępu** — planista może kręcić się wokół tego samego
   problemu. Mitygacje: limit cykli (twardy stop z raportem), sekcja
   „porównanie z cyklem N-1" w raporcie (nawracający problem jest jawny),
   człowiek rano.
2. **Późne wykrycie** — cena tego kroju: usterka fundamentu wychodzi dopiero
   na końcu, naprawa bywa droższa niż per zadanie. Akceptowane świadomie
   (koszt CI/hw per zadanie byłby gorszy); istniejące bramki per zadanie
   (testy, recenzja) zostają pierwszą linią obrony.
3. **`env_issue` w środku nocy** — brak sekretu/płytki zatrzymuje pętlę.
   Celowo: to sprawa człowieka; preflight (`probe_cmd`, smoke `--dry`)
   minimalizuje ryzyko przed startem.
4. **Werdykt „pass" na słowo** — pokryte bramką 3.4 (dowody rc muszą się
   zgadzać); subiektywna część oceny („zachowanie zgodne z DESIGN") pozostaje
   na odpowiedzialności agenta — backstop: człowiek.
5. **Sekrety CI** — `gh`/MCP wymagają tokenu na maszynie pętli; forge tylko
   dokumentuje wymóg (jak dziś `codex login`).
6. **Flaky hardware/CI** — darmowe powtórki + klasyfikacja `flaky`; nawroty
   eskalowane w raporcie zamiast zamiatane.

## 9. Kryteria akceptacji (E-V0/E-V1)

- [ ] `no_more_tasks` przy niepustych `verify_targets` NIE kończy pętli —
      wchodzi weryfikacja; przy `targets=[]`/`none` zachowanie bit-w-bit
      dzisiejsze (regresyjne testy pętli przechodzą bez zmian).
- [ ] FAIL: raport `feedback.md` zacommitowany i wypchnięty; następna
      iteracja planowania dostaje go w prompcie; pętla zadań rusza od nowa.
- [ ] PASS niemożliwy przy czerwonym rc któregokolwiek targetu (test bramki).
- [ ] Limit cykli: po `FORGE_MAX_VERIFY_CYCLES` porażkach czysty stop,
      kod wyjścia ≠ 0, log wskazuje raport.
- [ ] Restart w fazie `verify_goal` wznawia weryfikację tego samego SHA.
- [ ] Zmiany workflow/skryptów weryfikacji w cyklach naprawczych możliwe
      tylko przez jawnie zaplanowane zadanie (test kontroli diffu).
- [ ] Wszystkie nowe bramki mają testy jednostkowe bez agentów.
