REVIEWER ZAAKCEPTOWAŁ BIEŻĄCY DIFF, ale mimo roli read-only sam zmienił pliki
w drzewie.
To CYKL DOMYKAJĄCY: drugiej recenzji nie będzie, zadanie dostarczasz ty.
Zajmij się wyłącznie tym, co recenzent zostawił po sobie:

- obejrzyj jego diff na plikach wskazanych w handoffie;
- zachowaj zmianę, jeśli jest poprawna i mieści się w zakresie zadania;
- popraw ją albo przywróć poprzedni stan, jeśli jest błędna, zbędna lub
  wychodzi poza zakres;
- nie zaczynaj przy okazji nowej pracy — reszta diffu jest już zrecenzowana.

Gdy stan drzewa jest według ciebie poprawny, uruchom najwęższą wiarygodną
bramkę i wybierz finalize. W `reason` napisz, co zrobiłaś ze zmianami
recenzenta. `finalize` prowadzi do pełnej bramki `{{FULL_TEST_CMD}}` i commita
bez ponownego review.

Jeśli diff recenzenta odsłonił rzeczywisty błąd zachowania, wybierz red albo
code i domknij go normalnym cyklem TDD — ten cykl kończy się finalize także po
poprawce. Blocked zostaje na sytuację, w której nie da się iść dalej bez
decyzji człowieka. Dla red/code zwróć używaną komendę w `command`.
