# Plan wdrożenia — pięć zmian z „Przyspieszenia developmentu"

Dokument wykonawczy do [PRZYSPIESZENIE-DEVELOPMENTU.md](PRZYSPIESZENIE-DEVELOPMENTU.md).
Tam jest analiza i uzasadnienie; tutaj design i taski.

## Zakres

| # | strumień | źródło | status |
|---|---|---|---|
| W1 | metryka `$/zadanie` z cennika API | §1a (zmieniona: bez linii) | wchodzi |
| W2 | widoczny odsiew planisty | §1b | wchodzi |
| W3 | deterministyczna bramka przed mistrzem | P3 (z poprawionym uzasadnieniem) | **odrzucone po przeglądzie** — patrz „Stan wdrożenia" |
| W4 | wsad 8 + kadencja przed planowaniem | P6 + znaleziony błąd kolejności | wchodzi |
| W5 | mapa kryteriów i szersza pierwsza bramka | P4 (wariant umiarkowany) | wchodzi |
| — | równoległe tory w worktree | P1 | poza tym planem |
| — | fallback providera przy limicie | P2 | poza tym planem |
| — | prefetch planisty i spekulatywna bramka pakietu | P5 | **odrzucone** |
| — | większe zadania | P7 | odrzucone do czasu pomiaru W5 |

## Stan wdrożenia (2026-08-08)

### W3 wypada z planu — decyzja po przeglądzie

**Bramka mistrza zostaje wyłączona (`FORGE_MASTER_GATE=off`) i nie wchodzi w
tryb `on`.** Kod i testy zostają, ale jako mechanizm uśpiony.

Powód jest w bilansie, który sam ten dokument policzył w sekcji W3: mistrz to
2,7% tokenów i ~5% rachunku, więc sufit zysku to kilka procent. Po drugiej
stronie stoi pojedyncza pominięta interwencja — nieprzerwana pętla kosztuje
rundy po ~500 tys. tokenów wejścia, a przy `max_tdd_rounds` kończy się
`git reset --hard` i utratą całej pracy nad zadaniem. To ta sama asymetria
`p(porażki) × koszt zadania`, dla której odrzuciliśmy P7 — tylko że tutaj
staliśmy po jej złej stronie.

Przegląd pokazał też, że ryzyko nie jest hipotetyczne: bramka musi odwzorowywać
**wszystkie** warunki promptu mistrza 1:1, a pierwsza wersja pominęła piąty
(powtórzony odsiew planisty) i w trybie `on` wyciszyłaby go dokładnie tam, gdzie
wymaga tego W2. Każde przyszłe rozszerzenie promptu ma ten sam problem — to
stały koszt utrzymania, nie jednorazowa naprawa. Pilnuje go dziś
`test_every_prompt_rule_has_a_gate_predicate`.

Tryb `shadow` zostaje dostępny: nigdy nie wycisza mistrza i pozwoli zmierzyć
bramkę, gdyby temat wrócił. **T3.8 jest odwołane, nie odłożone.**

### Naprawy po przeglądzie

| znalezisko | naprawa |
|---|---|
| bramka nie znała warunku odsiewu planisty | `TRIGGER_PLAN_SIFT` + test odwzorowania 1:1 promptu i bramki |
| dwóch kolejnych odsiewów nie da się odczytać z okna dziennika | licznik `State.plan_sift_streak` liczony po WSADACH, przekazywany mistrzowi osobnym polem promptu — tak samo jak `round_limit_tasks` i z tego samego powodu |
| niepełna kotwica ceny Codeksa uchodziła za poprawną | wymagane obie wartości dodatnie, inaczej `None` |
| `llamacpp/*` bez telemetrii pokazywał `—` zamiast `0.00` | stawka zerowa jest faktem niezależnym od tokenów, więc liczy się jako wyceniona |
| kwota częściowa bez gwiazdki przy brakującej telemetrii | `_partial()` zapala `*` zarówno przy braku stawki, jak i przy braku tokenów |
| `FORGE_MASTER_GATE=1` czytało się jako `off` | `_master_gate_mode` przyjmuje `on`/`1`/`true`/`tak`; nierozpoznane = `off` |

### Co zostało do zmierzenia

| task | co zostało | dlaczego nie teraz |
|---|---|---|
| T1.6 | baseline z jednego przebiegu od czystego stanu | licznik i mianownik muszą pokrywać ten sam okres — patrz zastrzeżenie przy T1.6 |
| ~~T3.8~~ | — | **odwołane razem z W3** |
| T4.5 | przebieg pomiarowy wsadu 8 | porównanie z baselinem |
| T5.4 | przebieg porównawczy szerszej bramki | pięć bramek akceptacji |

Domyślne ustawienia po wdrożeniu: `FORGE_MASTER_GATE=off` (mistrz wołany
zawsze), `FORGE_BATCH_SIZE=8`, `FORGE_STEERING_BATCHES=2`.

P5 odpada decyzją: sama bramka spekulatywna to ~2–3% zegara (nie 8–12% —
ta liczba w dokumencie zawierała prefetch planisty), a wprowadza równoległe
uruchomienie pakietu w tym samym drzewie roboczym co reviewer. Stosunek
zysku do nowej klasy ryzyka jest zły.

## Zasady wspólne

1. **Nic nie wchodzi przed W1.** Bez `$/zadanie` żadna z pozostałych zmian
   nie jest weryfikowalna względem warunku „bez wzrostu kosztu".
2. **Jedna zmiana na przebieg pomiarowy.** W3, W4 i W5 zmieniają różne
   rzeczy, ale wszystkie ruszają liczbę rund albo liczbę wywołań. Wdrożone
   razem nie dadzą się rozliczyć osobno.
3. **Każdy strumień ma wycofanie w jednym kroku** — opisane przy każdym.
   Żaden nie wymaga migracji `STATE.json`.
4. **Zero nowych wywołań LLM.** W1–W4 są czystym Pythonem; W5 zmienia treść
   promptów, nie ich liczbę ani długość rzędu wielkości.

---

# W1. Metryka `$/zadanie` z cennika API

## Co się zmienia względem dokumentu źródłowego

Jednostką nie jest linia kodu, tylko **zadanie**. `git log --numstat` wypada
z planu w całości. Trzy liczby na przebieg: **`$/przebieg`**,
**`$/zadanie`**, **`$/rundę TDD`**.

`$/rundę` jest tu ważniejsze niż wygląda: runda, a nie zadanie, jest
właściwą jednostką kosztu w tej pętli (tester ~200 k in + koder ~307 k in na
rundę), a W5 działa dokładnie na liczbie rund. Bez tej liczby nie da się
odróżnić „zadania staniały" od „zadania się skurczyły".

**Zastrzeżenie do zapisania w dokumencie źródłowym:** `$/zadanie` jest
porównywalne tylko dopóki rozmiar zadania się nie zmienia. P7 tę metrykę z
definicji psuje — przy ewentualnym P7 punktem odniesienia musi być
`$/przebieg` przy tej samej zawartości briefu. Dla W2–W5 problemu nie ma,
bo żadna nie rusza rozmiaru zadania.

## Design

### Dane, które już są

`.forge/usage.jsonl` ma komplet potrzebnych pól — sprawdzone w
[agents.py:535-537](../forge/agents.py#L535-L537),
[agents.py:626-627](../forge/agents.py#L626-L627) i
[agents.py:688-689](../forge/agents.py#L688-L689):

```json
{"ts": "...", "agent": "claude", "phase": "review-r1",
 "model": "opus", "effort": "low", "usage": {...}}
```

Klucz `(agent, model)` wystarcza do wyceny. `phase` normalizuje się już
przez [`normalize_phase`](../forge/report.py#L34-L41).

### Pułapka semantyki tokenów — to jest jedyne miejsce, gdzie łatwo o błąd

Providerzy liczą inaczej i obecne
[`_tokens()`](../forge/report.py#L44-L53) tę różnicę zaciera:

| provider | `input_tokens` | cache |
|---|---|---|
| Claude | **wyłącznie tokeny nieocache'owane** | `cache_creation_input_tokens` (zapis) i `cache_read_input_tokens` (odczyt) osobno |
| Codex | **całość wejścia**, z cache włącznie | `cached_input_tokens` jest podzbiorem `input_tokens` |

Skutki, jeśli to zignorować:
- dla Claude'a **pominięcie `cache_creation_input_tokens`** gubi
  najdroższą pozycję (stawka 1,25× wejścia) — dokładnie ten składnik, przez
  który mistrz przepłaca (patrz W3);
- dla Codeksa **zsumowanie `input` i `cached`** liczy te same tokeny dwa
  razy.

Dlatego wycena idzie przez jawnie znormalizowaną czwórkę
`(uncached_in, cache_write, cache_read, out)`, wyliczaną per provider, a nie
przez dzisiejszą trójkę.

### Tabela stawek

Nowy moduł `forge/pricing.py` — jedyna nowa dana w całej zmianie. Stawki za
milion tokenów, w USD:

```python
CLAUDE_RATES = {
    #            in     cache_write(1.25×)  cache_read(0.1×)   out
    "opus":    (5.00,   6.25,               0.50,              25.00),
    "sonnet":  (3.00,   3.75,               0.30,              15.00),
    "haiku":   (1.00,   1.25,               0.10,               5.00),
}
```

Sonnet ma promocję `$2/$10` do 2026-08-31. **Nie wpisujemy jej** — stawka
promocyjna wygaśnie w połowie okresu pomiarowego i porównanie dwóch
przebiegów przestałoby być uczciwe. Zaniżenie kosztu jest gorsze niż jego
zawyżenie.

Codex — proporcje bez znanej kotwicy, więc tabela trzyma mnożniki:

```python
SOL_IN_USD  = float(os.environ.get("FORGE_PRICE_SOL_IN", "0"))
SOL_OUT_USD = float(os.environ.get("FORGE_PRICE_SOL_OUT", "0"))

CODEX_MULTIPLIERS = {
    "gpt-5.6-sol":   1.00,
    "gpt-5.6-terra": 0.40,   # Sol / 2,5
    "gpt-5.6-luna":  0.04,   # Terra / 10
}
```

Kotwica pusta = raport pokazuje tokeny Codeksa i **jawny brak wyceny**
(`—`), nigdy zera. Zero udawałoby, że codex jest darmowy, i przekłamywałoby
`$/zadanie` w dół.

`opencode/llamacpp/*` → jawnie `0.00` z komentarzem „prąd, nie API".
`neuralwatt/*` → osobny słownik, na razie pusty (`—`).

Nieznany `(agent, model)` → `—` plus **jedno ostrzeżenie na przebieg** z
listą nierozpoznanych kluczy. Cicha wycena na 0 jest tu najgorszym możliwym
zachowaniem.

### Mianowniki

`$/zadanie` potrzebuje liczby zadań, `$/rundę` — liczby rund. Obu nie ma w
`usage.jsonl`, ale obie są w `ledger.md`:

- zadania: wpisy `UKOŃCZONE po N rundach` (patrz
  [orchestrate.py:1141](../forge/orchestrate.py#L1141));
- rundy: suma `N` z tych samych wpisów.

To daje w gratis rozkład rund na zadanie — główną metrykę kontrolną W5. Nowa
funkcja `ledger.completed_tasks(project) -> list[tuple[str, int]]` obok
istniejącego [`round_limit_tasks`](../forge/ledger.py#L97).

**Zadania porzucone liczą się do kosztu, nie do mianownika.** Zadanie
`PORZUCONE` spaliło tokeny i nie dowiozło nic — wliczenie go do mianownika
maskowałoby dokładnie tę stratę, którą chcemy widzieć.

## Taski

**T1.1 — znormalizuj odczyt tokenów per provider.**
`forge/report.py`: zastąp [`_tokens()`](../forge/report.py#L44-L53) funkcją
zwracającą `(uncached_in, cache_write, cache_read, out)` z gałęzią per
provider wg tabeli wyżej. Zachowaj dotychczasowe kolumny raportu (`wejście`,
`z cache`, `wyjście`) wyliczane z nowej czwórki, żeby istniejący format nie
zniknął. ~30 linii.
*Wpierw sprawdź na realnym `usage.jsonl`, czy Claude faktycznie zapisuje
`cache_creation_input_tokens` — jeśli nie, trzeba je dołożyć w
[agents.py:535](../forge/agents.py#L535).*

**T1.2 — moduł `forge/pricing.py`.**
Tabele stawek + `cost_usd(agent, model, tokens) -> float | None`. `None`
oznacza „brak stawki", nigdy 0. ~60 linii.

**T1.3 — `ledger.completed_tasks()`.**
`forge/ledger.py`: parser wpisów `UKOŃCZONE po N rundach` zwracający
`[(task_id, rounds)]`. Wzorzec identyfikatora bierz z
[`_TASK_ID_BODY`](../forge/orchestrate.py#L129) — nie duplikuj gramatyki.
~15 linii.

**T1.4 — kolumna `$` i podsumowanie w raporcie.**
`forge/report.py`: `$` w tabeli per (agent, faza), pod tabelą blok:
```
zadania: 27 (+1 porzucone)   rundy: 71   rundy/zadanie: 2,63
$/przebieg: 12,84   $/zadanie: 0,48   $/rundę: 0,18
rozkład rund: 1×54 2×48 3×30 4×11 5×5 6-10×9
```
plus linia ostrzeżenia przy nierozpoznanych modelach. ~50 linii.

**T1.5 — testy.**
`tests/test_report.py`:
- semantyka Claude (cache_creation liczone) vs Codex (cached nie dubluje);
- brak stawki → `—`, nigdy 0, plus ostrzeżenie;
- `$/zadanie` liczone po ukończonych, porzucone tylko w liczniku;
- `completed_tasks` na sztucznym `ledger.md` z zaszumionymi wpisami.

**T1.6 — baseline.** *(wykonane częściowo — patrz zastrzeżenie)*

Raport uruchomiony na `total-battle-brothers-for-wesnoth`, stan na 2026-08-07:

```
RAZEM                           3363   32,821,286   23,849,452  645,581,656  8,046,192   349.62*

zadania: 6   rundy: 16   rundy/zadanie: 2.67
$/przebieg: 349.62*   $/zadanie: 58.27*   $/rundę: 21.85*
rozkład rund (rundy×zadania): 1×1 2×2 3×2 5×1
wsady planisty: 1, zadeklarowane 6, przyjęte 6 (odsiew 0%)
pushback kodera: 0/16 (0%)
UWAGA: brak stawki dla claude/fable, codex, codex/gpt-5.6-luna,
       codex/gpt-5.6-sol, codex/gpt-5.6-terra
UWAGA: 1160 wywołań bez telemetrii tokenów
```

**Zastrzeżenie — tego baseline'u NIE wolno użyć jako punktu odniesienia dla
W3–W5.** Licznik i mianownik pokrywają różne okresy:

- `usage.jsonl` jest kumulatywny i obejmuje całą historię projektu (3363
  wywołania, wiele przebiegów);
- mianowniki pochodzą z dziennika, a ten trzyma **ostatnie 80 wpisów** —
  stąd tylko 6 zadań. `$/zadanie: 58.27` jest więc artefaktem tej różnicy, nie
  pomiarem.

Baseline z przebiegu 26.07 jest **nieodtwarzalny**: `.forge/` jest w
`.gitignore`, więc ówczesny `ledger.md` został nadpisany i nie ma go w historii
gita, a `usage.jsonl` nie da się po nim rozciąć (wpisy dziennika mają znacznik
`[HH:MM]` bez daty). Dwie rzeczy są potrzebne, zanim `$/zadanie` zacznie
mierzyć to, co ma mierzyć:

1. **kotwica cenowa Codeksa** — `FORGE_PRICE_SOL_IN` i `FORGE_PRICE_SOL_OUT`;
   bez nich Codex (druga co do wielkości pozycja tokenowa) nie wchodzi do `$`,
   co widać po gwiazdce przy każdej liczbie;
2. **pomiar od czystego stanu** — pusty `usage.jsonl` i pusty `ledger.md` na
   początku przebiegu porównawczego, żeby licznik i mianownik pokrywały ten sam
   okres.

Do tego czasu jedyną uczciwą liczbą porównawczą jest `$/przebieg` przy tej
samej zawartości briefu.

**Wycofanie:** zmiana wyłącznie w warstwie raportowej. Nic w pętli.

---

# W2. Widoczny odsiew planisty

## Problem

[orchestrate.py:184-185](../forge/orchestrate.py#L184-L185) przyjmuje zadanie
tylko wtedy, gdy planista zapisał jego plik na dysku:

```python
if task["file"] and Path(project, task["file"]).is_file():
    tasks.append(task)
```

Brak `else`. Zadanie zadeklarowane w JSON-ie bez pliku znika **bez wpisu w
logu i bez wpisu w dzienniku** — inaczej niż zadanie o błędnym
identyfikatorze linijkę wyżej
([orchestrate.py:179-182](../forge/orchestrate.py#L179-L182)), które ma obie
ścieżki obsłużone.

Prosisz o 8, planista deklaruje 8, zapisuje 5 → w logu widzisz
`utworzono 5 zadań` i nic więcej. Nie da się odróżnić „planista uznał, że
więcej nie trzeba" od „planista się urwał w połowie wsadu". To jest jedyny
sygnał, że pokrętło `batch_size` przekroczyło jego zdolność domykania wsadu
— czyli **warunek wstępny W4**.

## Design

Symetria z istniejącą gałęzią błędnego identyfikatora: `log()` + wpis do
dziennika, plus jedna linia zbiorcza po pętli. Trzy poziomy widoczności, bo
mają trzech różnych odbiorców:

| poziom | odbiorca | treść |
|---|---|---|
| `log()` per zadanie | człowiek przy konsoli / GUI | który identyfikator odpadł i dlaczego |
| `ledger.append()` zbiorczo | **mistrz** — to jego jedyne wejście | `plan: zadeklarowano N, przyjęto M (odsiew: id, id)` |
| linia w raporcie | pomiar | `wsad: zadeklarowane/przyjęte` per wsad |

Wpis zbiorczy, nie per zadanie: dziennik jest wejściem mistrza, a trzy
osobne linie o tym samym zdarzeniu rozmyłyby jego słownik wzorców.

**Wpis do dziennika tylko przy niezerowym odsiewie.** Przy pełnym wsadzie
istniejąca linia `plan: utworzono N zadań (task-A…task-B)` wystarcza.

## Taski

**T2.1 — gałąź `else` w `phase_plan_batch`.**
[orchestrate.py:184-185](../forge/orchestrate.py#L184-L185): zbieraj
identyfikatory odsianych, `log()` per sztuka z rozróżnieniem powodu
(brak pola `file` vs plik nie istnieje na dysku — to dwie różne awarie
planisty). ~10 linii.

**T2.2 — wpis zbiorczy do dziennika.**
Po pętli, przy niezerowym odsiewie:
`ledger.append(project, f"plan: zadeklarowano {n}, przyjęto {m} (odsiew: {ids})")`.
~4 linie.

**T2.3 — mistrz ma to rozumieć.**
`forge/prompts/templates/master-system.md`: dopisz do listy warunków
interwencji piąty punkt — **powtarzający się** odsiew planisty (≥2 wsady z
rzędu z niezerowym odsiewem) uzasadnia uwagę dla planisty. Jednorazowy nie:
to szum. ~3 linie promptu.

**T2.4 — raport.**
`forge/report.py`: linia `wsady planisty: 6, zadeklarowane 48, przyjęte 45
(odsiew 6%)`. Parsowane z dziennika. ~15 linii.

**T2.5 — testy.**
`tests/test_orchestrate.py`: zadanie z `file` wskazującym nieistniejący plik
→ odsiane, obecne w logu **i** w dzienniku; pełny wsad → brak wpisu o
odsiewie; wszystkie zadania odsiane → istniejący `AgentError`
([orchestrate.py:186-187](../forge/orchestrate.py#L186-L187)) nadal leci.

**Wycofanie:** czysto addytywne. Nic nie zmienia zachowania pętli.

---

# W3. Deterministyczna bramka przed mistrzem *(ODRZUCONE)*

> **Ta sekcja jest zapisem projektu, nie instrukcją.** Bramka została po
> przeglądzie odrzucona i jest domyślnie wyłączona — uzasadnienie w „Stanie
> wdrożenia" na górze dokumentu. Etapowanie przez tryb cieni opisane niżej
> (T3.8) jest **odwołane**, a `FORGE_MASTER_GATE=1` z tamtego akapitu działa
> dziś jako alias `on`, którego nie zalecamy włączać. Sekcja zostaje, bo jej
> analiza udziałów tokenowych jest nadal aktualna i to ona ufundowała decyzję
> o odrzuceniu.

## Poprawione uzasadnienie

Dokument źródłowy przypisuje P3 „zdjęcie presji z kwoty, która o 06:07
zablokowała review na dwie godziny". **To jest przesadzone i wymaga
korekty.** Z tabeli tokenów przebiegu 26.07:

| rola | wywołań | wejście | % tokenów Claude'a | ~$ | % $ |
|---|---:|---:|---:|---:|---:|
| review | 35 | 4,62 M | 58% | ~7,0 | ~55% |
| plan | 6 | 3,12 M | 39% | ~5,0 | ~40% |
| **mistrz** | **93** | **0,22 M** | **2,7%** | **~0,6** | **~5%** |

Mistrz to 69% wywołań, ale 2,7% tokenów. Limity subskrypcji są ważone
tokenami, nie liczbą żądań — wycięcie 84 z 93 wywołań zwalnia ~2,5% puli i
**nie odblokowuje** dwugodzinnego backoffu. Ten backoff wygenerowały review
i planista; adresuje go P2, nie P3.

Rozbieżność z przebiegiem 25.07 (mistrz $2,15 z $7,88) jest prawdziwa, ale
dotyczy starej konfiguracji — mistrz chodził wtedy na mocniejszym modelu i z
pełnym harnessem agentowym. Dziś ma `efficient` i tryb cienki
([config.py:36-42](../forge/config.py#L36-L42)).

**Zostają trzy realne zyski, w kolejności ważności:**

1. **Patologia cache'u.** `cache_creation` mistrza (117 k) jest **wyższe**
   niż `cache_read` (98 k). Każde wywołanie zapisuje do cache'u unikalny
   dziennik, który nigdy nie zostanie odczytany, po stawce 1,25×. To nie
   jest nieefektywność — to jest płacenie premii za cache, którego z
   definicji nie da się użyć.
2. ~6,5 min zegara.
3. ~5% rachunku Claude'a.

## Design

### Zasada nadrzędna: bramka widzi dokładnie to, co widzi mistrz

Mistrz dostaje wyłącznie
[`ledger.compact_tail(project)`](../forge/ledger.py#L91) i
[`ledger.round_limit_tasks(project)`](../forge/ledger.py#L97) —
[orchestrate.py:703-705](../forge/orchestrate.py#L703-L705). Bramka **musi**
liczyć się z tych samych dwóch wejść, a nie ze `State`.

Powód jest praktyczny, nie estetyczny: gdyby bramka czytała `State`, a mistrz
dziennik, obie mogłyby się rozjechać po restarcie albo po zmianie sposobu
zapisu wpisu, i bramka wyciszałaby mistrza w sytuacjach, w których on by
zareagował. Wspólne wejście domyka ten problem konstrukcyjnie.

Efekt uboczny: bramka jest **funkcją czystą** — testowalną bez repozytorium,
bez `git` i bez LLM-a.

### Umiejscowienie

Nowy moduł `forge/master_gate.py`:

```python
def trigger(ledger_tail: str, round_limit_tasks: list[str],
            *, task_id: str, next_role: str) -> str:
    """Nazwa spełnionego warunku interwencji albo pusty string."""
```

`_master_notes` ([orchestrate.py:687](../forge/orchestrate.py#L687)) woła to
przed `run_agent` i przy pustym wyniku zwraca `{}` bez dotykania LLM-a.
Sygnatura `_master_notes` się nie zmienia — wołający
([orchestrate.py:846](../forge/orchestrate.py#L846) i
[orchestrate.py:897-907](../forge/orchestrate.py#L897-L907)) zostają bez
zmian.

### Cztery warunki — mapowanie 1:1 z promptem mistrza

Z [master-system.md](../forge/prompts/templates/master-system.md):

| warunek w promptcie | reguła na dzienniku |
|---|---|
| ≥2 kolejne tury tej samej roli z tą samą decyzją i `pliki=bez_zmian` | ostatnie ≥2 wpisy `{task_id} r… {rola}→{decyzja}` mają tę samą rolę, tę samą decyzję i `pliki=bez_zmian` |
| koder ruszył plik testowy | ostatni wpis `{task_id} r… koder→` ma w `pliki=` ścieżkę uznaną za testową |
| kolejne pełne cykle `recenzja→tester→koder→recenzja` bez zmian | ≥2 wpisy `{task_id} recenzja→request_changes`, a między nimi żaden wpis z `pliki=` innym niż `bez_zmian` |
| ≥2 zadania na `round_limit` | `len(round_limit_tasks) >= 2` — wyzwala **tylko** dla `next_role == "planner"` |

Gramatyka wpisu jest ustalona w
[`run_turn`](../forge/orchestrate.py#L945-L947) i przy recenzji
([orchestrate.py:1076-1078](../forge/orchestrate.py#L1076-L1078)):
`{id} r{N} {rola}→{status} pliki={zmiany}: {reason}`. `compact_tail` jest
świadomie zaprojektowany tak, żeby chronić `pliki=` przy cięciu
([ledger.py:76-90](../forge/ledger.py#L76-L90)) — bramka polega na tej
własności i test musi ją pilnować.

### Jedyne miejsce z heurystyką: „plik testowy"

Forge jest stack-agnostyczny, więc nie zna konwencji testów projektu.
Heurystyka: basename zawiera `test` albo `spec` (bez rozróżniania wielkości
liter), albo ścieżka przechodzi przez katalog `test`/`tests`/`spec`/`specs`.

To jest **jedyny warunek, który może dać fałszywy negatyw** — projekt z
nietypową konwencją nazw sprawi, że mistrz nie zostanie zawołany tam, gdzie
dziś by zareagował. Dlatego cała zmiana wchodzi przez tryb cieni.

### Tryb cieni — warunek wdrożenia, nie opcja

Etap 1 (`FORGE_MASTER_GATE=shadow`, domyślny na pierwszym przebiegu):
bramka liczy się i **loguje**, ale mistrz jest wołany jak dziś. Log:

```
mistrz-bramka: trigger=powtórzona-decyzja  → nota: tak
mistrz-bramka: trigger=""                  → nota: NIE
mistrz-bramka: trigger=""                  → nota: TAK   ← rozbieżność
```

Po przebiegu liczysz przypadki `trigger="" ∧ nota=TAK`. **Zero rozbieżności
= bramka nie gubi ani jednej interwencji.** Dopiero wtedy
`FORGE_MASTER_GATE=1` i mistrz przestaje być wołany przy pustym triggerze.

Koszt trybu cieni: jeden przebieg bez oszczędności i jedna linia logu na
wywołanie. Za to zamienia „mam nadzieję, że nic nie zgubiłam" w pomiar.
Przy 12 notach na 93 wywołania próbka jest mała — to argument za trybem
cieni, nie przeciw niemu.

### Czego bramka nie robi

Nie formułuje noty. Trafienie warunku → normalne wywołanie mistrza.
Sformułowanie zostaje przy modelu, bo nota cytuje `reason` i powtarzany wpis
dziennika, a tego regułą się nie zrobi. Reguły zostają identyczne — zmienia
się wyłącznie to, **kto sprawdza warunek wyzwolenia**.

## Taski

**T3.1 — `forge/master_gate.py`.**
Parser wpisów dziennika (jedno wyrażenie regularne, gramatyka
identyfikatora z [`_TASK_ID_BODY`](../forge/orchestrate.py#L129)) + cztery
predykaty + `trigger()`. Bez importu `orchestrate`, bez I/O. ~90 linii.

**T3.2 — heurystyka pliku testowego.**
Osobna funkcja `looks_like_test_path(path) -> bool` w tym samym module,
z komentarzem, że to heurystyka i dlaczego nie da się jej uściślić.
~12 linii.

**T3.3 — pokrętło `master_gate`.**
`forge/config.py`, obok istniejących:
`master_gate: str = os.environ.get("FORGE_MASTER_GATE", "shadow")`
— wartości `off` / `shadow` / `on`. Domyślnie `shadow`. ~5 linii.

**T3.4 — wpięcie w `_master_notes`.**
[orchestrate.py:700-706](../forge/orchestrate.py#L700-L706): policz trigger
przed `run_agent`. Tryb `on` + pusty trigger → `return {}`. Tryb `shadow` →
zaloguj trigger i wołaj mistrza normalnie. Tryb `off` → jak dziś.
~15 linii.

**T3.5 — log rozbieżności w trybie cieni.**
Po powrocie z mistrza w trybie `shadow`: zaloguj parę
`(trigger, czy_padła_nota)`. ~5 linii.

**T3.6 — testy jednostkowe bramki.**
`tests/test_master_gate.py` — na sztucznych ogonach dziennika, bez repo:
- dwie kolejne tury testera `red`/`bez_zmian` → trigger;
- dwie tury różnych ról bez zmian → **brak** triggera (warunek mówi „tej
  samej roli");
- koder ze zmianą `tests/test_x.py` → trigger; koder ze zmianą `src/x.py` →
  brak;
- dwa `recenzja→request_changes` bez zmian po drodze → trigger; z realną
  zmianą po drodze → brak;
- `round_limit_tasks` długości 2 → trigger **tylko** dla `next_role="planner"`;
- wpis obcego zadania w ogonie nie wyzwala triggera dla `task_id`;
- ogon przycięty przez `compact_tail` nadal zawiera `pliki=` i bramka działa
  (test regresyjny na własność [ledger.py:76-90](../forge/ledger.py#L76-L90));
- pusty dziennik → brak triggera, brak wyjątku.

**T3.7 — test integracyjny wyciszenia.**
`tests/test_orchestrate.py`: przy `master_gate="on"` i pustym triggerze
`_master_notes` zwraca `{}` **bez** wywołania `run_agent`.

**T3.8 — przebieg w trybie cieni.**
Jeden pełny przebieg, potem policzenie rozbieżności. Bramka
przechodzi tylko przy zerze.

**Wycofanie:** `FORGE_MASTER_GATE=off`. Jedna zmienna środowiskowa.

---

# W4. Wsad 8 + kadencja przed planowaniem

## Znaleziony błąd — to jest właściwa treść tego strumienia

Dokument źródłowy twierdzi w warunku (2) do P6, że przy poprawnej kadencji
„przegląd trafia w kolejkę prawie pustą, więc ryzyko jest ograniczone".
**Przy obecnym kodzie to nieprawda.**

Ciąg zdarzeń w [`one_iteration`](../forge/orchestrate.py#L1215-L1250):

1. wyzwalacz przeglądu sprawdzany jest, gdy `not state.current_task`, i
   **nie patrzy na kolejkę** ([orchestrate.py:1229](../forge/orchestrate.py#L1229));
2. kadencja to `plan_batches - steered_at_batch >= steering_batches`
   ([orchestrate.py:330](../forge/orchestrate.py#L330)), a `plan_batches`
   rośnie **w chwili zaplanowania wsadu**
   ([orchestrate.py:188](../forge/orchestrate.py#L188));
3. planowanie i start pierwszego zadania dzieją się w **tej samej iteracji**
   ([orchestrate.py:1236-1250](../forge/orchestrate.py#L1236-L1250)).

Przy `steering_batches=2` i wsadzie 6: wsad 1 → 6 zadań → kolejka pusta →
**wsad 2 zaplanowany** (`plan_batches=2`, kolejka = 6 świeżych zadań) →
pierwsze zadanie wykonane → granica zadań → kadencja dojrzała → **przegląd
startuje z 5 świeżymi zadaniami w kolejce**.

Przy `replan=true` [orchestrate.py:476-482](../forge/orchestrate.py#L476-L482)
czyści `state.task_queue` w całości. Ginie 5 z 6 właśnie zaplanowanych
zadań, czyli praktycznie całe wywołanie planisty (~520 k tokenów wejścia).
Tytuły wracają do planisty w notatce przeglądu, więc informacja nie przepada
— ale tokeny i pliki opisów tak.

To dotyczy dzisiejszej konfiguracji, niezależnie od P6. **Podniesienie wsadu
bez tej poprawki mnoży stratę.**

## Design

### Poprawka kolejności

[`_steering_trigger`](../forge/orchestrate.py#L319-L333), gałąź kadencji
dostaje warunek pustej kolejki:

```python
if (not state.task_queue
        and state.plan_batches - state.steered_at_batch >= cfg.steering_batches):
    return "cadence"
```

Gałęzie `brief` i `backlog` **bez zmian** — tam natychmiastowość jest
celowa. Zmiana briefu to najmocniejsze wejście, jakie przegląd może dostać,
i ma wygrywać z kolejką; `steering_due` jest ustawiane właśnie przy pustym
backlogu ([orchestrate.py:1247](../forge/orchestrate.py#L1247)).

Ponieważ wyzwalacz sprawdzany jest **przed** blokiem planowania
([orchestrate.py:1229](../forge/orchestrate.py#L1229) vs
[orchestrate.py:1236](../forge/orchestrate.py#L1236)), przegląd trafia
dokładnie w moment pustej kolejki. `replan` nie ma czego zniszczyć.

Efekt uboczny na plus: notatka przeglądu
([`_write_steering_note`](../forge/orchestrate.py#L477)) jest jednorazowym
wejściem do planowania — po poprawce ląduje bezpośrednio przed
planowaniem, zamiast czekać, aż dopracuje się reszta wsadu.

Kadencja liczy się identycznie. Zmienia się wyłącznie chwila wyzwolenia.

### Wsad i kadencja

`batch_size` 6 → 8 ([config.py:170](../forge/config.py#L170)).

`steering_batches` — decyzja wymaga świadomego odejścia od reguły
„iloczyn ~12" z [config.py:174-179](../forge/config.py#L174-L179):

| `steering_batches` | przegląd co | vs dziś (12) |
|---|---:|---|
| 1 | 8 zadań | ciaśniej, +50% wywołań przeglądu |
| **2** | **16 zadań** | luźniej, tyle samo wywołań przeglądu |

**Rekomendacja: zostawić 2 (=16).** Reguła „~12" była kalibrowana **pod
opisanym wyżej błędem**, gdzie przegląd trafiający w pełną kolejkę kosztował
cały wsad planisty. Po poprawce jedynym kosztem luźniejszej kadencji jest
opóźniona korekta kursu, a przegląd zawsze ląduje na granicy wsadu z pustą
kolejką — w najbezpieczniejszym możliwym momencie. Przegląd kierunku chodzi
przy tym na poziomie `max`
([config.py:28-30](../forge/config.py#L28-L30)), więc zejście do 1 kupuje
korektę kursu za +50% wywołań najdroższej roli w systemie — a to zjadłoby
oszczędność, dla której podnosimy wsad.

Wyzwalacz odwrotu: wzrost odsetka przeglądów z `replan=true` albo
zauważalny dryf kierunku → `FORGE_STEERING_BATCHES=1`.

### Bilans wsadu 6 → 8

Koszt planisty jest w 64% stały (czyta repo od zera). Przy jego ~40%
udziale w rachunku Claude'a:

| wsad | wywołań planisty / zadanie | oszczędność vs 6 |
|---|---:|---:|
| 6 | 0,167 | — |
| **8** | **0,125** | **~25% kosztu planisty ≈ ~10% rachunku** |
| 10 | 0,100 | ~40% ≈ ~16% rachunku |

Zysku zegara nie ma — to zmiana czysto kosztowa. 10 dopiero po pomiarze
odsiewu (W2) i `round_limit`.

## Taski

**T4.1 — poprawka kolejności kadencji.**
[orchestrate.py:329-331](../forge/orchestrate.py#L329-L331): warunek
`not state.task_queue` w gałęzi `cadence`. Komentarz musi wyjaśniać
**dlaczego** — inaczej ktoś to za pół roku „uprości" z powrotem. ~4 linie.

**T4.2 — testy kolejności.**
`tests/test_orchestrate.py`:
- kadencja dojrzała + **niepusta** kolejka → `_steering_trigger` zwraca `""`;
- kadencja dojrzała + pusta kolejka → `"cadence"`;
- zmiana briefu + niepusta kolejka → `"brief"` (regresja: gałąź niezmieniona);
- `steering_due` + niepusta kolejka → `"backlog"` (regresja);
- test scenariuszowy: dwa wsady, przegląd wypada na granicy, `task_queue`
  pusta w chwili `replan` → zero utraconych zadań.

**T4.3 — `batch_size` 6 → 8.**
[config.py:170](../forge/config.py#L170) + aktualizacja komentarza nad
pokrętłem (dziś mówi „Stąd 6, nie 8" — po zmianie kłamie). ~6 linii.

**T4.4 — komentarz przy `steering_batches`.**
[config.py:174-179](../forge/config.py#L174-L179): zapisz, że iloczyn to
teraz 16, i **dlaczego** reguła ~12 przestała obowiązywać (poprawka T4.1).
Bez tego następna osoba zobaczy sprzeczność z komentarzem. ~5 linii.

**T4.5 — przebieg pomiarowy.**
Po W1, W2 i T4.1. Porównanie z baselinem: `$/zadanie` w dół o ~10%,
`rundy/zadanie` bez zmian, odsiew planisty bez wzrostu.

**Wycofanie:** `FORGE_BATCH_SIZE=6`. Poprawka T4.1 zostaje — jest naprawą
błędu, nie pokrętłem, i nie ma powodu jej cofać.

---

# W5. Mapa kryteriów i szersza pierwsza bramka

## Wariant

Dokument źródłowy proponuje wersję maksymalną: pierwsza czerwona bramka
pokrywa **wszystkie** kryteria akceptacji. Wdrażamy wariant umiarkowany —
niemal ten sam zysk przy istotnie mniejszym ryzyku. Uzasadnienie:

**Reguła „jeden test na cykl" nie obowiązuje w tej pętli.** Klasyczne TDD
trzyma ją z dwóch powodów i żaden tu nie działa: (a) lokalizacja defektu —
koder dostaje pełne wyjście komendy, więc trzy nazwane asercje lokalizują
lepiej niż jedna; (b) latencja pętli — człowiek chce zielono co 2 minuty,
a tutaj jedna runda to ~200 k tokenów testera + ~307 k kodera. **Runda jest
jednostką kosztu, nie test.**

**Dane:** 17 z 48 decyzji `red` padło w turze potwierdzającej — tester wraca
po zielonym i dopiero wtedy czyta kolejne kryterium akceptacji. Mechanizm
jest wprost w promptach:
[tester-normal.md](../forge/prompts/templates/tester-normal.md) żąda
„minimalnego czerwonego testu", a
[tester-confirmation.md](../forge/prompts/templates/tester-confirmation.md)
w kroku 2 każe „sprawdzić, czy pozostały nieprzetestowane kryteria". Razem:
liczba rund ≈ liczba kryteriów akceptacji, a nie liczba realnych trudności.

**Ryzyko jest po stronie zielonej, nie czerwonej.** LLM napisze trzy testy
równie łatwo co jeden. Problem: koder musi zaspokoić N asercji w jednej
turze → większy diff → gorsza lokalizacja przy porażce → więcej
`test_changes_needed` / `tester_input_needed` (dziś 6 + 8 na 61 wywołań
kodera). To jest bezpośredni wskaźnik „bramka za szeroka" i **brakuje go w
metrykach kontrolnych dokumentu źródłowego**.

**Ryzyko, które ta zmiana zmniejsza:** przekroczenie `max_tdd_rounds=10`
woła `_fail_task`, a ten robi `git reset --hard`
([orchestrate.py:814](../forge/orchestrate.py#L814)) — cała praca nad
zadaniem przepada. Mniej rund to mniejsze `p(porażki)` przy niezmienionym
koszcie zadania. W5 działa więc **przeciwnie** do asymetrii, którą pogłębia
P7.

## Design

### Trzy zmiany w treści kontraktu, zero w kodzie

**1. Mapa kryteriów w pierwszej turze `red`.**
Tester wylicza kryteria akceptacji z pliku zadania, zanim napisze bramkę.
Nie jako nowe pole JSON-a — jako część `reason` i wpisu do notatnika. Powód:
nowe pole schematu dotknęłoby `parse_tester_decision` i schemat JSON, przez
co wycofanie przestałoby być rewertem dwóch szablonów.

**2. Bramka pokrywa 2–3 kryteria, nie jedno i nie wszystkie.**
Preferowaną formą jest parametryzacja — `tester-normal.md` już tego wymaga
(„preferuj rozszerzenie lub parametryzację istniejącej bramki"), więc to
wzmocnienie istniejącej reguły, nie nowa. Kryteria świadomie odłożone
tester wymienia jawnie z powodem.

**3. Tura potwierdzająca rozszerza tę samą bramkę w bieżącej rundzie.**
Zamiast otwierać nowy cykl `red`. Krok 2 w
[tester-confirmation.md](../forge/prompts/templates/tester-confirmation.md)
zmienia sens z „sprawdź, czy zostały kryteria" na „rozszerz bramkę o
pozostałe kryteria w tej rundzie, chyba że wymagają osobnego cyklu — wtedy
podaj powód".

### Warunek, którego nie wolno rozluźnić

`tester-normal.md` wymaga, żeby test padał **na asercji kontraktu, a nie na
błędzie składni / importu / nazwy**. Przy trzech testach jeden może paść z
niewłaściwego powodu i tester może tego nie zauważyć. Kontrakt musi więc
mówić wprost: **każdy** test w bramce kolekcjonuje się i pada na asercji.
Bez tego zmiana obniża jakość czerwonej bramki, a to jest dokładnie ta
rzecz, której pilnuje cała pętla.

### Czego nie ruszamy

- kontrakt kodera (`test_changes_needed` / `tester_input_needed`) — jest
  poprawny i staje się ważniejszy, nie mniej ważny;
- `max_tdd_rounds` — zostaje 10; celem jest zejście z rund, nie podniesienie
  sufitu;
- definicja `green` — bramka jest zielona, gdy zielona jest komenda podana
  przez testera; szersza bramka nie zmienia tej reguły;
- długość promptów — dokument źródłowy słusznie zauważa, że prompty ról
  mieszczą się w kilkuset tokenach i nie ma tam czego ciąć. W5 zmienia
  **treść kontraktu**, nie jego długość.

## Taski

**T5.1 — `tester-normal.md`: mapa kryteriów i szerokość bramki.**
Dopisz wyliczenie kryteriów przed napisaniem bramki, cel 2–3 kryteriów na
bramkę, jawne wymienienie odłożonych z powodem, oraz wzmocnienie warunku „na
asercji kontraktu" na *każdy* test w bramce. ~8 linii promptu.

**T5.2 — `tester-confirmation.md`: rozszerzanie zamiast nowego cyklu.**
Przepisz krok 2. ~4 linie promptu.

**T5.3 — metryka pushbacku kodera w raporcie.**
`forge/report.py`: odsetek `test_changes_needed` + `tester_input_needed` na
wywołanie kodera, liczony z dziennika. To jedyna metryka kontrolna W5, której
dziś nie ma, i jedyna, która wykryje „bramka za szeroka". ~20 linii.

**T5.4 — przebieg porównawczy.**
Ten sam brief, ten sam stan wyjściowy repo, przebieg przed i po. Jako jedyny
strumień W5 wymaga porównania dwóch przebiegów — stąd ostatnie miejsce w
kolejce.

**Bramki akceptacji** (wszystkie muszą przejść):

| metryka | warunek |
|---|---|
| `rundy/zadanie` | **musi spaść** (baseline ~2,63) |
| `$/zadanie` | musi spaść |
| `recenzja→changes` | nie może wzrosnąć (baseline 7/35) |
| `blocked` i `PORZUCONE` | zero → zero |
| pushback kodera (T5.3) | nie może wzrosnąć (baseline 14/61 ≈ 23%) |

Naruszenie któregokolwiek → wycofanie.

**Wycofanie:** `git revert` dwóch plików szablonów. Bez migracji stanu, bez
zmian w `State`, bez zmian w kodzie pętli.

---

# Kolejność i bramki decyzyjne

```
W1 (metryka)  ──┬──> W2 (odsiew)  ──> W4 (kadencja, potem wsad 8)
                │
                └──> W3 (bramka mistrza: shadow → on)

                                     W5 (kryteria) — na końcu
```

| krok | co | bramka przejścia dalej |
|---|---|---|
| 1 | W1 + baseline z 26.07 | raport liczy `$` bez `—` dla ról produkcyjnych |
| 2 | W2 | odsiew widoczny w logu, dzienniku i raporcie |
| 3 | W3 w trybie cieni, jeden przebieg | **zero** przypadków `trigger="" ∧ nota=TAK` |
| 4 | W3 `on` + T4.1 (kadencja) | testy kolejności zielone |
| 5 | W4 wsad 8, przebieg pomiarowy | `$/zadanie` ↓, `rundy/zadanie` bez zmian, odsiew bez wzrostu |
| 6 | W5, przebieg porównawczy | pięć bramek akceptacji z T5.4 |

Uzasadnienie kolejności: W1 jest warunkiem wstępnym wszystkiego. W2 jest
tanie i odblokowuje W4 (bez widocznego odsiewu nie wiadomo, czy wsad 8 się
domyka). W3 jest niezależne i może iść równolegle, ale jego tryb cieni
zajmuje cały przebieg, więc startuje wcześnie. W4 to naprawa błędu, po
której pokrętło wsadu robi się bezpieczne. W5 na końcu, bo jako jedyne
wymaga porównania dwóch przebiegów, a przy pozostałych zmianach już
wdrożonych baseline jest stabilny.

# Poprawki do dokumentu źródłowego

Do naniesienia w [PRZYSPIESZENIE-DEVELOPMENTU.md](PRZYSPIESZENIE-DEVELOPMENTU.md):

1. **§1a** — `$/linia` → `$/zadanie`, `$/rundę`; usunąć `git log --numstat`;
   dopisać tabelę stawek i zastrzeżenie o nieporównywalności przy P7.
2. **Nagłówek dokumentu i tabela kolejności** — warunek „bez wzrostu kosztu
   na linię kodu" → „na zadanie".
3. **§P3** — usunąć zdanie o kwocie blokującej review na dwie godziny;
   wstawić tabelę udziałów z W3 i przenieść argument z limitu do P2.
4. **§P5** — oznaczyć jako odrzucone, z powodem (zysk ~2–3%, nie 8–12%;
   ta liczba zawierała prefetch planisty).
5. **§P6 warunek 2** — poprawić twierdzenie o „prawie pustej kolejce" na
   opis błędu kolejności i jego naprawy; zaktualizować rekomendację na
   „wsad 8, `steering_batches=2`, iloczyn 16" z uzasadnieniem, dlaczego
   reguła ~12 przestała obowiązywać.
6. **§P4** — dopisać wariant umiarkowany jako wdrażany i dołożyć pushback
   kodera do metryk kontrolnych.
7. **§Czego nie ruszać** — dopisać `compact_tail` ochronę `pliki=` jako
   własność, od której zależy teraz bramka mistrza (W3).

# Ryzyka

| ryzyko | strumień | ograniczenie |
|---|---|---|
| bramka wycisza mistrza tam, gdzie by zareagował | W3 | tryb cieni jako obowiązkowy etap; wyzwalacz odwrotu = jedna zmienna |
| heurystyka „plik testowy" nie łapie konwencji projektu | W3 | wykryje to tryb cieni; heurystyka w osobnej funkcji, łatwa do rozszerzenia |
| luźniejsza kadencja (16 zadań) opóźnia korektę kursu | W4 | monitorowanie odsetka `replan=true`; odwrót przez `FORGE_STEERING_BATCHES=1` |
| planista nie domyka wsadu 8 | W4 | W2 czyni to widocznym **przed** podniesieniem wsadu |
| szersza bramka zwiększa pushback kodera | W5 | T5.3 mierzy to wprost; bramka akceptacji blokuje wdrożenie |
| błędna semantyka tokenów daje błędne dolary | W1 | T1.1 rozdziela providerów; T1.5 testuje obie ścieżki |
| brak stawki dla modelu przekłamuje `$/zadanie` w dół | W1 | `—` zamiast 0 plus ostrzeżenie na przebieg |
