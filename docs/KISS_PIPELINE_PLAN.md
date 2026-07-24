# Forge KISS — plan nowego pipeline'u

Dokładna kolejność wdrożenia, zakres tasków i lista kodu przeznaczonego do
usunięcia znajdują się w `docs/KISS_IMPLEMENTATION_TASKS.md`.

## 1. Cel

Forge ma wymuszać prawdziwą kolejność TDD z jedną małą pętlą sterowaną przez
testera:

```text
TASK Z PLANU
  → (
       TESTER decyduje:
         nowy test → TEST RED → CODE GREEN → REFACTOR
         bez testu  → CODE → REFACTOR
         ukończone  → wyjście z pętli
    )
  → REVIEW
  → POPRAWKI (jeśli potrzebne)
  → COMMIT
```

Jednocześnie ma:

- izolować konteksty testera, kodera i reviewera;
- nie przenosić rozmów ani rozumowania między rolami;
- utrzymywać kontekst każdej roli tylko przez jedno zadanie i czyścić go po
  commicie;
- używać mocnego modelu do planowania, a tanich modeli do wykonania;
- opierać ocenę jakości na agentach, nie na rozbudowanych heurystykach;
- zachować tylko kilka prostych, binarnych bramek.

## 2. Główna zasada

Mechanika pilnuje faktów:

- fazy wykonały się we właściwej kolejności;
- w normalnej pętli TDD test nie został zmieniony przez kodera;
- finalne testy są zielone;
- niezależny reviewer ocenił diff przed poprawkami i commitem.

Agenci oceniają znaczenie:

- czy test opisuje wymagane zachowanie;
- czy czerwień wynika z brakującej funkcjonalności;
- czy kod jest minimalny;
- czy refaktor jest sensowny;
- czy kryteria zadania są spełnione;
- czy zmiana nie osłabia testów lub kontraktu.

Forge nie próbuje zastępować tej oceny literalnymi walidatorami.

Najważniejsza decyzja sterująca należy do testera. W każdej rundzie ten sam
tester, zachowujący własny kontekst bieżącego zadania, ocenia aktualny stan i
wybiera dokładnie jedno:

- dopisać nowy czerwony test;
- przekazać pracę koderowi bez nowego testu;
- uznać implementację za ukończoną i skierować ją do review.

Po każdym CODE→REFACTOR sterowanie wraca do sesji testera tego zadania. To
tester, a nie licznik, mapa DONE lub mechaniczny smell gate, decyduje o końcu
pętli. Sesje testera i kodera pozostają od siebie całkowicie oddzielone.

## 3. Pipeline

Zadanie przechodzi przez małą pętlę TDD:

```text
TESTER
  ├─ red    → KODER: green → refactor → wróć do TESTERA
  ├─ code   → KODER: implement/refactor → wróć do TESTERA
  └─ review → REVIEWER → POPRAWKI → COMMIT
```

Planista pracuje raz na wsad kilku zadań.

### 3.1. PLAN

Planista używa mocnego modelu. Czyta brief, dokumentację, backlog i krótkie
podsumowania porażek. Tworzy do 3–5 małych zadań.

Jedno zadanie opisuje jedno zachowanie i zawiera:

```markdown
# Zadanie: <tytuł>

## Cel
<po co powstaje przyrost>

## Kryteria akceptacji
- <obserwowalne zachowanie>
- <wymagane pokrycie testowe>
- <warunek końcowy>

## Publiczny kontrakt
<API lub rezultat widoczny dla konsumenta>

## Ścieżki testów
<pliki lub globy>

## Ścieżki kodu
<pliki lub globy>

## Test ukierunkowany
<sugerowana komenda albo puste>

## Trudność
simple | standard | complex

## Poza zakresem
<czego zadanie nie obejmuje>
```

Małe zadanie zwykle potrzebuje jednej rundy RED→GREEN i jednej końcowej decyzji
testera. Pętla pozwala dopisać kolejny test, gdy po pierwszym przyroście tester
widzi jeszcze niepokryte kryterium. Nie ma osobnych smell counters ani
rozbudowanych stanów mikro-TDD. Jedynym bezpiecznikiem jest mały limit rund
(domyślnie 4); jego przekroczenie oznacza, że zadanie należy podzielić.

### 3.2. TESTER — decyzja sterująca

Na starcie zadania Forge tworzy osobny, pusty kontekst testera przypisany do
tego zadania. Pierwsze wywołanie TESTERA inicjalizuje sesję, a każda następna
runda wznawia tę samą sesję. Kontekst nie jest zerowany między testem 1,
kodowaniem i testem 2. Tester dostaje wyłącznie:

- plik bieżącego zadania;
- wskazane pliki testowe;
- publiczny kontrakt;
- minimum kodu potrzebnego do znalezienia punktu wejścia;
- aktualny diff zadania;
- krótkie wyniki ostatnich uruchomień testów, jeśli to kolejna runda;
- komendę testową projektu.

Nie dostaje historii planowania ani poprzednich rozmów.

Tester:

1. czyta kryteria;
2. ocenia aktualne testy i implementację;
3. wybiera następną akcję: `red`, `code`, `review` albo `blocked`.

#### Decyzja `red`

Tester tworzy minimalny test opisujący następne brakujące zachowanie i
uruchamia go.

Test jest gotowy, gdy wykonuje się i failuje przez brak wymaganego zachowania,
a nie przez składnię, import, brak zależności lub środowisko.

Wynik:

```json
{
  "status": "red",
  "command": "pytest -q tests/test_feature.py",
  "test_files": ["tests/test_feature.py"],
  "reason": "Brakujące zachowanie powoduje błędny rezultat."
}
```

#### Decyzja `code`

Tester nie dopisuje sztucznego testu i przekazuje krok koderowi, gdy:

- istniejący test już opisuje brakujące zachowanie;
- potrzebny jest czysto strukturalny refaktor;
- potrzebna jest aktualizacja dokumentacji lub inny krok bez nowego
  zachowania.

```json
{
  "status": "code",
  "command": "pytest -q tests/test_feature.py",
  "reason": "Istniejący test już specyfikuje brakujące zachowanie."
}
```

Tester nie może wybrać `code` dla nowego zachowania, które nie ma testu.
W takim przypadku musi najpierw wybrać `red`.

#### Decyzja `review`

Jeżeli wszystkie kryteria są zaimplementowane i odpowiednio pokryte, tester
uruchamia test ukierunkowany oraz pełną suitę. Dopiero gdy obie komendy są
zielone, nie zmienia już plików i kończy pętlę:

```json
{"status": "review", "reason": "Implementacja i pokrycie zadania są kompletne."}
```

Jeżeli tester nie potrafi podjąć wiarygodnej decyzji:

```json
{"status": "blocked", "reason": "..."}
```

Forge parsuje decyzję, zapisuje aktualny diff i przekazuje sterowanie dalej.
Nie sprawdza mechanicznie, czy tester zmienił wyłącznie testy ani czy proces
zwrócił czerwony kod wyjścia. To następny, niezależny koder ocenia test, jego
zakres i przyczynę czerwieni.

Po `red` lub `code` sterowanie przechodzi do kodera. Po `review` pętla się
kończy. Forge zapisuje hash testów wyłącznie po to, aby wykryć ich późniejszą
zmianę przez kodera w normalnej pętli.

### 3.3. CODE GREEN → REFACTOR

Koder ma drugi, całkowicie oddzielny kontekst przypisany do tego samego zadania.
Pierwsze przekazanie `red` lub `code` inicjalizuje sesję KODERA, a każde
następne przekazanie wznawia tę samą sesję. Kontekst kodera nie jest zerowany
między rundami ani przed poprawkami review. Koder dostaje tylko:

- plik zadania;
- gotowe czerwone lub istniejące testy;
- krótkie uzasadnienie decyzji testera;
- krótki ogon czerwonego uruchomienia, jeśli tester wybrał `red`;
- wskazane ścieżki kodu;
- komendę testu ukierunkowanego.

Nie dostaje rozmowy, sesji ani rozumowania testera.

W normalnej pętli przed review koder wykonuje dokładnie tę kolejność:

1. ocenia, czy test testera jest poprawny i rzeczywiście czerwony z właściwego
   powodu;
2. jeśli test wymaga zmiany, nie edytuje go i zwraca decyzję
   `test_changes_needed`;
3. jeśli test jest poprawny, pisze najprostszy kod zazieleniający test;
4. uruchamia test ukierunkowany;
5. dopiero po zieleni wykonuje mały refaktor;
6. ponownie uruchamia test ukierunkowany;
7. kończy bez zmiany testów.

Jeżeli refaktor nie jest potrzebny, pozostawia kod bez dodatkowych zmian.

Wynik:

```json
{
  "status": "green",
  "summary": "Zaimplementowano zachowanie i wykonano refaktor.",
  "refactor": "done | not_needed"
}
```

Jeśli test wymaga poprawy:

```json
{
  "status": "test_changes_needed",
  "reason": "Test failuje przez błąd składni zamiast przez brak zachowania."
}
```

Forge wznawia wtedy zachowaną sesję TESTERA i przekazuje jej wyłącznie uwagę
kodera. Tester poprawia test lub podejmuje inną decyzję. Koder nie może
samodzielnie zmienić testu w normalnej pętli.

W normalnej pętli Forge sprawdza tylko:

- hash testów nie zmienił się;

Wyniki uruchomień kodera są informacją dla testera w następnej rundzie, nie
mechaniczną bramką po każdym CODE+REFACTOR. To tester decyduje, czy potrzebny
jest kolejny test, kolejne kodowanie, czy można uruchomić finalną zieleń i
przejść do review.

Kolejność green→refactor jest odpowiedzialnością kodera. Forge nie tworzy
osobnego agenta ani dodatkowego automatu tylko po to, aby ją obserwować.
Prawdziwe TDD jest wspierane przez fizyczne oddzielenie faz TEST i CODE,
subiektywną ocenę testu przez kodera oraz zamrożenie testu przed startem
kodera.

Po zieleni sterowanie nie idzie automatycznie do review. Wraca do zachowanej
sesji testera, który ocenia cały aktualny stan i podejmuje kolejną decyzję.

### 3.4. REVIEW

Review zaczyna się wyłącznie po jawnej decyzji `status=review` testera.

Reviewer zaczyna w świeżej sesji. Dostaje:

- plik zadania;
- finalny diff od taga startowego;
- listę zmienionych plików;
- krótkie podsumowanie czerwonego testu;
- wynik testu ukierunkowanego;
- wynik pełnej suity.

Nie dostaje rozmów, raportów narzędziowych ani rozumowania testera i kodera.

Reviewer ocenia:

- czy test naprawdę sprawdza wymagane zachowanie;
- czy początkowa czerwień była wiarygodna;
- czy test nie jest tautologiczny;
- czy implementacja nie hardkoduje wyniku;
- czy refaktor zachował kontrakt;
- czy wszystkie kryteria zadania są spełnione;
- czy diff jest mały i mieści się w zakresie;
- czy dokumentacja jest aktualna.

Wynik:

```json
{"verdict": "approve", "notes": []}
```

albo:

```json
{"verdict": "changes", "notes": ["Jedna konkretna blokująca uwaga."]}
```

Forge nie wymaga mapy kryterium→test, nie dopasowuje tekstów checkboxów i nie
szuka nazw testów jako substringów pliku.

### 3.5. POPRAWKI I COMMIT

Po `approve` nie ma poprawek. Forge przechodzi do commita.

Przy `changes` uwagi review stają się krótkim kontraktem jednej fazy poprawek:

- poprawki zawsze wykonuje KODER w swojej zachowanej sesji zadania;
- koder może wtedy zmieniać zarówno testy, jak i kod produkcyjny, jeśli wynika
  to z uwag review;
- dla poprawki zachowania koder najpierw dodaje lub poprawia test i potwierdza
  jego właściwą czerwień, następnie poprawia kod do zieleni i refaktoruje;
- dla uwagi dotyczącej wyłącznie testu, refaktoru albo dokumentacji zmienia
  tylko potrzebne pliki;
- po poprawkach test ukierunkowany i pełna suita muszą być zielone.

Domyślnie dozwolona jest jedna runda poprawek. Jeśli nie wystarczy, zadanie
zostaje zachowane jako artefakt porażki i wraca do planisty do podziału.
Po poprawkach nie wracamy do testera i nie uruchamiamy domyślnie drugiej pełnej
recenzji. Forge zapisuje nowy hash testów, wykonuje finalną zieloną bramkę i
przechodzi do commita. Dzięki temu przepływ pozostaje
`REVIEW → POPRAWKI KODERA → COMMIT`.

Forge commituje dokładnie drzewo, dla którego:

- test ukierunkowany był zielony;
- pełna suita była zielona;
- testy miały hash zapisany po fazie TEST albo po dozwolonych poprawkach
  KODERA wynikających z review;
- reviewer wydał `approve` albo wszystkie jego konkretne uwagi zostały
  zastosowane w dozwolonej rundzie poprawek.

Następnie wykonuje push jak obecnie.

## 4. Minimalne bramki

W domyślnym pipeline pozostają trzy bramki:

1. Koder nie zmienił testów w normalnej pętli; jeśli test wymaga zmiany,
   przekazuje go z powrotem testerowi. Po review może zmienić testy
   wyłącznie w ramach zgłoszonych poprawek.
2. Test ukierunkowany i pełna suita są zielone przed wysłaniem zadania do
   REVIEW.
3. Tylko tester może zakończyć pętlę; niezależny, zawsze świeży reviewer ocenia
   diff przed ewentualnymi poprawkami i commitem.

Do tego dochodzą techniczne zabezpieczenia:

- checkpoint fazy;
- tag startowy zadania;
- timeout procesu wraz z jego procesami potomnymi;
- zachowanie artefaktu porażki;
- commit zweryfikowanego stanu.

## 5. Co usuwamy

Nowy pipeline nie używa:

- dokładnie jednego testu na cykl;
- map DONE;
- literalnego porównywania kryteriów;
- sprawdzania nazw funkcji testowych w plikach;
- `no_test_count`;
- `gate_not_red_count`;
- `done_reject_count`;
- smell gates;
- chmod testów;
- snapshotów każdego mikrocyklu;
- globalnego worktree anty-osłabiania;
- selektywnych automatycznych revertów;
- wspólnego lub przenoszonego kontekstu między rolami;
- limitu 12 mikrocykli;
- wielu rund review i fix.

Zmiana poza zwykłym zakresem jest oceniana przez review. Twardo blokowane
pozostają tylko krytyczne naruszenia, np. zapis poza repo, sekret lub zmiana
chronionego artefaktu weryfikacyjnego.

## 6. Kontekst

Kontekst każdej roli jest budowany oddzielnie i ma stały limit:

| Element | Limit |
|---|---:|
| treść zadania | 6 tys. znaków |
| ogon błędu | 3 tys. znaków |
| diff w prompcie | 12 tys. znaków |
| lista plików | 30 pozycji |

Pełne logi pozostają na dysku. Prompt zawiera ich ścieżki i krótkie
podsumowanie.

Każdy prompt mówi agentowi:

- zacznij od wskazanych plików;
- szukaj tylko konkretnego symbolu;
- nie listuj całego repo;
- nie czytaj pełnego DESIGN/ARCHITECTURE/BACKLOG, jeśli rola ich nie wymaga;
- nie powtarzaj niezmienionego polecenia;
- ogranicz output narzędzi do podsumowania i istotnego błędu.

Tester i koder mają dwa niezależne identyfikatory sesji. Każda kolejna runda
wznawia sesję właściwej roli. Żaden prompt nie dostaje sesji ani transkryptu
drugiej roli. Reviewer zawsze startuje bez sesji.

Po commicie zadania Forge czyści sesję testera i kodera. Następne zadanie
zaczyna z dwoma pustymi kontekstami. Dzięki temu kontekst pomaga w obrębie
jednego zadania, ale nie rośnie przez całą historię projektu.

## 7. Modele

Routing:

| Rola | Model |
|---|---|
| bootstrap | max |
| planista | strong |
| tester | efficient; balanced dla complex |
| koder | economy dla simple, efficient dla standard/complex |
| reviewer | efficient dla simple, balanced dla standard, strong dla complex |
| weryfikator celu | balanced |

Mocny planista amortyzuje koszt na kilka zadań. Tanie modele wykonują małe,
precyzyjne kroki. Reviewer pozostaje niezależny i może dostać silniejszy model
przy publicznym API, toolchainie, migracji lub wcześniejszej porażce.

## 8. Porażki i retry

Obsługa jest celowo prosta:

- niepoprawny JSON — jedna prośba o zwrócenie wyłącznie poprawnego obiektu,
  bez narzędzi i nowych zmian;
- koder ocenił test jako niewiarygodny — sterowanie wraca do tej samej sesji
  TESTERA z konkretną uwagą;
- wynik kodera nadal nie spełnia zadania — tester ponownie wybiera `red` albo
  `code` i wznawiana jest właściwa sesja;
- tester wykonał 4 rundy bez decyzji `review` — zadanie jest za duże i wraca
  do planisty do podziału;
- reviewer zwrócił `changes` — jedna runda poprawek wykonywana przez KODERA,
  który może zmienić wskazane testy i kod;
- druga porażka tego samego etapu — zachowanie artefaktu i replan.

Nie ma selektywnych automatycznych revertów. Przy porażce całego zadania Forge
zachowuje artefakt i wraca do taga startowego.

## 9. Stan

Minimalny stan bieżącego zadania:

```python
task
phase          # test | code | review | fix
start_tag
test_files
test_hash
targeted_cmd
tdd_round
retry_used
review_note
tester_session
coder_session
```

Sesje są pamięcią wyłącznie bieżącego zadania. Po jego commicie oba
identyfikatory są czyszczone. Pełne logi i wyniki żyją w `.forge/`, a wiedza
produktowa w repo.

## 10. Implementacja

### Etap 1 — szybkie naprawy

1. Usunąć fallback `brak JSON → no_test`.
2. Dodać zabijanie całej grupy procesu po timeout.
3. Utrzymać osobne `resume` testera i kodera w ramach zadania oraz czyścić oba
   dopiero po commicie.
4. Ograniczyć ogony logów przekazywane agentom.

### Etap 2 — KISS flow za flagą

```text
FORGE_PIPELINE_MODE=kiss
FORGE_PIPELINE_MODE=micro
```

W `forge/prompts.py` dodać trzy krótkie prompty:

- `tester_decision_prompt`;
- `code_prompt`;
- `review_prompt`.

W `forge/orchestrate.py` dodać prosty przepływ:

```python
while True:
    decision = run_tester_phase()
    if decision == "review":
        break
    code_result = run_code_phase(decision)
    if code_result == "test_changes_needed":
        continue
run_review_phase()
run_corrections_if_needed()
commit()
```

Współdzielić istniejące planowanie, routing modeli, `run_gate`, checkpointy,
git/push i końcową weryfikację celu. Nie tworzyć nowej warstwy polityk.

### Etap 3 — pilotaż

Uruchomić reprezentatywne zadania:

- simple;
- publiczne API;
- bugfix;
- refaktor;
- toolchain;
- zewnętrzny proces;
- zadanie zakończone przez testera bez kolejnego kodowania;
- zadanie wymagające dwóch rund TDD;
- przekazanie `code` bez sztucznego nowego testu;
- niepoprawny JSON;
- timeout.

Mierzyć:

- liczbę wywołań na zadanie;
- tokeny i czas na zadanie;
- odsetek `review: changes`;
- porażki wynikające ze złego testu;
- defekty znalezione po commicie.

### Etap 4 — zmiana defaultu

KISS zostaje domyślny, gdy typowe zadanie kończy się po jednej rundzie TDD i
jednej końcowej decyzji testera, zużycie tokenów wyraźnie spada, a jakość
review i liczba defektów nie pogarsza się. Następnie stary micro pipeline i
jego pola stanu są usuwane.

## 11. Testy Forge

Nowe testy orkiestratora powinny potwierdzać:

1. Tester wybiera `red`, `code`, `review` albo `blocked`, a Forge nie ocenia
   mechanicznie semantyki tej decyzji.
2. Koder jako następny agent ocenia test i przyczynę czerwieni.
3. Koder może zwrócić `test_changes_needed`, co wznawia zachowaną sesję
   testera z jego uwagą.
4. `code` nie wymaga tworzenia sztucznego nowego testu.
5. Tylko decyzja `review` kończy pętlę TDD.
6. Po CODE+REFACTOR sterowanie wraca do zachowanej sesji testera.
7. Hash testów nie może zmienić się w normalnej fazie CODE.
8. Test ukierunkowany i pełna suita są zielone dopiero na granicy przed
   REVIEW, a nie jako mechaniczna bramka każdej rundy CODE.
9. Reviewer nie dostaje kontekstu autorów.
10. Wszystkie poprawki review wykonuje KODER w zachowanej sesji.
11. KODER może po review zmienić testy, ale w normalnej pętli nie może.
12. Poprawka zachowania zachowuje wewnętrzną kolejność test-red → code-green
    → refactor.
13. Niepoprawny JSON nie staje się decyzją semantyczną.
14. Przekroczenie małego limitu rund kieruje zadanie do podziału.
15. Timeout zabija procesy potomne.
16. Restart wznawia właściwą fazę i rundę.
17. Commit odpowiada zweryfikowanemu drzewu.

## 12. Definicja KISS

Cały mechanizm powinien dać się opisać jednym zdaniem:

> Mocny planner tworzy małe zadanie; tani tester we własnym kontekście decyduje,
> czy dopisać czerwony test, przekazać krok koderowi, czy zakończyć
> implementację; koder pracuje w osobnym kontekście, decyzja wraca do testera
> po każdym kodowaniu i refaktorze, a dopiero potem całkowicie świeży reviewer
> ocenia finalny diff; wszystkie jego poprawki w testach i kodzie wykonuje ten
> sam koder, po czym następuje commit i wyczyszczenie obu kontekstów.

Nowa reguła sterowania powinna trafić do pipeline'u tylko wtedy, gdy rozwiązuje
konkretną, powtarzalną porażkę, której nie potrafią rozstrzygnąć dobrze
zaprojektowane prompty i niezależne konteksty.
