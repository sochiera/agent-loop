# Plan 3: faza weryfikacji (CI, hardware, smoke) — projekt

Data: 2026-07-17. Kontynuacja `PLAN-2-HARDENING.md`. Cel: po ukończeniu zadania
(mikro-TDD + recenzja) pętla ma **zweryfikować produkt w jego realnym
środowisku**, nie tylko na lokalnej bramce testów:

- projekt z CI → obserwować i debugować przebieg CI po pushu (agent podpięty
  przez MCP albo CLI, np. `gh`),
- projekt embedded → wgrać na hardware, uruchomić testy na targecie, zebrać
  logi (serial/RTT) i ocenić, czy wszystko działa,
- każdy inny projekt → przynajmniej dymny bieg produktu (`run`/smoke), bo
  zielone unit-testy nie dowodzą, że program w ogóle startuje.

---

## 1. Zasada projektowa

Dzisiejsza „weryfikacja" forge to lokalna bramka `build_cmd`+`test_cmd` plus
recenzja LLM. Brakuje trzeciego poziomu: **obserwacji działającego produktu w
środowisku docelowym**. Plan trzyma się trzech istniejących filarów:

1. **Stack-agnostyczność przez deklarację.** Forge nie zna GitHub Actions ani
   ESP-IDF, tak jak nie zna Pythona. To agent bootstrapu (i człowiek przez
   env/flagi) deklaruje **profil weryfikacji**: jakie komendy, jaki tryb.
   Kontrakt komend ten sam co dla `test_cmd`: pojedyncza komenda bez operatorów
   powłoki (złożone kroki → skrypt).
2. **Mechanika poza modelem, tokeny tylko na diagnozę.** Czekanie na CI,
   flashowanie, zbieranie logów i rozstrzyganie zielone/czerwone robi
   orkiestrator (0 tokenów). Agent (nowa rola: WERYFIKATOR) dostaje głos
   dopiero przy czerwieni — z ogonem dowodów (log CI / log seriala), żeby
   zdiagnozować i wyprodukować wykonalne uwagi dla kodera.
3. **Wypchnięta historia jest nienaruszalna.** Weryfikacja CI z natury dzieje
   się PO pushu, więc jej porażka nie może robić rollbacku — naprawy idą
   naprzód (`fix:`-commity). Weryfikacja lokalna (smoke, hardware) dzieje się
   PRZED pushem i ma normalną semantykę rollbacku zadania.

## 2. Profil weryfikacji w STATE.json

Bootstrap (rozszerzony prompt) deklaruje obok `test_cmd`/`run_cmd` nowy obiekt:

```json
"verify": {
  "mode": "none | smoke | ci | hardware",
  "smoke_cmd":   "<komenda dymna, np. bash scripts/smoke.sh — rc==0 = OK>",
  "flash_cmd":   "<tylko hardware: wgranie na target, np. bash scripts/flash.sh>",
  "target_cmd":  "<tylko hardware: testy na targecie / bieg z logiem na stdout>",
  "ci_status_cmd": "<tylko ci: status checków dla SHA, np. bash scripts/ci-status.sh {sha}>",
  "ci_logs_cmd":   "<tylko ci: log porażek dla SHA, np. bash scripts/ci-logs.sh {sha}>"
}
```

Zasady:

- `mode` wybiera bootstrap na podstawie briefu i repo: katalog
  `.github/workflows/` (lub `.gitlab-ci.yml` itd.) → `ci`; brief mówi o
  płytce/firmware/targecie → `hardware`; inaczej `smoke`. Człowiek nadpisuje
  przez `FORGE_VERIFY_MODE` / `--verify-mode` (w tym `none` — wyłącznik).
- Komendy CI dostają placeholder `{sha}` (rozwijany przez orkiestrator jak w
  `adapters.expand_template`) — kontrakt: `ci_status_cmd` wychodzi kodem
  0 = zielono, 1 = czerwono, 2 = jeszcze trwa; `ci_logs_cmd` wypisuje na
  stdout log porażek. Typowa implementacja to skrypt na `gh run list/view
  --log-failed`, ale forge zna tylko kody wyjścia — provider CI jest wymienny.
- Kontrakt hardware: `flash_cmd` rc==0 = wgrane; `target_cmd` uruchamia testy
  na targecie i wychodzi rc==0 przy sukcesie, a jego stdout/stderr (log
  seriala/RTT) jest dowodem dla weryfikatora. Skrypt musi sam zadbać o timeout
  odczytu portu (orkiestrator i tak tnie po `FORGE_VERIFY_TIMEOUT`).
- Pola trafiają do `State` (płasko: `verify_mode`, `smoke_cmd`, ... — spójnie
  z `test_cmd`), walidacja w `phase_bootstrap` jak dziś dla wymaganych pól:
  tryb zadeklarowany bez swoich komend = błąd bootstrapu.

Dla projektów bootstrapowanych starszą wersją: migracja w `State.load` →
`verify_mode="none"` + log z podpowiedzią, jak włączyć ręcznie.

## 3. Umiejscowienie w pętli i maszyna stanów

Nowe fazy wpinają się między recenzję a zamknięcie zadania:

```
micro → review ─approve→ VERIFY-LOCAL (smoke|hardware) ─OK→ push → VERIFY-CI ─OK→ done
                   │            │ czerwono                        │ czerwono
                   │            └→ fix_verify (koder) ↺ limit → porażka zadania (rollback do taga)
                   │                                              └→ fix_ci (koder) → commit+push ↺ limit
                   │                                                                   → porażka „miękka" (bez rollbacku)
```

- **VERIFY-LOCAL** (`phase="verify"`): tryby `smoke` i `hardware`. Dzieje się po
  approve recenzji, ale PRZED `_finish_task` (czyli przed pushem). Czerwień →
  pętla `fix_verify` (koder, sesja zadania, uwagi od weryfikatora), po każdej
  poprawce bramka testów + ponowna weryfikacja; limit `FORGE_MAX_VERIFY_FIXES`
  (start: 2) → porażka zadania z normalnym rollbackiem do taga startu.
- **VERIFY-CI** (`phase="verify_ci"`): po pushu zadania. Orkiestrator zapisuje
  w stanie SHA wypchniętego commita (`verify_sha`) i odpytuje `ci_status_cmd`
  z backoffem (start 30 s, sufit 5 min, timeout całości `FORGE_CI_TIMEOUT`,
  domyślnie 45 min). Zero tokenów podczas czekania. Czerwień → `ci_logs_cmd`
  → WERYFIKATOR diagnozuje → koder naprawia → commit `fix: ... (CI)` + push →
  obserwacja nowego SHA. Limit rund → **porażka miękka**: wpis do
  `failures.md` (planista to przeczyta i zaplanuje naprawę), zadanie oznaczone,
  pętla idzie dalej — commity zostają, bo już są na remote.
- **Wznawialność**: `verify_sha`, licznik rund i faza w `STATE.json`; po
  restarcie w fazie `verify_ci` wystarczy znów odpytać status dla zapisanego
  SHA. Timeout CI bez rozstrzygnięcia = porażka miękka z powodem „CI timeout"
  (nie blokujemy nocnej pętli na wiszącym runnerze).
- Tryb `none` → obie fazy przezroczyste (zachowanie dokładnie jak dziś).
- Legacy mode: świadomie BEZ weryfikacji (stary przebieg zostaje zamrożony).

Rozstrzygnięcie „per zadanie czy per batch": startowo **per zadanie** — spójne
z jednostką pusha i rollbacku. Jeśli E-V4 (pomiar) pokaże, że hardware/CI
wydłuża zadania nadmiernie, dodać `FORGE_VERIFY_EVERY=N` (weryfikacja co N-te
zadanie + zawsze na końcu batcha); to czysta zmiana konfiguracji pętli.

## 4. Rola WERYFIKATOR

Nowa rola obok planisty/testera/kodera — konfigurowalna tak samo
(`--verifier-agent/-model/-effort`, `FORGE_VERIFIER_*`; domyślnie agent
testera). Wywoływana **wyłącznie przy czerwonej weryfikacji**, bezsesyjnie
(świeży kontekst — diagnoza ma patrzeć na dowody, nie na historię intencji).

Prompt (`prompts.verify_diagnose_prompt`): dostaje tryb, komendę która padła,
ogon dowodów (log CI / log seriala / stdout smoke — ucięty do ~4–6 kB, jak
`test_tail` dziś) i zadanie (`task_file`). Ma rozstrzygnąć i zwrócić:

```json
{"verdict": "code_bug | env_issue | flaky",
 "notes": ["<konkretna, wykonalna poprawka>", "..."],
 "suspect_files": ["<ścieżki>"]}
```

- `code_bug` → uwagi idą do istniejącej pętli poprawek kodera
  (`fix_review_prompt` niemal bez zmian — uwagi to uwagi).
- `env_issue` (brak sekretu w CI, odpięta płytka, brak toolchaina na runnerze)
  → nie palimy rund kodera; porażka miękka z opisem dla człowieka.
- `flaky` → jedna darmowa ponowna próba weryfikacji (re-run), potem traktowane
  jak `code_bug`. Licznik `flaky` w `failures.md` — nawracająca flakiness to
  zadanie dla planisty (ustabilizować test), nie do zamiatania.

**Anty-osłabianie weryfikacji** (symetria z bramką anty-osłabiania testów,
czysto mechaniczna): w rundach `fix_verify`/`fix_ci` diff kodera nie może
dotykać plików konfiguracji weryfikacji — `.github/workflows/**`, plików
zadeklarowanych komend weryfikacji (skryptów smoke/flash/ci) — chyba że
weryfikator jawnie wskazał je w `suspect_files`. Niedozwolona zmiana →
`revert_paths` + ponowna bramka, jak dziś przy testach. Inaczej najtańszą
„naprawą" CI będzie wyłączenie kroku w workflow.

## 5. Dostęp agenta do CI: MCP albo CLI

Dwie warstwy, zgodnie z zasadą z sekcji 1:

1. **Warstwa mechaniczna (orkiestrator)** — `ci_status_cmd`/`ci_logs_cmd`.
   Zero tokenów, deterministyczna, provider-agnostyczna. To ona czeka i
   rozstrzyga zielone/czerwone. Referencyjne skrypty dla GitHub Actions
   (`gh run list --commit {sha}`, `gh run view --log-failed`) dołączyć jako
   przykłady w docs — bootstrap i tak generuje własne pod repo.
2. **Warstwa diagnostyczna (weryfikator)** — agent może dostać narzędzia do
   samodzielnego grzebania w CI, gdy ogon loga nie wystarcza:
   - `claude` jako weryfikator: `--mcp-config` z serwerem MCP GitHuba
     (nowa opcja `FORGE_VERIFIER_MCP_CONFIG` → dokładane do argv w
     `run_claude` tylko dla tej roli),
   - `codex` jako weryfikator: serwery MCP konfiguruje się w
     `~/.codex/config.toml` (`[mcp_servers.github]`) — poza forge, wystarczy
     udokumentować,
   - fallback bez MCP: skoro sandbox i tak ma sieć (`danger-full-access`),
     prompt wskazuje `gh` jako narzędzie („możesz użyć `gh run view ...`").

   MCP jest tu **opcjonalnym wzmocnieniem diagnozy**, nie rdzeniem pętli —
   rdzeń musi działać z samym `gh`/skryptami, żeby nie uzależniać nocnego
   biegu od konfiguracji MCP.

## 6. Hardware-in-the-loop: szczegóły

- **Preflight**: tryb `hardware` dodaje do `preflight()` sprawdzenie
  zadeklarowanej komendy `probe_cmd` (opcjonalne pole profilu, np.
  `bash scripts/probe.sh` sprawdzający obecność urządzenia/portu). Brak
  urządzenia przy starcie pętli = twardy błąd od razu, nie w środku nocy przy
  pierwszym zadaniu.
- **Dowody**: stdout/stderr `target_cmd` zapisywane w całości do
  `.forge/verify/task-NNN-r<runda>.log` (jak logi faz); do weryfikatora idzie
  ogon. Człowiek rano ma pełne logi seriala per zadanie.
- **Bezpieczeństwo**: flashowanie to operacja na fizycznym świecie — forge
  nigdy nie wymyśla komendy flashowania sam; wykonuje wyłącznie zadeklarowaną.
  Dokumentacja: skrypt flashujący powinien być przypięty do konkretnego
  urządzenia (serial/port), nie „pierwszego lepszego".
- **Serializacja**: jedna płytka = weryfikacja siłą rzeczy sekwencyjna; to
  kolejny argument przeciw równoległości zadań (E5 z Planu 2) — bez zmian.
- Retry flashowania (`FORGE_FLASH_RETRIES`, start: 1) — flash bywa flaky
  z natury (USB), jedna darmowa ponowna próba przed angażowaniem agenta.

## 7. Zmiany w kodzie (per plik, minimalny przekrój)

| Plik | Zmiana |
|---|---|
| `config.py` | pola: `verify_mode` (nadpisanie), `verifier_agent/model/effort`, `max_verify_fixes`, `ci_timeout_s`, `ci_poll_start_s/max_s`, `verify_timeout_s`, `flash_retries`, `verifier_mcp_config`; `agents_in_use()` uwzględnia weryfikatora gdy tryb ≠ none |
| `state.py` | pola profilu (`verify_mode`, `smoke_cmd`, `flash_cmd`, `target_cmd`, `ci_status_cmd`, `ci_logs_cmd`), checkpoint (`verify_sha`, `verify_round`); migracja starych STATE.json |
| `prompts.py` | rozszerzony `bootstrap_prompt` (profil weryfikacji + reguły komend), nowy `verify_diagnose_prompt`, drobne rozszerzenie `fix_review_prompt` o kontekst „to porażka weryfikacji, nie recenzji" |
| `orchestrate.py` | czyste funkcje-bramki: `run_verify_local()` (smoke/flash+target, retry), `poll_ci()` (status z backoffem, obsługa rc 0/1/2), `verify_config_violations()` (anty-osłabianie); fazy `verify`/`verify_ci`/`fix_verify`/`fix_ci` wpięte w `_task_iteration`; `_finish_task` rozbite na commit/push + domknięcie |
| `agents.py` | `run_claude`: opcjonalne `--mcp-config` per rola (tylko weryfikator); reszta bez zmian — weryfikator to zwykłe `run_agent` |
| `report.py` | grupy faz `verify*` w `normalize_phase`; podsumowanie rund weryfikacji |
| `smoke.py` | `--dry`: walidacja profilu weryfikacji (parsowalność komend, `probe_cmd` dla hardware) |
| `README.md` | sekcja „Faza weryfikacji" + tabela pokręteł + referencyjne skrypty `gh` |

Kształt bramek celowo powiela `run_gate`/`_run_shellfree` — bez shella, z
timeoutem, testowalne bez agentów (komendy mockowane skryptami, jak w testach
bramki anty-osłabiania).

## 8. Etapy wdrożenia

**E-V0 — smoke lokalny (fundament, bez nowych zależności).**
Profil w bootstrapie + faza `verify` z trybem `smoke` + pętla `fix_verify` +
anty-osłabianie skryptu smoke. Działa dla KAŻDEGO projektu i przewierca całą
maszynę stanów (checkpointy, wznawialność, limity) na najprostszym trybie.
Testy jednostkowe wszystkich nowych bramek.

**E-V1 — obserwacja CI.** `verify_ci` po pushu: polling, pobranie logów,
weryfikator, forward-fix, porażka miękka, klasyfikacja `env_issue`/`flaky`.
Referencyjne skrypty `gh` + dokumentacja MCP (claude `--mcp-config`, codex
`config.toml`). Żywy test na tym repo (ma GitHub Actions? jeśli nie — minimalny
workflow z `python3 -m unittest` jako królik doświadczalny).

**E-V2 — hardware.** `flash_cmd`/`target_cmd`/`probe_cmd`, preflight, retry
flashowania, pełne logi seriala w `.forge/verify/`. Żywy test wymaga fizycznego
targetu — do tego czasu bramki na skryptach-atrapach (mock „płytki" jako skrypt
echo/exit, dokładnie jak testujemy `run_gate`).

**E-V3 — hartowanie po danych.** Po pierwszych biegach: kalibracja limitów
(`FORGE_MAX_VERIFY_FIXES`, timeouty), ewentualny `FORGE_VERIFY_EVERY=N`,
raport skuteczności (ile zadań łapie weryfikacja, których nie złapała bramka
testów — to uzasadnia jej koszt), decyzja czy weryfikator dostaje sesję.

Kolejność jest nieprzypadkowa: E-V0 daje 80% maszynerii przy 20% ryzyka;
CI i hardware to już tylko inne „źródła czerwieni" wpięte w gotowe fazy.

## 9. Ryzyka i świadome ograniczenia

1. **Flaky CI / infra** — największy zjadacz rund naprawczych. Mitygacje:
   werdykt `flaky`/`env_issue` (nie pali rund kodera), jedna darmowa
   ponowna próba, porażka miękka zamiast blokowania pętli.
2. **Koszt czasu zegarowego** — CI potrafi mielić 20+ min per push. Pętla
   czeka za darmo (tokeny=0), ale zadania/h spadną. Odpowiedź: pomiar w E-V3,
   ewentualnie `FORGE_VERIFY_EVERY`, świadomie NIE asynchroniczność
   (obserwowanie CI zadania N podczas pracy nad N+1 wymaga rozplątania
   „naprawa czego?" — złożoność jak E5, odłożona z tego samego powodu).
3. **Agent psuje weryfikację zamiast kodu** — pokryte anty-osłabianiem
   (sekcja 4); pozostaje ryzyko sprytniejszych obejść (np. warunkowe skipy w
   testach na targecie) — backstop: recenzja i człowiek rano, jak przy testach.
4. **Sekrety CI** — `gh`/MCP wymagają tokenu na maszynie pętli; forge nie
   zarządza sekretami, tylko dokumentuje wymóg (jak dziś `codex login`).
5. **Hardware = świat fizyczny** — zawieszony target potrafi wisieć mimo
   timeoutów subprocessa (np. otwarty port). Kontrakt: to skrypt `target_cmd`
   odpowiada za sprzątanie po sobie; forge dokłada swój timeout i loguje.
6. **Bootstrap może zadeklarować bzdurne komendy weryfikacji** — jak dziś z
   `test_cmd`; mitygacja: walidacja niepustości per tryb + smoke `--dry` +
   (dla `smoke`/`hardware`) natychmiastowy pierwszy bieg weryfikacji po
   bootstrapie, żeby usterka profilu wyszła od razu, nie po pierwszym zadaniu.

## 10. Kryteria akceptacji planu (do E-V0/E-V1)

- [ ] Zadanie z czerwonym smoke NIE jest pushowane; po limicie napraw —
      rollback do taga, wpis w failures.md.
- [ ] Zadanie z czerwonym CI: naprawy jako kolejne commity `fix:`, historia
      remote nieprzepisana; po limicie — porażka miękka, pętla idzie dalej.
- [ ] Restart w fazie `verify_ci` wznawia obserwację tego samego SHA.
- [ ] Tryb `none` = bit-w-bit dzisiejsze zachowanie (regresyjne testy pętli
      przechodzą bez zmian).
- [ ] Żadna runda naprawcza nie zmienia plików workflow/skryptów weryfikacji
      bez jawnej deklaracji weryfikatora.
- [ ] Wszystkie nowe bramki mają testy jednostkowe bez agentów (mock-skrypty).
