REVIEWER ZAAKCEPTOWAŁ BIEŻĄCY DIFF Z SUGESTIAMI. Oryginalny wynik jest
bezpieczny do commita bez tych sugestii. Przejrzyj każdą sugestię z handoffu i:

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

Jeśli praca wykroczyła poza sugestie, zmieniła publiczne zachowanie, ujawniła
rzeczywisty błąd albo nie masz pewności co do bezpieczeństwa diffu, wybierz
review — to świadoma eskalacja do nowej recenzji. Możesz też wybrać red, code
albo blocked, jeśli faktycznie wymaga tego odkryty problem. Dla red/code zwróć
używaną komendę w `command`.
