# Plan wdrożenia — Product Owner, preflight i weryfikacja historyjek

Dokument wykonawczy. Diagnoza jest w sekcji 1, design i strumienie dalej.

## 0. Stan po przeglądzie

Pięć uwag z przeglądu, wszystkie przyjęte i wprowadzone do designu:

| # | uwaga | rozstrzygnięcie |
|---|---|---|
| 1 | nikt nie aktualizuje statusu historyjki → pusty raport weryfikatora | statusami zarządza **Forge deterministycznie** (§S4a), PO nie dotyka ich poza `porzucona`; dodatkowo weryfikator dostaje siatkę bezpieczeństwa po `verified_sha` |
| 2 | parking wywraca się na unborn HEAD | potwierdzone lokalnie (`fatal: invalid reference: @{-1}`); powrót **po nazwie gałęzi**, nie przez `@{-1}`, a unborn HEAD nie jest parkowany wcale (§S1) |
| 3 | naruszenia formatu mogły przejść przez recenzenta | walidacja rozdzielona na **twarde invarianty strukturalne** (parser, przed recenzentem) i **miękkie oceny semantyczne** (recenzent) — §S3 |
| 4 | brak deterministycznej świeżości raportu | `verified_at_batch` + `verified_sha` w `STATE.json`, nagłówek raportu tylko dla ludzi (§S5) |
| 5 | bilans liczony przy `batch_size=6` | poprawione na faktyczne `FORGE_BATCH_SIZE=8` → kadencja co ~16 zadań |

## 1. Diagnoza

Jakość zadań jest dobra, jakość *decyzji o tym, co budujemy* — słaba. Przyczyna
jest strukturalna, nie promptowa:

1. **`diff_bootstrap` robi trzy prace naraz** — wchłania zmianę briefu
   (mechanika), myśli produktowo, i steruje procesem (`replan`, `goal_reached`).
   W [diff-bootstrap.md](../forge/prompts/templates/diff-bootstrap.md) większość
   tekstu to strażnicy zakresu zapisu, a nie instrukcja myślenia o produkcie.
2. **Ta rola nie widzi produktu.** Cały materiał dowodowy to `git log --oneline`
   z `_recent_commits`, diff briefu i lista ID w kolejce. Tematy commitów są
   fatalnym proxy dla „czy to działa i czy jest dobre". Profil weryfikacji
   (`smoke_cmd`, [forge/verify.py](../forge/verify.py)) istnieje i nie jest tu
   używany.
3. **Asymetria formatu.** Zadanie ma wymuszony kształt (Cel, Kryteria,
   Kontrakt, Trudność, Poza zakresem) i dlatego trzyma jakość. `BACKLOG.md` to
   wolna proza bez kontraktu. Jakość jest tam, gdzie jest format.
4. **Zero pamięci produktowej.** Każdy przegląd to świeży model odtwarzający
   „gdzie jesteśmy" z tematów commitów.
5. **Weryfikator jest pusty.** [verify-goal.md](../forge/prompts/templates/verify-goal.md)
   to cztery linijki „oceń MVP" plus kody wyjścia.
6. **Twardy stop na brudnym drzewie.** `_require_clean` przerywa start, gdy w
   drzewie leży niezacommitowana praca — bez aktywnego zadania nie ma żadnej
   ścieżki wyjścia poza ręczną interwencją.

## 2. Zakres

| # | strumień | rodzaj |
|---|---|---|
| S1 | preflight deterministyczny (parking pracy, wykrycie migracji) | kod, 0 tokenów |
| S2 | rozdzielenie ról: `bootstrap` zostaje, `diff_bootstrap` → `product_owner` | rola |
| S3 | kontrakt formatu user story w `BACKLOG.md` | prompt + walidacja |
| S4 | pole `story` w zadaniu | kod + prompt planisty |
| S5 | weryfikator historyjek (raport per-story, własna kadencja) | rola |
| S6 | notatnik Product Ownera | kod (reuse) |
| S7 | wyzwalanie PO: niski stan backlogu + kadencja | kod |
| S8 | recenzent PO przeprofilowany na checklistę zasad | rola |
| S9 | poziomy modeli bez wymiaru trudności dla ról nadzadaniowych | config |
| S10 | automatyczna migracja starego backlogu | preflight + PO |

Świadomie **odrzucone**: zlanie `bootstrap` i `diff_bootstrap` w jedną rolę
(uzasadnienie w §11).

## 3. Docelowy przepływ

```text
START PROCESU
  └─ preflight (kod)          — parking brudnego drzewa, stare tagi, flaga migracji

  ┌─ bootstrap (raz)          — szkielet, test_cmd, profil weryfikacji, drzewo docs
  │
  └─ pętla, wyłącznie na GRANICY ZADAŃ:
       │
       ├─ wyzwalacz `refill`   (mało historyjek)      → PO
       ├─ wyzwalacz `brief`    (zmiana briefu)        → PO
       ├─ wyzwalacz `cadence`  (co N wsadów)          → weryfikator historyjek → PO
       │                                                 (weryfikator pomijany,
       │                                                  gdy nie ma czego weryfikować)
       ├─ verify_goal          (PO orzekł goal_reached) → weryfikator historyjek
       │
       └─ planista → tester ↔ koder → recenzent → commit

     każda tura PO:  PO → parser (twarde invarianty) → recenzent PO → commit
```

Trzy pliki, trzy różne cykle życia — podział musi być ostry, inaczej notatnik PO
stanie się trzecią kopią planu:

| plik | zawartość | kto pisze |
|---|---|---|
| `docs/PROJECT.md` | trwała intencja: po co, dla kogo, kryterium sukcesu, ograniczenia, nie-cele | PO |
| `BACKLOG.md` | uporządkowana kolejka historyjek ze statusem | PO |
| `.forge/notebooks/product-owner.md` | pamięć robocza: hipotezy, obserwacje, czego próbowaliśmy | PO |

---

## S1. Preflight deterministyczny

Uruchamiany raz na start procesu w `main`, **przed** pętlą i przed bootstrapem.
Zero wywołań modelu.

**Brudne drzewo — cztery przypadki, cztery reakcje:**

| stan | reakcja |
|---|---|
| czyste | nic |
| brudne + aktywne zadanie (`state.current_task`) | nic; to wznowienie, zastane zmiany idą do oceny testera (już działa) |
| brudne + brak aktywnego zadania + **HEAD istnieje** | **parking** |
| brudne + **unborn HEAD** (repo bez ani jednego commita) | **nie parkujemy** — wpis do ledgera i tyle |

Przypadek unborn HEAD jest realnie osiągalny: `ensure_repo` robi wyłącznie
`git init`, nie tworzy commita bazowego. Parkowanie nie ma tam sensu — nie ma
punktu odniesienia, do którego można wrócić, a cała zawartość drzewa **jest**
stanem początkowym, który bootstrap i tak wchłonie swoim
`commit_all(project, "chore: bootstrap projektu")`. To zresztą zgodne z
dzisiejszym zachowaniem: `one_iteration` jawnie pomija `_require_clean`, gdy
`rev-parse --verify HEAD` zawodzi.

**Parking (tylko przy istniejącym HEAD):**

1. **Zapamiętaj punkt powrotu** — `git symbolic-ref --short HEAD` (nazwa gałęzi)
   albo `git rev-parse HEAD` przy odłączonym HEAD.
2. `git switch -c forge/parked/<YYYYMMDD-HHMMSS>`
3. `commit_all(project, "wip: zaparkowana praca sprzed startu Forge")`
4. **Powrót po zapamiętanej wartości** — `git switch <nazwa>` albo
   `git switch --detach <sha>`.
5. `.forge/parked.md`: nazwa gałęzi, punkt powrotu, lista ścieżek, data
6. wpis do ledgera
7. `.forge/parked.md` trafia do promptu **najbliższego PO** jako jednorazowa
   notatka (ten sam wzorzec, co `.forge/steering.md`) i jest kasowany po
   konsumpcji

**`git switch -` jest zakazany w tym kodzie.** Opiera się na `@{-1}`, czyli na
reflogu HEAD, którego w świeżym repozytorium nie ma — sprawdzone lokalnie:
kończy się `fatal: invalid reference: @{-1}` i procesem uwięzionym na gałęzi
parkingowej. Jawny punkt powrotu załatwia przy okazji odłączony HEAD.

Operacja jest w pełni odwracalna (`git switch forge/parked/<data>`) i **nigdy
cicha** — log, ledger, plik i wejście PO. To ostatnie jest istotne: bez tego PO
zaplanuje od zera pracę, która już leży na gałęzi.

Każdy krok parkingu jest sprawdzany; niepowodzenie któregokolwiek zatrzymuje
start jawnym błędem i zostawia drzewo tak, jak je zastało. Cichy start po
nieudanym parkingu byłby gorszy niż dzisiejszy twardy stop.

**Pozostałe czynności preflightu:** usunięcie osieroconych tagów `task-*-start`
bez odpowiadającego aktywnego zadania; wykrycie starego backlogu (S10).

**Zadania**

- `T1.1` `forge/preflight.py`: `park_dirty_tree`, `drop_stale_task_tags`,
  `detect_legacy_backlog`; wpięcie w `main` przed pętlą.
- `T1.2` `_require_clean` zostaje jako asercja niezmiennika w miejscach
  wywołania — po preflighcie nie ma prawa się odpalić, a jeśli się odpali, to
  jest to prawdziwy błąd, nie stan startowy.
- `T1.3` Testy, każdy przypadek z tabeli osobno:
  - brudne + aktywne zadanie → brak parkingu;
  - brudne + bez zadania → gałąź, powrót **po nazwie**, czyste drzewo, notatka;
  - **świeże repo bez ani jednego commita** → brak parkingu, brak gałęzi, wpis
    w ledgerze, `one_iteration` dochodzi do bootstrapu;
  - odłączony HEAD → powrót przez `--detach` na ten sam SHA;
  - niepowodzenie parkingu → jawny błąd i nietknięte drzewo, nie cichy start.
- `T1.4` Test regresyjny zakazu: `git switch -` / `@{-1}` nie występuje w
  `forge/`.

---

## S2. Rozdzielenie ról

`bootstrap` zostaje bez zmian — nieidempotentne rusztowanie, raz.

`diff_bootstrap` → `product_owner`, z **wyciętą** mechaniką i **dodaną**
ewidencją:

**Wejście PO** (prompt niesie tylko to, czego nie ma na dysku):
- powód uruchomienia (`refill` / `brief` / `cadence`)
- diff briefu — tylko przy `brief` (mechanika [forge/brief.py](../forge/brief.py) bez zmian)
- raport weryfikatora historyjek — przy `cadence`; przy `brief` ostatni raport,
  jeśli świeższy niż `FORGE_STEERING_BATCHES` wsadów
- lista ID zadań w kolejce (nie duplikuj)
- notatka `.forge/parked.md`, jeśli istnieje
- ścieżka własnego notatnika

PO sam czyta `docs/PROJECT.md` i `BACKLOG.md`. Zakres zapisu bez zmian:
wyłącznie te dwa pliki, walidowane manifestem drzewa i cofane
(`_revert_out_of_scope`).

**Wyjście PO** — JSON rozszerzony o rozliczenie historyjek:

```json
{"summary":"...","stories_added":["US-007"],
 "stories_dropped":[{"id":"US-005","reason":"..."}],
 "stories_reopened":[{"id":"US-003","reason":"co dokładnie nie działa"}],
 "changes":["..."],"replan":false,"goal_reached":false,
 "notebook":"..."}
```

Nie ma tu `stories_closed`: **zamknięcie historyjki nie jest opinią PO, tylko
faktem wyliczanym przez Forge** z ukończonych zadań i potwierdzenia
weryfikatora (§S4a). PO jest jedynym właścicielem *treści* backlogu i jedyną
rolą mogącą historyjkę **porzucić**; statusy cyklu życia należą do kodu.

`stories_reopened` jest drugą stroną tej samej zasady i jedynym kanałem, którym
PO może powiedzieć „dostarczone, ale nie działa". Forge cofa wskazaną historyjkę
do statusu `nowa`, a `reason` — obowiązkowy, jak przy porzuceniu — trafia do
dziennika i do notatki kierunku, więc planista dostaje opis usterki zamiast
planować historyjkę od zera. Bez tego kanału jedynym sposobem zgłoszenia usterki
było `stories_dropped`, czyli skasowanie wciąż potrzebnej historyjki: PO płacił
za zgłoszenie utratą wymagania. Rozróżnienie jest ostre — `porzucona` znaczy
„potrzeba zniknęła", `stories_reopened` znaczy „potrzeba została, wykonanie nie".

**Właściciel `goal_reached`: PO proponuje, raport weryfikatora historyjek jest
dowodem.** Bez tego zdania decyzja wpada w szczelinę między nowymi rolami.

**Zadania**

- `T2.1` Nowy szablon `product-owner.md` + warianty wyzwalaczy; usunięcie
  szablonów `diff-bootstrap*` po przeniesieniu treści wartościowej.
- `T2.2` `phase_product_owner` na bazie `phase_diff_bootstrap`; zachować
  kotwiczenie na SHA sprzed fazy i rewert poza zakresem.
- `T2.3` Rozliczenie `stories_*` w ledgerze.

---

## S3. Kontrakt user story

To jest sedno całej zmiany: jakość jest tam, gdzie jest format.

### Kanoniczne zasady (do wklejenia w prompt PO)

1. **Pionowa i pokazywalna.** Każda historyjka kończy się czymś, co człowiek
   zobaczy albo uruchomi. Żadnych „dodaj warstwę danych".
2. **Jedna zmiana widoczna dla użytkownika**, w rozmiarze domykalnym jednym
   wsadem planisty. Nie mieści się → to epik, dziel.
3. **Wynik, nie rozwiązanie.** Kto / co / po co. Zakaz rzeczowników
   implementacyjnych (klasa, tabela, endpoint, moduł), chyba że narzuca je brief.
4. **`Sprawdzenie:`** — jedna linia opisująca, co robi ktoś z zewnątrz, żeby
   potwierdzić działanie: komenda, ekran do którego dociera, obserwowalne
   zachowanie. **Ta zasada czyni weryfikatora możliwym.** Bez niej pytanie „czy
   zrobione" nie ma odpowiedzi inaczej niż przez czytanie diffu.
5. **`Poza zakresem:`** — jawne nie-cele historyjki. Dokładnie to pole działa
   dobrze na poziomie zadania i powstrzymuje planistę przed pęcznieniem.
6. **`Dlaczego teraz:`** — jedna linia wiążąca historyjkę z celem z
   `PROJECT.md` albo z zaobserwowanym dowodem. Najtańszy mechanizm antydryfowy.
7. **Stabilne ID.** `US-NNN`. Dziś prompt zakazuje cichego kasowania, ale nie ma
   **żadnego sposobu wykrycia** naruszenia; ID daje wykrywalność, a
   weryfikatorowi jednostkę raportowania. **Status pisze Forge, nie PO** —
   patrz §S4a. PO nadaje nowej historyjce `nowa` i może przestawić dowolną na
   `porzucona(powód)`; każda inna zmiana statusu jest twardym naruszeniem.
8. **Kolejność = priorytet.** Backlog to kolejka, góra to następne.

**Anty-zasady, zapisane wprost w promptcie:**
- Żadnych estymat ani story pointów — nie ma modelu prędkości, to czyste tokeny
  szumu.
- **Sufit `FORGE_MAX_BACKLOG_STORIES` (domyślnie 6).** Nieograniczony PO napisze
  dwadzieścia historyjek i cała teza o cienkich plasterkach zamieni się w
  wodospad w przebraniu.

### Format zapisu

```markdown
## US-007 — Gracz widzi wynik potyczki  [nowa]

Jako gracz chcę po zakończeniu potyczki zobaczyć, kto wygrał i ile straciłem,
żeby zdecydować, czy warto było ryzykować.

- Dlaczego teraz: PROJECT.md stawia decyzyjność gracza jako kryterium sukcesu,
  a dziś potyczka kończy się bez żadnego podsumowania.
- Sprawdzenie: uruchom `make demo`, przejdź potyczkę do końca — pojawia się
  ekran z wynikiem i listą strat.
- Poza zakresem: statystyki historyczne, porównania między potyczkami.
```

### Walidacja: dwa rozłączne poziomy

Naruszeń formatu **nie wolno oddawać recenzentowi jako uwag**. Recenzent może
turę mimo nich zaakceptować, a wtedy S5 i S7 dostają backlog, którego parser nie
umie wiarygodnie zinterpretować — i awaria wychodzi dwa wsady później, w
zupełnie innym miejscu.

**Poziom 0 — koercja statusów.** `backlog.coerce_statuses` przepisuje kolumnę
statusu na stan cyklu życia znany Forge, **zanim** cokolwiek jest walidowane:
status sprzed tury wraca na miejsce, nowa historyjka dostaje `nowa`, a wartość
spoza zbioru (§S4a) — `do weryfikacji`. Nic tu nie wraca do PO i nic nie kosztuje
tury.

Podział jest celowy: **regułę, którą Forge umie wymusić zapisem, wymuszamy
zapisem; do PO wraca wyłącznie to, czego naprawić nie umiemy.** Poprzednia
wersja walidowała statusy i para reguł („status musi być legalny" + „PO nie może
zmienić statusu") zakleszczała się na sobie, gdy w pliku stał status spoza
kontraktu: zostawienie go było naruszeniem, a poprawienie — drugim. Kosztowało
to cały budżet korekt na najdroższym modelu, bez żadnego wyjścia dla PO.
Koercja nie umie zakleszczyć się z definicji, bo jest idempotentna: jej wynik
zawsze spełnia kontrakt. Leczy przy okazji skażenie spoza tej fazy (status
wpisany ręcznie albo przez turę roli zadaniowej), więc BACKLOG.md nie potrzebuje
osobnego strażnika przy każdej turze.

**Poziom 1 — twarde invarianty strukturalne.** Sprawdza parser
`forge/backlog.py`, **przed** recenzentem, i świadomie nie mówi nic o statusach:

- format i unikalność `US-NNN`;
- każda historyjka ma niepuste `Sprawdzenie:` i `Dlaczego teraz:`;
- żadne ID obecne przed turą nie zniknęło bez wpisu w `stories_dropped`;
- każde ID z `stories_reopened` istnieje w backlogu i nie stoi jednocześnie w
  `stories_dropped` — wznowienie jest ogłaszane planiście jako fakt, więc
  zmyślone ID dałoby pracę do wykonania na historyjce, której nie ma;
- backlog parsuje się w całości — żadnych sierocych bloków.

Naruszenie **wymusza korektę**: treść naruszenia wraca do PO jako uwagi, w tym
samym budżecie prób co recenzje (`FORGE_MAX_BOOTSTRAP_REVIEWS`). Identyczny
zestaw naruszeń dwa razy z rzędu kończy fazę natychmiast: pełna tura z tymi
uwagami już się nie powiodła, więc kolejne nie wniosą nic poza rachunkiem.
Wyczerpanie budżetu cofa fazę i zatrzymuje przebieg z checkpointem — dokładnie
tak, jak dziś zachowuje się wyczerpany budżet recenzji kierunku. Lepiej stanąć,
niż wpuścić nieparsowalny backlog do trzech kolejnych ról.

Parser stojący przed recenzentem oszczędza przy okazji wywołania: źle
sformatowana tura nigdy nie dociera do modelu recenzenta.

**Poziom 2 — miękkie oceny semantyczne.** Zostają uwagami recenzenta PO (§S8):
czy przyrost jest najcieńszy sensowny, czy teza wynika z dowodów, czy
`goal_reached` jest uczciwe, czy historyjka faktycznie opisuje wynik, a nie
rozwiązanie. Tego parser nie policzy i nie ma udawać, że potrafi.

Sufit `FORGE_MAX_BACKLOG_STORIES` jest **miękki** — może zostać przekroczony w
dobrej wierze podczas migracji (§S10), więc trafia do recenzenta, nie do
parsera.

**Zadania**

- `T3.1` `forge/backlog.py`: `parse`, `validate_hard`, `diff_stories`.
- `T3.2` Zasady w szablonie PO + szablon uwag korekcyjnych parsera.
- `T3.3` Pętla korekty w `phase_product_owner`: parser → korekta → recenzent.
- `T3.4` Testy parsera na realnych i zdegenerowanych backlogach; test, że
  wyczerpanie budżetu korekt cofa fazę i zostawia checkpoint.

---

## S4. Pole `story` w zadaniu

**To jest spoina, która trzyma cały projekt razem.** Bez linku task → historyjka
weryfikator nie wie, co w ogóle było próbowane, ledger nie grupuje, a „czy
historyjka zrobiona" zostaje zgadywaniem.

- Planista zwraca `"story": "US-007"` obok `depends_on`.
- `build_task_from_plan` przenosi pole; `""` jest **dozwolone** i znaczy „dług
  techniczny/dokumentacyjny" (zadania z `planner-debt-requirement.md`).
- ID nieznane w backlogu → ostrzeżenie w logu i ledgerze, **zadanie zostaje**.
  Nie kasujemy pracy za literówkę; sygnał trafia do PO i weryfikatora.
- Ledger: `task-041 UKOŃCZONE (US-007)`.

**Zadania**

- `T4.1` `build_task_from_plan` + `_write_current_task` + prompt planisty.
- `T4.2` Walidacja przynależności i wpis ostrzegawczy.
- `T4.3` Grupowanie po historyjce w [forge/report.py](../forge/report.py).

---

## S4a. Cykl życia statusu historyjki

Bez tego automatu cała reszta jest martwa: historyjka zostawałaby `nowa` przez
cały wsad, weryfikator dostawałby pustą listę, a raport — na którym opiera się
PO i decyzja `goal_reached` — byłby pusty mimo ukończonej pracy.

**Statusy pisze Forge, nie PO.** Jedynym wyjątkiem jest `porzucona`. To dzieli
własność czysto: PO odpowiada za *treść i kolejność* backlogu, kod za *fakty o
postępie*. Usuwa też całą klasę błędu agenta — status przestaje być prozą do
zgadnięcia. Forge i tak commituje `BACKLOG.md` przy planowaniu, więc nie
potrzeba do tego żadnej nowej maszynerii zapisu.

| status | znaczenie |
|---|---|
| `nowa` | nic nie ruszyło |
| `w toku` | ≥1 zadanie tej historyjki wystartowało, nie wszystkie zamknięte |
| `do weryfikacji` | żadne zadanie tej historyjki nie jest już aktywne ani w kolejce |
| `zrobiona` | weryfikator potwierdził |
| `porzucona(powód)` | decyzja PO |

**Przejścia — wszystkie deterministyczne:**

| z | do | moment | kto |
|---|---|---|---|
| `nowa` | `w toku` | start pierwszego zadania z `story=US-NNN` | Forge |
| `w toku` | `do weryfikacji` | commit zadania, po którym żadne zadanie tej historyjki nie jest aktywne ani w kolejce | Forge |
| `do weryfikacji` | `w toku` | nowy wsad dołożył zadanie do tej historyjki | Forge |
| `do weryfikacji` | `zrobiona` | weryfikator zwrócił `potwierdzona` | Forge |
| `do weryfikacji` | `w toku` | weryfikator zwrócił `niepotwierdzona` / `częściowa` | Forge |
| dowolny | `porzucona` | tura PO | PO |

**Ukończenie zadania celowo NIE oznacza `zrobiona`.** Zielona bramka mówi, że
kod robi to, co zapisał tester — nie, że użytkownik dostał obiecaną wartość.
Rozdzielenie tych dwóch rzeczy jest jedynym powodem, dla którego weryfikator
historyjek w ogóle istnieje.

„Wszystkie zadania historyjki" jest domykane po **bieżącej kolejce i aktywnym
zadaniu**, bo późniejszy wsad może dołożyć kolejne. Dlatego `do weryfikacji`
wraca do `w toku`, gdy tak się stanie — to normalny stan, nie regres.

**Siatka bezpieczeństwa.** Sam automat nie wystarcza: dowolny wyjątek, ręczna
edycja backlogu albo zadanie bez `story` mogłyby dać pusty raport przy realnie
wykonanej pracy. Dlatego weryfikator dostaje **sumę** dwóch zbiorów (§S5):

1. historyjki o statusie `do weryfikacji`;
2. historyjki wskazane przez zadania ukończone od `stories_verified_sha`,
   **niezależnie od zapisanego statusu**.

Drugi warunek jest tani (odczyt ledgera) i czyni pusty raport przy niepustej
pracy niemożliwym.

**Zadania**

- `T4a.1` `forge/backlog.py`: `set_status(story_id, status)` — punktowa edycja
  zachowująca resztę pliku bajt w bajt.
- `T4a.2` Przejścia wpięte w start zadania, commit zadania, wynik planowania i
  wynik weryfikatora.
- `T4a.3` `stories_verified_sha` w [forge/state.py](../forge/state.py) + zbiór
  „historyjki dotknięte od ostatniej weryfikacji" z ledgera.
- `T4a.4` Testy: pełny wsad ukończony → historyjki w `do weryfikacji`;
  dołożenie zadania cofa do `w toku`; zadanie bez `story` nie rusza niczego;
  historyjka z ręcznie zepsutym statusem i tak trafia do weryfikatora przez
  siatkę bezpieczeństwa.

---

## S5. Weryfikator historyjek

Zastępuje czterolinijkowe „oceń MVP" wykonywalną checklistą.

**Wejście:** suma zbiorów z §S4a — historyjki o statusie `do weryfikacji` **plus**
historyjki wskazane przez zadania ukończone od `stories_verified_sha` niezależnie
od statusu. Dla każdej jej linia `Sprawdzenie:`, plus dowody mechaniczne z
`verify.collect_evidence` (kody wyjścia, ścieżki logów — nie treści). Pusty
zbiór → weryfikator **nie jest wołany wcale** i kadencja przechodzi prosto do PO;
zero tokenów za pytanie bez materiału.

**Praca:** wolno mu uruchamiać produkt i eksperymentować w drzewie; potrzebuje
tego, żeby wykonać `Sprawdzenie:`. Ochrona jak u recenzenta kierunku —
`_snapshot_tree` przed turą, `_restore_snapshot` po niej, cofnięte ścieżki do
ledgera. Nie czyta diffu: to nie jest code review, tester i koder już to zrobili.

**Wyjście:**

```json
{"stories":[{"id":"US-007","status":"potwierdzona|niepotwierdzona|częściowa",
             "evidence":"co zrobiłem i co zobaczyłem"}],
 "verdict":"complete|changes","notes":["..."]}
```

**Kadencja — dwa miejsca, nie jedno:**

1. przy wyzwalaczu `cadence`, **przed** PO — jego raport jest wejściem PO; to
   jest domknięcie pętli i główny powód istnienia tej roli;
2. przy `verify_goal`, jak dziś.

Uruchamianie wyłącznie na końcu marnuje tę rolę. Raport ląduje w
`.forge/verification/stories-latest.md`.

**Świeżość jest liczona deterministycznie, nigdy z czasu modyfikacji pliku.**
Mtime kłamie po skopiowaniu projektu, wznowieniu z checkpointu i przy
przestawionym zegarze. Autorytatywne są dwa pola w `STATE.json`:

- `stories_verified_at_batch: int` — wartość `plan_batches` w chwili raportu;
- `stories_verified_sha: str` — HEAD w chwili raportu.

Ten sam nagłówek jest wpisywany na górę pliku raportu, ale **wyłącznie dla
ludzi i jako wykrywacz podmiany**: gdy nagłówek nie zgadza się ze stanem, raport
jest traktowany jak nieistniejący. Reuse przy wyzwalaczu `brief` wymaga
`plan_batches - stories_verified_at_batch < FORGE_STEERING_BATCHES`; inaczej PO
dostaje informację, że świeżego raportu nie ma, zamiast starego udającego nowy.

`stories_verified_sha` pełni podwójną rolę — jest też granicą zbioru „historyjki
dotknięte od ostatniej weryfikacji" z siatki bezpieczeństwa §S4a.

Przy `FORGE_BATCH_SIZE=8` i `FORGE_STEERING_BATCHES=2` to jeden przebieg na
około **16 zadań**.

**Zadania**

- `T5.1` Szablon `verify-stories.md` + `phase_verify_stories`.
- `T5.2` Snapshot/restore wokół tury; ledger dla cofniętych ścieżek.
- `T5.3` Wpięcie przed PO przy `cadence` i przed `verify_goal`; pominięcie przy
  pustym zbiorze wejściowym.
- `T5.4` `verify-goal.md` konsumuje raport zamiast pytać o „MVP" na sucho.
- `T5.5` `stories_verified_at_batch` i `stories_verified_sha` w stanie; test, że
  raport z niezgodnym nagłówkiem jest odrzucany, a nie reużywany.

---

## S6. Notatnik Product Ownera

Reuse [forge/notebooks.py](../forge/notebooks.py), z jedną różnicą: notatnik PO
jest **projektowy**, nie zadaniowy — `.forge/notebooks/product-owner.md`, poza
katalogami `<task-id>/`.

- `notebooks.prune_orphans` musi go pominąć — bez tego housekeeping skasuje
  jedyną pamięć produktową projektu.
- Zapis polem `notebook` decyzji, jak u testera i kodera. PO nie czyta go z
  dysku ani nie zapisuje sam.
- Treść: hipotezy i obserwacje („podejrzewam, że sterowanie jest za wolne —
  obserwuję"), czego próbowaliśmy i z jakim skutkiem, czego pilnuję.
  **Nie** kopia planu — od tego są `PROJECT.md` i `BACKLOG.md`.

**Zadania**

- `T6.1` Ścieżka projektowa w `notebooks.py` + wyjątek w `prune_orphans`.
- `T6.2` Notatnik w kapsule PO; test, że housekeeping go nie usuwa.

---

## S7. Wyzwalanie PO

Dziś backlog musi zejść do zera, planista pali wywołanie na `strong`, żeby
zwrócić `no_more_tasks`, ustawia `steering_due` i **dopiero wtedy** rusza
przegląd ([orchestrate.py:1450-1457](../forge/orchestrate.py#L1450-L1457)). Niski
próg wycina całą tę turę.

Częściowo już to mamy: `batch_drained` mierzy drenaż po rozmiarze wsadu.
Ulepszenie to przeniesienie miary z „ile planista wyprodukował" na „ile
historyjek stoi w backlogu" — miara bezpośrednia zamiast pochodnej.

**Wyzwalacze, w kolejności pierwszeństwa** (`brief` wygrywa, jak dziś):

| wyzwalacz | warunek | weryfikator przed PO |
|---|---|---|
| `brief` | skrót briefu ≠ snapshot | nie (reuse raportu) |
| `refill` | < `FORGE_BACKLOG_LOW_WATER` (2) historyjek o statusie `nowa` | nie |
| `cadence` | `plan_batches - steered_at_batch >= FORGE_STEERING_BATCHES` | **tak** |

Wszystkie sprawdzane **wyłącznie na granicy zadań przy pustej kolejce** —
warunek z `_steering_trigger` zostaje bez zmian i nie wolno go „uprościć"
(komentarz w kodzie tłumaczy dlaczego: `replan` skasowałby dopiero co
zaplanowany wsad).

**Zabezpieczenie przed pętlą:** `state.po_refill_batch` — `refill` nie odpala
się dwa razy w tym samym wsadzie planisty. Bez tego PO, który odmówi dołożenia
historyjek, kręci `max`-model w kółko.

Nazwy pól stanu (`steered_at_batch`, `steering_due`) **zostają** — zmiana
kosztowałaby migrację `STATE.json` bez żadnego zysku. Dopisujemy komentarz, że
„steering" znaczy teraz „przegląd Product Ownera".

**Zadania**

- `T7.1` `_po_trigger` na bazie `_steering_trigger`, z liczeniem historyjek
  przez `forge/backlog.py`.
- `T7.2` `FORGE_BACKLOG_LOW_WATER`, `FORGE_MAX_BACKLOG_STORIES` w
  [forge/config.py](../forge/config.py).
- `T7.3` `po_refill_batch` w [forge/state.py](../forge/state.py) + test pętli.

**Nowe pola `STATE.json`** (komplet z całego planu):

| pole | strumień | znaczenie |
|---|---|---|
| `po_refill_batch: int` | S7 | ostatni wsad, w którym odpalił się `refill` |
| `stories_verified_at_batch: int` | S5 | wsad ostatniego raportu historyjek |
| `stories_verified_sha: str` | S5, S4a | HEAD ostatniego raportu; granica „ukończone od" |
| `backlog_migrated: bool` | S10 | czy backlog jest już w formacie historyjek |

Nazwy `steered_at_batch` i `steering_due` **zostają** — zmiana kosztowałaby
migrację `STATE.json` bez zysku.

---

## S8. Recenzent PO

Bramka **zostaje**. To poziom, na którym jakość jest dziś najniższa — ostatnie
miejsce, z którego zdejmuje się kontrolę. Ale zmienia profil i cenę.

- `bootstrap_reviewer` (`max`) zostaje wyłącznie do jednorazowej recenzji
  architektury bootstrapu.
- Nowa rola `po_reviewer` (`strong`) recenzuje turę PO. `_reviewed_bootstrap`
  dostaje parametr nazwy roli — reszta maszynerii (budżet
  `FORGE_MAX_BOOTSTRAP_REVIEWS`, rewert, kotwica na SHA) bez zmian.
- Nowy profil recenzji: **nie proza, tylko checklista**
  1. czy każda nowa historyjka spełnia **semantyczne** zasady z §S3 — opisuje
     wynik, a nie rozwiązanie; `Sprawdzenie:` da się faktycznie wykonać z
     zewnątrz; `Dlaczego teraz:` wiąże się z `PROJECT.md` albo z dowodem.
     Struktury nie sprawdza wcale: do recenzenta dociera wyłącznie backlog już
     przepuszczony przez twarde invarianty parsera;
  2. czy teza o kierunku wynika z raportu weryfikatora, a nie z domysłu;
  3. czy nic nie zniknęło bez wpisu w `stories_dropped`;
  4. czy `goal_reached` jest uczciwe wobec raportu;
  5. czy przyrost jest najcieńszy sensowny.

To ocena obiektywna, więc `strong` wystarcza — nie potrzeba dwóch `max` na
rundę.

**Zadania**

- `T8.1` Parametr roli w `_reviewed_bootstrap`.
- `T8.2` Szablon `po-review.md` (checklista) + `po-corrections.md`.

---

## S9. Poziomy modeli

Role nadzadaniowe nie mają prawa zależeć od trudności zadania. Krotka
3-elementowa uzależniałaby model od wymiaru, który dla nich nic nie znaczy — to
gorzej niż za słaby model, bo myli następnego czytelnika. Idiom płaskiego wpisu
jest już w repo (`bootstrap`, `planner`).

```python
"product_owner":     {d: "max"      for d in TASK_DIFFICULTIES},
"po_reviewer":       {d: "strong"   for d in TASK_DIFFICULTIES},
"bootstrap_reviewer":{d: "max"      for d in TASK_DIFFICULTIES},  # bez zmian
"verifier":          {d: "strong"   for d in TASK_DIFFICULTIES},  # było economy/efficient/balanced
```

`phase_verify_goal` przestaje przekazywać `DEFAULT_TASK_DIFFICULTY` jako alibi
dla wymiaru, którego nie używa.

**Zadania**

- `T9.1` Wpisy w `ROLE_MODEL_LEVELS`, usunięcie `diff_bootstrap`.
- `T9.2` Aktualizacja [tests/test_architecture.py](../tests/test_architecture.py)
  i testów routingu.

---

## S10. Automatyczna migracja

Bootstrap **nie może** tego zrobić: odpala się wyłącznie przy
`not state.bootstrapped`, więc w istniejącym projekcie nigdy się nie uruchomi.
Migracja tam byłaby martwym kodem.

Podział pracy — wykrycie deterministyczne, konwersja przy okazji:

1. **Preflight (kod, 0 tokenów):** `BACKLOG.md` istnieje, ale nie zawiera ani
   jednego nagłówka `US-NNN` → `state.backlog_migrated = False`.
2. **Pierwszy przebieg PO** dostaje w promptcie sekcję migracyjną: „backlog jest
   w starym formacie prozy; przepisz istniejące wpisy na historyjki wg zasad,
   zachowując ich treść i kolejność; nie wymyślaj nowego zakresu przy tej
   okazji; nadaj ID od US-001; wszystkim nadaj status `nowa`".
   Statusy istniejącego dorobku ustala potem **weryfikator**, nie PO zgadujący
   z prozy.
3a. **Jednorazowa inwentaryzacja.** Pusty `stories_verified_sha` (projekt
   sprzed mechanizmu) oznacza, że nie ma od czego liczyć „ukończone od". W tym
   jednym przebiegu weryfikator dostaje **wszystkie nieporzucone historyjki**
   niezależnie od statusu, ograniczone do `FORGE_MAX_BACKLOG_STORIES` od góry
   kolejki; reszta czeka na następną kadencję. To zamienia migrację w
   inwentaryzację dorobku zamiast kazać komukolwiek zgadywać, co już działa.
3. Po zwalidowanym parserem wyniku → `state.backlog_migrated = True`, sekcja
   znika z promptu.

Precedens jest w repo: „projekt zbootstrapowany przed tym mechanizmem przechodzi
jednorazową synchronizację początkową".

**Ograniczenie sufitu przy migracji:** stary backlog może mieć więcej pozycji niż
`FORGE_MAX_BACKLOG_STORIES`. Migracja **nie kasuje** nadmiaru — przenosi ogon do
`PROJECT.md` jako „kolejne prawdopodobne etapy". Sufit obowiązuje dopiero od
następnej tury PO.

**Zadania**

- `T10.1` `detect_legacy_backlog` w preflighcie + pole `backlog_migrated`.
- `T10.2` Sekcja migracyjna w szablonie PO.
- `T10.3` Test end-to-end: backlog-proza → historyjki, nic nie zniknęło.

---

## 11. Co odrzucone i dlaczego

**Zlanie `bootstrap` i `diff_bootstrap` w jedną rolę „przygotowywacza".**
To dwie różne natury: bootstrap jest nieidempotentnym rusztowaniem (szkielet,
`test_cmd`, profil weryfikacji, drzewo docs) uruchamianym raz, PO jest
cyklicznym przeglądem. Zlanie oznacza, że każde cykliczne wywołanie na `max`
niesie ~2 KB instrukcji rusztowania, których nigdy nie użyje, a logika
wznowienia robi się mętna. Problem, który miał rozwiązać ten merge — twardy stop
na brudnym drzewie — jest deterministyczną robotą gitową i rozwiązuje go S1 za
zero tokenów.

**Zdjęcie recenzenta z PO.** Diagnoza mówi, że jakość jest najniższa właśnie na
tym poziomie. Zamiast usuwać bramkę, potaniamy ją i czynimy obiektywną (S8).

**Estymaty i story pointy.** Brak modelu prędkości → czysty szum.

## 12. Bilans kosztu

**Oszczędności:**
- `refill` zamiast wyczerpania backlogu: −1 wywołanie planisty (`strong`) na
  każdy cykl przeglądu;
- odchudzone prompty PO/weryfikatora zamiast jednego przeciążonego;
- `po_reviewer` na `strong` zamiast `max`;
- reuse raportu historyjek przy wyzwalaczu `brief`;
- preflight zastępuje ręczną interwencję (dziś: przerwany przebieg).

**Koszty:**
- weryfikator historyjek dochodzi w kadencji (~1 na 16 zadań przy domyślnych
  `FORGE_BATCH_SIZE=8` i `FORGE_STEERING_BATCHES=2`) i skacze z
  `efficient` na `strong` — to jest świadoma inwestycja w jedyną ewidencję, jaką
  PO ma o produkcie;
- pole `story` i notatnik PO: pojedyncze linie w kapsułach.

Netto spodziewamy się zbliżonego rachunku przy istotnie lepszej jakości decyzji
produktowych. Do zmierzenia po wdrożeniu przez `$/zadanie` z
[forge/report.py](../forge/report.py).

## 13. Kolejność wdrożenia

Kolejność jest wymuszona zależnościami, nie preferencją:

1. **S1** (preflight) — niezależny, natychmiastowa ulga w codziennym użyciu.
2. **S3** (`forge/backlog.py`) — parser i twarde invarianty są wejściem dla
   S4a, S5, S7 i S8.
3. **S2 + S9** — PO jako rola, poziomy modeli, pętla korekty parsera.
4. **S6** — notatnik (wymaga istniejącej roli PO).
5. **S10** — migracja (wymaga PO i parsera).
6. **S4** — pole `story` (wymaga historyjek z ID w backlogu).
7. **S4a** — automat statusów (wymaga `story` w zadaniach i `set_status`
   w parserze). **Musi wyprzedzić S5**: bez niego weryfikator dostaje pustą
   listę i cała kadencja jest bezwartościowa.
8. **S5** — weryfikator historyjek (wymaga S4a i pól świeżości w stanie).
9. **S7** — wyzwalacze (wymaga parsera i weryfikatora).
10. **S8** — recenzent PO (domyka miękką bramkę na gotowym kształcie).

Po każdym kroku pełna suita: `python3 -m pytest -q`.
Aktualizacja [PIPELINE.md](PIPELINE.md) należy do kroków 3, 5 i 9.
