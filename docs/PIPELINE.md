# Forge KISS pipeline

Jedno zadanie przechodzi przez pętlę `tester ↔ coder`, następnie `review`.
Reviewer zwraca `approve`, `suggestions` albo `request_changes`.
`request_changes` wraca do testera i rozpoczyna nowy cykl TDD zakończony
świeżym review. `suggestions` wraca do jednorazowej oceny testera i może
zakończyć się `finalize` bez drugiego review. `approve` prowadzi bezpośrednio
do `commit`.

Tester decyduje o dalszym kroku: `red`, `code`, `review` albo `blocked`. Po
`red` lub `code` koder odpowiada `green`, `test_changes_needed` albo
`tester_input_needed`. Oba niezielone wyniki wraz z powodem wracają do tej
samej sesji testera. Limit `max_tdd_rounds` wynosi domyślnie 10 i oznacza
potrzebę podziału zadania. Wyłącznie po `suggestions` tester może też zwrócić
`finalize` z niepustym uzasadnieniem rozliczającym sugestie jako zastosowane
albo odrzucone.

## Bootstrap i przegląd kierunku

Projekt prowadzimy zwinnie: zakres nie jest ustalany z góry, tylko rośnie w
kolejnych przeglądach kierunku.

Bootstrap czyta cały brief raz i buduje szkielet z **najcieńszym pionowym
plasterkiem** w `BACKLOG.md` — maksymalnie trzema wpisami prowadzącymi do
uruchamialnego demo, nawet niepełnego. Cała reszta wizji trafia do
`docs/PROJECT.md`: opis i odbiorca, cel docelowy z kryterium sukcesu,
ograniczenia i priorytety, klimat, sugestie autora, kolejne prawdopodobne etapy
i rzeczy świadomie odłożone, z jawnym rozróżnieniem wymagań, preferencji i
pomysłów opcjonalnych. Po zaakceptowanej recenzji Forge zapisuje kopię briefu w
`docs/BRIEF-SNAPSHOT.md` i jego skrót w stanie.

Przegląd kierunku (`diff-bootstrap`) rusza na granicy między zadaniami — przed
planowaniem i przed weryfikacją celu, nigdy w trakcie aktywnego zadania — gdy
zajdzie którykolwiek warunek:

- **zmiana briefu** (skrót różny od snapshotu) — najmocniejsze wejście, wygrywa
  z pozostałymi powodami;
- **kadencja** — minęły `FORGE_STEERING_BATCHES` (domyślnie 3) wsady planisty
  od ostatniego przeglądu;
- **wyczerpany backlog** — planista zgłosił `no_more_tasks`.

Rola dostaje powód uruchomienia, diff briefu (tylko gdy się zmienił), listę
commitów od poprzedniego przeglądu i listę niezaczętych zadań; `docs/PROJECT.md`
i `BACKLOG.md` czyta sama. Wolno jej zapisać wyłącznie te dwa pliki — każdą inną
zmianę Forge wykrywa manifestem drzewa i cofa, zanim cokolwiek trafi do commita.
Bramka kotwiczy się na SHA sprzed fazy, nie na bieżącym HEAD: własny commit roli
albo recenzenta jest wycofywany (`reset --mixed`), więc nie da się przemycić
zmiany poza zakresem ani pokazać recenzentowi pustego diffu. Pełny bootstrap nie
jest powtarzany, bo jest nieidempotentny.

Diff briefu nigdy nie jest po cichu obcinany — po udanym przeglądzie snapshotem
staje się cały nowy brief, więc nieprzeczytany ogon zmian zniknąłby bez śladu.
Zbyt duży diff zastępuje pełna treść briefu, a brief niemieszczący się w
promptcie zatrzymuje przegląd z prośbą o podział dokumentu.

Kierunek jest recenzowany, bo błąd na tym poziomie propaguje się na wszystkie
kolejne zadania. Świeży, read-only recenzent (`bootstrap_reviewer`, najsilniejszy
model) ocenia kierunek, nie styl: czy zmiana wynika ze stanu projektu, czy krok
jest najcieńszym sensownym przyrostem, czy nic nie zniknęło po cichu i czy
`goal_reached` jest uczciwe. `request_changes` wraca do roli przeglądu z uwagami;
budżet to `FORGE_MAX_BOOTSTRAP_REVIEWS` (domyślnie 4) recenzji. Wyczerpanie
budżetu cofa zmiany i zatrzymuje przebieg z checkpointem — dalej potrzebna jest
decyzja użytkownika. Ta sama pętla obowiązuje recenzję architektury bootstrapu.

Nowy snapshot, skrót i kadencję zapisujemy dopiero po zaakceptowanym werdykcie,
więc awaria zostawia poprzedni punkt odniesienia i operację można wznowić.
Werdykt niesie `replan` — przy `true` niezaczęta kolejka wraca do planisty razem
z jednorazową notatką `.forge/steering.md` (podsumowanie, przeniesione zmiany,
wycofane zadania), którą konsumuje najbliższy wsad — oraz `goal_reached`.
Ukończonego kodu nikt nie cofa automatycznie: usunięte wymaganie staje się jawną
decyzją albo zadaniem w backlogu. Projekt zbootstrapowany przed tym mechanizmem
nie ma snapshotu i przechodzi jednorazową synchronizację początkową.

Pusty backlog nie kończy projektu. `no_more_tasks` bez potwierdzonego
`goal_reached` prosi o przegląd kierunku; dopiero jego zgoda kończy pracę.
Zaakceptowany `goal_reached` przechodzi PROSTO do końcowej weryfikacji celu —
bez kolejnego wsadu planisty i bez dokańczania starej kolejki. Czerwona
weryfikacja kasuje tę zgodę, bo dowód mówi, że celu nie osiągnięto.
Bezpiecznikiem są dwa jałowe wsady z rzędu — wtedy weryfikacja rusza mimo
wszystko, żeby para planista↔przegląd nie kręciła się w kółko na najsilniejszym
modelu.

Planista czyta `docs/PROJECT.md`, a nie brief: zmiany intencji docierają do
niego przez ten plik i backlog. Nie rozwija zakresu samodzielnie — gdy backlog
jest pusty, zwraca `no_more_tasks` i oddaje decyzję przeglądowi kierunku.

Identyfikator zadania to dokładnie `task-NNN` i jest to kontrakt, nie
konwencja: Forge wylicza z tego formatu numer następnego wsadu oraz kolejność
archiwum. Zadanie o innym identyfikatorze jest odrzucane z wpisem w logu i
ledgerze, a nie renumerowane — zgadnięty numer mógłby wskazać istniejący plik
cudzego zadania. Odrzucenie wszystkich zadań wsadu kończy fazę jawnym błędem
i checkpointem.

Planista opisuje zachowanie i publiczny kontrakt, ale nie wybiera testów ani
komend. Tester sam wybiera najwęższą wiarygodną bramkę i zwraca jej komendę w
decyzji `red` albo `code`; Forge przekazuje tę samą komendę koderowi. Koder może
dołożyć inne wąskie testy dotkniętych komponentów. Pełna suita nie należy do
wewnętrznych rund TDD: Forge uruchamia ją razem z buildem po zaakceptowanym
review, bezpośrednio przed commitem. Jej regresja wraca do testera z pełną
komendą i ogonem wyniku.

Tester odpowiada również za jakość dotkniętych testów. Przed dodaniem testu
szuka realistycznego, dotąd niewykrywanego defektu; preferuje rozszerzenie lub
parametryzację istniejącej bramki. Po green może i powinien wykonać mały
refaktor testów oraz wspólnej infrastruktury, usuwając duplikacje i
change-detectory bez osłabiania pokrycia. Kod produkcyjny i jego refaktor nadal
należą do kodera.

Po decyzji `review` świeży, read-only reviewer wykonuje zwykłe code review:
szuka błędów, przypadków brzegowych, naruszeń kontraktu i SOLID/KISS, design
smells, zbędnej złożoności, duplikacji, mylących nazw oraz testów bez wartości.
Nie zastępuje pełnej bramki, ale może uruchomić wąski test dla konkretnego
podejrzenia. `approve` wymaga pustej listy uwag. `suggestions` jest dozwolone
tylko wtedy, gdy diff można bezpiecznie commitować bez zastosowania uwag.
Tester ocenia każdą sugestię, może sam poprawić testy albo przekazać
zaakceptowaną zmianę koderowi, a następnie wybiera `finalize`. Jeśli poprawki
wyjdą poza mały zakres, zmienią publiczne zachowanie albo wzbudzą wątpliwości,
tester wybiera `review`, świadomie ponosząc koszt nowej recenzji.

Przy `request_changes` uwagi wracają do zachowanej sesji testera, która
rozpoczyna nowy cykl TDD. Jeśli reviewer mimo roli read-only zapisze pliki,
Forge nie porzuca ani nie cofa zadania: podaje testerowi dokładne ścieżki do
oceny i wymaga zwykłej ścieżki review. `approve`, a także poprawne `finalize`,
przechodzą do pełnej bramki i commitu.
Sesje są czyszczone po udanym commicie albo zakończeniu zadania przez testera
jako `blocked`.

Bramka przed commitem i zapis reviewera raportują się w logu i w ledgerze.
Czerwona bramka po `finalize` cofała zadanie do testera bez żadnego śladu:
z zewnątrz wyglądało to jak zwis albo pętla, a Mistrz — który widzi wyłącznie
ledger — dostawał w tym miejscu niewyjaśnioną lukę.

Checkpoint opisuje następną czynność. Przed wywołaniem kodera Forge zapamiętuje
odcisk całego drzewa wyłącznie po to, by po restarcie nie powtarzać częściowo
wykonanej tury. Zastane zmiany wracają do oceny testera; żaden plik testowy
nie jest mechanicznie chroniony przed edycją kodera.

Każdy wpis rundy w ledgerze zawiera dokładne ścieżki zmienione przez daną
rolę. Mistrz uruchamia się na początku każdej rundy i może na tej podstawie
poprosić testera o ocenę testu zmienionego przez kodera. Razem z ledgerem
dostaje pozycję pętli: id aktywnego zadania i rolę, która zaraz ruszy. Bez tego
brak wpisu tury jeszcze niewykonanej czytał jako urwany cykl. Uwagi dla testera
i kodera są dodatkowo filtrowane deterministycznie — nazwanie w nich innego
zadania niż aktywne odrzuca uwagę, bo okno ledgera obejmuje kilka zamkniętych
zadań wstecz. Reguła `round_limit` dotyczy planisty i filtra nie podlega.
`reason` testera
trafia do promptu kodera, a `summary` kodera wraca jako handoff do następnej
tury testera. Werdykty review i zapisane przez reviewera ścieżki również
trafiają do ledgera. Gdy kolejne cykle `request_changes` nie robią postępu,
Mistrz poleca testerowi zwrócić `blocked` z konkretnym powodem; wtedy
standardowa obsługa porażki zapisuje artefakt, przywraca tag startowy i oddaje
sterowanie planiście.

Niepoprawna decyzja JSON dostaje dokładnie jedną prośbę o korektę samego
formatu. Druga niepoprawna odpowiedź zatrzymuje przebieg z zapisanym
checkpointem.

Kanoniczna pełna suita repozytorium:

```bash
python3 -m pytest -q
```
