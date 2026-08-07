# Przyspieszenie developmentu — 7 propozycji

Cel: **więcej kodu na jednostkę czasu, bez spadku jakości i bez wzrostu kosztu
na linię kodu.** Trzeci warunek jest wiążący: propozycja, która przyspiesza
przebieg kosztem $/linię, jest tu odrzucona, nawet jeśli skraca zegar.

Źródła danych: `docs/ANALIZA-2026-07-25.md` (okno 1 h, 76 wywołań),
`docs/ANALIZA-2026-07-26.md` (przebieg 6 h 20 m, 291 wywołań, 27 zadań) oraz
bieżący kod `forge/`.

---

## 0. Diagnoza wyjściowa

Trzy fakty wyznaczają całą resztę dokumentu.

**Pętla jest ściśle sekwencyjna.** `one_iteration()` obsługuje jedno zadanie
(`state.task_queue.pop(0)`, [orchestrate.py:865](../forge/orchestrate.py#L865)),
jedną rolę na turę, jedno drzewo robocze. `State` jest z konstrukcji
jednozadaniowy: `current_task`, `task_phase`, `tdd_round` to pojedyncze pola
([state.py:32-51](../forge/state.py#L32-L51)).

**Na jednym torze nie ma już czego wyciskać z narzutów.** W oknie 25.07 agenci
pracowali 3290 s z 3600 s. Narzut orkiestratora to reszta — kilka procent.
Optymalizacje typu „szybsze hashowanie drzewa" nie zmienią przepustowości.

**Za to poza pracą agentów leży dużo czasu.** W przebiegu 26.07 **33,4%
(2 h 07 m) to martwy backoff limitu Claude'a**, podczas gdy codex był w pełni
dostępny. Fallbacku providera w `forge/` nie ma (sprawdzone grepem).

Wniosek: dźwignie są trzy — **dołożyć tory**, **usunąć martwy czas**, **usunąć
rundy, które nie produkują kodu**. Wszystko inne jest szumem.

---

## 1. Warunek wstępny: metryka $/linia i widoczny odsiew planisty

Bez tego żadna propozycja z listy nie jest weryfikowalna względem trzeciego
warunku zadania.

**1a. `$/linia` w `forge/report.py`.** Raport sumuje dziś tokeny per
(agent, faza) i nie zna ani linii, ani dolarów
([report.py:72](../forge/report.py#L72)). Wszystkie dane już są:

- linie: `git log --numstat` między tagiem `forge/<task-id>-start`
  ([orchestrate.py:867](../forge/orchestrate.py#L867)) a commitem zadania;
- tokeny: `.forge/usage.jsonl` z polem `phase`, które łączy jedno z drugim;
- stawki: tabela cen per (agent, model) — jedyna nowa dana w całej zmianie.

Docelowo trzy liczby na przebieg: `$/zadanie`, `$/linię netto`, `linie/h`.
Bez nich „nie pogorszyliśmy $/linię" jest opinią, nie pomiarem.

**1b. Odsiew planisty musi być widoczny.** `phase_plan_batch` przyjmuje zadanie
tylko wtedy, gdy planista faktycznie zapisał jego plik:

```python
if task["file"] and Path(project, task["file"]).is_file():
    tasks.append(task)
```
([orchestrate.py:184-185](../forge/orchestrate.py#L184-L185))

Zadanie zadeklarowane w JSON-ie, ale bez pliku na dysku, znika **bez wpisu w
logu i bez wpisu w dzienniku** — inaczej niż zadanie o błędnym identyfikatorze,
które ma obie ścieżki obsłużone ([orchestrate.py:179-182](../forge/orchestrate.py#L179-L182)).
Dziś to drobiazg. Przy większym wsadzie (P6) to jedyny sygnał, że planista
przestał domykać wsad — i musi być policzalny, zanim ruszymy pokrętło.

Koszt obu punktów: kilkadziesiąt linii, zero wywołań LLM.

---

## P1. Równoległe tory w git worktree

**Jedyna dźwignia, która mnoży przepustowość, a nie odejmuje od narzutu.**

### Co

Scheduler bierze z wsadu K zadań gotowych do pracy, uruchamia każde w osobnym
`git worktree` na gałęzi `forge/lane-N`, a integrator scala tory do gałęzi
głównej **szeregowo**, uruchamiając przy każdym scaleniu istniejącą bramkę
`build_then_test_result` ([orchestrate.py:1131](../forge/orchestrate.py#L1131)).
Bramka nie znika — przenosi się z „przed commitem w jednym drzewie" na „przed
scaleniem toru".

### Jak wybrać zadania do torów

Tu trzeba skorygować oczywisty pierwszy pomysł. Pole `file` w zadaniu planisty
to **ścieżka opisu zadania** (`.forge/tasks/task-NNN.md`), a nie plik źródłowy,
który zadanie dotknie — widać to w kontrakcie JSON w
[planner.md](../forge/prompts/templates/planner.md) i w użyciu `{{TASK_FILE}}`
przez [reviewer.md](../forge/prompts/templates/reviewer.md). Rozłączności
zakresu **nie da się dziś odczytać ze stanu**. Dostępne są dwie drogi:

1. **Poprawnościowa, dostępna od razu:** `depends_on` jest już wymagane od
   planisty i budowane w
   [`build_task_from_plan`](../forge/orchestrate.py#L137-L147), ale używane
   wyłącznie do propagacji porażki
   ([`_dependent_task_ids`](../forge/orchestrate.py#L843)). Tor dostaje zadanie
   dopiero, gdy wszystkie jego zależności są scalone. To gwarantuje kolejność,
   nie brak kolizji.
2. **Optymalizacyjna, wymaga jednego pola:** poproś planistę w kontrakcie JSON
   o `touches: ["ścieżki/źródeł"]` — deklarację, nie kontrakt. Scheduler
   wstępnie odsiewa tory o przecinających się zbiorach. Deklaracja bywa
   nietrafna, więc konflikt i tak trzeba obsłużyć przy scalaniu: tor, który się
   nie scala czysto, wraca do swojego testera z handoffem o konflikcie zamiast
   być porzucony.

Zacznij od (1) + obsługi konfliktu przy scaleniu; (2) dokładaj, gdy pomiar
pokaże, ile torów odpada na kolizjach.

### Co trzeba ruszyć

Największa praca z całej siódemki. `_TASK_STATE_FIELDS`
([orchestrate.py:32-39](../forge/orchestrate.py#L32-L39)) trzeba wyciągnąć do
osobnego `TaskState`, a `State` dostaje `lanes: list[TaskState]`. Reszta jest
łatwiejsza, niż wygląda: `run_task` operuje na `project` jako ścieżce, więc w to
miejsce podstawia się katalog worktree. Dziennik (`ledger`) i notatniki
zostają w repo głównym — mistrz ma nadal widzieć jeden proces, nie K procesów.

### Bilans

| pozycja | ocena |
|---|---|
| przepustowość | **×1,8–2,2** przy K=3 i ~30% torów traconych na kolizjach |
| $/linię | neutralne — te same wywołania i tokeny na zadanie, krótszy zegar |
| jakość | bez zmian — każde zadanie zachowuje pełny łańcuch tester ↔ koder → świeży reviewer |
| ryzyko | rework z konfliktów; tnie je K=2–3 i twarde respektowanie `depends_on` |

---

## P2. Fallback providera przy limicie

### Co

`_run_with_backoff` ([agents.py:333](../forge/agents.py#L333)) przy limicie
tylko śpi, geometrycznie. W przebiegu 26.07 jeden sen trwał 64 minuty, łącznie
2 h 07 m — przy w pełni dostępnym codeksie. Po N (proponuję 3) nieudanych
podejściach z powodu limitu rola ma przejść na zapasowego agenta:
`FORGE_<ROLA>_FALLBACK_AGENT`.

### Dlaczego to jest tanie do zrobienia dobrze

Abstrakcja, której to wymaga, już istnieje. `MODEL_LEVEL_ROUTING`
([config.py:49-89](../forge/config.py#L49-L89)) mapuje **poziom**
(`economy`…`max`) na `(model, effort)` osobno dla każdego providera. Fallback
nie zgaduje więc modelu — bierze ten sam poziom u innego dostawcy i z definicji
zachowuje zamierzoną wagę roli. To różnica między „awaryjnym zejściem na
słabszy model" a „tym samym poziomem, innym narzędziem".

Dla ról świeżych i bezsesyjnych (review, verifier, mistrz, planista) zmiana
narzędzia w połowie zadania nic nie kosztuje — review jest z definicji świeży
([orchestrate.py:1060-1070](../forge/orchestrate.py#L1058-L1070)). Dla
testera/kodera na codeksie traci się sesję i 93% trafień w cache, więc tam
fallback wolno włączać **wyłącznie na granicy rundy**.

Fakt przełączenia musi trafić do `usage.jsonl` i do dziennika — inaczej nie da
się zmierzyć, czy werdykty fallbacku różnią się jakością.

### Bilans

| pozycja | ocena |
|---|---|
| przepustowość | do **+33% zegara** w przebiegu z limitem, 0% w przebiegu bez limitu |
| $/linię | neutralne lub w dół — poziom ten sam, najczęstszy fallback (opus → codex/opencode) tańszy |
| jakość | mierzalna: odsetek `recenzja→changes` w podziale na providera, dane już w `ledger.md` |

---

## P3. Deterministyczna bramka przed wywołaniem mistrza

### Co

Mistrz jest wołany raz na rundę (`ensure_notes`,
[orchestrate.py:897-907](../forge/orchestrate.py#L897-L907)). W przebiegu 26.07:
**102 wywołania, 12 not.** Wszystkie cztery warunki jego interwencji z
[master-system.md](../forge/prompts/templates/master-system.md) są mechanicznie
sprawdzalne z danych, które Forge **już liczy**:

| warunek | dane w Forge |
|---|---|
| 2× ta sama decyzja roli bez zmian plików | `state.no_change_rounds`, `ledger.tail_for_task` |
| koder ruszył plik testowy | `_turn_changes` w `run_turn` ([orchestrate.py:938](../forge/orchestrate.py#L938)) |
| kolejne `recenzja→changes` bez zmian | `state.round_changed`, dziennik |
| ≥2 zadania na `round_limit` | `ledger.round_limit_tasks()` — liczone osobno już dziś |

Policz je w Pythonie, wołaj LLM tylko przy trafieniu. Sformułowanie noty zostaw
modelowi — cytuje `reason` i powtarzany wpis dziennika, tego regułą nie zrobisz.

### Bilans

~102 → ~18 wywołań. To ~6,5 min zegara, ale ważniejsze jest zdjęcie presji z tej
samej kwoty, która o 06:07 zablokowała review na dwie godziny. Znika też
patologia z analizy: `cache_creation` mistrza (117 k) jest **wyższe** niż
`cache_read` (98 k) — każde wywołanie zapisuje do cache'u unikalny dziennik,
który nigdy nie zostanie odczytany, po stawce 1,25×.

$/linię: **ściśle w dół**. Jakość: bez zmian, reguły zostają identyczne —
zmienia się tylko to, kto sprawdza warunek wyzwolenia.

---

## P4. Jedna czerwona bramka na komplet kryteriów akceptacji

### Co

Najdroższa strukturalnie rzecz w pętli; analiza 26.07 nazwała ją wprost: **17 z
48 decyzji `red` padło w turze potwierdzającej.** Tester pisze wąską bramkę na
jedno kryterium, koder ją zazielenia, tester wraca i dopiero wtedy zauważa
kolejne kryterium. Każde kryterium dostaje własną rundę TDD — stąd rozkład
3–10 rund i `task-435` na dziesięciu.

Mechanizm jest wprost w promptach, nie w kodzie. `tester-confirmation.md` każe
w kroku 2 „sprawdzić, czy pozostały nieprzetestowane kryteria akceptacji", a
`tester-normal.md` żąda „minimalnego czerwonego testu". Razem produkują pętlę:
minimalna bramka → green → odkrycie kolejnego kryterium → nowy cykl `red`.

Zmiana: pierwsza czerwona bramka ma pokrywać **wszystkie** kryteria akceptacji
z pliku zadania albo jawnie wymieniać, które są świadomie odłożone i dlaczego.
Tura potwierdzająca ma prowadzić do rozszerzenia tej samej bramki w bieżącej
rundzie, a nie do otwarcia nowego cyklu.

### Bilans

Runda jest jednocześnie jednostką czasu i kosztu: tester ~200 k in, koder
~307 k in na wywołanie. Zejście ze średnio ~2,6 do ~2,0 rund na zadanie to
**~20–25% mniej wywołań ról — szybciej i taniej na linię jednocześnie**.

**To jedyna propozycja z realnym ryzykiem jakości** i nie wolno jej wdrażać w
ciemno. Metryki kontrolne, wszystkie dostępne z `ledger.md` i `usage.jsonl`:

- rundy/zadanie — musi spaść;
- `recenzja→changes` — nie może wzrosnąć;
- `blocked` i `PORZUCONE` — w przebiegu 26.07 oba zero, muszą zostać zerem.

Wycofanie jest tanie: to zmiana dwóch szablonów promptów, bez migracji stanu.

---

## P5. Prefetch planisty i spekulatywna bramka pakietu

Dwie rzeczy blokują dziś pętlę, choć niczego nie potrzebują od bieżącej tury.

**Planowanie wsadu.** ~520 k tokenów wejścia i kilka minut, w czasie których nie
powstaje ani jedna linia kodu. Uruchom je w tle, gdy `len(task_queue) <= 1` —
ale **tylko wtedy, gdy następna iteracja na pewno nie będzie przeglądem
kierunku**. `_steering_trigger` ([orchestrate.py:319](../forge/orchestrate.py#L319))
jest deterministyczny i policzalny z wyprzedzeniem, więc prefetch nigdy nie
zmarnuje wywołania na kolejkę, którą `replan` zaraz wyrzuci
([orchestrate.py:476-482](../forge/orchestrate.py#L476-L482)).

**Bramka pełnego pakietu przed commitem.** ~30 s na zadanie, dziś szeregowo po
recenzji. Uruchom ją równolegle z turą reviewera i zwaliduj po fakcie przez
`_tree_fingerprint()` ([orchestrate.py:555](../forge/orchestrate.py#L555)):
jeśli drzewo się nie ruszyło, wynik jest ważny; jeśli się ruszyło — a ten
przypadek jest już obsłużony
([orchestrate.py:1090-1103](../forge/orchestrate.py#L1090-L1103)) — bramka leci
ponownie.

$/linię: bez zmian, to te same wywołania nałożone w czasie. Zegar: ~8–12%.

---

## P6. Większy wsad planisty *(rozważone — warunkowo tak)*

### Argument za jest mocniejszy, niż wygląda z tokenów

Udział planisty w tokenach jest umiarkowany: 3,12 M in na 27 zadań to ~116 k
in/zadanie, czyli ~7% wejścia. Ale **w dolarach planista to połowa rachunku**:
w oknie 25.07 `plan` kosztował $3,98 z $7,88 wydanych na Claude'a — przy trzech
wywołaniach. Powód jest w komentarzu przy samym pokrętle: koszt planisty jest
w 64% **stały** (czyta repo od zera, niezależnie od rozmiaru wsadu), a rola
chodzi na poziomie `strong` ([config.py:160-171](../forge/config.py#L160-L171),
[config.py:30](../forge/config.py#L30)).

Wsad 6 → 10 amortyzuje tę stałą na 10 zadaniach zamiast 6: **~40% mniej
wywołań planisty na zadanie**, czyli realne zejście $/linię, bez dotykania
modelu, effortu ani jakości planowania.

### Trzy warunki, bez których to jest strata

1. **Kadencja przeglądu kierunku liczy WSADY, nie zadania**
   ([config.py:174-179](../forge/config.py#L174-L179)). `steering_batches=2` przy
   wsadzie 6 to przegląd co 12 zadań; przy wsadzie 10 — co 20. Komentarz w
   kodzie ostrzega przed tym wprost: „podniesienie wsadu bez zejścia tutaj
   kupiłoby oszczędność opóźnieniem korekty kursu". Iloczyn
   `batch_size × steering_batches` ma zostać ~12.
2. **Przy `replan` ginie CAŁA kolejka**
   ([orchestrate.py:476-482](../forge/orchestrate.py#L476-L482)). Większy wsad to
   większa strata na jeden przegląd zmieniający kierunek. Przy warunku (1)
   przegląd trafia w kolejkę prawie pustą, więc ryzyko jest ograniczone — ale
   to warunek (1) je ogranicza, nie sam wsad.
3. **Odsiew planisty musi być widoczny** (punkt 1b). Rosnący wsad to rosnąca
   szansa, że planista nie domknie ostatnich zadań; dziś znikają bez śladu.

### Dlaczego to idzie w parze z P1, a nie przeciw niemu

Górna granica wsadu jest jakościowa: dalsze zadania planuje się na coraz
starszym stanie repo. Równoległe tory **nie zmniejszają** tej starości —
mierzy się ją liczbą zadań zamkniętych od chwili planowania, nie zegarem. Ale
P1 potrzebuje kolejki dość głębokiej, by zapełnić K torów po odsianiu
zależności, a przy wsadzie 6 i K=3 często zapełni dwa. Wsad 8–10 jest więc
**wymuszony przez P1**, nie niezależnym pomysłem.

**Rekomendacja:** 6 → 8 razem z P1, `steering_batches` skorygowane do 1 albo 2
tak, by iloczyn został ~12; 10 dopiero po pomiarze `round_limit` i odsiewu.
Nie ruszać wsadu przed P1 — samo pokrętło daje wtedy oszczędność dolara przy
zerowym zysku czasu i realnym koszcie starzenia planu.

---

## P7. Większe zadania *(rozważone — nie, poza jednym warunkiem)*

### Co obiecuje

Stały koszt na zadanie to udział planisty (~116 k in), review (~170 k in,
1,3 wywołania na zadanie po ~132 k), bramka pakietu (~30 s) i obsługa cyklu
(tag, notatniki, housekeeping). Razem ~290 k z ~1,57 M in na zadanie, czyli
**~18% wejścia**. Dwukrotnie większe zadania zdejmują w najlepszym razie połowę
tego — **sufit zysku to ~9% na linię**.

### Dlaczego to jest zły zakład

**Rundy nie skalują się liniowo z rozmiarem.** Ustalenie z P4 jest wprost o tym:
każde dodatkowe kryterium akceptacji dziś kupuje własną rundę TDD. Podwojenie
liczby kryteriów daje **więcej** niż podwojenie rund, dopóki P4 nie wejdzie.

**Przekroczenie limitu rund kasuje całą pracę.** `round_limit` idzie do
`_fail_task`, a ten robi `git reset --hard state.task_start_tag`
([orchestrate.py:814](../forge/orchestrate.py#L814)) — wszystkie tokeny wydane
na to zadanie są stracone. Oczekiwana strata to `p(porażki) × koszt zadania`,
a większe zadanie podnosi **oba** czynniki naraz. W przebiegu 26.07 było 0
porzuceń przy `max_tdd_rounds=10`; to jest dokładnie ten zapas, który większe
zadania wydają. Zysk jest ograniczony do ~9%, strata sięga 100% zadania —
zakład jest asymetryczny w złą stronę, wbrew regule z `CLAUDE.md`
o optymalizacji oczekiwanego kosztu całego procesu.

**Review na dużym diffie jest gorsze, nie tylko droższe.** Reviewer czyta
`git diff <start-tag>` w całości ([reviewer.md](../forge/prompts/templates/reviewer.md)).
Wejście rośnie z rozmiarem diffu, a trafność werdyktu na dużym diffie spada —
to naruszenie warunku „bez spadku jakości", nie tylko kosztu.

**Kolizja z P1.** Mniej, większych zadań to mniej niezależnych jednostek do
zapełnienia torów — czyli odbieranie paliwa największej dźwigni.

### Kiedy to wraca na stół

Dokładnie jeden scenariusz: **po** wdrożeniu i zmierzeniu P4. Jeśli P4 zamieni
„runda na kryterium" w „runda na zadanie", koszt rundy przestaje rosnąć z
liczbą kryteriów i większe zadanie staje się policzalne. Wtedy, w tej
kolejności: podnieś `max_tdd_rounds` proporcjonalnie **przed** rozmiarem
zadania, przesuń o jeden stopień tylko `difficulty=simple` → `standard`
w opisie w [planner.md](../forge/prompts/templates/planner.md) („maksymalnie
N **małych** zadań"), i zmierz `round_limit` oraz `recenzja→changes`. Pierwsze
porzucenie na `round_limit` po tej zmianie oznacza wycofanie.

---

## Kolejność wdrożenia

| # | propozycja | zysk zegara | $/linię | ryzyko jakości |
|---|---|---|---|---|
| 1 | metryka `$/linia` + widoczny odsiew | — | — | brak (warunek wstępny) |
| P3 | bramka przed mistrzem | ~2% + zwolniona kwota | **w dół** | brak |
| P2 | fallback providera | do +33% | neutralny/w dół | niskie, mierzalne |
| P5 | prefetch planisty + spekulatywna bramka | ~8–12% | neutralny | brak |
| P1 | równoległe tory (K=3) | **×1,8–2,2** | neutralny | średnie, tnie je K |
| P6 | wsad 6 → 8 (tylko z P1) | pośrednio, przez P1 | **w dół** | niskie przy warunkach 1–3 |
| P4 | jedna bramka na komplet kryteriów | ~20% mniej rund | **w dół** | realne — tylko z pomiarem |
| P7 | większe zadania | — | pozornie w dół | **odrzucone** do czasu P4 |

Uzasadnienie kolejności: P3 i P2 są tanie i natychmiast zwalniają kwotę oraz
zegar. P5 nie zmienia żadnego kontraktu. P1 to właściwy skok przepustowości i
zjada najwięcej pracy, więc idzie po tanich zyskach. P6 jest domknięciem P1, nie
osobnym pokrętłem. P4 na końcu, bo jako jedyna wymaga porównania dwóch
przebiegów — a przy włączonym P1 porównanie jest szybsze o połowę.

---

## Czego nie ruszać

Lista z `docs/ANALIZA-2026-07-26.md` pozostaje aktualna i warto ją tu powtórzyć,
bo trzy z powyższych propozycji kuszą, by w te miejsca wejść:

- **Prompty ról** — mieszczą się w kilkuset tokenach, nie ma tam czego ciąć.
  P4 zmienia w nich *treść kontraktu*, nie *długość*.
- **`ledger.compact_tail()`** ([ledger.py:91](../forge/ledger.py#L91)) — cięcie
  chroniące `pliki=…` jest dobrze pomyślane i jest wejściem bramki z P3.
- **Sesje codeksa** — 93–94% trafień w cache. To jest powód, dla którego P2
  wolno fallbackować testera/kodera wyłącznie na granicy rundy.
- **Bramka pełnego pakietu przed commitem** — ~13 min na 6 h 20 m. P5 ją
  przesuwa w czasie, ale jej nie usuwa i nie osłabia.
- **Świeży reviewer na każdym zadaniu** — łapie rzeczy, których pętla nie widzi.
  Żadna propozycja z tej listy nie skraca łańcucha ról.
