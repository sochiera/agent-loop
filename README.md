# forge — pętla agentów budujących grę

Orkiestrator, który w kółko odpala dwóch agentów CLI, aż skończą się limity
subskrypcji:

```
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
python3 -m forge.orchestrate --check
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
| Limit iteracji | bez limitu | `--max-iters 20` |
| Opóźnienie startu | brak | `--sleep 30s`, `--sleep 5m`, `--sleep 2h` |
| Timeout agenta | 3600 s | `FORGE_AGENT_TIMEOUT=...` |
| Push do remote | włączony (`origin`) | `FORGE_GIT_PUSH=0`, `FORGE_GIT_REMOTE=...` |

> **Uwaga o Opusie na Pro $20:** Opus w każdej fazie wyczerpie tygodniowy limit
> bardzo szybko. Gdy zacznie boleć, zejdź na `--claude-model sonnet` — pętla
> pociągnie znacznie dłużej. Orkiestrator sam wykrywa komunikaty o limitach,
> robi backoff (z logiem czasu wznowienia), a po wyczerpaniu — czysty stop.

## Co powstaje w `game/`

```
game/
  STATE.json            # stan pętli + ustalone komendy test/build/run
  BACKLOG.md            # kolejka zadań (planista pisze, implementator zjada)
  docs/DESIGN.md        # żywy projekt gry
  docs/ARCHITECTURE.md  # decyzje techniczne
  .forge/               # logi każdej fazy, bieżące zadanie, lista niepowodzeń
  <kod gry>             # w TDD
```

Każdy commit = jeden ukończony, przetestowany przyrost.

Testy samego orkiestratora:

```bash
python3 -m unittest discover -v
```

## Licencja

Projekt jest udostępniany na licencji MIT. Szczegóły znajdują się w pliku
[`LICENSE`](LICENSE).
