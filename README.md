# forge — pętla agentów budujących oprogramowanie

Orkiestrator, który w kółko odpala agentów CLI, aż skończą się limity subskrypcji.
Buduje **grę albo dowolny inny program** — agent bootstrapu sam rozpoznaje z briefu
rodzaj produktu (`game`/`app`) i dostosowuje słownictwo planowania. Rolę każdego
agenta może pełnić **dowolne narzędzie CLI** — wbudowane `claude`, `codex`/`gpt`,
gotowe do użycia `grok` i `kiro`, a przez szablon komendy także dowolne inne —
patrz „Dowolny agent CLI — pełna dowolność ról".

**Domyślny model (mikro-TDD ping-pong).** Claude planuje wsadowo (kilka zadań w
przód), a każde zadanie realizuje para instancji Codeksa w rytmie red→green→refactor:

```
PLAN WSADOWY (Claude)              →  N zadań w kolejce (.forge/tasks/)
  dla każdego zadania po kolei (ciągły kontekst sesji per rola):
    TESTER (Codex-1)  → jeden failujący test  → [bramka CZERWONA]
    KODER  (Codex-2)  → kod → zieleń → refaktor → [bramka ZIELONA] → commit cyklu
    ↺ aż TESTER orzeknie DONE (kryteria pokryte)
  RECENZJA całości (Codex-1)  →  poprawki (Codex-2)  →  commit + push zadania
```

Podziału ról pilnuje **orkiestrator mechanicznie** (bramka czerwona/zielona,
kontrola diffu `git`, mapa kryterium→test przy DONE) — nie same prompty. Stary
przebieg `plan → implement → review(Claude) → fix` jest wciąż dostępny pod
`--legacy` (albo `FORGE_LEGACY_MODE=1`).

```
# legacy:
PLAN (Claude lub Codex)  →  IMPLEMENT-TDD (Codex)  →  [bramka testów]
   →  REVIEW (wybrany planista)  →  FIX (Codex)  →  commit  ↺   (albo rollback)
```

**Stack-agnostyczny.** Nie zna Pythona ani Wesnotha. Podajesz prosty opis gry
(`game_brief.md`); agent bootstrapu wybiera stack, tworzy dokumentację i szkielet,
ustala komendy `test`/`build`/`run` i zapisuje je w `STATE.json`. Potem pętla
buduje grę małymi, przetestowanymi przyrostami.

## Filozofia (dlaczego to działa i nie gubi wiedzy)

- **Pamięć = repozytorium.** Wiedza o grze żyje w `docs/DESIGN.md`,
  `docs/ARCHITECTURE.md`, `BACKLOG.md` i historii gita — nie w kontekście modelu.
  Każdy agent to świeże, bezstanowe wywołanie, które **samo czyta potrzebne pliki**.
  To jest optymalne tokenowo (żadnego przepychania transkryptu) i wznawialne.
- **Guardraile pełnej autonomii.** Każda iteracja kończy się commitem tylko gdy
  testy są zielone **i** review zaakceptował; inaczej `git reset --hard`. Rano
  oglądasz czytelną historię commitów zamiast sieczki.
- **Wznawialność faz.** Przed każdą fazą zapisywany jest checkpoint w `STATE.json`.
  Limit lub Ctrl-C zachowuje bieżące zmiany, więc kolejne uruchomienie wznawia
  `plan`, `implement`, `review` albo `fix` zamiast zaczynać iterację od początku.

## Wymagania

1. **Codex CLI** (masz: `codex` 0.142.3). Zalogowany kontem ChatGPT: `codex login`.
   Nowy model używa sesji `codex exec --json` (przechwycenie session id) i
   `codex exec resume <id>` dla ciągłego kontekstu per zadanie — potrzebna wersja
   wspierająca `resume` (nowsze 0.14x mają).
2. **Claude Code jako CLI** — u Ciebie **jeszcze go nie ma na PATH** (działasz przez
   rozszerzenie VSCode). Doinstaluj standalone, np.:
   ```bash
   curl -fsSL https://claude.ai/install.sh | bash    # instalator natywny
   #   albo, jeśli masz Node:  npm i -g @anthropic-ai/claude-code
   claude login          # zaloguj kontem z subskrypcją
   ```
   Jeśli binarka jest pod inną nazwą/ścieżką: `export FORGE_CLAUDE_BIN=/pełna/ścieżka/claude`.
3. **git**, **python 3.10+**.

Sprawdź gotowość bez uruchamiania pętli:

```bash
python3 -m forge.orchestrate --check   # preflight konfiguracji
python3 -m forge.smoke --dry           # binarki + wsparcie `codex exec resume` (0 tokenów)
python3 -m forge.smoke                 # żywy test sesji/resume/pomiaru (2 najtańsze wywołania)
```

Zużycie tokenów obejrzysz po każdym biegu (drukowane na końcu) albo ręcznie:

```bash
python3 -m forge.report game
```

## Uruchomienie

```bash
python3 -m forge.orchestrate --brief game.md --project game
```

Start można opóźnić parametrem `--sleep`; liczba bez sufiksu oznacza sekundy,
a dostępne sufiksy to `s`, `m` i `h`:

```bash
python3 -m forge.orchestrate --sleep 45m
```

Przy starcie program kolejno pyta, czy planistą ma być Claude czy Codex, następnie
o jego model i effort, a potem o model i effort implementatora (Codex). Wybrany
planista wykonuje bootstrap, planowanie i review. Enter zachowuje pokazaną wartość
domyślną. W skryptach i zadaniach bez terminala pytania można wyłączyć flagą
`--non-interactive` i podać ustawienia flagami, np.:

```bash
python3 -m forge.orchestrate --non-interactive \
  --planner-agent codex --planner-model gpt-5.6-sol --planner-effort high \
  --codex-model gpt-5.6-sol --codex-effort high
```

Zostaw działające — pętla leci sama. Zatrzymanie: utwórz plik `game/STOP`
albo Ctrl-C (stan zostaje zapisany).

### Repo gry i push

Katalog `--project` może być **klonem repo gry** (osobnym od tego narzędzia):

```bash
git clone git@github.com:<user>/<repo>.git game     # SSH → push bez tokenów
python3 -m forge.orchestrate --brief game.md --project game
```

Orkiestrator wykrywa istniejące `.git` (nie robi `init`), commituje jako Twoja
globalna tożsamość git i po **każdym udanym commicie pcha bieżący branch** do
`origin`. Push jest niekrytyczny — błąd sieci/auth tylko się loguje, pętla leci
dalej; wypchnięta historia nigdy nie jest przepisywana (rollback dotyka tylko
niezacommitowanych zmian). Wyłącznik: `FORGE_GIT_PUSH=0`. `STATE.json` i `.forge/`
są ignorowane, więc repo gry zostaje czyste od metadanych narzędzia.

## Pokrętła (zmienne środowiskowe / flagi)

| Co | Domyślnie | Jak zmienić |
|---|---|---|
| Agent planujący | `claude` | `--planner-agent codex` lub `FORGE_PLANNER_AGENT` |
| Model planisty | `opus` dla Claude | `--planner-model ...` lub `FORGE_PLANNER_MODEL` |
| Effort planisty | `high` dla Claude | `--planner-effort medium` lub `FORGE_PLANNER_EFFORT` |
| Model Codex | z `~/.codex/config.toml` | `--codex-model ...` lub `FORGE_CODEX_MODEL` |
| Effort Codex | `medium` | `--codex-effort high` lub `FORGE_CODEX_EFFORT` |
| Sandbox Codeksa | `danger-full-access` (pełny dostęp) | zawęź: `FORGE_CODEX_SANDBOX=workspace-write` |
| Ścieżka do Claude | `claude` | `FORGE_CLAUDE_BIN=/path/claude` |
| Tryb pętli | mikro-TDD (nowy) | `--legacy` lub `FORGE_LEGACY_MODE=1` |
| Rozmiar wsadu planowania | `5` | `--batch-size N` lub `FORGE_BATCH_SIZE` |
| Sufit mikro-cykli/zadanie | `12` | `--max-micro-cycles N` lub `FORGE_MAX_MICRO_CYCLES` |
| Dogrywki „zazielenienia"/cykl | `2` | `FORGE_MAX_GREEN_RETRIES` |
| Agent testera | `codex` | `--tester-agent NAZWA` lub `FORGE_TESTER_AGENT` |
| Agent kodera | `codex` | `--coder-agent NAZWA` lub `FORGE_CODER_AGENT` |
| Model/effort testera | z `codex_model`/`effort` | `--tester-model/--tester-effort` lub `FORGE_TESTER_*` |
| Model/effort kodera | z `codex_model`/`effort` | `--coder-model/--coder-effort` lub `FORGE_CODER_*` |
| Limit iteracji | bez limitu | `--max-iters 20` |
| Opóźnienie startu | brak | `--sleep 30s`, `--sleep 5m`, `--sleep 2h` |
| Timeout agenta | 3600 s | `FORGE_AGENT_TIMEOUT=...` |
| Push do remote | włączony (`origin`) | `FORGE_GIT_PUSH=0`, `FORGE_GIT_REMOTE=...` |
| Recenzent zadania | agent testera, ŚWIEŻY kontekst | `--reviewer-agent/model/effort` lub `FORGE_REVIEWER_AGENT/MODEL/EFFORT` |
| Weryfikator celu | agent planisty | `--verifier-agent/model/effort` lub `FORGE_VERIFIER_AGENT/MODEL/EFFORT` |
| Globy toolchainu testów (extra) | heurystyka + deklaracja bootstrapu | `FORGE_TOOLCHAIN_GLOBS` (CSV) |
| Rotacja sesji ról co K cykli | `6` (0 = wyłączona) | `FORGE_SESSION_ROTATE_CYCLES` |
| Sufit skrótu dziennika zadania | `8000` znaków | `FORGE_JOURNAL_TAIL_CHARS` |
| Testy read-only na turę kodera | włączone | `FORGE_LOCK_TESTS=0` |
| Re-plan wsadu po porażce zadania | włączony | `FORGE_REPLAN_ON_FAILURE=0` |

> **Uwaga o Opusie na Pro $20:** Opus w każdej fazie wyczerpie tygodniowy limit
> bardzo szybko. Gdy zacznie boleć, zejdź na `--claude-model sonnet` — pętla
> pociągnie znacznie dłużej. Orkiestrator sam wykrywa komunikaty o limitach,
> robi backoff (z logiem czasu wznowienia), a po wyczerpaniu — czysty stop.

## Weryfikacja celu (CI + real hardware)

Gdy planista orzeknie `no_more_tasks`, pętla NIE kończy pracy: startuje
**weryfikacja celu** — świeży agent (weryfikator-QA) sprawdza, czy całość
naprawdę działa w środowisku docelowym. Szczegóły projektu:
`docs/PLAN-3-WERYFIKACJA.md`.

- **Profil deklaruje bootstrap** w `STATE.json` (jak `test_cmd`): targety
  (`smoke`/`ci`/`hardware`) i komendy — `smoke_cmd`, `flash_cmd`+`target_cmd`
  (+opcjonalny `probe_cmd`), `ci_status_cmd`/`ci_logs_cmd` z placeholderem
  `{sha}` (kontrakt: rc 0=zielono, 1=czerwono, 2=trwa). Nadpisanie:
  `FORGE_VERIFY_TARGETS=ci,hardware` albo `FORGE_VERIFY_TARGETS=none` (wyłącz).
- **Dowody zbiera orkiestrator za darmo** (polling CI z backoffem, flash z
  retry, pełne logi w `.forge/verification/cycle-N/`), agent dostaje kody
  wyjścia i ścieżki. PASS wymaga zielonych rc WSZYSTKICH targetów i pustego
  rejestru blokerów — agent nie przegłosuje czerwieni.
- **Porażka = rejestr problemów + obszerny `feedback.md`** → planista planuje
  zadania naprawcze (`fixes` + `repro_cmd`) i cykl rusza od nowa. Repro jest
  bramką zadania: czerwone na starcie, zielone na koniec (razem z suitą).
- **Twarde stopy:** potwierdzony `env_issue` (kod wyjścia 4 — sprawa
  człowieka), brak postępu `FORGE_MAX_STALL_CYCLES` cykli z rzędu albo sufit
  `FORGE_MAX_VERIFY_CYCLES` (kod 5).
- **Ochrona przed osłabianiem:** workflow CI, skrypty weryfikacji i testy
  targetowe (`verify_test_globs`) są wycofywane z diffu, chyba że zadanie
  naprawia problem klasy `verify_defect` z rejestru.
- Pokrętła: `FORGE_VERIFIER_AGENT/MODEL/EFFORT` (domyślnie planista),
  `FORGE_CI_TIMEOUT` (45 min), `FORGE_VERIFY_TIMEOUT`, `FORGE_FLASH_RETRIES`,
  `FORGE_MAX_REPRO_RUNS`, `FORGE_CI_EARLY_WARN` (ostrzeżenie o czerwonym CI
  przy każdym planowaniu), `FORGE_VERIFIER_MCP_CONFIG` (plik MCP doklejany
  do Claude'a tylko w roli weryfikatora — np. serwer GitHuba do debugowania CI).

## Uszczelnienie bramek i higiena kontekstu (PLAN-4)

Projekt: `docs/PLAN-4-BRAMKI-I-KONTEKST.md`. W skrócie:

- **Toolchain testowy pod ochroną.** Pliki konfigurujące uruchamianie testów
  (`package.json`, `pytest.ini`, `Makefile`… + deklaracja bootstrapu
  `test_toolchain_globs` + `FORGE_TOOLCHAIN_GLOBS`) przechodzą bramkę
  anty-osłabiania: testy cyklu i toolchain w bieżącej postaci MUSZĄ failować
  na kodzie sprzed cyklu (worktree na HEAD, z baseline'em odsiewającym pomiary
  niemiarodajne środowiskowo). Wykastrowanie runnera nie może być „naprawą".
- **Koniec samocertyfikacji DONE.** Mapa kryterium→test jest walidowana
  mechanicznie (plik istnieje, jest ścieżką testową, nazwa testu występuje
  w treści; `justified` wymaga merytorycznego `why`), a powody odrzucenia
  wracają do testera w kolejnym prompcie.
- **Recenzent ≠ autor.** Recenzja zadania idzie w świeżym kontekście (bez sesji
  testera/kodera i bez dziennika), z kontekstem budowanym przez orkiestrator:
  tag startu zadania, lista zmian, zmiany toolchainu, kryteria `justified` do
  rozstrzygnięcia. Domyślnie to agent testera; **zalecana dywersyfikacja** —
  wspólny model to wspólne ślepe punkty, np. `FORGE_REVIEWER_AGENT=claude`
  przy koderze-Codeksie (decyzja kosztowa).
- **Higiena kontekstu.** Sesje ról rotują co `FORGE_SESSION_ROTATE_CYCLES`
  cykli (świeża sesja startuje z dziennika zadania, wzbogacanego mechanicznie
  o pliki każdego cyklu); porażka zadania czyści resztę wsadu planowania —
  plan budowany przy założeniu sukcesu jest przeplanowywany z `failures.md`.

## Dowolny agent CLI — pełna dowolność ról (claude, gpt, grok, Kiro, …)

Każdą rolę — **planistę, testera, kodera, recenzenta, weryfikatora** — może
pełnić inny agent CLI, z innym modelem. Zero ograniczeń co do kombinacji: np.
Fable planuje, GPT (Codex) pisze testy, Grok pisze kod, a Sonnet recenzuje:

```bash
python3 -m forge.orchestrate --non-interactive \
  --planner-agent claude --planner-model claude-fable-5 --planner-effort high \
  --tester-agent  gpt   --codex-effort high \
  --coder-agent   grok \
  --reviewer-agent claude --reviewer-model claude-sonnet-5
```

(albo równoważnie przez zmienne środowiskowe `FORGE_PLANNER_AGENT`,
`FORGE_TESTER_AGENT`, `FORGE_CODER_AGENT`, `FORGE_REVIEWER_AGENT` + `*_MODEL`/`*_EFFORT`
— patrz tabela pokręteł niżej; jest też `FORGE_VERIFIER_AGENT` dla roli
weryfikatora celu).

### Agenci wbudowani i wspierani z gotowa

| Nazwa | Co to jest | Obsługa |
|---|---|---|
| `claude` | Claude Code CLI | wbudowana (sesje przez dziennik zadania, `--output-format json`) |
| `codex` / `gpt` | Codex CLI (OpenAI, modele GPT) | wbudowana, z ciągłością sesji (`codex exec resume`) — `gpt` to wygodny alias na `codex` |
| `grok` | xAI Grok Build CLI (`grok`) | gotowy domyślny szablon (`grok -p {prompt} -m {model} --always-approve`), nadpisywalny |
| `kiro` | Kiro CLI (AWS, `kiro-cli`) | gotowy domyślny szablon (`kiro-cli chat --no-interactive --trust-all-tools {prompt}`); model ustawiasz w `~/.kiro/settings/cli.json` (headless nie ma dziś flagi `--model`) |

`grok` i `kiro` działają "z pudełka" pod swoją nazwą — bez ustawiania żadnej
zmiennej środowiskowej — bo mają wbudowany domyślny szablon komendy (zgodny z
oficjalną dokumentacją, stan 2026-07). Jeśli Twoja wersja CLI ma inne flagi,
nadpisz go tak samo jak dla zupełnie nowego narzędzia (patrz niżej).

> **Effort dla grok/kiro.** Domyślne szablony **nie** przekazują `effort`
> (Grok/Kiro nie mają dziś odpowiednika flagi „reasoning effort" w headless).
> Ustawienie `--coder-effort`/`FORGE_CODER_EFFORT` dla tych agentów jest więc
> **po cichu ignorowane** — jeśli Twoja wersja CLI to obsługuje, dodaj
> `{effort}` do własnego `FORGE_AGENT_<NAZWA>_CMD`.

### Zupełnie inny/nieznany CLI

Dowolne inne narzędzie (aider, własny skrypt, inna wersja grok/kiro, …) wpinasz
**bez zmian w kodzie**, podając szablon jego komendy w zmiennej
`FORGE_AGENT_<NAZWA>_CMD` — to samo działa, by NADPISAĆ domyślny szablon `grok`/`kiro`:

```bash
export FORGE_AGENT_GROK_CMD='grok --model {model} --exec {prompt} --out {output}'
python3 -m forge.orchestrate --coder-agent grok --planner-agent claude
```

Placeholdery szablonu: `{prompt}` `{model}` `{effort}` `{project}` `{output}`.
Jeśli szablon zawiera `{output}`, wynik czytamy z tego pliku; inaczej ze stdout.
Token będący samym placeholderem, który rozwinie się do pusta (np. `{model}` bez
ustawionego modelu), jest pomijany **razem z poprzedzającą go flagą** — czyli
`--model {model}` bez modelu znika w całości, a nie zostawia wiszącego `--model`.

Kontrakt, którego forge nie wyegzekwuje za Ciebie (CLI bywają różne):
- przy porażce agent musi wyjść **kodem ≠ 0** (inaczej „błąd w treści" przy kodzie
  0 zostanie uznany za sukces),
- finalny blok ```json wypisz na **stdout** albo do pliku `{output}`; diagnostykę na stderr,
- zużycia tokenów generyka nie znamy — nie trafia do `.forge/usage.jsonl`.

Tylko `codex`/`gpt` wznawia sesje (`codex exec resume`) — dający ciągły kontekst
per zadanie. Pozostali agenci są bezsesyjni: ciągłość zapewnia im **dziennik
zadania** (`.forge/task_journal.md`), który orkiestrator dokleja do promptu. To
ta sama filozofia „pamięć w repo" — działa dla każdego CLI, tylko trochę drożej
tokenowo.

## Co powstaje w `game/`

```
game/
  STATE.json            # stan pętli (kolejka zadań, sesje ról, mikro-cykl) + komendy test/build/run
  BACKLOG.md            # kolejka zadań wysokiego poziomu (planista pisze)
  BACKLOG-ARCHIVE.md    # ukończone pozycje (odciąża rosnący BACKLOG)
  docs/DESIGN.md        # żywy projekt gry
  docs/ARCHITECTURE.md  # decyzje techniczne
  .forge/tasks/         # task-NNN.md — zadania z planowania wsadowego (kryteria, ścieżki)
  .forge/usage.jsonl    # pomiar zużycia tokenów per faza/agent
  .forge/               # logi każdej fazy, bieżące zadanie, lista niepowodzeń
  <kod gry>             # w TDD
```

W nowym modelu każdy mikro-cykl (test+kod+refaktor) to jeden lokalny commit; push
całego, zielonego zadania następuje raz — po recenzji. Porażka zadania cofa się do
taga startu zadania (lokalnie; nic niezielonego nie trafia na remote).

Testy samego orkiestratora:

```bash
python3 -m unittest discover -v
```

## Licencja

Projekt jest udostępniany na licencji MIT. Szczegóły znajdują się w pliku
[`LICENSE`](LICENSE).
