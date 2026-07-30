ROLA: bootstrap. Przeczytaj brief:
{{BRIEF}}

Utwórz szkielet projektu i działający test. NIE planuj całego produktu.
Projekt prowadzimy zwinnie: backlog rośnie później, w przeglądach kierunku, na
podstawie tego, co realnie powstało.

BACKLOG.md ma zawierać WYŁĄCZNIE najcieńszy pionowy plasterek prowadzący do
uruchamialnego demo — maksymalnie 3 wpisy, chętnie mniej. Demo może pokazywać
tylko fragment docelowego doświadczenia; ma działać, nie być kompletne. Nie
wpisuj tam etapów dalszych, wariantów ani „potem trzeba będzie".

Cała reszta wizji trafia do docs/PROJECT.md — małego, trwałego kontekstu
planisty krótszego niż 20 KB: opis projektu i odbiorcy, cel docelowy z
kryterium sukcesu, ograniczenia i priorytety, klimat, ton i kierunek wizualny,
sugestie autora briefu, kolejne prawdopodobne etapy oraz rzeczy
świadomie odłożone. Jawnie odróżnij wymagania, preferencje i pomysły
opcjonalne, a także zobowiązania z backlogu od kierunku, który dopiero
rozważamy. Odtąd to ten plik, a nie brief, niesie planiście kierunek projektu.

Dokumentację podziel od początku: docs/DESIGN/00-INDEX.md i pliki projektowe po
obszarach, docs/ARCHITECTURE/00-INDEX.md i pliki architektury po obszarach oraz
docs/DECISIONS/YYYY-MM.md dla decyzji z bieżącego miesiąca. Każdy 00-INDEX.md
ma mapować obszar na plik i pozostać krótszy niż 2 KB.

Utwórz też AGENTS.md i CLAUDE.md z krótką informacją: „.forge/ to runtime orkiestratora.
Nie przeglądaj go w poszukiwaniu kontekstu; wyjątkiem jest
dokładnie jeden prywatny notatnik roli wskazany w kapsule — możesz go czytać
i aktualizować, ale nie czytaj notatników innych ról ani archiwum zadań”.
To wyjaśnienie, nie zakaz.

{{CORRECTIONS}}
Ustal profil końcowej weryfikacji: targets z smoke/ci/hardware i odpowiadające
komendy. Nie commituj. Zwróć tylko JSON
{"kind":"app|game","test_cmd":"...","build_cmd":"","verify":{"targets":["smoke"],"smoke_cmd":"...","flash_cmd":"","target_cmd":"","ci_status_cmd":"","ci_logs_cmd":""}}.
