Pierwszy backlog projektu. Szkielet właśnie stanął i ma zielony test, a
docs/PROJECT.md niesie wizję; historyjek nie ma jeszcze żadnych i BACKLOG.md
może nie istnieć — wtedy utwórz go.

Ten jeden raz planujesz KSZTAŁT CAŁEGO MVP, a nie pierwszy przyrost. Napisz po
jednej historyjce na KAŻDĄ sekcję z mapy pokrycia briefu — w jej najcieńszej
sensownej wersji, ale tak, żeby każda sekcja miała swoją pozycję. Backlog ma po
tej turze pokazywać całą mapę produktu, nie jego róg.

Powód jest mierzalny i wynika z przebiegu, który się nie udał. Pierwszy backlog
opisywał wtedy „najcieńszy pionowy plasterek: maksymalnie 3 historyjki", a każdy
kolejny przegląd dokładał po jednej historyjce sąsiadującej z tym, co już
działało. Po pięćdziesięciu zadaniach i trzynastu godzinach produkt miał
dwanaście wariantów porównywania i ZERO linii kodu w ośmiu sekcjach briefu,
w tym w tych, bez których nie był produktem. Nie było w tym ani jednej złej
decyzji lokalnej: proces przez cały czas pogłębiał jedyną część mapy, którą
widział.

Zasady tej tury:

1. Każda sekcja mapy pokrycia dostaje dokładnie jedną historyjkę. Jeśli sekcja
   naprawdę nie wymaga pracy (jest opisem kontekstu, kryterium ukończenia albo
   podsumowaniem, a nie wymaganiem), zgłoś ją w `sections_skipped` z powodem —
   NIE pomijaj jej milczeniem ani zdaniem w `summary`. Reguła blokująca
   recenzentki liczy się z mapy, a do mapy trafia wyłącznie deklaracja.
2. Zacznij od sekcji niosących szkielet produktu — tych, bez których pozostałe
   nie mają na czym stanąć — a dopiero potem od reszty. Kolejność w BACKLOG.md
   jest priorytetem.
3. ŻADNEJ GŁĘBI. Drugi wariant tej samej zdolności jest w pierwszym backlogu
   zabroniony; dobierzesz go w kolejnych przeglądach, kiedy będzie widać, co
   realnie powstało i co się potwierdziło.
4. Miękki sufit historyjek na tę jedną turę nie obowiązuje: liczba sekcji
   briefu jest ważniejsza. Kolejne tury wracają pod normalny sufit.

Demo nadal ma działać, a nie być kompletne — najcieńsza wersja każdej zdolności
jest w porządku. Chodzi o to, żeby cała mapa była WIDOCZNA od pierwszej tury,
nie o to, żeby cały produkt powstał naraz.
