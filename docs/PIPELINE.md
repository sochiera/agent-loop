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

## Bootstrap i synchronizacja briefu

Bootstrap czyta cały brief raz i materializuje trwały kontekst projektu w
`docs/PROJECT.md`: opis i odbiorcę, ogólny cel z kryterium sukcesu, ograniczenia
i priorytety, klimat oraz sugestie autora, z jawnym rozróżnieniem wymagań,
preferencji i pomysłów opcjonalnych. Po zaakceptowanej recenzji architektury
Forge zapisuje kopię briefu w `docs/BRIEF-SNAPSHOT.md` i jego skrót w stanie.

Zmiana głównego briefu nie uruchamia ponownie bootstrapu, bo ten jest
nieidempotentny. Na granicy między zadaniami — przed planowaniem i przed
weryfikacją celu, nigdy w trakcie aktywnego zadania — Forge porównuje brief ze
snapshotem i przy różnicy uruchamia `diff-bootstrap`. Rola dostaje sam diff
briefu, listę niezaczętych zadań i czyta `docs/PROJECT.md` oraz `BACKLOG.md`.
Wolno jej zapisać wyłącznie te dwa pliki; każdą inną zmianę Forge wykrywa
manifestem drzewa i cofa, zanim cokolwiek trafi do commita. Osobnego review nie
ma — dlatego rola pracuje na najsilniejszym modelu.

Nowy snapshot i skrót zapisujemy dopiero po poprawnym werdykcie i walidacji
zakresu, więc awaria zostawia poprzednią wersję jako punkt odniesienia i
operację można bezpiecznie wznowić. Werdykt niesie `replan`: przy `true`
niezaczęta kolejka wraca do planisty razem z jednorazową notatką
`.forge/brief-change.md` (podsumowanie, przeniesione zmiany, wycofane zadania),
którą konsumuje najbliższy wsad. Ukończonego kodu nikt nie cofa automatycznie —
usunięte wymaganie staje się jawną decyzją albo zadaniem w backlogu. Projekt
zbootstrapowany przed tym mechanizmem nie ma snapshotu i przechodzi jednorazową
synchronizację początkową.

Planista czyta odtąd `docs/PROJECT.md`, a nie brief: zmiany intencji docierają
do niego przez ten plik i backlog.

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

Checkpoint opisuje następną czynność. Przed wywołaniem kodera Forge zapamiętuje
odcisk całego drzewa wyłącznie po to, by po restarcie nie powtarzać częściowo
wykonanej tury. Zastane zmiany wracają do oceny testera; żaden plik testowy
nie jest mechanicznie chroniony przed edycją kodera.

Każdy wpis rundy w ledgerze zawiera dokładne ścieżki zmienione przez daną
rolę. Mistrz uruchamia się na początku każdej rundy i może na tej podstawie
poprosić testera o ocenę testu zmienionego przez kodera. `reason` testera
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
