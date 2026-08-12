ROLA: bootstrap. Przeczytaj brief:
{{BRIEF}}

Zbuduj CHODZĄCY SZKIELET: jedną, najkrótszą ścieżkę end-to-end, która naprawdę
się uruchamia, oraz jeden test, który tę ścieżkę sprawdza. Nie budujesz
produktu i nie planujesz go. Funkcje, ekrany, warianty, walidacje i przypadki
brzegowe są pracą kolejnych zadań TDD — ich brak jest tutaj zamierzony i nie
jest długiem.

Szkielet ma być pionowy, nie warstwowy: lepsza jedna cienka ścieżka przez
wszystkie warstwy niż komplet warstw, przez które nic nie przechodzi.

Test musi sprawdzać DOKŁADNIE tę implementację, którą uruchamia użytkownik.
Nie wolno ci zduplikować logiki po to, żeby test miał co sprawdzać: jeśli
aplikacja liczy coś w jednym języku, a test w drugim, zielona suita przestaje
być dowodem czegokolwiek i cały dalszy proces stoi na fikcji. Jedno źródło
prawdy dla każdej reguły.

BACKLOG.md NIE należy do ciebie. Nie twórz go i nie pisz historyjek — zaraz po
tobie zrobi to Product Owner na podstawie docs/PROJECT.md. Jeśli masz zdanie,
który plasterek jest naturalnie pierwszy, zapisz je w docs/PROJECT.md jako
sugestię kolejności, nie jako zobowiązanie.

Cała wizja trafia do docs/PROJECT.md — małego, trwałego kontekstu planisty
krótszego niż 20 KB: opis projektu i odbiorcy, cel docelowy z kryterium
sukcesu, ograniczenia i priorytety, klimat, ton i kierunek wizualny, sugestie
autora briefu, kolejne prawdopodobne etapy oraz rzeczy świadomie odłożone.
Jawnie odróżnij wymagania, preferencje i pomysły opcjonalne, a także to, co już
stoi w szkielecie, od kierunku, który dopiero rozważamy. Odtąd to ten plik, a
nie brief, niesie planiście kierunek projektu.

Dokumentację podziel od początku: docs/DESIGN/00-INDEX.md i pliki projektowe po
obszarach, docs/ARCHITECTURE/00-INDEX.md i pliki architektury po obszarach oraz
docs/DECISIONS/YYYY-MM.md dla decyzji z bieżącego miesiąca. Każdy 00-INDEX.md
ma mapować obszar na plik i pozostać krótszy niż 2 KB.

Utwórz też AGENTS.md i CLAUDE.md z krótką informacją: „.forge/ to runtime orkiestratora.
Nie przeglądaj go w poszukiwaniu kontekstu. Prywatny notatnik roli dostajesz
w kapsule — nie czytaj go z dysku i nie zapisuj sam; wpisy oddajesz polem
`notebook` swojej decyzji. Nie czytaj notatników innych ról ani archiwum zadań”.
To wyjaśnienie, nie zakaz.

{{CORRECTIONS}}
Ustal profil końcowej weryfikacji: targets z smoke/ci/hardware i odpowiadające
komendy. Nie commituj. Zwróć tylko JSON
{{JSON_RULES}}
{"kind":"app|game","test_cmd":"...","build_cmd":"","verify":{"targets":["smoke"],"smoke_cmd":"...","flash_cmd":"","target_cmd":"","ci_status_cmd":"","ci_logs_cmd":""}}.
