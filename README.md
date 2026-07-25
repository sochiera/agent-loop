# Forge

Forge buduje oprogramowanie przez małe zadania TDD:

`tester ↔ coder → tester → review`; `changes → tester` (nowy cykl TDD), `approve → commit`

Wymaga Pythona 3.10+, Gita oraz skonfigurowanych CLI agentów. Zależność
deweloperską do uruchomienia testów instaluje
`python3 -m pip install -r requirements-dev.txt`.

Planista tworzy mały wsad zadań. Tester zachowuje własną sesję przez jedno
zadanie i w każdej rundzie wybiera `red`, `code`, `review` lub `blocked`.
Koder ma osobną sesję i może zwrócić `green`, `test_changes_needed` albo
`tester_input_needed`; oba niezielone wyniki wracają do testera.
Reviewer zawsze pracuje w świeżym, read-only kontekście.

Tester i koder odpowiadają za uruchomienie właściwych testów zgodnie ze swoimi
promptami, a świeży reviewer ocenia diff, implementację i testy. Forge nie
uruchamia dodatkowej automatycznej bramki i nie blokuje zmian na podstawie
ścieżki pliku. Ledger pokazuje Mistrzowi ścieżki zmienione w każdej turze;
tester przekazuje uwagi koderowi w decyzji, a podsumowanie kodera wraca jako
handoff do testera. Uwagi review oraz ewentualne pliki zapisane przez reviewera
wracają do testera, który rozpoczyna kolejny cykl TDD. Dopiero zaakceptowany
review bez zapisów prowadzi do commitu i wyczyszczenia sesji. Mistrz obserwuje
ledger i przy powtarzającej się bez postępu pętli review poleca testerowi
zakończyć zadanie jako `blocked`.

```bash
python3 -m forge.orchestrate --brief game.md --project game --max-tdd-rounds 4
```

Zadanie planisty zawiera: cel, kryteria akceptacji, publiczny kontrakt, ścieżki testów i kodu, opcjonalną komendę ukierunkowaną, trudność oraz zakres wykluczony. Szczegóły wykonania opisuje [docs/PIPELINE.md](docs/PIPELINE.md).

Migracja: wcześniejszy `STATE.json` z katalogu projektu jest odczytywany przed startem. Bezczynny stan zostanie przeniesiony do `.forge/STATE.json`. Aby świadomie porzucić aktywne zadanie starego pipeline’u bez utraty bootstrapu, komend i profilu weryfikacji, uruchom `python3 -m forge.orchestrate --project game --discard-legacy-task`; oryginał trafi do `.forge/STATE.legacy-discarded.json`.

Testy Forge są zbierane przez pytest:

```bash
python3 -m pytest -q
```
