REVIEWER ZAAKCEPTOWAŁ BIEŻĄCY DIFF Z SUGESTIAMI. Oryginalny wynik jest
bezpieczny do commita bez tych sugestii.
To CYKL DOMYKAJĄCY: drugiej recenzji nie będzie, zadanie dostarczasz ty.
Przejrzyj każdą sugestię z handoffu i:

- zastosuj ją, jeśli jest trafna i pozostaje małą zmianą w jej zakresie;
- albo odrzuć ją, podając konkretny powód w `reason`;
- testy i ich infrastrukturę możesz poprawić sama;
- dla zaakceptowanej sugestii dotyczącej kodu produkcyjnego lub dokumentacji
  wybierz code i przekaż koderowi dokładny, ograniczony zakres;
- nie twórz czerwonego testu dla opcjonalnego refaktoru bez zmiany zachowania.

Gdy wszystkie sugestie zostały zastosowane albo świadomie odrzucone, uruchom
najwęższą wiarygodną bramkę i wybierz finalize. W `reason` rozlicz każdą
sugestię jako zastosowaną lub odrzuconą. `finalize` prowadzi do pełnej bramki
`{{FULL_TEST_CMD}}` i commita bez ponownego review.

Jeśli praca ujawniła rzeczywisty błąd zachowania, wybierz red albo code i
domknij go normalnym cyklem TDD — ten cykl kończy się finalize także po
poprawce. Blocked zostaje na sytuację, w której nie da się iść dalej bez
decyzji człowieka. Dla red/code zwróć używaną komendę w `command`.
