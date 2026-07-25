# Forge KISS pipeline

Jedno zadanie przechodzi przez pętlę `tester ↔ coder`, następnie `review`. Werdykt `changes` wraca do testera i rozpoczyna nowy cykl TDD; dopiero `approve` prowadzi do `commit`.

Tester decyduje o dalszym kroku: `red`, `code`, `review` albo `blocked`. Po
`red` lub `code` koder odpowiada `green`, `test_changes_needed` albo
`tester_input_needed`. Oba niezielone wyniki wraz z powodem wracają do tej
samej sesji testera. Limit `max_tdd_rounds` wynosi domyślnie 10 i oznacza
potrzebę podziału zadania.

Tester i koder uruchamiają testy wymagane przez swoje prompty. Po decyzji
`review` Forge nie dokłada mechanicznej bramki: świeży, read-only reviewer
ocenia kontrakt, diff, implementację i testy. Przy `changes` uwagi wracają do
zachowanej sesji testera, która rozpoczyna nowy cykl TDD. Jeśli reviewer mimo
roli read-only zapisze pliki, Forge nie porzuca ani nie cofa zadania: podaje
testerowi dokładne ścieżki do oceny. Dopiero `approve` bez zapisów przechodzi
do commitu. Sesje są czyszczone po udanym commicie albo zakończeniu zadania
przez testera jako `blocked`.

Checkpoint opisuje następną czynność. Przed wywołaniem kodera Forge zapamiętuje
odcisk całego drzewa wyłącznie po to, by po restarcie nie powtarzać częściowo
wykonanej tury. Zastane zmiany wracają do oceny testera; żaden plik testowy
nie jest mechanicznie chroniony przed edycją kodera.

Każdy wpis rundy w ledgerze zawiera dokładne ścieżki zmienione przez daną
rolę. Mistrz uruchamia się na początku każdej rundy i może na tej podstawie
poprosić testera o ocenę testu zmienionego przez kodera. `reason` testera
trafia do promptu kodera, a `summary` kodera wraca jako handoff do następnej
tury testera. Werdykty review i zapisane przez reviewera ścieżki również
trafiają do ledgera. Gdy kolejne cykle review nie robią postępu, Mistrz poleca
testerowi zwrócić `blocked` z konkretnym powodem; wtedy standardowa obsługa
porażki zapisuje artefakt, przywraca tag startowy i oddaje sterowanie planiście.

Niepoprawna decyzja JSON dostaje dokładnie jedną prośbę o korektę samego
formatu. Druga niepoprawna odpowiedź zatrzymuje przebieg z zapisanym
checkpointem.

Kanoniczna pełna suita repozytorium:

```bash
python3 -m pytest -q
```
