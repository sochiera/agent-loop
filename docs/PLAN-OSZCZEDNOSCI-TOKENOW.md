# Plan wdrożenia: cztery wycieki tokenów (3a, 3b, 3d, 3e)

**Status:** propozycja do wdrożenia
**Data:** 2026-08-09
**Podstawa pomiarowa:** przebieg 10:16–14:30, 8 zadań, `.forge/usage.jsonl`
(88 wywołań), `.forge/ledger.md`, `STATE.json` (`plan_batches=68`,
`steered_at_batch=66`).
**Zakres:** punkty a, b, d, e z analizy. Punkt c (abort diff-bootstrapu) ma
własny dokument — [PLAN-NAPRAWY-JSON.md](PLAN-NAPRAWY-JSON.md).

> **Uwaga o stanie drzewa.** Plan naprawy JSON jest już częściowo wdrożony w
> katalogu roboczym (`json-rules.md`, slot `{{JSON_RULES}}`, domyślka w
> `render()`, zmiany w `agents.py` / `orchestrate.py` / `task_pipeline.py`).
> Poniższe zmiany są z nim rozłączne — nie dotykają tych samych funkcji.

---

## 0. Dwie korekty względem analizy w rozmowie

Weryfikacja w kodzie wykazała, że dwa punkty są **większe**, niż je opisałam.
Obie korekty zmieniają zakres pracy, więc idą na początek, a nie w przypisie.

**Korekta do (a).** Napisałam, że „strumień opencode `--format json` niesie
liczniki, a `_extract_opencode_text` je odrzuca”. To prawda **tylko dla roli
mistrza**. Zwykły szablon opencode ([adapters.py:96](../forge/adapters.py#L96))
brzmi:

```
opencode run {prompt} -m {model} --variant {effort} --auto --dir {project}
```

— **bez `--format json`**. Recenzent, planista i diff-bootstrap dostają dziś
zwykły tekst na stdout i nie ma tam żadnych liczników do wydobycia. Naprawa
wymaga więc **włączenia strumienia JSON dla wszystkich ról opencode**, a to
zmienia format stdout na ścieżce, którą płynie każda odpowiedź tych ról. To
jest realne ryzyko regresji, nie „najtańsza możliwa naprawa metryki”, jak
napisałam.

**Korekta do (b).** Sugerowałam, że wystarczy przestawić routing. Sprawdziłam
`Config.role` ([config.py:320-335](../forge/config.py#L320)): `MODEL_LEVEL_ROUTING`
**wygrywa** z `configured_model` dla wszystkich ról **poza** rodziną planisty.
Czyli `FORGE_MASTER_MODEL=openai/gpt-5.6-luna` jest dziś po cichu ignorowane —
punkt (b) nie da się załatwić samą zmienną środowiskową, wymaga zmiany w kodzie.

---

## a) Pomiar zużycia dla opencode

### a.1 Dowód, że dane istnieją

Nie jest to założenie — sprawdzone w lokalnej bazie opencode
(`~/.local/share/opencode/opencode.db`, tabela `message`, ostatnia wiadomość
asystenta):

```json
{"role":"assistant","modelID":"glm-5.2","providerID":"zai-coding-plan",
 "tokens":{"total":166587,"input":724,"output":898,"reasoning":613,
           "cache":{"write":0,"read":164352}},
 "cost":0}
```

Binarka emituje zdarzenia `message.updated`, `message.part.updated` i część
typu `step-finish` (potwierdzone `strings` na `~/.opencode/bin/opencode`).
Kształt `tokens` pokrywa **wszystkie cztery wymiary** cennika z
[pricing.py:69](../forge/pricing.py#L69) — łącznie z `cache.write`, którego
`$/zadanie` potrzebuje, a którego trójka „wejście/cache/wyjście” nie niesie.

Pole `cost: 0` jest faktem abonamentu (Coding Plan), nie brakiem danych —
**nie wolno go używać** jako wyceny, bo `pricing.py` świadomie liczy „jak po
API” ([komentarz przy `API_LIST_RATES`](../forge/pricing.py#L52)). Bierzemy
wyłącznie `tokens`.

### a.2 Zmiany

**Krok 1 — włączyć strumień zdarzeń.** `forge/adapters.py`, `AGENT_TEMPLATES["opencode"]`:
dopisać `--format json`. Szablon cienki już go ma, więc oba tory się zrównują.

**Krok 2 — wyciąg tekstu na ścieżce nie-cienkiej (obowiązkowy).**
`run_generic` ([agents.py:867](../forge/agents.py#L867)) woła
`_extract_opencode_text` **tylko** dla `thin and spec.name == "opencode"`;
w pozostałych wypadkach zwraca surowy `stream`. Po kroku 1 ten surowy strumień
przestaje być tekstem odpowiedzi. Warunek trzeba rozszerzyć do
`spec.name == "opencode"` niezależnie od `thin`.

Bez tego kroku **każda** rola opencode zwróci JSON zdarzeń zamiast werdyktu.
`_extract_opencode_text` ma już bezpieczny fallback (`or stream`), więc krok 1
bez kroku 2 nie wywali się głośno — po cichu poda śmieci do parsera. Dlatego
kroki 1 i 2 muszą wejść **jednym commitem**.

**Krok 3 — nowy `extract_opencode_usage(stream) -> dict`** w `agents.py`, obok
istniejącego `extract_codex_usage` ([agents.py:220](../forge/agents.py#L220)),
tą samą konwencją:

* iterować po liniach, `json.loads`, pomijać niesparowane;
* brać zdarzenia niosące wiadomość asystenta z polem `tokens`
  (`message.updated`, `info.role == "assistant"`);
* **trzymać ostatnią wersję per `id` wiadomości** — strumień emituje tę samą
  wiadomość wielokrotnie w miarę jak rośnie, dokładnie jak w docstringu
  `_extract_opencode_text`. Sumowanie wszystkich wystąpień zawyżyłoby
  licznik kilkudziesięciokrotnie. **To jest główna pułapka tego zadania.**
* zsumować po wiadomościach i zmapować na schemat już używany w `usage.jsonl`:

| opencode | `usage.jsonl` |
|---|---|
| `tokens.input` | `input_tokens` |
| `tokens.cache.read` | `cached_input_tokens` |
| `tokens.cache.write` | `cache_creation_input_tokens` |
| `tokens.output` | `output_tokens` |
| `tokens.reasoning` | `reasoning_output_tokens` |

**Krok 4 — zapis zamiast zaślepki.** W `run_generic` zamienić bezwarunkowe
`_log_call_without_tokens` ([agents.py:862](../forge/agents.py#L862)) na:
`usage = extract_opencode_usage(stream)`; gdy niepuste → `log_usage(...)`
z pełnym rekordem; gdy puste → dotychczasowe `_log_call_without_tokens`.
Fallback zostaje na stałe: nieznane generyczne CLI nadal nie raportuje niczego,
a `usage_unavailable: True` jest uczciwszym zapisem niż zero.

**Krok 5 — raport.** `pricing.py` ma już stawki `zai-coding-plan/glm-5.2`
i `openai/gpt-5.6-luna`, a `report._tokens` czyta czterowymiarowo. Po krokach
1–4 `$/zadanie` zaczyna widzieć drugą połowę rachunku **bez zmian w raporcie**.
Zweryfikować to jednym uruchomieniem raportu, nie zakładać.

### a.3 Testy

`tests/test_agents.py`:

| test | asercja |
|---|---|
| `test_opencode_usage_sums_last_version_of_each_message` | strumień z tą samą wiadomością powtórzoną 3× rosnąco → licznik **raz**, wartość z ostatniej wersji |
| `test_opencode_usage_maps_cache_read_and_write` | `cache.read`→`cached_input_tokens`, `cache.write`→`cache_creation_input_tokens` |
| `test_opencode_usage_empty_stream_returns_empty` | brak zdarzeń → `{}`, wywołujący wraca do `usage_unavailable` |
| `test_generic_opencode_extracts_text_without_thin` | strumień zdarzeń → czysty tekst odpowiedzi, **nie** JSON |

Fixture: zapisać jeden prawdziwy strumień z `--format json` z krótkiego
wywołania i przyciąć do kilku linii. Fixture syntetyczny z palca nie obroni
kroku 2, bo to właśnie zgodność z realnym kształtem jest tu przedmiotem testu.

### a.4 Ryzyko i wycofanie

Ryzyko: `--format json` zmienia stdout wszystkich ról opencode; błąd = każdy
werdykt nieparsowalny. Wycofanie: usunięcie `--format json` z jednego szablonu
przywraca stary tor w całości. **Pierwsze uruchomienie po wdrożeniu obserwować
na krótkim biegu** (`--max-iters 1`), nie puszczać nocą.

---

## b) Mistrz z powrotem na tani model

### b.1 Co się stało

Commit `33bdb60` „change model” przeniósł `economy`/`efficient`/`balanced` na
opencode z Luny na GLM-5.2. Mistrz jedzie na poziomie `efficient`
([config.py:42](../forge/config.py#L42)) → wylądował na GLM.

Pomiar z tego przebiegu: **31 wywołań mistrza, 1 wyprodukowana notatka**
(14:05, „koder ruszył plik testowy” — trafna). Stawki: GLM `1,40 / 4,40` za mln
vs Luna `0,20 / 1,20` — **7× na wejściu, 3,7× na wyjściu**. To rola cienka, bez
narzędzi, bez pętli agentowej, czytająca ~20 linii dziennika: dokładnie ta, dla
której tani model był właściwy.

### b.2 Wariant zalecany — cofnąć dwa dolne poziomy

`forge/config.py`, `MODEL_LEVEL_ROUTING["opencode"]`:

```python
"economy":   ("openai/gpt-5.6-luna", "medium"),
"efficient": ("openai/gpt-5.6-luna", "high"),
"balanced":  ("zai-coding-plan/glm-5.2", "high"),   # bez zmian
"strong":    ("zai-coding-plan/glm-5.2", "high"),   # bez zmian
"max":       ("zai-coding-plan/glm-5.2", "max"),    # bez zmian
```

To jest dosłowne cofnięcie połowy `33bdb60` (identyfikator modelu musi mieć
prefiks providera — `openai/gpt-5.6-luna`, inaczej niż goły `gpt-5.6-luna` u Codeksa).

**Zasięg zmiany — kto jeszcze na tym jedzie:**

| rola | poziom (standard) | efekt |
|---|---|---|
| mistrz | `efficient` | **→ Luna** (cel zmiany) |
| koder | `efficient` | → Luna, **tylko gdy `coder_agent=opencode`** (w tym przebiegu był Codex) |
| weryfikator | `efficient` | → Luna |
| tester | `balanced` | bez zmian (GLM) |
| recenzent | `balanced` | bez zmian (GLM) |

Recenzent i tester zostają na GLM świadomie: recenzja na GLM znalazła w tym
przebiegu jeden prawdziwy defekt (task-667, jawny cel poza zasięgiem).

### b.3 Wariant alternatywny — wyjątek dla mistrza

Jeśli koder i weryfikator mają zostać na GLM, zamiast tabeli zmieniamy
`Config.role` ([config.py:322](../forge/config.py#L322)): dopisać `"master"` do
zbioru ról, w których jawny `configured_model` wygrywa z routingiem poziomu.
Uzasadnienie mieści się w istniejącym komentarzu — „routing trudności dotyczy
**wykonawców pojedynczego zadania**”, a mistrz jest nadzorcą procesu, nie
wykonawcą. Wtedy `FORGE_MASTER_MODEL` / `FORGE_MASTER_EFFORT` zaczynają
działać, a routing zostaje nietknięty.

Wariant b.2 jest prostszy i odwraca konkretną, świeżą zmianę; b.3 jest
precyzyjniejszy, ale dokłada wyjątek do reguły routingu. **Zalecam b.2**, chyba
że koder faktycznie ma jechać na opencode.

### b.4 Test i weryfikacja

`tests/test_adapters.py` (tam już stoją asercje routingu z `33bdb60`):
`test_opencode_economy_and_efficient_route_to_luna` — `cfg.role("master")`
zwraca `openai/gpt-5.6-luna`, a `cfg.role("reviewer")` nadal GLM.

**Weryfikacja skutku wymaga punktu (a).** Bez pomiaru zużycia opencode ta
zmiana jest nieweryfikowalna liczbowo — dlatego (a) idzie przed (b) w
kolejności wdrożenia.

---

## d) Kolejność planowania a drenaż backlogu

### d.1 Co naprawdę się stało — rekonstrukcja z liczników

Warunek kadencji ([orchestrate.py:393](../forge/orchestrate.py#L393)) **już
dziś** wymaga pustej kolejki:

```python
if (not state.task_queue
        and state.plan_batches - state.steered_at_batch >= cfg.steering_batches):
    return "cadence"
```

Przy `FORGE_STEERING_BATCHES=2` i `steered_at_batch=66`:

| iteracja | `plan_batches` | różnica | co się stało |
|---|---|---|---|
| 14 (13:26) | 67 | 1 < 2 | **planowanie** → 1 zadanie po 996 s |
| 16 (14:09) | 68 | 2 ≥ 2 | przegląd kierunku |

### d.2 Dlaczego dosłowny warunek „kolejka <2 ⇒ steering przed planowaniem” nie zadziała

W iteracji 14 kolejka była **pusta** (0 zadań) — czyli warunek „<2” był
spełniony, a mimo to właściwym krokiem było planowanie. Fakt, którego brakowało,
to **drenaż BACKLOG.md**, a Forge tego pliku świadomie nie czyta (należy do
planisty i przeglądu kierunku). Ta informacja pojawia się dopiero **po**
wywołaniu planisty — w rozmiarze zwróconego wsadu.

Innymi słowy: sygnału, którego potrzebujemy, nie ma w momencie, w którym
chcielibyśmy podjąć decyzję. Dlatego plan realizuje intencję punktu (d) dwoma
zmianami, które da się oprzeć na danych faktycznie dostępnych.

### d.3 Zmiana 1 — krótki wsad jako sygnał drenażu

`forge/state.py`: nowe pole

```python
# Ostatni wsad planisty był wyraźnie krótszy od zamówionego — backlog jest
# drenowany, więc kolejna granica wsadów należy do przeglądu kierunku, a nie
# do kolejnego drogiego wywołania planisty na tym samym pustym backlogu.
batch_drained: bool = False
```

`phase_plan_batch`: po utworzeniu zadań ustawić
`state.batch_drained = len(tasks) * 2 < cfg.batch_size` (przy `batch_size=8`
próg to ≤3 zadania; obserwowany wsad miał 1).

`_steering_trigger`: rozszerzyć warunek kadencji

```python
if not state.task_queue and (
        state.plan_batches - state.steered_at_batch >= cfg.steering_batches
        or state.batch_drained):
    return "cadence"
```

`phase_diff_bootstrap` zeruje `batch_drained` razem ze `steering_due`.

**Uwaga krytyczna — nie wolno użyć do tego `steering_due`.** Gałąź
`if state.steering_due: return "backlog"` ([orchestrate.py:381](../forge/orchestrate.py#L381))
**nie ma** strażnika pustej kolejki; jej docstring opiera się na inwariancie
„ustawia go dokładnie wyczerpany backlog, więc kolejka i tak jest wtedy pusta”.
Ustawienie `steering_due` po wsadzie 1-zadaniowym złamałoby ten inwariant:
przegląd wystartowałby z zadaniem w kolejce, a `replan=true` skasowałby świeżo
zaplanowany wsad — dokładnie ta strata (~520 tys. tokenów wejścia), przed którą
ostrzega komentarz na [orchestrate.py:383-392](../forge/orchestrate.py#L383).
Osobne pole obok warunku `not state.task_queue` tego nie rusza.

**Uczciwy bilans:** w tym konkretnym przebiegu zmiana oszczędziłaby **0 s** —
przegląd i tak wypadł na następnej granicy. Wartość jest zapobiegawcza: przy
`FORGE_STEERING_BATCHES` większym niż 2 dzisiejszy kod przemieliłby kilka
wsadów po 1 zadaniu, każdy po ~1000 s, zanim licznik dojrzeje.

### d.4 Zmiana 2 — kadencja liczona w zadaniach, nie we wsadach

Komentarz przy `steering_batches` ([config.py:178](../forge/config.py#L178)) sam
przyznaje, że „iloczyn z `batch_size` to dziś 2×8 = 16” — czyli intencją jest
**praca**, a jednostką jest **wsad**. Wsad 1-zadaniowy liczy się tak samo jak
ośmiozadaniowy, więc kadencja rozjeżdża się z intencją dokładnie wtedy, gdy
backlog się kończy.

Propozycja: nowy licznik `planned_tasks` w `State` (rośnie o `len(tasks)`),
`steered_at_tasks` zamiast `steered_at_batch`, próg
`FORGE_STEERING_TASKS` domyślnie `steering_batches * batch_size` (=16), co
zachowuje dzisiejsze zachowanie dla pełnych wsadów.

To zmiana **opcjonalna** — d.3 rozwiązuje objaw mniejszym kosztem. Wdrażać
tylko, jeśli d.3 okaże się za wąskie. Migracja starego `STATE.json` musi
przewidzieć brak nowych pól (`plan_batches * batch_size` jako wartość startowa).

### d.5 Testy

`tests/test_steering.py`:
* `test_short_batch_triggers_cadence_on_next_empty_queue` — wsad 1 zadania →
  po jego konsumpcji `_steering_trigger` zwraca `"cadence"` mimo
  niedojrzałego licznika wsadów;
* `test_short_batch_does_not_trigger_while_queue_not_empty` — **strażnik
  regresji d.3**: z zadaniem w kolejce trigger jest pusty;
* `test_full_batch_keeps_existing_cadence` — wsad 8 zadań nie zmienia
  dotychczasowego zachowania.

---

## e) Sugestie recenzenta: `blocking` vs `nit`

### e.1 Pomiar

| zadanie | werdykt review | treść uwag | dodatkowa iteracja |
|---|---|---|---|
| task-663 | approve | — | nie |
| task-664 | suggestions | próg `MIN_SETTLEMENT_DEFENDERS + 1` | tak |
| task-665 | suggestions | martwy klik, redundantna asercja, docstring | tak |
| task-666 | approve | — | nie |
| task-667 | suggestions | **prawdziwy defekt** (cel poza zasięgiem) | tak |
| task-668 | approve | — | nie |
| task-669 | suggestions | usunąć martwą kopię `EXPECTED_STATUS_TEXT` | tak |
| task-670 | suggestions | komentarz + rename `REASON_STATUS_PL` | tak |

**6 z 8 zadań** dostało `suggestions`; każde uruchomiło pełną dodatkową
iterację (mistrz + tester + koder + tester ≈ 4 wywołania, 2–5 min). Realny
defekt: **jeden** (task-667). Reszta to kosmetyka — ok. **20 wywołań agentów**
w jednym przebiegu.

### e.2 Dlaczego dzisiejszy kontrakt to wymusza

`reviewer.md` ma jeden kubeł na wszystko, co nie jest `request_changes`, a jego
test rozstrzygający brzmi: *czy diff nadal można bezpiecznie zacommitować?*
Poprawka docstringa przechodzi ten test, więc **poprawnie** ląduje w
`suggestions` — a `suggestions` bezwarunkowo zawraca zadanie do testera
([orchestrate.py:1284-1292](../forge/orchestrate.py#L1284)). Wina leży w
kontrakcie, nie w recenzencie.

### e.3 Zmiana kontraktu

**Prompt** (`reviewer.md`) — rozdzielić `notes` na dwa pola i dodać drugi test
rozstrzygający:

```
- notes: uwagi, których pominięcie zostawi w repo błąd zachowania, złamany
  kontrakt, mylącą nazwę publiczną albo test nieweryfikujący tego, co deklaruje;
- nits: wszystko pozostałe (brzmienie docstringa, nazwa prywatnej stałej,
  redundantna asercja, drobne uproszczenie). Trafiają do notatnika zadania i
  NIE uruchamiają dodatkowej rundy.

Drugi test rozstrzygający: czy pominięcie tej uwagi na zawsze zostawi w repo
coś, co wprowadzi w błąd czytelnika kodu albo użytkownika? Jeśli nie — to nit.
```

Werdykt `approve` przestaje wymagać pustych `nits` (dziś `approve` z uwagami
jest odrzucane przez parser), `suggestions` wymaga niepustego `notes`.

**Parser** (`task_pipeline.parse_review_decision`,
[task_pipeline.py:66](../forge/task_pipeline.py#L66)):
* `data["nits"] = _as_strings(data.get("nits"))`;
* `approve` → wymaga pustego `notes`, **dopuszcza** niepuste `nits`;
* `suggestions`/`request_changes` → jak dziś, co najmniej jedna pozycja w `notes`.

**Pętla** (`orchestrate`): `nits` nigdy nie trafiają do `state.tester_handoff`
ani `state.review_notes`. Zamiast tego:
* wpis do dziennika `review-nits: …` (mistrz widzi dziennik, więc powtarzający
  się nit dalej może wywołać jego reakcję);
* dopisanie do `.forge/nits.md` — lista do wykorzystania przy okazji, przez
  koder następnego zadania dotykającego tego pliku. **Zero dodatkowych wywołań.**

### e.4 Oczekiwany efekt i ryzyko

Na danych z tego przebiegu: z 6 zawrotów zostaje **1–2** (task-667 na pewno;
task-664 z progiem `MIN_SETTLEMENT_DEFENDERS + 1` jest graniczny — dotyczy
czytelności warunku, nie zachowania). Oszczędność rzędu **16 wywołań agentów**,
czyli ~15–20 min czasu przebiegu i cztery pełne cykle mistrz→tester→koder.

Ryzyko: recenzent zacznie chować prawdziwe defekty w `nits`, żeby „nie robić
kłopotu”. Mitygacja — dwie:
1. drugi test rozstrzygający jest sformułowany przez **skutek** („zostawi w
   repo coś mylącego”), nie przez rozmiar poprawki;
2. `nits` lądują w dzienniku i w `.forge/nits.md`, więc nadal są policzalne —
   po dwóch przebiegach da się sprawdzić, czy coś w nich nie powinno było być
   `notes`. Bez tego zapisu zmiana byłaby nieodwracalna poznawczo.

### e.5 Testy

`tests/test_task_pipeline.py`:
* `test_approve_allows_nits` — `{"verdict":"approve","notes":[],"nits":["…"]}` parsuje;
* `test_approve_still_rejects_notes` — `approve` z niepustym `notes` → `InvalidDecision`;
* `test_suggestions_still_requires_notes`.

`tests/test_task_flow.py`:
* `test_nits_do_not_reopen_tdd_loop` — review `approve` + `nits` → zadanie idzie
  **prosto do bramki przedcommitowej**, `review_suggestions_pending` pozostaje `False`;
* `test_suggestions_still_return_to_tester` — strażnik, że nie rozmontowaliśmy
  ścieżki dla prawdziwych uwag.

---

## Kolejność wdrożenia

| # | punkt | pliki | ryzyko | dlaczego tutaj |
|---|---|---|---|---|
| 1 | **a** pomiar | `adapters.py`, `agents.py` | **średnie** — dotyka stdout wszystkich ról opencode | bez pomiaru pozostałe zmiany są nieweryfikowalne |
| 2 | **b** routing | `config.py` | niskie | efekt widoczny dopiero po (1) |
| 3 | **e** nits | `reviewer.md`, `task_pipeline.py`, `orchestrate.py` | niskie | największa oszczędność, niezależna od reszty |
| 4 | **d** kadencja | `state.py`, `orchestrate.py` | niskie | zapobiegawcza, najmniejszy zwrot dziś |

Punkt (a) jest pierwszy mimo najwyższego ryzyka, bo jest **warunkiem
weryfikacji** (b) i daje twardą liczbę dla (e). Wdrażać go osobnym commitem i
sprawdzić krótkim biegiem `--max-iters 1`, zanim wejdzie reszta.

**Kryterium „gotowe” dla całości:** kolejny pełny przebieg pokazuje w
`usage.jsonl` zero rekordów `usage_unavailable` dla opencode, `$/zadanie` w
raporcie obejmuje obu providerów, mistrz stoi na `openai/gpt-5.6-luna`, a
udział zadań z zawrotem po review spada z 6/8 do najwyżej 2/8.

---

## Czego ten plan świadomie nie robi

* **Nie rusza `master_gate`.** Bilans opisany przy
  [config.py:236-248](../forge/config.py#L236) pozostaje aktualny: wyciszenie
  mistrza oszczędza kilka procent, a jedna pominięta interwencja kosztuje całe
  zadanie. Po punkcie (b) mistrz przestaje być drogi i temat znika sam.
  Jeśli kiedyś wróci — `FORGE_MASTER_GATE=shadow` mierzy zgodność bramki za
  cenę jednej linii logu, bez ryzyka.
* **Nie tnie objętości wyjścia narzędzi** (`0,3–0,5 MB` na turę, pojedyncze
  wyjścia do 829 k znaków). Przy 92% trafień cache to nie jest wyciek
  pieniędzy, a przycinanie kontekstu testera grozi utratą dowodu — sprzecznie
  z zasadą kosztu całego procesu z `CLAUDE.md`.
* **Nie zmienia poziomu testera ani recenzenta** (`balanced` → GLM). To role,
  które w tym przebiegu wykryły realne defekty.
* **Nie wprowadza cache'owania promptów po stronie opencode** — najpierw
  pomiar (a), potem ewentualna decyzja na danych.
