# forge — pętla agentów budujących oprogramowanie

Orkiestrator, który w kółko odpala agentów CLI, aż skończą się limity subskrypcji.
Buduje **grę albo dowolny inny program** — agent bootstrapu sam rozpoznaje z briefu
rodzaj produktu (`game`/`app`) i dostosowuje słownictwo planowania. Rolę każdego
agenta może pełnić **dowolne narzędzie CLI** (claude, codex, a przez szablon
komendy także grok, Kiro i inne — patrz „Dowolny agent CLI").

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

> **Uwaga o Opusie na Pro $20:** Opus w każdej fazie wyczerpie tygodniowy limit
> bardzo szybko. Gdy zacznie boleć, zejdź na `--claude-model sonnet` — pętla
> pociągnie znacznie dłużej. Orkiestrator sam wykrywa komunikaty o limitach,
> robi backoff (z logiem czasu wznowienia), a po wyczerpaniu — czysty stop.

## Dowolny agent CLI (claude, codex, grok, Kiro, …)

Każdą rolę — planistę, testera, kodera — może pełnić dowolny agent CLI. `claude`
i `codex` mają wbudowaną obsługę. Inne narzędzie wpinasz **bez zmian w kodzie**,
podając szablon jego komendy w zmiennej `FORGE_AGENT_<NAZWA>_CMD`:

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

Tylko `codex` wznawia sesje (`codex exec resume`) — dający ciągły kontekst per
zadanie. Pozostali agenci są bezsesyjni: ciągłość zapewnia im **dziennik zadania**
(`.forge/task_journal.md`), który orkiestrator dokleja do promptu. To ta sama
filozofia „pamięć w repo" — działa dla każdego CLI, tylko trochę drożej tokenowo.

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
