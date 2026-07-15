# Analiza: zużycie tokenów i nowy model pracy (planista + 2× Codex)

Data: 2026-07-15. Stan repo: commit `c2f392b` (feat: add configurable agents and resumable execution).
Dokument jest analizą i planem — **nie zmienia kodu**.

---

## 1. Jak pętla działa dzisiaj (skrót)

Jedna iteracja = jedno zadanie z backlogu:

| Faza | Agent | Model (domyślnie) | Wywołań na iterację |
|---|---|---|---|
| PLAN | planista (Claude) | opus / effort **high** | 1 |
| IMPLEMENT (TDD) | Codex | z config.toml / medium | 1 |
| REVIEW | planista (Claude) | opus / effort **high** | 1 + po każdym fixie (max 4) |
| FIX | Codex | z config.toml / medium | 0–3 |

Każde wywołanie agenta jest **bezstanowe** — agent od zera czyta `docs/DESIGN.md`,
`docs/ARCHITECTURE.md`, `BACKLOG.md`, `.forge/current_task.md` i kod (tak każe
`SHARED_PRINCIPLES` w `forge/prompts.py`).

Bilans na jedno zadanie:

- **najlepszy przypadek**: 3 wywołania, w tym **2 drogie** (opus/high: plan + review);
- **najgorszy przypadek**: 9 wywołań (plan + implement + 4× review + 3× fix), w tym
  **5 drogich**, a przy braku akceptacji — `rollback` i **100% tych tokenów idzie do kosza**.

---

## 2. Gdzie uciekają tokeny

### 2.1. Brak pomiaru — to problem nr 1

Orkiestrator **nigdzie nie loguje zużycia tokenów**, choć dane są na wyciągnięcie ręki:
`claude --output-format json` zwraca pole `usage` (input/output/cache tokens), a Codex
raportuje zużycie w logach sesji. Obecnie „tokeny schodzą za szybko" to odczucie, nie
pomiar — nie wiadomo, czy winny jest planner (effort high × 2–5 wywołań na zadanie),
re-czytanie rosnących docs, czy pętle fix.

**Wniosek:** zanim cokolwiek zoptymalizujemy, dopisać `.forge/usage.jsonl`
(iteracja, faza, agent, model, effort, tokeny in/out, czas, wynik). Koszt: ~30 linii kodu.

### 2.2. Czy zadania mają optymalną wielkość?

Działają tu dwie przeciwstawne siły:

- **Zadanie za duże** → rundy fix (każda = 1 drogi review + 1 codex), a po 3 nieudanych
  rundach rollback, czyli utrata *całej* iteracji. To najdroższy scenariusz.
- **Zadanie za małe** → koszt stały iteracji (plan + review na opus/high) **dominuje nad
  wartością**. Prompt planisty każe wybrać „najmniejszy sensowny przyrost", więc pętla
  systemowo pcha w stronę wielu mikro-iteracji, z których każda płaci pełny podatek
  planisty.

Obecna konfiguracja płaci **~2 drogie wywołania podatku od każdego zadania, niezależnie
jak małe ono jest**. Przy zadaniach typu „dodaj jedno pole do struktury" podatek planisty
może kosztować więcej niż sama implementacja. Zadania są więc najprawdopodobniej
**za drobne względem kosztu stałego** — ale nie względem ryzyka fix-loopów. Rozwiązaniem
nie jest powiększanie zadań (to podnosi ryzyko rollbacku), tylko **obniżenie kosztu
stałego na zadanie**:

1. **Planowanie wsadowe.** Jedno wywołanie planisty produkuje kolejkę **3–5 zadań**
   (każde jako osobny plik `.forge/task-NNN.md`). Pętla konsumuje kolejkę bez udziału
   planisty; planista wraca dopiero, gdy kolejka pusta albo zadanie padło. To tnie liczbę
   wywołań opus/high w fazie PLAN ~4-krotnie przy tym samym rozmiarze zadań.
2. **Review nie musi być opus/high.** Review to porównanie diffu z kryteriami akceptacji —
   praca dla `sonnet/medium`. Osobne pokrętła `review_model`/`review_effort` w `Config`.

### 2.3. Konkretne przecieki (uszeregowane wg szacowanego zysku)

1. **REVIEW przy czerwonej bramce jest z definicji stracony.** Orkiestrator i tak nie
   zaakceptuje przy czerwonych testach (`verdict=="approve" and state.tests_green`),
   a `review_prompt` wprost mówi recenzentowi, że czerwona bramka to podstawa do
   „changes". Mimo to po każdym nieudanym implement/fix odpala się **pełny opus/high**,
   żeby powiedzieć oczywistość. Fix powinien dostawać **ogon wyjścia testów
   bezpośrednio od orkiestratora** (już jest łapany w `run_tests`), a review odpalać się
   **tylko przy zielonej bramce**. W pesymistycznym scenariuszu to 3 z 5 drogich wywołań.
2. **Monotoniczny wzrost kontekstu wejściowego.** `BACKLOG.md` tylko rośnie (statusy
   zostają), `DESIGN.md` jest „żywy" czyli też rośnie, a **każda faza każdej iteracji**
   czyta je od zera. Koszt wejściowy iteracji rośnie liniowo z wiekiem projektu.
   Mitygacja: planista przy okazji planowania przenosi ukończone pozycje do
   `BACKLOG-ARCHIVE.md` (nieczytany przez nikogo), a `DESIGN.md` trzyma sekcję
   „MVP-aktualne" oddzielnie od historii decyzji.
3. **Podwójne uruchamianie testów.** Prompt każe agentowi odpalić pełny pakiet testów,
   po czym orkiestrator odpala tę samą bramkę jeszcze raz. Czas + tokeny na wyjście
   testów w sesji agenta. Drobne, ale darmowe do naprawienia: agent może odpalać tylko
   testy zadania, pełna bramka należy do orkiestratora.
4. **`fix_attempt` bez eskalacji strategii.** Trzy rundy fix robią to samo w ten sam
   sposób. Po 2. nieudanej rundzie taniej jest przerwać, zapisać w `failures.md`
   *ogon testów + notatki reviewera* (dziś ląduje tam jedna linijka, więc planista musi
   od nowa odkrywać, co poszło nie tak) i pozwolić planiście podzielić zadanie.

### 2.4. Szybkie wygrane — podsumowanie

| Zmiana | Szacowany efekt | Koszt |
|---|---|---|
| Logowanie usage do `.forge/usage.jsonl` | widoczność (warunek reszty) | mały |
| Review tylko przy zielonej bramce; czerwień → od razu FIX z ogonem testów | −30–50% drogich wywołań w złych iteracjach | mały |
| Planowanie wsadowe 3–5 zadań | −60–75% wywołań PLAN | średni |
| Review na tańszym modelu/effort | −50% kosztu pojedynczego review | mały |
| Archiwizacja BACKLOG + dyscyplina DESIGN | hamuje liniowy wzrost inputu | mały |
| Bogatszy zapis w `failures.md` | mniej ponownego odkrywania po rollbacku | mały |

---

## 3. Nowy model: planista + 2 instancje Codeksa (tester ↔ koder)

### 3.1. Cel

Rozdzielenie autorstwa testów i kodu ma dwie zalety:

- **testy przestają być pisane „pod implementację"** — tester zna tylko specyfikację
  zadania (kryteria akceptacji), więc testuje kontrakt, nie własne bugi;
- **testy przejmują rolę recenzenta** — skoro kod musi przejść testy napisane przez
  niezależnego agenta, osobna faza REVIEW robi się w dużej mierze zbędna, co wprost
  adresuje problem tokenowy z sekcji 2.

Ważna obserwacja upraszczająca: ponieważ każde wywołanie agenta jest bezstanowe,
„dwie instancje Codeksa" **nie wymagają żadnej infrastruktury** — to po prostu dwie
role z różnymi promptami (i opcjonalnie różnym modelem/effort). Cała trudność leży
w orkiestracji i egzekwowaniu podziału ról, nie w uruchamianiu.

### 3.2. Warianty architektury

**Wariant A — sekwencyjny ping-pong (rekomendowany na start):**

```
PLAN (Claude)                        — kontrakt zadania + kolejka wsadowa
  └→ TESTY (Codex-tester)            — pisze WYŁĄCZNIE testy do zadania
       └→ BRAMKA CZERWONA            — orkiestrator: nowe testy MUSZĄ failować
            └→ KOD (Codex-koder)     — pisze WYŁĄCZNIE kod, testów nie dotyka
                 └→ BRAMKA ZIELONA   — pełny pakiet musi przejść
                      └→ commit  ↺   (albo arbitraż → patrz 3.5)
```

Zalety: brak konfliktów (jedna kopia repo), prosta maszyna stanów, każda faza ma
twardą, *mechaniczną* bramkę (nie opinię modelu). Wady: brak równoległości — ale
równoległość w obrębie jednego zadania jest w TDD iluzoryczna, bo czerwone testy
muszą istnieć zanim koder zacznie.

**Wariant B — pipelining między zadaniami (rozszerzenie A):**

Prawdziwy zysk z dwóch instancji to praca na **różnych zadaniach jednocześnie**:
tester pisze testy do zadania N+1 (w osobnym `git worktree`), podczas gdy koder
implementuje zadanie N. Wymaga: kolejki zadań (i tak wynika z planowania wsadowego),
`git worktree` per rola, merge testów do głównej kopii po zakończeniu N. Złożoność
zauważalna (konflikty przy merge, wznawialność dwóch równoległych faz w STATE.json),
zysk: ~40–50% czasu zegarowego, **zero oszczędności tokenów**. Robić dopiero, gdy A
działa stabilnie i to czas, nie tokeny, będzie wąskim gardłem.

**Wariant C — równolegle na tym samym zadaniu (odradzam):** tester i koder startują
jednocześnie, koder pisze do interfejsów „w ciemno". W praktyce koder zgaduje API,
które tester właśnie ustala testami → niezgodności → dodatkowe rundy uzgadniania,
czyli *więcej* tokenów. Sprzeczne z celem.

### 3.3. Nowa maszyna stanów (wariant A)

Fazy w `State.phase`: `idle → plan → write_tests → implement → (arbitrate) → idle`.

- **plan** (Claude, opus/high, wsadowo): produkuje `.forge/task-NNN.md` × 3–5. Format
  zadania rozszerzony o sekcję **„Kontrakt API"** (sygnatury/nazwy modułów, które tester
  i koder muszą współdzielić — to jedyny punkt uzgodnienia między nimi) oraz jawne
  **„Ścieżki testów" / „Ścieżki kodu"** (do mechanicznej kontroli diffu).
- **write_tests** (Codex-tester): czyta zadanie + kontrakt API; pisze wyłącznie pliki
  z „Ścieżek testów". Po fazie orkiestrator:
  1. sprawdza diff — zmiany poza ścieżkami testów → odrzucenie fazy (checkout tych plików) i jedno ponowienie;
  2. **bramka czerwona**: pełny pakiet musi failować, a stare testy przechodzić
     (uruchomienie pakietu dwa razy: nowe testy failują *z właściwego powodu*, tzn.
     brak implementacji, nie błąd składni — rozróżnialne po kodzie/komunikacie).
     Jeśli nowe testy od razu przechodzą → zadanie puste albo testy wydmuszki → arbitraż.
- **implement** (Codex-koder): czyta zadanie + kontrakt + **treść testów** (to jest jego
  specyfikacja wykonywalna); pisze wyłącznie poza ścieżkami testów. Orkiestrator:
  kontrola diffu (dotknął testów → checkout plików testowych i jedno ponowienie z
  adnotacją), potem **bramka zielona** na pełnym pakiecie. Czerwona → do 2 rund
  poprawek kodera z ogonem testów (bez udziału drogiego modelu).
- **arbitrate** (Claude, może być sonnet/medium): wchodzi **tylko** gdy koder po
  limicie rund twierdzi, że testy są błędne, albo bramka czerwona wykryła anomalię.
  Werdykt JSON: `{"blame": "tests"|"code"|"task", ...}` → poprawa testów przez
  testera / podział zadania przez planistę / rollback. To jedyne miejsce, gdzie
  dawny „review" przetrwał — i odpala się wyjątkowo, nie co iterację.

Committ i rollback bez zmian koncepcyjnych: commit po zielonej bramce (można commitować
testy i kod osobno — czytelniejsza historia: `test:` + `feat:`), rollback przy porażce.

### 3.4. Egzekwowanie podziału ról

Kluczowe: podziału **nie pilnuje prompt, tylko orkiestrator** (prompty się „nie słuchają"
wystarczająco niezawodnie):

- `git diff --name-only` po każdej fazie, porównanie ze ścieżkami z zadania;
- naruszenie → `git checkout -- <pliki spoza roli>` + jedno ponowienie fazy z ostrą
  adnotacją; drugie naruszenie → porażka fazy;
- bramka czerwona (testy muszą failować przed implementacją) jest mechanicznym
  odpowiednikiem „czy jest TDD?" z obecnego review — i nie kosztuje ani tokena.

Ryzyko modelu: **słabe testy** (tester pisze wydmuszki, koder trywialnie je przechodzi).
Mitygacje: bramka czerwona odsiewa testy-tautologie; kryteria akceptacji w zadaniu
muszą być mierzalne (odpowiedzialność planisty); opcjonalny **audyt co N iteracji**
(planista przegląda ostatnie N commitów jednym wywołaniem — dużo taniej niż review
co iterację).

### 3.5. Zmiany w plikach (mapa implementacji)

| Plik | Zmiana |
|---|---|
| `forge/prompts.py` | nowy `plan_batch_prompt` (kolejka + kontrakt API + ścieżki), `write_tests_prompt`, `implement_against_tests_prompt`, `arbitrate_prompt`; usunięcie `review_prompt`/`fix_prompt` (lub zostawienie za flagą legacy) |
| `forge/config.py` | pokrętła: `tester_model/effort`, `coder_model/effort`, `arbiter_model/effort`, `batch_size`, `legacy_mode` |
| `forge/orchestrate.py` | nowe fazy + bramka czerwona + kontrola diffu per rola; review-tylko-przy-zielonym (jeśli legacy zostaje); logowanie usage |
| `forge/state.py` | kolejka zadań (`task_queue: list[str]`), nowe wartości `phase`, licznik naruszeń ról |
| `forge/agents.py` | wyciągnięcie `usage` z JSON-a Claude'a i (na ile się da) z wyjścia Codeksa → `.forge/usage.jsonl` |
| `tests/` | testy bramki czerwonej, kontroli diffu, konsumpcji kolejki, wznawialności nowych faz |

### 3.6. Kolejność wdrożenia

1. **Etap 0 — pomiar** (niezależny od reszty): `.forge/usage.jsonl` + proste podsumowanie
   na koniec biegu. Daje bazę do porównań przed/po.
2. **Etap 1 — szybkie wygrane w obecnym modelu**: review tylko przy zielonej bramce,
   tańszy model review, bogatszy `failures.md`. Małe diffy, od razu zwracają tokeny.
3. **Etap 2 — planowanie wsadowe + kolejka zadań w STATE.json.** Potrzebne i staremu,
   i nowemu modelowi — naturalny wspólny fundament.
4. **Etap 3 — rozdział tester/koder (wariant A)** z bramką czerwoną i kontrolą diffu;
   stary tryb za flagą `legacy_mode` na czas porównania.
5. **Etap 4 (opcjonalny) — pipelining (wariant B)** przez `git worktree`, tylko jeśli
   po etapie 3 wąskim gardłem okaże się czas zegarowy, nie tokeny.

---

## 4. Otwarte pytania (do decyzji przed etapem 3)

1. **Modele dwóch Codeksów**: ten sam model dla testera i kodera, czy tester na niższym
   effort (testy do małego zadania to zwykle prostsza praca niż implementacja)?
2. **Kontrakt API w zadaniu**: jak szczegółowy? Za luźny → tester i koder rozjadą się na
   nazwach; za sztywny → planista de facto projektuje implementację (drogo). Propozycja
   startowa: tylko publiczne sygnatury modułu, którego dotyczy zadanie.
3. **Commit testów osobno od kodu** (`test:` + `feat:`) czy razem? Osobno daje
   czytelniejszą historię i naturalny punkt wznowienia, ale czerwone testy w historii
   commitów łamią zasadę „każdy commit zielony" — proponuję razem, a testy trzymać
   w indeksie/stash do czasu zielonej bramki.
4. Czy audyt co N iteracji (3.4) ma być od początku, czy dopiero gdy jakość spadnie?
