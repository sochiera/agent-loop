# Przyspieszenie developmentu — 7 propozycji

Cel: **więcej kodu na jednostkę czasu, bez spadku jakości i bez wzrostu kosztu
na zadanie.** Trzeci warunek jest wiążący: propozycja, która przyspiesza
przebieg kosztem $/zadanie, jest tu odrzucona, nawet jeśli skraca zegar.

> **Stan wdrożenia.** Plan wykonawczy pięciu wybranych strumieni jest w
> [PLAN-WDROZENIA.md](PLAN-WDROZENIA.md); ten dokument został po nim poprawiony
> tam, gdzie plan znalazł w nim błędy. Jednostką kosztu jest **zadanie**, nie
> linia kodu — patrz §1a.

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

## 1. Warunek wstępny: metryka $/zadanie i widoczny odsiew planisty

Bez tego żadna propozycja z listy nie jest weryfikowalna względem trzeciego
warunku zadania.

**1a. `$/zadanie` w `forge/report.py`.** *(wdrożone)* Jednostką jest **zadanie**,
nie linia kodu — `git log --numstat` wypadł z planu w całości. Linia kodu nie
jest tu jednostką pracy: koder pisze i kasuje w tej samej rundzie, a refaktor
o ujemnym bilansie linii bywa najcenniejszą turą przebiegu.

Trzy liczby na przebieg: **`$/przebieg`**, **`$/zadanie`**, **`$/rundę`**.
`$/rundę` jest ważniejsze, niż wygląda: runda, a nie zadanie, jest właściwą
jednostką kosztu w tej pętli (tester ~200 k in + koder ~307 k in na rundę), a
P4 działa dokładnie na liczbie rund. Bez tej liczby nie da się odróżnić
„zadania staniały" od „zadania się skurczyły".

Mianowniki biorą się z dziennika: wpisy `UKOŃCZONE po N rundach`
([`ledger.completed_tasks`](../forge/ledger.py)). **Zadania `PORZUCONE` liczą
się do kosztu, nie do mianownika** — doliczenie ich maskowałoby dokładnie tę
stratę, którą chcemy widzieć.

**Pułapka semantyki tokenów** — to jedyne miejsce, gdzie łatwo o błąd wart
dziesiątek procent rachunku. Providerzy liczą wejście niezgodnie:

| provider | `input_tokens` | cache |
|---|---|---|
| Claude | **wyłącznie tokeny nieocache'owane** | `cache_creation_input_tokens` (zapis, 1,25×) i `cache_read_input_tokens` (odczyt, 0,1×) osobno |
| Codex | **całość wejścia**, z cache włącznie | `cached_input_tokens` jest podzbiorem `input_tokens` |

Dlatego wycena idzie przez znormalizowaną czwórkę
`(uncached_in, cache_write, cache_read, out)`, a nie przez dawną trójkę.
Pominięcie `cache_creation` dla Claude'a gubi najdroższą pozycję; zsumowanie
`input` i `cached` dla Codeksa liczy te same tokeny dwa razy.

Stawki — `forge/pricing.py`, USD za milion tokenów:

```python
CLAUDE_RATES = {
    #            in     cache_write(1,25×)  cache_read(0,1×)   out
    "opus":    (5.00,   6.25,               0.50,              25.00),
    "sonnet":  (3.00,   3.75,               0.30,              15.00),
    "haiku":   (1.00,   1.25,               0.10,               5.00),
}
```

Promocji Sonneta ($2/$10 do 2026-08-31) świadomie nie wpisujemy: wygaśnie w
połowie okresu pomiarowego i porównanie dwóch przebiegów przestałoby być
uczciwe. Codex trzyma mnożniki względem Sola (`terra` 0,40, `luna` 0,04) i
kotwicę w `FORGE_PRICE_SOL_IN`/`FORGE_PRICE_SOL_OUT`; pusta kotwica daje w
raporcie jawne `—`, nigdy zera. `llamacpp/*` → `0.00` z adnotacją „prąd, nie
API". Nieznany `(agent, model)` → `—` plus jedno ostrzeżenie na przebieg.

**Zastrzeżenie:** `$/zadanie` jest porównywalne tylko dopóki rozmiar zadania
się nie zmienia. **P7 tę metrykę z definicji psuje** — przy ewentualnym P7
punktem odniesienia musi być `$/przebieg` przy tej samej zawartości briefu.
Dla P3–P6 problemu nie ma, bo żadna z nich nie rusza rozmiaru zadania.

**1b. Odsiew planisty musi być widoczny.** *(wdrożone)* `phase_plan_batch` przyjmuje zadanie
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

Wdrożone na trzech poziomach widoczności, bo mają trzech różnych odbiorców:
`log()` per odsiane zadanie (człowiek przy konsoli, z rozróżnieniem „brak pola
`file`" od „plik nie istnieje na dysku"), jeden wpis **zbiorczy** do dziennika
`plan: zadeklarowano N, przyjęto M (odsiew: …)` (mistrz — to jego jedyne
wejście, więc trzy osobne linie rozmyłyby jego słownik wzorców) i linia w
raporcie (pomiar). Wpis zbiorczy powstaje **tylko przy niezerowym odsiewie**;
przy pełnym wsadzie istniejąca linia `plan: utworzono N zadań` wystarcza.

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

## P3. Deterministyczna bramka przed wywołaniem mistrza *(ODRZUCONE)*

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

### Bilans — poprawiony

Wcześniejsza wersja tego akapitu przypisywała P3 „zdjęcie presji z kwoty, która
o 06:07 zablokowała review na dwie godziny". **To było przesadzone.** Z tabeli
tokenów przebiegu 26.07:

| rola | wywołań | wejście | % tokenów Claude'a | ~$ | % $ |
|---|---:|---:|---:|---:|---:|
| review | 35 | 4,62 M | 58% | ~7,0 | ~55% |
| plan | 6 | 3,12 M | 39% | ~5,0 | ~40% |
| **mistrz** | **93** | **0,22 M** | **2,7%** | **~0,6** | **~5%** |

Mistrz to 69% wywołań, ale 2,7% tokenów. Limity subskrypcji są ważone
tokenami, nie liczbą żądań — wycięcie 84 z 93 wywołań zwalnia ~2,5% puli i
**nie odblokowuje** dwugodzinnego backoffu. Ten backoff wygenerowały review i
planista; adresuje go **P2**, nie P3. Rozbieżność z przebiegiem 25.07 (mistrz
$2,15 z $7,88) dotyczy starej konfiguracji: mistrz chodził wtedy na mocniejszym
modelu i z pełnym harnessem agentowym, a dziś ma `efficient` i tryb cienki.

Zostają trzy realne zyski, w kolejności ważności:

1. **Patologia cache'u.** `cache_creation` mistrza (117 k) jest **wyższe** niż
   `cache_read` (98 k) — każde wywołanie zapisuje do cache'u unikalny dziennik,
   który nigdy nie zostanie odczytany, po stawce 1,25×. To nie jest
   nieefektywność, tylko płacenie premii za cache, którego z definicji nie da
   się użyć.
2. ~6,5 min zegara (~102 → ~18 wywołań).
3. ~5% rachunku Claude'a.

$/zadanie: **ściśle w dół**. Jakość: bez zmian, reguły zostają identyczne —
zmienia się tylko to, kto sprawdza warunek wyzwolenia.

### Werdykt: bramka NIE wchodzi — `FORGE_MASTER_GATE=off` domyślnie

Kod bramki istnieje i jest przetestowany, ale **domyślnie jest wyłączona i nie
rekomendujemy jej włączania.** Powód jest wprost w powyższym bilansie: mistrz
to ~2,7% tokenów i ~5% rachunku, więc górna granica zysku to kilka procent.
Po drugiej stronie stoi pojedyncza pominięta interwencja — pętla, której nikt
nie przerwał, kosztuje rundy po ~500 tys. tokenów wejścia każda, a przy
`max_tdd_rounds` kończy się `git reset --hard` i utratą **całej** pracy nad
zadaniem. Kilka procent oszczędności przeciw ryzyku straty rzędu setek procent
kosztu zadania to ten sam asymetryczny zakład, za który odrzucamy P7.

Ryzyko nie jest hipotetyczne. Bramka musi odwzorowywać **wszystkie** warunki
promptu mistrza 1:1; przegląd wykazał, że pierwsza wersja pominęła piąty
(powtórzony odsiew planisty) i w trybie `on` wyciszałaby mistrza dokładnie tam,
gdzie wymaga tego 1b. Każde przyszłe rozszerzenie promptu ma ten sam problem —
i to jest stały koszt utrzymania, a nie jednorazowa naprawa. Pilnuje go test
`test_every_prompt_rule_has_a_gate_predicate`.

`FORGE_MASTER_GATE=shadow` zostaje dostępne: nigdy nie wycisza mistrza,
kosztuje jedną linię logu na wywołanie i pozwala zmierzyć bramkę, gdyby temat
wrócił. Włączenie rozpoznaje `on`, `1`, `true` i `tak`; wartość nierozpoznana
znaczy `off`, bo pokrętło o niepewnym znaczeniu nie ma prawa wyciszyć nadzoru.

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

### Wdrażany jest wariant UMIARKOWANY

Powyższa wersja maksymalna („wszystkie kryteria") nie wchodzi. Wdrożony wariant
to **2–3 kryteria na bramkę**, plus mapa kryteriów wyliczana w pierwszej turze
`red` (w `reason` i notatniku, nie jako nowe pole JSON-a — nowe pole dotknęłoby
`parse_tester_decision` i schematu, przez co wycofanie przestałoby być rewertem
dwóch szablonów). Niemal ten sam zysk przy istotnie mniejszym ryzyku.

Uzasadnienie szerokości: **reguła „jeden test na cykl" nie obowiązuje w tej
pętli.** Klasyczne TDD trzyma ją z dwóch powodów i żaden tu nie działa —
lokalizacja defektu (koder dostaje pełne wyjście komendy, więc trzy nazwane
asercje lokalizują lepiej niż jedna) i latencja pętli (człowiek chce zielono co
2 minuty, a tu jedna runda to ~200 k tokenów testera + ~307 k kodera).
**Runda jest jednostką kosztu, nie test.**

Warunku, którego nie wolno rozluźnić razem z poszerzeniem bramki: **każdy** test
w bramce ma się kolekcjonować i padać na asercji kontraktu, a nie na błędzie
składni/importu/nazwy. Przy trzech testach jeden może paść z niewłaściwego
powodu i tester może tego nie zauważyć — a to psuje dokładnie tę własność
czerwonej bramki, dla której cała pętla istnieje.

Zmiana działa też **przeciwnie do asymetrii, którą pogłębia P7**: przekroczenie
`max_tdd_rounds` woła `_fail_task`, a ten robi `git reset --hard`, więc mniej
rund to mniejsze `p(porażki)` przy niezmienionym koszcie zadania.

### Bilans

Runda jest jednocześnie jednostką czasu i kosztu: tester ~200 k in, koder
~307 k in na wywołanie. Zejście ze średnio ~2,6 do ~2,0 rund na zadanie to
**~20–25% mniej wywołań ról — szybciej i taniej na zadanie jednocześnie**.

**To jedyna propozycja z realnym ryzykiem jakości** i nie wolno jej wdrażać w
ciemno. Ryzyko jest po stronie **zielonej**, nie czerwonej: LLM napisze trzy
testy równie łatwo co jeden, ale koder musi zaspokoić N asercji w jednej turze
→ większy diff → gorsza lokalizacja przy porażce → więcej
`test_changes_needed` / `tester_input_needed`.

Metryki kontrolne, wszystkie dostępne z `ledger.md` i `usage.jsonl`:

- rundy/zadanie — musi spaść (baseline ~2,63);
- `$/zadanie` — musi spaść;
- `recenzja→changes` — nie może wzrosnąć (baseline 7/35);
- `blocked` i `PORZUCONE` — w przebiegu 26.07 oba zero, muszą zostać zerem;
- **pushback kodera** — odsetek `test_changes_needed` + `tester_input_needed`
  na turę kodera; nie może wzrosnąć (baseline 14/61 ≈ 23%). Tej metryki
  brakowało w pierwszej wersji dokumentu, a jest jedyną, która wprost wykrywa
  „bramka za szeroka". Liczy ją `report.coder_pushback`.

Naruszenie któregokolwiek warunku → wycofanie (`git revert` dwóch szablonów).

Wycofanie jest tanie: to zmiana dwóch szablonów promptów, bez migracji stanu.

---

## P5. Prefetch planisty i spekulatywna bramka pakietu *(ODRZUCONE)*

**Odrzucone decyzją planu wdrożenia.** Podana niżej liczba 8–12% zegara
zawierała prefetch planisty; **sama bramka spekulatywna to ~2–3%**. Za te 2–3%
wprowadza równoległe uruchomienie pełnego pakietu w tym samym drzewie roboczym,
w którym pracuje reviewer — nową klasę ryzyka w miejscu, które dziś jest
szeregowe i przewidywalne. Stosunek zysku do ryzyka jest zły. Prefetch samego
planisty może wrócić osobno, po P1, gdy równoległość drzew i tak będzie
rozwiązana.

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
   wsadzie 6 to przegląd co 12 zadań; przy wsadzie 10 — co 20.
2. **Przy `replan` ginie CAŁA kolejka**
   ([orchestrate.py:476-482](../forge/orchestrate.py#L476-L482)) — i to jest
   miejsce, w którym pierwsza wersja tego dokumentu **się myliła**. Twierdziła,
   że „przy warunku (1) przegląd trafia w kolejkę prawie pustą, więc ryzyko
   jest ograniczone". Przy ówczesnym kodzie było odwrotnie:

   - wyzwalacz przeglądu sprawdzany był, gdy `not state.current_task`, i **nie
     patrzył na kolejkę**;
   - `plan_batches` rośnie w chwili **zaplanowania** wsadu, a planowanie i start
     pierwszego zadania dzieją się w **tej samej iteracji**.

   Przy `steering_batches=2` i wsadzie 6: wsad 1 → 6 zadań → kolejka pusta →
   wsad 2 zaplanowany (`plan_batches=2`, kolejka = 6 świeżych zadań) → pierwsze
   zadanie wykonane → granica zadań → kadencja dojrzała → **przegląd startował z
   5 świeżymi zadaniami w kolejce**, a `replan=true` kasował je razem z całym
   wywołaniem planisty (~520 k tokenów wejścia). Dotyczyło to konfiguracji
   sprzed poprawki niezależnie od P6; **podniesienie wsadu bez tej poprawki
   mnożyłoby stratę**.

   Naprawa: gałąź kadencji w `_steering_trigger` dostaje warunek
   `not state.task_queue`. Gałęzie `brief` i `backlog` zostają natychmiastowe —
   zmiana briefu ma wygrywać z kolejką, a `steering_due` jest ustawiane właśnie
   przy pustym backlogu. Ponieważ wyzwalacz jest sprawdzany **przed** blokiem
   planowania, przegląd trafia dokładnie w moment pustej kolejki i `replan` nie
   ma czego zniszczyć. Efekt uboczny na plus: jednorazowa notatka przeglądu
   ląduje bezpośrednio przed planowaniem, zamiast czekać na dopracowanie reszty
   wsadu.
3. **Odsiew planisty musi być widoczny** (punkt 1b). Rosnący wsad to rosnąca
   szansa, że planista nie domknie ostatnich zadań; dawniej znikały bez śladu.

### Dlaczego to idzie w parze z P1, a nie przeciw niemu

Górna granica wsadu jest jakościowa: dalsze zadania planuje się na coraz
starszym stanie repo. Równoległe tory **nie zmniejszają** tej starości —
mierzy się ją liczbą zadań zamkniętych od chwili planowania, nie zegarem. Ale
P1 potrzebuje kolejki dość głębokiej, by zapełnić K torów po odsianiu
zależności, a przy wsadzie 6 i K=3 często zapełni dwa. Wsad 8–10 jest więc
**wymuszony przez P1**, nie niezależnym pomysłem.

**Rekomendacja (poprawiona):** **wsad 8, `steering_batches=2`, iloczyn 16** —
świadome odejście od dawnej reguły „iloczyn ~12". Reguła ~12 była kalibrowana
**pod błędem opisanym w warunku (2)**, gdzie przegląd trafiający w pełną
kolejkę kosztował cały wsad planisty, więc ciasna kadencja była tanim
ubezpieczeniem. Po poprawce jedynym kosztem luźniejszej kadencji jest opóźniona
korekta kursu, a przegląd zawsze ląduje na granicy wsadów z pustą kolejką —
w najbezpieczniejszym możliwym momencie. Przegląd kierunku chodzi przy tym na
poziomie `max` ([config.py:28-30](../forge/config.py#L28-L30)), więc zejście do
`steering_batches=1` kupowałoby korektę kursu za +50% wywołań najdroższej roli
w systemie, czyli zjadłoby oszczędność, dla której podnosimy wsad. Wyzwalacz
odwrotu: wzrost odsetka przeglądów z `replan=true` albo zauważalny dryf
kierunku → `FORGE_STEERING_BATCHES=1`.

Bilans wsadu 6 → 8: wywołań planisty na zadanie 0,167 → 0,125, czyli ~25%
kosztu planisty ≈ ~10% rachunku Claude'a. Zysku zegara nie ma — to zmiana
czysto kosztowa. 10 dopiero po pomiarze odsiewu (1b) i `round_limit`.

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

| # | propozycja | zysk zegara | $/zadanie | ryzyko jakości |
|---|---|---|---|---|
| 1 | metryka `$/zadanie` + widoczny odsiew | — | — | brak (warunek wstępny) |
| P3 | bramka przed mistrzem | ~2% | w dół (~5% rachunku) | **ODRZUCONE** — zysk kilka %, ryzyko utraty zadania |
| P2 | fallback providera | do +33% | neutralny/w dół | niskie, mierzalne |
| P5 | prefetch planisty + spekulatywna bramka | ~2–3% (nie 8–12%) | neutralny | **odrzucone** |
| P1 | równoległe tory (K=3) | **×1,8–2,2** | neutralny | średnie, tnie je K |
| P6 | wsad 6 → 8 | — (czysto kosztowa) | **w dół** (~10% rachunku) | niskie przy warunkach 1–3 |
| P4 | szersza pierwsza bramka (wariant umiarkowany) | ~20% mniej rund | **w dół** | realne — tylko z pomiarem |
| P7 | większe zadania | — | pozornie w dół | **odrzucone** do czasu P4 |

Wsad 8 (P6) nie czeka już na P1: po naprawie kolejności kadencji jest zmianą
samodzielną i bezpieczną. P1 nadal go potrzebuje, żeby zapełnić tory.

Uzasadnienie kolejności: P2 jest tanie i natychmiast zwalnia zegar. P1 to
właściwy skok przepustowości i zjada najwięcej pracy, więc idzie po tanich
zyskach. P6 wchodzi samodzielnie po naprawie kolejności kadencji i domyka P1.
P4 na końcu, bo jako jedyna wymaga porównania dwóch przebiegów — a przy
włączonym P1 porównanie jest szybsze o połowę. P3 i P5 wypadły z kolejki:
obie kupowały kilka procent za nową klasę ryzyka.

---

## Czego nie ruszać

Lista z `docs/ANALIZA-2026-07-26.md` pozostaje aktualna i warto ją tu powtórzyć,
bo trzy z powyższych propozycji kuszą, by w te miejsca wejść:

- **Prompty ról** — mieszczą się w kilkuset tokenach, nie ma tam czego ciąć.
  P4 zmienia w nich *treść kontraktu*, nie *długość*.
- **`ledger.compact_tail()`** ([ledger.py](../forge/ledger.py)) — cięcie
  chroniące `pliki=…` (tnie POWÓD, nie listę plików) jest dobrze pomyślane i
  jest wejściem bramki z P3. Po wdrożeniu P3 to już nie tylko dobra praktyka,
  ale **własność, od której zależy poprawność bramki**: trzy z jej czterech
  warunków czytają `pliki=`, więc utrata tej listy przy cięciu wyciszałaby
  mistrza tam, gdzie miał zareagować. Pilnuje jej test regresyjny w
  `tests/test_master_gate.py`.
- **Sesje codeksa** — 93–94% trafień w cache. To jest powód, dla którego P2
  wolno fallbackować testera/kodera wyłącznie na granicy rundy.
- **Bramka pełnego pakietu przed commitem** — ~13 min na 6 h 20 m. P5 ją
  przesuwa w czasie, ale jej nie usuwa i nie osłabia.
- **Świeży reviewer na każdym zadaniu** — łapie rzeczy, których pętla nie widzi.
  Żadna propozycja z tej listy nie skraca łańcucha ról.
