ROLA: bootstrap. Przeczytaj brief:
{{BRIEF}}

Utwórz minimalny projekt, BACKLOG.md oraz działający test. Dokumentację podziel
od początku: docs/DESIGN/00-INDEX.md i pliki projektowe po obszarach,
docs/ARCHITECTURE/00-INDEX.md i pliki architektury po obszarach oraz
docs/DECISIONS/YYYY-MM.md dla decyzji z bieżącego miesiąca. Każdy 00-INDEX.md
ma mapować obszar na plik i pozostać krótszy niż 2 KB.

Utwórz też docs/PROJECT.md — mały, trwały kontekst planisty, krótszy niż 20 KB:
opis projektu i odbiorcy, ogólny cel z kryterium sukcesu, ograniczenia i
priorytety, klimat, ton i kierunek wizualny, sugestie autora briefu oraz jawne
rozróżnienie wymagań, preferencji i pomysłów opcjonalnych. Odtąd to ten plik, a
nie cały brief, niesie planiście kierunek projektu.

Utwórz też AGENTS.md i CLAUDE.md z krótką informacją: „.forge/ to runtime orkiestratora
— plik twojego zadania i cały potrzebny kontekst dostajesz w promptcie, więc
nie ma tam nic, czego potrzebujesz”. To wyjaśnienie, nie zakaz.

Ustal profil końcowej weryfikacji: targets z smoke/ci/hardware i odpowiadające
komendy. Nie commituj. Zwróć tylko JSON
{"kind":"app|game","test_cmd":"...","build_cmd":"","verify":{"targets":["smoke"],"smoke_cmd":"...","flash_cmd":"","target_cmd":"","ci_status_cmd":"","ci_logs_cmd":""}}.
