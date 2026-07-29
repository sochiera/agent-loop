# Context Capsule — design i plan wdrożenia

Status: propozycja zaakceptowana do implementacji.

## 1. Problem

Tester i koder muszą zachować ciągłość w obrębie jednego zadania TDD. Obecnie
Forge realizuje ją dwoma różnymi mechanizmami:

- Codex wznawia pełną sesję;
- pozostali agenci dostają dołączony surowy zapis maksymalnie dwóch ostatnich
  odpowiedzi własnej roli, ograniczony do 4000 znaków.

Oprócz tego prompt testera zawiera poprzednią decyzję, `coder_summary`, handoff,
listę zmienionych plików i osiem wpisów dziennika. Część informacji występuje
więc kilka razy:

- po `green` handoff jest równy `coder_summary`;
- poprzednia decyzja testera znajduje się w `tester_decision`, prywatnym
  rekordzie testera i dzienniku;
- lista plików jest dostępna w Git, promptcie i dzienniku;
- surowa odpowiedź zawiera tekst oraz JSON, choć sterowanie pętlą wykorzystuje
  już sparsowany JSON.

Mechanizm jest odporny na restart, ale nie jest optymalny:

- budżet rekordu wybiera tekst według kolejności i długości, a nie znaczenia;
- duplikaty zużywają kontekst i mogą przedstawiać ten sam fakt innymi słowami;
- pełna sesja Codeksa rośnie przez całe zadanie;
- trudno zmierzyć koszt poszczególnych źródeł kontekstu.

## 2. Cel

Każde wywołanie testera i kodera ma dostać jedną, deterministyczną kapsułę
bieżącego stanu zadania:

1. tylko fakty potrzebne do najbliższej decyzji;
2. dokładnie jedna kopia każdego faktu;
3. ten sam kontrakt semantyczny niezależnie od providera;
4. jawny i testowalny budżet rozmiaru;
5. pełną zdolność wznowienia po przerwaniu procesu.

Kapsuła nie jest dodatkową pamięcią. Jest widokiem generowanym przy wywołaniu
z kanonicznego `State`, bieżącego Git oraz deterministycznych wyników bramek.

## 3. Poza zakresem

- streszczanie historii przez dodatkowy model;
- przenoszenie kontekstu testera do kodera lub odwrotnie poza jawnym handoffem;
- dołączanie pełnych transkryptów albo outputu narzędzi;
- zastępowanie pliku zadania, diffu i kodu ich opisem;
- zmiana odpowiedzialności ról TDD;
- automatyczne wyłączenie sesji Codeksa bez pomiaru jakości.

## 4. Warstwy kontekstu

Kontekst należy traktować jako trzy osobne warstwy:

### 4.1. Stabilne instrukcje roli

Reguły testera albo kodera, kontrakt JSON i niezmienne ograniczenia. Powinny
tworzyć stabilny prefiks, aby provider mógł wykorzystać prompt cache. Nie
powinny zawierać stanu zadania.

### 4.2. Context Capsule

Mały, zmienny snapshot opisany poniżej. Jest jedynym tekstowym handoffem między
turami i jedynym miejscem, w którym prompt przedstawia historię procesu.

### 4.3. Źródła prawdy w repozytorium

Plik zadania, aktualny diff, testy i kod pozostają na dysku. Agent czyta tylko
potrzebne fragmenty narzędziami. Kapsuła wskazuje, od czego zacząć, ale nie
kopiuje treści plików.

## 5. Model kapsuły

Kapsuła jest budowana osobno dla roli. Proponowany model logiczny:

```json
{
  "version": 1,
  "task": {
    "id": "task-123",
    "file": ".forge/tasks/task-123.md",
    "round": 2
  },
  "turn": {
    "role": "tester",
    "phase": "confirmation",
    "objective": "potwierdź bramkę po green kodera"
  },
  "last_own_decision": {
    "status": "red",
    "reason": "brakuje walidacji granicy",
    "command": "pytest -q tests/test_api.py"
  },
  "incoming_handoff": {
    "from": "coder",
    "kind": "green",
    "text": "dodano walidację; test ukierunkowany jest zielony"
  },
  "changed_files": [
    "src/api.py",
    "tests/test_api.py"
  ],
  "last_gate": {
    "kind": "targeted",
    "command": "pytest -q tests/test_api.py",
    "status": "green",
    "tail": ""
  },
  "review_notes": []
}
```

Pola nieistotne dla danej fazy są pomijane, a nie renderowane jako seria
`(brak)`. Koder otrzymuje analogiczną kapsułę, w której
`incoming_handoff` jest bieżącą decyzją testera, a `last_own_decision` opisuje
ostatni wynik kodera.

## 6. Kanoniczne źródło każdego pola

| Pole kapsuły | Źródło prawdy |
|---|---|
| `task.id`, `task.file` | `state.current_task` |
| `task.round` | `state.tdd_round` |
| `turn.role`, `turn.phase`, `turn.objective` | automat faz Forge |
| `last_own_decision` testera | `state.tester_decision` |
| `last_own_decision` kodera | nowe `state.coder_decision` |
| `incoming_handoff` | jedno nowe pole `state.incoming_handoff` |
| `changed_files` | `_changed(project, state.task_start_tag)` |
| `last_gate` | nowe `state.last_gate` zapisywane przez Forge |
| `review_notes` | `state.review_notes` tylko w aktywnym cyklu review |

Docelowo `coder_summary` i tekstowy `tester_handoff` znikają. Ich znaczenie
przejmuje strukturalny `coder_decision` oraz pojedynczy `incoming_handoff`.
W okresie migracji stare pola są odczytywane wyłącznie jako fallback przy
wznawianiu istniejącego `STATE.json`.

## 7. Format renderowany

Model danych pozostaje słownikiem, ale prompt dostaje krótki Markdown zamiast
JSON-a. Markdown jest czytelniejszy dla agenta, a kolejność sekcji jest stała:

```text
KAPSUŁA KONTEKSTU v1
Zadanie: task-123, runda 2, plik .forge/tasks/task-123.md
Tura: tester / confirmation
Cel: potwierdź bramkę po green kodera
Poprzednia własna decyzja: red — brakuje walidacji granicy
Handoff od kodera (green): dodano walidację; test ukierunkowany jest zielony
Zmiany od startu zadania: src/api.py, tests/test_api.py
Ostatnia bramka: targeted / green / pytest -q tests/test_api.py
```

Nie dołączamy równolegle prywatnego rekordu ani wpisów dziennika zadania.
Dziennik pozostaje telemetrią oraz pamięcią mistrza.

## 8. Budżety

Budżety obowiązują per pole, aby ważnego pola nie wyparł długi tekst innego:

| Element | Limit |
|---|---:|
| handoff | 1200 znaków |
| reason/summary własnej decyzji | 800 znaków |
| ogon ostatniej czerwonej bramki | 2000 znaków |
| lista zmienionych plików | 30 pozycji |
| pojedyncza ścieżka | 240 znaków |
| uwagi review łącznie | 1600 znaków |
| cała kapsuła | 6000 znaków |

Cięcie tekstu jest jawne (`…[ucięto]`) i zachowuje ogon outputu bramki, bo tam
zwykle znajduje się właściwy błąd. Listy są cięte po całych elementach.

## 9. Obserwowalność przed zmianą zachowania

Pierwszy etap wdrożenia nie zmienia promptów. Forge zapisuje do
`.forge/context-usage.jsonl` dla każdej tury:

- rolę, zadanie, rundę i tryb sesji;
- liczbę znaków stabilnych instrukcji, dynamicznych pól, prywatnego rekordu
  i całego promptu;
- rozmiar kapsuły wygenerowanej w trybie shadow;
- SHA-256 promptu i kapsuły, bez ponownego zapisywania ich treści;
- usage providera, jeżeli adapter go raportuje;
- wynik parsowania decyzji i liczbę zmian plików.

Znaki są metryką provider-neutral. Tokeny raportowane przez CLI są metryką
rozliczeniową; nie szacujemy ich jednym współczynnikiem znak/token, bo kod,
polski tekst i różne tokenizery dają inne wyniki.

## 10. Sesje Codeksa

Kapsuła rozwiązuje ciągłość bez pełnej historii, ale samo jej wdrożenie nie
wyłącza `codex exec resume`.

Po uruchomieniu kapsuły dla agentów bezsesyjnych należy porównać dwa tryby
Codeksa na reprezentatywnych zadaniach:

- `resume + capsule`;
- świeże wywołanie `capsule-only`.

Porównujemy input tokens, liczbę tur, odsetek błędnych kontraktów, powtórzone
odczyty plików i końcowy werdykt review. `capsule-only` może zostać domyślny
dopiero wtedy, gdy zmniejszy wejście bez pogorszenia tych wskaźników. Do tego
czasu sesje pozostają bez zmian.

## 11. Plan implementacji

### Etap 1 — pomiar i shadow capsule

1. Dodać `forge/context.py` z czystymi funkcjami budowania, limitowania i
   renderowania kapsuły.
2. Dodać zapis rozmiarów do `.forge/context-usage.jsonl`.
3. Generować kapsułę obok starego promptu, ale jeszcze jej nie wysyłać.
4. Dodać raport porównujący rozmiar starego kontekstu i kapsuły per rola.

Pliki: `forge/context.py`, `forge/orchestrate.py`, `forge/report.py`,
`tests/test_context.py`, `tests/test_report.py`.

### Etap 2 — kanoniczny stan handoffu

1. Dodać do `State` pola `coder_decision`, `incoming_handoff` i `last_gate`.
2. Zapisywać wynik kodera po poprawnym parsowaniu, nie przed nim.
3. Zapisywać obserwowany wynik bramki w kodzie Forge.
4. Dodać migrację ze starych `coder_summary` i `tester_handoff`.
5. Czyścić nowe pola razem z pozostałym stanem zadania.

Pliki: `forge/state.py`, `forge/task_pipeline.py`, `forge/orchestrate.py`,
`tests/test_task_pipeline.py`, `tests/test_task_lifecycle.py`.

### Etap 3 — przełączenie agentów bezsesyjnych

1. Zastąpić argumenty kontekstowe `tester_task_prompt` i
   `coder_task_prompt` jedną renderowaną kapsułą.
2. Wyłączyć dopinanie `tester_record` i `coder_record`.
3. Usunąć dziennik zadania z promptu testera.
4. Pozostawić tymczasowy fallback dla aktywnego starego checkpointu.
5. Po jednym pełnym cyklu zgodności usunąć stare pola ze `State`.

Pliki: `forge/prompts/__init__.py`, `forge/prompts/templates/tester.md`,
`forge/prompts/templates/coder.md`, `forge/orchestrate.py`, `forge/state.py`,
`tests/test_role_context.py`, `tests/test_task_flow.py`.

### Etap 4 — eksperyment sesji Codeksa

1. Dodać kontrolowany tryb `resume` kontra `capsule-only`.
2. Zbierać usage jako przyrost bieżącej tury.
3. Porównać co najmniej zadania proste, standardowe i z cyklem review.
4. Zmienić domyślne zachowanie tylko na podstawie kryteriów z sekcji 10.

### Etap 5 — porządki

1. Usunąć `_append_record`, `tester_record`, `coder_record`,
   `coder_summary` i `tester_handoff`, gdy nie istnieją już checkpointy
   wymagające migracji.
2. Zaktualizować `docs/PIPELINE.md`.
3. Usunąć przejściowy tryb shadow i flagę rollbacku.

## 12. Testy akceptacyjne

Implementacja jest gotowa, gdy:

- test renderera potwierdza, że każdy fakt występuje w promptcie dokładnie raz;
- restart działa przed testerem, przed koderem, po `green`, po review i po
  czerwonej pełnej bramce;
- długa odpowiedź nie może wyciąć identyfikatora zadania, fazy ani komendy;
- kapsuła jednej roli nie zawiera prywatnego rekordu drugiej;
- pełne transkrypty i output narzędzi nigdy nie trafiają do kapsuły;
- dynamiczna część promptu agentów bezsesyjnych jest mniejsza co najmniej
  o 30% w korpusie pomiarowym;
- nie rośnie liczba `InvalidDecision`, medianowa liczba rund TDD ani odsetek
  `request_changes`;
- cały pakiet testów Forge pozostaje zielony.

## 13. Ryzyka i zabezpieczenia

| Ryzyko | Zabezpieczenie |
|---|---|
| kapsuła zgubi istotny szczegół | etap shadow i fallback starego checkpointu |
| nowy stan zdubluje stare pola | jedno źródło prawdy z tabeli w sekcji 6 |
| limit utnie błąd testu | osobny budżet i zachowanie ogona bramki |
| stateless Codex pogorszy jakość | osobny eksperyment, bez automatycznej zmiany |
| telemetria zacznie kopiować prompty | zapis wyłącznie rozmiarów i hashy |
| format będzie zależny od providera | wspólny renderer i testy kontraktowe |

## 14. Decyzja

Wdrażamy Context Capsule etapami. Najpierw pomiar i shadow mode, następnie
przełączenie testerów i koderów bezsesyjnych. Sesje Codeksa są osobnym,
mierzalnym eksperymentem, a nie częścią pierwszej migracji.
