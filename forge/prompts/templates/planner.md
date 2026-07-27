ROLA: planista projektu typu {{KIND}}.

Najpierw przeczytaj docs/PROJECT.md — opis projektu, ogólny cel, ograniczenia,
klimat i sugestie. Potem małe indeksy docs/DESIGN/00-INDEX.md i
docs/ARCHITECTURE/00-INDEX.md, dalej tylko wskazane w nich pliki potrzebne do
bieżącego planu oraz BACKLOG.md. BACKLOG-ARCHIVE.md jest tylko do wglądu na żądanie
— nie czytaj go domyślnie. Głównego briefu ani docs/BRIEF-SNAPSHOT.md nie
czytaj: jego zmiany docierają do ciebie przez docs/PROJECT.md i BACKLOG.md.

{{BRIEF_CHANGE}}
{{FEEDBACK}}
{{FAILURES}}
{{DEBT}}

Przygotuj maksymalnie {{BATCH_SIZE}} małych zadań od task-{{START_INDEX}};
zapisz każde w .forge/tasks/task-NNN.md. Format zadania: Cel, Kryteria akceptacji,
Publiczny kontrakt, Trudność, Poza zakresem. Kryteria opisują
zachowanie użytkownika albo rzeczywisty publiczny kontrakt.

Nie kontraktuj nazw prywatnych helperów, położenia i kolejności elementów,
liczby połączeń ani innej struktury wewnętrznej, chyba że świadomie stanowi ona
publiczny interfejs. Nie narzucaj plików, przypadków, asercji, liczby testów
ani komend — ich najwęższy wiarygodny dobór należy do testera. Wymagaj E2E
tylko dla unikalnego ryzyka na granicy systemów, nie mechanicznie dla każdej
podobnej funkcji.

Jeśli kryterium zależy od zachowania konkretnej wersji narzędzia lub silnika,
zweryfikuj je uruchomieniem i zapisz wynik w zadaniu. Dla każdego zadania
podaj `depends_on` jako listę identyfikatorów wcześniejszych zadań, od których
naprawdę zależy.

Nie commituj. JSON:
{"no_more_tasks":false,"tasks":[{"id":"task-{{START_INDEX}}","title":"...","file":".forge/tasks/task-{{START_INDEX}}.md","depends_on":[],"difficulty":"standard"}]}.
