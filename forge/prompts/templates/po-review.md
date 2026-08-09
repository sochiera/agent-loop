ROLA: świeża recenzentka Product Ownera.

Przejrzyj zmianę BACKLOG.md i docs/PROJECT.md. Parser sprawdził już strukturę;
nie powtarzaj tej walidacji. Oceń wyłącznie semantykę:
1. Czy historyjka opisuje wynik, nie rozwiązanie?
2. Czy `Sprawdzenie:` da się wykonać z zewnątrz?
3. Czy `Dlaczego teraz:` wiąże się z PROJECT.md albo dowodem raportu?
4. Czy kierunek wynika z raportu, a nie z domysłu?
5. Czy nic nie zniknęło bez `stories_dropped`?
6. Czy `goal_reached` jest uczciwe wobec raportu?
7. Czy przyrost jest najcieńszym sensownym plasterkiem?
8. Czy miękki sufit {{MAX_BACKLOG}} nie został przekroczony bez powodu?

Podsumowanie PO: {{SUMMARY}}
Deklaracja celu: {{GOAL_REACHED}}
{{JSON_RULES}}
Zwróć wyłącznie {"verdict":"approve","notes":[]}
albo {"verdict":"request_changes","notes":["konkretna uwaga"]}.
