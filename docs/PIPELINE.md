# Forge KISS pipeline

Jedno zadanie przechodzi przez pętlę `tester ↔ coder`, następnie `review`.
Reviewer zwraca `approve`, `suggestions` albo `request_changes`.
`request_changes` wraca do testera i rozpoczyna nowy cykl TDD zakończony
świeżym review. `suggestions` otwiera CYKL DOMYKAJĄCY: tester rozlicza uwagi i
sam dostarcza zadanie — drugiego review już nie ma. `approve` prowadzi
bezpośrednio do `commit`.

Tester decyduje o dalszym kroku: `red`, `code`, `review` albo `blocked`. Po
`red` lub `code` koder odpowiada `green`, `test_changes_needed` albo
`tester_input_needed`. Oba niezielone wyniki wraz z powodem wracają do tej
samej sesji testera. Limit `max_tdd_rounds` wynosi domyślnie 10 i oznacza
potrzebę podziału zadania. W cyklu domykającym kontrakt testera zmienia się na
`red|code|finalize|blocked`: `review` znika (recenzja tego diffu już zapadła),
a `finalize` wymaga niepustego uzasadnienia rozliczającego uwagi jako
zastosowane albo odrzucone.

Jałowe okrążenia są liczone. Powrót z recenzji do testera, po którym
fingerprint drzewa jest identyczny jak przy poprzednim powrocie, zwiększa
licznik; `max_review_cycles` (domyślnie 3) przekroczony kończy zadanie jako
`PORZUCONE: review_loop`. Realna zmiana w drzewie zeruje licznik.

## Bootstrap, preflight i Product Owner

Projekt prowadzimy zwinnie: zakres nie jest ustalany z góry, tylko rośnie w
kolejnych przeglądach kierunku.

Bootstrap czyta cały brief raz i buduje **chodzący szkielet**: jedną cienką
ścieżkę end-to-end, która się uruchamia, plus jeden test sprawdzający dokładnie
tę implementację, którą uruchamia użytkownik. Historyjek **nie pisze** —
`BACKLOG.md` należy do Product Ownera, który zakłada go zaraz po bootstrapie
(wyzwalacz `start`). Rozdział ról jest tu warunkiem, żeby recenzja szkieletu
miała sens: dopóki bootstrap sam deklarował plasterek w backlogu, recenzent
odrzucał go za niezrealizowanie własnej deklaracji, choć wykonanie jej było
poza jego rolą. Cała wizja trafia do
`docs/PROJECT.md`: opis i odbiorca, cel docelowy z kryterium sukcesu,
ograniczenia i priorytety, klimat, sugestie autora, kolejne prawdopodobne etapy
i rzeczy świadomie odłożone, z jawnym rozróżnieniem wymagań, preferencji i
pomysłów opcjonalnych. Po zaakceptowanej recenzji Forge zapisuje kopię briefu w
`docs/BRIEF-SNAPSHOT.md` i jego skrót w stanie.

Preflight na starcie parkuje zastane zmiany tylko przy istniejącym HEAD i braku
aktywnego zadania. Przy unborn HEAD niczego nie parkuje, bo cały katalog jest
materiałem dla bootstrapu. Powrót odbywa się po jawnej nazwie gałęzi albo SHA;
`git switch -` jest zakazane.

Przegląd kierunku Product Ownera rusza na granicy między zadaniami — przed
planowaniem i przed weryfikacją celu, nigdy w trakcie aktywnego zadania — gdy
zajdzie którykolwiek warunek:

- **zmiana briefu** (skrót różny od snapshotu) — najmocniejsze wejście, wygrywa
  z pozostałymi powodami;
- **start** — backlogu nie ma wcale i żaden refill jeszcze nie padł, czyli PO
  zakłada pierwszą kolejkę po bootstrapie: maksymalnie 3 historyjki prowadzące
  do uruchamialnego demo;
- **kadencja** — minęły `FORGE_STEERING_BATCHES` (domyślnie 2) wsady planisty
  od ostatniego przeglądu, czyli co 2×`FORGE_BATCH_SIZE` = 16 zadań;
- **refill** — otwartych historyjek jest mniej niż `FORGE_BACKLOG_LOW_WATER`
  (domyślnie 2);
- **wyczerpany backlog** — planista zgłosił `no_more_tasks` i to jest bezpiecznik
  refill.

Rola dostaje powód uruchomienia, diff briefu (tylko gdy się zmienił), raport
weryfikatora historyjek przy kadencji, listę niezaczętych zadań, notatkę
parkingu, nierozstrzygnięte uwagi recenzenta architektury (`.forge/po-handoff.md`
— materiał, nie zobowiązanie) i ścieżkę notatnika; `docs/PROJECT.md`
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
kolejne zadania. Świeży recenzent (`bootstrap_reviewer`, najsilniejszy
model) ocenia kierunek, nie styl: czy zmiana wynika ze stanu projektu, czy krok
jest najcieńszym sensownym przyrostem, czy nic nie zniknęło po cichu i czy
`goal_reached` jest uczciwe. `request_changes` wraca do roli przeglądu z uwagami;
budżet to `FORGE_MAX_BOOTSTRAP_REVIEWS` (domyślnie 4) recenzji. Wyczerpanie
budżetu cofa zmiany i zatrzymuje przebieg z checkpointem — dalej potrzebna jest
decyzja użytkownika.

Recenzja architektury bootstrapu chodzi tą samą pętlą, ale z własnym kontraktem
zakresu, bo ocenia szkielet, a nie produkt. `request_changes` wolno postawić
wyłącznie za wadę strukturalną: kierunek do przepisania, test mierzący inną
implementację niż uruchamiana, `docs/PROJECT.md` bez kierunku dla planisty albo
ścieżkę end-to-end, która nie działa. Brak funkcji, walidacji, przypadków
brzegowych i historyjek jest jawnie poza zakresem — to praca kolejnych zadań i
Product Ownera. Dla takich obserwacji recenzent ma werdykt `suggestions`:
przyjmuje szkielet, a uwagi jadą do `.forge/po-handoff.md`.

Recenzent widzi numer rundy i skumulowane uwagi z poprzednich rund, a
budowniczy dostaje je wszystkie naraz. Bez tego świeży recenzent zaczynał każdą
rundę od zera i wymieniał zarzut na nowy zamiast zbiegać do akceptacji, a
budowniczy potrafił cofnąć starszą poprawkę. Wyczerpany budżet rozstrzyga się
więc dwojako: seria **różnych** uwag oznacza recenzję bez dna, więc szkielet
zostaje przyjęty, a nierozliczone uwagi trafiają do Product Ownera jako materiał
na historyjki; uwaga **wracająca mimo poprawek** (podobieństwo słów treściowych
≥ 0,5) dowodzi, że bootstrap nie umie jej rozliczyć, i dopiero ona zatrzymuje
przebieg do decyzji człowieka. Pierwsze powtórzenie jeszcze nie kończy pracy —
bywa parafrazą świeżego recenzenta, a fałszywy stop kosztuje cały bootstrap od
nowa; przerywa dopiero drugie albo powtórzenie zastane na końcu budżetu.
`.forge/po-handoff.md` jest jednorazowym wejściem — najbliższa tura PO czyta go
i kasuje.

Rundy tej samej pętli zużywa też sprawdzian samego podejścia: brak `test_cmd`,
brak `docs/PROJECT.md` i czerwony build albo test po zielonej deklaracji autora.
To pomyłki widoczne maszynowo, a nie spór o kierunek, więc wracają do autora
razem z wyjściem sprawdzianu i wskazówką, że komendę wolno naprawić z obu stron
— dorobić brakujący cel albo zadeklarować ten, który istnieje. Zatrzymanie
przebiegu kosztowałoby tu cały bootstrap i decyzję człowieka za literówkę w
jednym poleceniu. Uwagi te omijają recenzenta, bo jego prompt gwarantuje zieloną
suitę i zaległy wpis o czerwonej kazałby mu sprawdzać rzecz sprawdzoną już przez
Forge. Nie kumulują się też jak uwagi recenzenta: pierwsze zielone podejście
kasuje je wszystkie, bo jako jedyne są odwoływalne dowodowo — Forge właśnie
uruchomił te komendy. Niesione dalej kazałyby autorowi „naprawiać" działające
polecenie, a regresję i tak złapie sprawdzian następnej rundy, bo chodzi za
każdym razem.

Przebieg zatrzymuje dopiero to samo wyjście sprawdzianu dwa razy z rzędu albo
sprawdzian czerwony do końca budżetu: zielona suita jest warunkiem wejścia do
dalszej pętli, nie opinią, więc takiego szkieletu nie przyjmujemy nigdy.
Powtórzenie rozstrzyga tu **równość** znormalizowanego wyjścia (bez czasów,
liczników, numerów linii i adresów), a nie próg podobieństwa od uwag recenzenta:
log jest w większości wspólną ramką, więc pod tamtym progiem dwa przebiegi
pytest różniące się liczbą czerwonych testów wychodzą identyczne i wyraźny
postęp autora zatrzymywałby przebieg. Rundę zużywa zarówno recenzja, jak i
obalone podejście, więc przy domyślnych czterech trzy nieudane podejścia
zostawiają jedną rundę na recenzję architektury.

Recenzentowi kierunku wolno uruchamiać kod i eksperymentować w drzewie — bez
tego mocna teza o kierunku wymagałaby zgadywania. Jego jedynym wynikiem
pozostaje werdykt: po turze drzewo i HEAD wracają do stanu, który sam oglądał
(snapshot sprzed recenzji, więc praca autora przeglądu zostaje nietknięta), a
cofnięte ścieżki trafiają do ledgera.

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
komendą i diagnostycznym ogonem wyniku, aby nie musiał ponownie uruchamiać
potencjalnie kosztownej albo niestabilnej suity.

Tester odpowiada również za jakość dotkniętych testów. Przed dodaniem testu
szuka realistycznego, dotąd niewykrywanego defektu; preferuje rozszerzenie lub
parametryzację istniejącej bramki. Po green może i powinien wykonać mały
refaktor testów oraz wspólnej infrastruktury, usuwając duplikacje i
change-detectory bez osłabiania pokrycia. Kod produkcyjny i jego refaktor nadal
należą do kodera.

Porażka bramki niesie ze sobą output nieudanej komendy — także dla testów
bootstrapu — żeby błąd startu procesu (np. brakujący interpreter) dało się
odróżnić od nieprzechodzącej asercji.

Po decyzji `review` świeży, read-only reviewer wykonuje zwykłe code review:
szuka błędów, przypadków brzegowych, naruszeń kontraktu i SOLID/KISS, design
smells, zbędnej złożoności, duplikacji, mylących nazw oraz testów bez wartości.
Nie zastępuje pełnej bramki, ale może uruchomić wąski test dla konkretnego
podejrzenia. `approve` wymaga pustej listy uwag. `suggestions` jest dozwolone
tylko wtedy, gdy diff można bezpiecznie commitować bez zastosowania uwag.
Tester ocenia każdą sugestię, może sam poprawić testy albo przekazać
zaakceptowaną zmianę koderowi, a następnie wybiera `finalize`. Jeśli poprawki
odsłonią rzeczywisty błąd, domyka go zwykłym cyklem TDD (`red`/`code`) i kończy
tym samym `finalize` — drugiej recenzji ten cykl nie ma.

Przy `request_changes` uwagi wracają przez kapsułę do świeżego wywołania
testera, które rozpoczyna nowy cykl TDD. Jeśli reviewer mimo roli read-only
zapisze pliki, Forge nie porzuca ani nie cofa zadania: otwiera cykl domykający,
podaje testerowi dokładne ścieżki do oceny i pozwala mu dostarczyć zadanie po
rozstrzygnięciu tego diffu. Mechanicznej bramki „zapis reviewera ⇒ jeszcze
jedno review" nie ma — to ona zapętliła bieg z 2026-08-13
(`docs/AWARIE-2026-08-13.md`), bo katalog sesji agenta CLI powstawał w projekcie
przy każdym wywołaniu. Stan runtime agentów (`.opencode/`, `.claude/`,
`.codex/`, `.grok/`, `.kiro/`, `.aider/`) jest odsiewany razem z cache'ami
narzędzi i wykluczany lokalnie w `.git/info/exclude`. `approve`, a także
poprawne `finalize`, przechodzą do pełnej bramki i commitu.

## Kapsuła kontekstu i notatniki ról

Przed każdą turą testera albo kodera Forge buduje małą, deterministyczną
kapsułę z aktywnego zadania, fazy TDD, bieżącej decyzji lub handoffu, listy
zmian od tagu startowego i aktywnych uwag review. Kapsuła nie jest zapisywana
do `STATE.json`. Prompt wykonawcy nie zawiera ledgera ani surowych rekordów
poprzednich odpowiedzi; jedynym outputem narzędzia przekazywanym w kapsule jest
diagnostyczny ogon czerwonej pełnej bramki. Handoff po `green` występuje tylko
raz.

Każda tura testera i kodera jest świeżym wywołaniem, także dla Codexa.
Pipeline nigdy nie przekazuje `session_id` do resume i nie zapisuje ID zwróconej
sesji. Stare identyfikatory w kompatybilnym checkpointcie są czyszczone przy
wznowieniu zadania; całą kontrolowaną ciągłość zapewniają kapsuła i notatnik.

Każde zadanie ma dwa opcjonalne, prywatne notatniki:
`.forge/notebooks/<task-id>/tester.md` i
`.forge/notebooks/<task-id>/coder.md`. Prompt wskazuje wyłącznie plik własnej
roli, ale nie wkleja jego zawartości. Rola sama decyduje, czy go przeczytać
lub przepisać. Kod, diff, plik zadania i wyniki uruchomionych testów zawsze
mają przed nim pierwszeństwo.

Brakujące template'y powstają zarówno przy starcie, jak i wznowieniu zadania,
bez nadpisywania istniejących notatek. Stare `tester_record` i `coder_record`
są jednorazowo przenoszone pod nagłówek migracyjny, po czym czyszczone w
checkpointcie. Po udanym commicie katalog zadania jest usuwany. Przy porażce
trafia do `.forge/failed/<task-id>/notebooks/` razem z pozostałą diagnostyką;
housekeeping usuwa tylko osierocone katalogi aktywnych notatników i stosuje
zwykłą retencję do całego artefaktu porażki.

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

Tester, koder i reviewer zatwierdzają werdykt skryptem `.forge/verdict.py`
(kopia `forge/verdict.py`, wgrywana przed każdą turą razem z plikiem kontraktu
tej tury). Skrypt sprawdza kontrakt natychmiast: błąd to exit 1 z powodem i
oczekiwanym kształtem, więc rola poprawia werdykt **w tej samej sesji**, kosztem
jednego kroku narzędziowego. Zatwierdzony werdykt wygrywa z tekstem tury; brak
pliku znaczy „rola nie użyła skryptu" i wtedy werdykt czytamy jak dotąd, z
ostatniego bloku ```` ```json ```` odpowiedzi.

Z tekstu wybieramy kandydata **świadomie kontraktem roli**: rola bywa gadatliwa
po werdykcie (2026-08-10 tester dokleił drugi blok z samą poprawką notatnika i
skasował tym 40-minutową turę), więc wygrywa ostatni obiekt, który przechodzi
walidację, a nie ostatni obiekt w ogóle.

Dopiero gdy zawiodą obie drogi, niepoprawna decyzja dostaje jedną prośbę o
korektę samego formatu — czyli powtórzenie całej tury; powód odrzucenia i
surowe wyjście pierwszej próby lądują wtedy od razu w logu i w
`.forge/failed/<zadanie>/invalid_json/`. Druga niepoprawna odpowiedź zatrzymuje
przebieg z zapisanym checkpointem.

### Historyjki, statusy i raport

`BACKLOG.md` jest parsowalną kolejką `US-NNN` i w całości należy do Product
Ownera — zakłada go jego pierwsza tura (`start`), a nie bootstrap. Każda historyjka ma
`Dlaczego teraz`, `Sprawdzenie` i `Poza zakresem`; parser przed recenzją egzekwuje
format, unikalność ID, brak znikających wpisów i zakaz samodzielnej zmiany
statusu przez PO. Recenzent ocenia dopiero semantykę.

Recenzja tury PO kończy się tą samą asymetrią, co recenzja bootstrapu, i z tego
samego powodu: strukturę orzekł już parser, więc dalej idzie sama opinia o
kierunku. `suggestions` przyjmuje turę i odkłada uwagę do `.forge/po-handoff.md`
na następną turę PO — to jest domyślne wyjście dla uwagi słusznej, ale
niekrytycznej. Wyczerpany budżet `max_bootstrap_reviews` z serią **różnych**
uwag oznacza recenzję bez dna: tura zostaje przyjęta, a ostatnie uwagi jadą do
handoffu. Dopiero uwaga **wracająca mimo korekt** (drugie powtórzenie) albo
tura, która nigdy nie przeszła parsera, zatrzymuje przebieg do decyzji
człowieka. Twarde zatrzymanie za samą opinię kosztowało 2026-08-13 cały bieg z
pięcioma zacommitowanymi zadaniami.

Zadania niosą pole `story`, a Forge deterministycznie przeprowadza statusy
`nowa → w toku → do weryfikacji → zrobiona` albo wraca do `w toku` po
niepotwierdzeniu. Weryfikator historyjek wykonuje zewnętrzne `Sprawdzenie:` i
zapisuje raport z `verified_at_batch` oraz `verified_sha`; nie czyta diffu i nie
zastępuje code review. Niezgodny nagłówek raportu oznacza brak świeżego raportu.

Kanoniczna pełna suita repozytorium:

```bash
python3 -m pytest -q
```
