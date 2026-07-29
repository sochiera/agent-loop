# Context Capsule i prywatne notatniki — design i plan wdrożenia

Status: zaakceptowane do implementacji.

## 1. Problem

Tester i koder muszą zachować ciągłość w obrębie jednego zadania TDD. Obecnie
Forge realizuje ją dwoma mechanizmami:

- Codex wznawia pełną sesję;
- pozostali agenci dostają surowy zapis maksymalnie dwóch ostatnich odpowiedzi
  własnej roli, ograniczony do 4000 znaków.

Prompt testera zawiera dodatkowo poprzednią decyzję, `coder_summary`, handoff,
listę zmienionych plików i osiem wpisów dziennika. Te same informacje
pojawiają się więc w kilku miejscach:

- po `green` handoff jest równy `coder_summary`;
- decyzja testera występuje w `State`, prywatnym rekordzie i dzienniku;
- lista plików jest dostępna w Git, promptcie i dzienniku;
- rekord przechowuje całą końcową wypowiedź, chociaż tylko część jest przydatna
  po powrocie roli.

Pełna sesja rośnie, a surowy rekord wybiera tekst według kolejności i długości,
nie według znaczenia. Potrzebujemy małego kontekstu procesu oraz miejsca, w
którym sama rola może zachować wybrane przez siebie ustalenia.

## 2. Decyzja

Wprowadzamy dwa proste, niezależne elementy:

1. **Context Capsule** — krótki, deterministyczny snapshot bieżącej tury,
   generowany przez Forge z istniejącego stanu i Git.
2. **Prywatny notatnik roli** — zwykły plik Markdown, który agent czyta i
   modyfikuje według własnej oceny.

Kapsuła odpowiada na pytanie „co dzieje się teraz?”. Notatnik odpowiada na
pytanie „co chcę pamiętać, kiedy wrócę?”.

Nie dodajemy osobnego modelu streszczającego, pól `self_note`,
`knowledge_delta`, bazy wiedzy, automatycznego scalania ani mechanicznej
izolacji notatników.

## 3. Cele

- Jedna kopia każdego faktu procesowego w promptcie.
- Ten sam minimalny handoff niezależnie od providera.
- Brak pełnych transkryptów i dziennika zadania w promptach wykonawców.
- Swobodna, prywatna pamięć testera i kodera bez rozbudowy `State`.
- Odporność na restart procesu i utratę sesji CLI.
- Brak notatników w commitach.
- Zachowanie notatników jako diagnostyki nieudanego zadania.
- Mały zakres zmian i krótka instrukcja dla agentów.

## 4. Poza zakresem

Poniższe rzeczy nie należą do pierwszego wdrożenia:

- streszczanie historii przez dodatkowy model;
- automatyczne wklejanie całego notatnika do każdego promptu;
- twarde blokowanie jednej roli przed otwarciem notatnika drugiej;
- walidowanie struktury lub treści notatnika;
- mechaniczne limity, przycinanie i scalanie sekcji notatnika;
- przenoszenie pełnej prywatnej historii między rolami;
- zastępowanie pliku zadania, diffu i kodu ich streszczeniem;
- zmiana odpowiedzialności ról TDD;
- wyłączenie `codex exec resume` bez osobnego pomiaru jakości.

„Poza zakresem” oznacza brak implementacji w tym feature, a nie zakaz
rozważenia w przyszłości.

## 5. Context Capsule

Kapsuła nie jest pamięcią ani kolejnym obiektem zapisywanym do `State`. Jest
widokiem budowanym tuż przed wywołaniem roli z danych, które Forge już posiada.

Przykład dla testera:

```text
KAPSUŁA KONTEKSTU
Zadanie: task-123, runda 2, plik .forge/tasks/task-123.md
Tura: tester / potwierdzenie po green kodera
Handoff od kodera: dodano walidację; test ukierunkowany jest zielony
Zmiany od startu zadania: src/api.py, tests/test_api.py
Aktywne uwagi review: brak
Prywatny notatnik: .forge/notebooks/task-123/tester.md
```

Przykład dla kodera:

```text
KAPSUŁA KONTEKSTU
Zadanie: task-123, runda 2, plik .forge/tasks/task-123.md
Tura: koder / implementacja po red
Decyzja testera: red — brakuje walidacji pustej listy
Bramka testera: pytest -q tests/test_api.py
Zmiany od startu zadania: tests/test_api.py
Prywatny notatnik: .forge/notebooks/task-123/coder.md
```

Kapsuła pokazuje wyłącznie pola potrzebne w bieżącej fazie. Nie renderuje
serii `(brak)`. W szczególności:

- po `green` pokazuje handoff tylko raz, bez osobnego `coder_summary`;
- nie dołącza prywatnego rekordu ostatnich odpowiedzi;
- nie dołącza wpisów `ledger.md`;
- nie kopiuje treści notatnika;
- wskazuje dokładnie jeden notatnik właściwej roli.

## 6. Źródła prawdy

| Informacja | Źródło |
|---|---|
| identyfikator i plik zadania | `state.current_task` |
| numer rundy | `state.tdd_round` |
| bieżąca faza i cel tury | automat TDD Forge |
| decyzja testera i komenda bramki | `state.tester_decision` |
| handoff do testera | istniejący `state.tester_handoff` |
| zmienione pliki | `_changed(project, state.task_start_tag)` |
| uwagi review | `state.review_notes` w aktywnym cyklu |
| prywatna pamięć roli | wskazany plik notatnika |
| historia procesu dla mistrza | `.forge/ledger.md` |

Pierwsza implementacja wykorzystuje istniejące pola `State`. Nie dodajemy
nowego modelu handoffu ani kopii kapsuły w `STATE.json`.

Aktualny plik zadania, kod, diff, wyniki testów i obserwacje Forge mają
pierwszeństwo przed treścią notatnika. Notatnik jest pomocą roli, nie źródłem
kontraktu.

## 7. Prywatne notatniki

### 7.1. Lokalizacja

```text
.forge/notebooks/<task-id>/tester.md
.forge/notebooks/<task-id>/coder.md
```

`.forge/` jest runtime'em ignorowanym przez Git. Zmiany notatnika nie mogą
trafić do listy zmian zadania, bramki zakresu ani commita.

### 7.2. Template testera

```markdown
# Prywatny notatnik testera

## Następna tura

## Ustalenia

## Próby i pułapki
```

### 7.3. Template kodera

```markdown
# Prywatny notatnik kodera

## Następna tura

## Ustalenia

## Próby i pułapki
```

Sekcje mogą pozostać puste. Agent może dowolnie przepisywać, skracać i usuwać
zawartość. Notatnik nie jest dziennikiem append-only.

### 7.4. Instrukcja roli

Do promptu trafia jedno krótkie zdanie z konkretną ścieżką:

> Twój prywatny notatnik to `.forge/notebooks/task-123/tester.md`. Czytaj i
> aktualizuj go tylko wtedy, gdy pomoże ci w kolejnej turze; utrzymuj go krótko
> i aktualnie. Nie czytaj notatników innych ról.

To konwencja, nie zabezpieczenie. Zakładamy dobrą wolę agentów. Nie tworzymy
osobnych sandboxów, uprawnień plikowych ani filtrów narzędzi.

Notatnik nie jest automatycznie wklejany do promptu. Agent sam decyduje, czy
warto wykonać jego odczyt. Nie musi aktualizować go po każdej turze.

## 8. Cykl życia notatników

### 8.1. Start zadania

Po aktywowaniu nowego zadania Forge:

1. tworzy `.forge/notebooks/<task-id>/`;
2. tworzy `tester.md` i `coder.md` z powyższych template'ów;
3. nigdy nie nadpisuje istniejących notatników przy wznowieniu tego samego
   aktywnego zadania.

### 8.2. Praca i restart

Notatniki przeżywają checkpoint oraz restart Forge. Każde kolejne wywołanie
dostaje tę samą ścieżkę właściwej roli.

`codex exec resume` pozostaje bez zmian. Gdy resume zawiedzie i Forge uruchomi
świeżą sesję, ścieżka notatnika nadal znajduje się w kapsule, więc agent może
odzyskać wybrane przez siebie ustalenia.

### 8.3. Sukces

Po zielonej pełnej bramce i udanym commicie Forge usuwa:

```text
.forge/notebooks/<task-id>/
```

Usunięcie następuje dopiero po commicie. Awaria przed commitem nie może
skasować pamięci potrzebnej do wznowienia.

### 8.4. Nieudane zadanie

Notatniki są częścią artefaktu porażki. `_fail_task` przenosi je do:

```text
.forge/failed/<task-id>/notebooks/tester.md
.forge/failed/<task-id>/notebooks/coder.md
```

Obok pozostają istniejące `reason.txt`, zachowane pliki nieśledzone i ref
`forge/failed/<task-id>`. Po przeniesieniu aktywny katalog
`.forge/notebooks/<task-id>/` znika.

Artefakt podlega istniejącej retencji `.forge/failed/` w housekeepingu. Nie
jest commitowany.

### 8.5. Osierocone katalogi

Housekeeping może usunąć aktywny katalog notatników wyłącznie wtedy, gdy:

- `State` nie ma aktywnego zadania o tym identyfikatorze;
- nie jest to katalog wewnątrz `.forge/failed/`.

Nie stosujemy dodatkowej archiwizacji udanych notatników.

## 9. Migracja istniejącego stanu

Nowe zadania korzystają wyłącznie z notatników. Dla zadania aktywnego podczas
wdrożenia:

1. Forge tworzy brakujące template'y bez nadpisywania istniejących plików.
2. Jeżeli istnieje `tester_record` albo `coder_record`, zapisuje jego treść
   jednorazowo pod dodatkowym nagłówkiem `## Poprzedni rekord po migracji`
   w odpowiednim notatniku.
3. Po zapisaniu checkpointu stare rekordy są czyszczone i nie są już dopinane
   do promptu.

Pola mogą pozostać w dataclassie `State` przez jeden okres zgodności, aby stare
pliki JSON nadal się ładowały. Później zostaną usunięte.

## 10. Plan implementacji

### Etap 1 — helpery i cykl życia

1. Dodać mały moduł `forge/notebooks.py`.
2. Zaimplementować tworzenie template'ów bez nadpisywania.
3. Wywołać inicjalizację przy starcie nowego zadania i przy wznowieniu starego.
4. Dodać usuwanie katalogu po udanym commicie.
5. Dodać przeniesienie notatników do artefaktu w `_fail_task`.
6. Rozszerzyć housekeeping o bezpieczne usuwanie osieroconych katalogów.

Pliki: `forge/notebooks.py`, `forge/orchestrate.py`,
`tests/test_notebooks.py`, `tests/test_task_lifecycle.py`.

### Etap 2 — kapsuła promptu

1. Dodać czystą funkcję renderującą kapsułę z istniejącego `State`.
2. Przekazywać tylko pola istotne dla aktualnej fazy.
3. Dodać dokładną ścieżkę prywatnego notatnika roli.
4. Usunąć duplikat `handoff`/`coder_summary`.
5. Usunąć dziennik zadania z promptu testera.
6. Przestać dopinać surowe `tester_record` i `coder_record`.

Pliki: `forge/prompts/__init__.py`, `forge/prompts/templates/tester.md`,
`forge/prompts/templates/coder.md`, `forge/orchestrate.py`,
`tests/test_role_context.py`, `tests/test_task_flow.py`.

### Etap 3 — migracja i porządki

1. Przenieść stare rekordy aktywnego zadania do notatników.
2. Utrzymać ładowanie starych pól `STATE.json` przez okres zgodności.
3. Usunąć `_append_record` i aktualizowanie rekordów.
4. Po okresie zgodności usunąć pola `tester_record` i `coder_record`.
5. Zaktualizować `docs/PIPELINE.md`.

### Etap 4 — osobny eksperyment Codeksa

Po stabilizacji kapsuły porównać:

- `resume + capsule + notebook`;
- świeże wywołanie `capsule + notebook`.

Porównać input tokens, liczbę tur, powtarzane odczyty plików, błędne kontrakty
i werdykty review. Nie zmieniać domyślnego resume bez pomiaru.

## 11. Testy akceptacyjne

Implementacja jest gotowa, gdy:

- start zadania tworzy oba notatniki z dokładnymi template'ami;
- wznowienie nie nadpisuje treści zapisanej przez agenta;
- prompt testera wskazuje tylko `tester.md`, a kodera tylko `coder.md`;
- treść notatnika nie jest automatycznie kopiowana do promptu;
- agent może zmienić notatnik bez oznaczenia rundy jako zmieniającej worktree;
- notatniki nie trafiają do commita;
- udany commit usuwa aktywny katalog notatników;
- porażka przenosi oba notatniki do `.forge/failed/<task-id>/notebooks/`;
- restart zachowuje aktywne notatniki;
- migracja zachowuje zawartość starych rekordów;
- prompt nie zawiera równolegle prywatnego rekordu ani dziennika zadania;
- handoff po `green` występuje dokładnie raz;
- pełne transkrypty i output narzędzi nie trafiają do kapsuły;
- cały pakiet testów Forge pozostaje zielony.

## 12. Ryzyka i odpowiedzi

| Ryzyko | Odpowiedź KISS |
|---|---|
| agent nie przeczyta notatnika | notatnik jest opcjonalny; prompt podaje dokładną ścieżkę |
| agent zapisze za dużo | prosimy o krótki, aktualny zapis; nie budujemy walidatora |
| agent przeczyta cudzy notatnik | ufamy roli i dajemy prostą instrukcję |
| notatka stanie się nieaktualna | agent może ją dowolnie przepisać; repo ma pierwszeństwo |
| zapis notatnika doda tool call | agent aktualizuje ją tylko wtedy, gdy widzi wartość |
| awaria zgubi pamięć | plik istnieje poza procesem i przeżywa checkpoint |
| porażka usunie diagnostykę | notatniki wchodzą do artefaktu nieudanego zadania |
| stary rekord zniknie przy migracji | jednorazowo przenosimy go do notatnika |
| rozwiązanie urośnie | brak izolacji, schematów wiedzy, auto-injection i osobnego modelu |

## 13. Decyzja końcowa

Wdrażamy małą kapsułę procesu oraz dwa swobodne notatniki Markdown na zadanie.
Role same decydują, czy je czytać i aktualizować. Po sukcesie notatniki są
usuwane; po porażce stają się częścią `.forge/failed/<task-id>/`.

To rozwiązanie zastępuje surowe rekordy ról bez budowania nowego systemu
pamięci.
