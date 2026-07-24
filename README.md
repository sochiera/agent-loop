# Forge

Forge buduje oprogramowanie przez małe zadania TDD:

`tester → coder → tester → review → [corrections] → commit`

Wymaga Pythona 3.10+, Gita oraz skonfigurowanych CLI agentów. Zależność
deweloperską do uruchomienia testów instaluje
`python3 -m pip install -r requirements-dev.txt`.

Planista tworzy mały wsad zadań. Tester zachowuje własną sesję przez jedno zadanie i w każdej rundzie wybiera `red`, `code`, `review` lub `blocked`. Koder ma osobną sesję i może zwrócić `green` lub `test_changes_needed`. Reviewer zawsze pracuje w świeżym, read-only kontekście.

Jedyna automatyczna ochrona normalnej pętli porównuje hash testów przed i po turze kodera. Gdy tester wybierze `review`, Forge uruchamia test ukierunkowany (jeżeli zadanie go ma) oraz pełną suitę. Po uwagach review ten sam koder wykonuje jedną turę poprawek, testy są uruchamiane ponownie, a następnie Forge commituje wynik i czyści sesje.

```bash
python3 -m forge.orchestrate --brief game.md --project game --max-tdd-rounds 4
```

Zadanie planisty zawiera: cel, kryteria akceptacji, publiczny kontrakt, ścieżki testów i kodu, opcjonalną komendę ukierunkowaną, trudność oraz zakres wykluczony. Szczegóły wykonania opisuje [docs/PIPELINE.md](docs/PIPELINE.md).

Migracja: wcześniejszy `STATE.json` z katalogu projektu jest odczytywany przed startem. Bezczynny stan zostanie przeniesiony do `.forge/STATE.json`. Aby świadomie porzucić aktywne zadanie starego pipeline’u bez utraty bootstrapu, komend i profilu weryfikacji, uruchom `python3 -m forge.orchestrate --project game --discard-legacy-task`; oryginał trafi do `.forge/STATE.legacy-discarded.json`.

Testy Forge są zbierane przez pytest:

```bash
python3 -m pytest -q
```
