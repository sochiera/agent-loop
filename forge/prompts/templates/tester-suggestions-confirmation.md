TURA POTWIERDZAJĄCA po green kodera w cyklu domykającym. {{SUGGESTED}}

Uruchom celowaną bramkę, sprawdź zakres zmian kodera i rozlicz wszystko, co
przyszło w handoffie — uwagi review albo diff zostawiony przez recenzenta.
Jeśli zaakceptowane poprawki pozostały małe, wynik jest zielony, a resztę
świadomie odrzuciłaś z powodem, wybierz finalize.
Forge uruchomi pełną bramkę `{{FULL_TEST_CMD}}` i zacommituje bez drugiego
review.

Drugiej recenzji w tym cyklu nie ma — to ty domykasz zadanie. Jeśli zmiany
odsłoniły konkretny problem, wybierz red albo code i domknij go normalnym
cyklem TDD; blocked zostaje na sytuację bez wyjścia bez decyzji człowieka.
Dla red/code zwróć faktycznie używaną komendę w `command`.
