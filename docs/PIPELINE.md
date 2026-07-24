# Forge KISS pipeline

Jedno zadanie przechodzi kolejno przez `tester`, `coder`, `review`, opcjonalne `corrections` i `commit`.

Tester decyduje o dalszym kroku: `red`, `code`, `review` albo `blocked`. Po `red` lub `code` koder odpowiada `green` albo `test_changes_needed`; w drugim przypadku sterowanie wraca do tej samej sesji testera. Limit `max_tdd_rounds` wynosi domyślnie 4 i oznacza potrzebę podziału zadania.

Przed review Forge uruchamia test ukierunkowany oraz pełną suitę. Reviewer jest świeży i read-only. Przy `changes` zachowany koder realizuje jedną turę poprawek, po której Forge ponawia granicę testową. Sesje są czyszczone dopiero po udanym commicie. Błąd zapisuje artefakt w `.forge/failed/<task-id>` i przywraca tag startowy zadania.

Checkpoint opisuje następną czynność. Przed wywołaniem kodera Forge zapamiętuje
odcisk testów i całego drzewa. Po restarcie zmieniony test blokuje automatyczne
wznowienie, a zastane zmiany produkcyjne wracają do oceny testera zamiast
powodować drugą turę edycji kodera.

Niepoprawna decyzja JSON dostaje dokładnie jedną prośbę o korektę samego
formatu. Druga niepoprawna odpowiedź zatrzymuje przebieg z zapisanym
checkpointem.

Kanoniczna pełna suita repozytorium:

```bash
python3 -m pytest -q
```
