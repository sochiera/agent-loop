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
**za drobne względem kosztu stałego** — ale nie względem ryzyka fix-loopów.

Model docelowy z sekcji 3 rozwiązuje ten dylemat **z obu stron naraz**:
planowanie wsadowe (1 drogie wywołanie na ~5 zadań) tnie koszt stały zadania, a ciągły
kontekst sesji w obrębie zadania tnie koszt stały *pojedynczego kroku* — dzięki czemu
kroki mogą być bardzo małe (jeden test na cykl) bez płacenia za każde wywołanie pełnego
zimnego startu.

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

Uwaga: pozycje 1–2 z tej tabeli dotyczą starej pętli; jeśli idziemy prosto w model
docelowy (sekcja 3), review-przy-czerwonym znika razem z całą fazą REVIEW Claude'a.
Pomiar (usage.jsonl), archiwizacja BACKLOG-u i bogatszy `failures.md` obowiązują
w obu modelach.

---

## 3. Model docelowy: mikro-TDD ping-pong z ciągłym kontekstem per zadanie

Podział pracy: **Claude planuje wsadowo** (5 zadań w przód), a każde zadanie realizuje
**para instancji Codeksa** w pętli mikro-TDD — Codex-1 (tester) dyktuje po jednym
failującym teście, Codex-2 (koder) doprowadza do zieleni i refaktoryzuje. Obie instancje
trzymają **ciągły kontekst w obrębie zadania** i startują od zera przy następnym.

### 3.1. Przebieg pętli

```
a) PLAN (Claude, opus/high, 1 wywołanie)
   → 5 zadań w przód, każde jako .forge/task-NNN.md (kryteria akceptacji!)

   dla każdego zadania po kolei (świeże sesje Codex-1 i Codex-2):
   ┌→ b) Codex-1 (tester): czego jeszcze brakuje względem kryteriów?
   │     → pisze JEDEN test, który ma failować
   │     → albo deklaruje "brak sensownego testu" (dozwolone, jawne) → c) bez testu
   │        [bramka czerwona orkiestratora: nowy test failuje, reszta zielona]
   │  c) Codex-2 (koder): kod → pełny pakiet zielony → refaktor pod zielonymi
   │        [bramka zielona orkiestratora; commit mikro-cyklu]
   └─ powtarzaj, aż Codex-1 orzeknie DONE:
      wszystkie testy zielone ORAZ żadnej funkcjonalności zadania nie brakuje

d) Codex-1: review całości zadania (zwłaszcza kodu Codeksa-2)
   → Codex-2: naprawia wszystkie uwagi → bramka zielona → commit końcowy
e) następne zadanie z kolejki; kolejka pusta → wróć do a)
```

Co ten układ daje względem dzisiejszej pętli:

- **Claude znika z pętli wykonawczej.** Drogi model odpala się raz na ~5 zadań,
  zamiast 2–5 razy na *każde* zadanie. To największa pojedyncza oszczędność.
- **Prawdziwy rytm TDD** (red → green → refactor po jednym teście), zamiast
  „napisz wszystkie testy, potem cały kod" — mniejsze kroki, mniejsze ryzyko
  rozjazdu, refaktor wbudowany w każdy cykl, a nie doklejony.
- **Naturalny podział ról**: tester nie widzi implementacji zanim napisze test,
  koder dostaje test jako specyfikację wykonywalną.
- **Review robi Codex-1, nie Claude** — jest inny niż autor kodu (cross-check
  zostaje), a kontekst zadania ma już załadowany, więc review jest tanie.

### 3.2. Ciągły kontekst per zadanie — mechanika

Obecny `run_codex` jest one-shotem. Potrzebne są **sesje wznawialne**:

- Pierwsze wywołanie roli w zadaniu: `codex exec ...` z przechwyceniem **session id**
  (z `codex exec --json` — zdarzenie startu sesji; nie polegać na `resume --last`,
  bo w pętli żyją DWIE przeplatające się sesje). Kolejne kroki tej roli:
  `codex exec resume <session_id> <prompt>`.
- Session id obu ról trzymane w `STATE.json` → po restarcie orkiestratora sesje da
  się wznowić (żyją na dysku w `~/.codex/sessions`). Jeśli wznowienie się nie uda,
  zadanie restartuje od tagu początkowego — commit po każdym mikro-cyklu (3.5)
  ogranicza stratę do bieżącego cyklu.
- Claude: bez sesji — robi tylko a), każde planowanie jest świeże (i tak musi
  przeczytać aktualny stan repo).
- Fallback, gdyby `codex exec resume` zawodził: wspólny dziennik zadania
  `.forge/task_journal.md` (orkiestrator dopisuje po każdej fazie jedno-dwa zdania +
  ogon testów), doklejany do promptów zamiast sesji. Gorsze, ale działa wszędzie.

Skutki tokenowe ciągłego kontekstu:

- **(+)** koniec z zimnym startem co fazę: pliki przeczytane raz zostają w kontekście,
  kolejne kroki płacą tylko za przyrost (a cache promptu tnie koszt powtarzanego
  prefiksu);
- **(+)** dzięki temu kroki mogą być mikroskopijne (jeden test) bez podatku od wywołania —
  to rozwiązuje dylemat z 2.2 od drugiej strony: zamiast powiększać zadania, taniejemy
  na kroku;
- **(−)** kontekst sesji rośnie z każdym cyklem — limit mikro-cykli (3.3) ogranicza
  jednocześnie budżet i rozmiar sesji; reset po zadaniu (wymóg modelu) zapobiega
  nieograniczonemu wzrostowi;
- **(−)** dwie żywe sesje na tym samym repo: w tej pętli b) i c) są ściśle sekwencyjne,
  więc nie ma konfliktów — ale NIE wolno ich zrównoleglać bez osobnych worktree.

### 3.3. Bramki orkiestratora (mechaniczne, zero tokenów)

Podziału ról **nie pilnuje prompt, tylko orkiestrator**:

- **Bramka czerwona po b):** nowy test musi failować (z powodu braku implementacji,
  nie błędu składni), a dotychczasowy pakiet pozostać zielony. Kontrola diffu:
  tester zmienia wyłącznie ścieżki testów z zadania.
- **„Brak sensownego testu"** to legalne wyjście z b), ale musi być jawne w werdykcie
  JSON z uzasadnieniem (`{"no_test": true, "reason": ...}`). Orkiestrator je liczy:
  taki cykl jest chroniony tylko regresyjnie (stary pakiet zielony), a nadużywanie
  (np. >⅓ cykli zadania) to smell — wcześniejsze wymuszenie review d).
- **Bramka zielona po c):** pełny pakiet. Czerwony → ogon testów wraca do sesji
  Codeksa-2 (tanio — kontekst już jest), do 2 dogrywek w cyklu.
- **Polityka zmian w testach przez kodera** — nie „zakaz", tylko **anty-osłabianie
  specyfikacji**: (1) zmiany niezadeklarowane → checkout + ponowienie; (2) adaptacyjne
  (renamy/importy po refaktorze) zawsze wolno, pod bramką mechaniczną: zmodyfikowany
  test na snapshocie kodu sprzed cyklu musi nadal failować; (3) merytoryczne — z
  deklaracją w JSON; rozstrzyga review d) (w razie sporu w trakcie cyklu: tani
  diff-audyt samych testów).
- **Werdykt DONE Codeksa-1** nie może być gołym „skończone": JSON musi mapować
  **każde kryterium akceptacji z task-NNN.md na test albo uzasadnienie**. Orkiestrator
  sprawdza kompletność mapy mechanicznie (lista kryteriów pochodzi z pliku zadania).
- **Limit mikro-cykli** (np. 10–12) → porażka zadania → wpis do `failures.md`
  z ogonem testów i ostatnimi werdyktami → reset do tagu → planista przy następnym
  a) dzieli zadanie. Limit chroni budżet i rozmiar sesji jednocześnie.

### 3.4. Review d) — uwaga o kotwicy

Review w **ciągłej sesji** Codeksa-1 jest tanie (kontekst załadowany), ale recenzent
widział, jak kod powstawał, i recenzuje m.in. własne testy — będzie zakotwiczony.
Silniejszy wariant: review w **świeżej sesji** Codeksa-1, która dostaje tylko
`git diff <tag-zadania>..HEAD` + plik zadania. Droższe o jeden zimny start na zadanie,
niezależność wyraźnie większa. Decyzja otwarta (sekcja 4); proponuję zacząć od wersji
ciągłej (prostsza, zgodna z założeniem) i porównać jakość po kilkunastu zadaniach —
usage.jsonl da liczby, historia commitów da jakość.

Po review Codex-2 naprawia wszystkie uwagi w swojej sesji; bramka zielona zamyka
zadanie. Spory „test vs kod" rozstrzyga review — osobny arbitraż Claude'a nie jest
potrzebny (Claude wraca do gry dopiero przy planowaniu, gdzie widzi `failures.md`).

### 3.5. Commity, rollback, refaktor duży

- **Tag na starcie zadania** (`forge/task-NNN-start`). **Commit po każdym zielonym
  mikro-cyklu** (test + kod + refaktor razem — każdy commit zielony) — to zachowuje
  dzisiejszą odporność na limity: przerwanie w środku zadania traci najwyżej bieżący
  cykl. Porażka zadania → `git reset --hard <tag>`.
- Po d) commit końcowy; ewentualny squash cykli do jednego commita na zadanie —
  decyzja kosmetyczna (sekcja 4).
- **Makro-refaktor** (duplikacja międzymodułowa, niewidoczna z poziomu jednego
  zadania): obowiązek Claude'a w a) — przy każdym batchu ocenia stan kodu i w razie
  potrzeby wstawia zadanie refaktoryzacyjne do kolejki. Takie zadanie przechodzi
  normalną pętlę b/c z tą różnicą, że b) zwykle deklaruje „brak nowego testu"
  (zachowanie bez zmian), a specyfikacją jest istniejący pakiet.

### 3.6. Zmiany w plikach (mapa implementacji)

| Plik | Zmiana |
|---|---|
| `forge/agents.py` | sesje Codeksa: start + przechwycenie session id (`--json`) + `codex exec resume`; logowanie usage do `.forge/usage.jsonl` |
| `forge/state.py` | kolejka zadań (`task_queue`), session id obu ról, licznik mikro-cykli i „no_test", nowe fazy, tag zadania |
| `forge/prompts.py` | `plan_batch_prompt` (5 zadań + kryteria + ścieżki), `next_test_prompt`, `make_green_and_refactor_prompt`, `task_done_verdict` (mapa kryteriów), `review_task_prompt`, `fix_review_prompt`; stare prompty za flagą legacy |
| `forge/orchestrate.py` | maszyna stanów a–e, bramka czerwona/zielona/anty-osłabiania, kontrola diffu per rola, tag+rollback per zadanie, limity cykli |
| `forge/config.py` | `batch_size`, `max_micro_cycles`, `max_green_retries`, modele/effort per rola (tester/koder), `legacy_mode` |
| `tests/` | bramka czerwona, mapa kryteriów DONE, konsumpcja kolejki, wznawianie sesji po restarcie, kontrola diffu, anty-osłabianie |

### 3.7. Kolejność wdrożenia

1. **Etap 0 — pomiar**: `.forge/usage.jsonl` + podsumowanie na koniec biegu.
   Niezależny od reszty; daje bazę do porównania starej i nowej pętli.
2. **Etap 1 — fundament**: sesje wznawialne w `agents.py` + kolejka zadań w
   `STATE.json` + planowanie wsadowe. Działa jeszcze ze starą pętlą (mniej wywołań
   PLAN od razu).
3. **Etap 2 — pętla mikro-TDD**: fazy b/c z bramkami czerwoną/zieloną, limitem cykli
   i werdyktem DONE; review chwilowo wyłączone. Stary tryb za `legacy_mode`.
4. **Etap 3 — domknięcie**: review d) + naprawa uwag + pełny obieg e→a.
5. **Etap 4 — szlif**: bramka anty-osłabiania, liczniki „no_test", makro-refaktor
   w promptcie planisty, archiwizacja BACKLOG-u.

---

## 4. Ryzyka i otwarte pytania

1. **Sędzia we własnej sprawie.** Codex-1 pisze testy, orzeka DONE **i** robi review —
   ryzyko przedwczesnego DONE i pobłażliwego review. Mitygacje już wbudowane: DONE
   wymaga mechanicznie sprawdzanej mapy kryterium→test, a bramki są poza modelem.
   Do decyzji: czy dodać rzadki audyt Claude'a (np. co 3–4 batche, jedno wywołanie na
   ostatnie N commitów)?
2. **Review: sesja ciągła czy świeża?** Ciągła = taniej, zakotwiczona; świeża = jeden
   zimny start na zadanie, niezależna. Start: ciągła; rewizja po danych z usage.jsonl.
3. **Squash cykli do jednego commita na zadanie?** Mikro-commity dają wznawialność
   i czytelny przebieg TDD; squash daje czystą historię „1 commit = 1 zadanie".
4. **Kalibracja limitów**: `max_micro_cycles` (start: 12), dogrywki zieleni w cyklu
   (start: 2), próg smella „no_test" (start: ⅓ cykli zadania).
5. **Modele/effort ról**: tester i koder na tym samym modelu? Tester bywa prostszą
   pracą (jeden test) — kandydat na niższy effort; ale to on trzyma jakość specyfikacji.
   Rozstrzygnąć pomiarem, nie z góry.
