# Plan 2: hartowanie nowego modelu (co zrobione bez „żywego pacjenta", co dalej)

Data: 2026-07-15. Kontynuacja `ANALIZA-TOKENY-I-NOWY-MODEL.md` — model mikro-TDD
jest zaimplementowany; ten dokument zbiera ustalenia z riserczu, prace wykonane
offline, procedurę pierwszego żywego testu i plan kolejnych etapów.

---

## 1. Ustalenia z riserczu (zamiast testu na żywo)

### 1.1. Format integracji z Codex CLI — POTWIERDZONY dokumentacją

Największe ryzyko implementacji (parser strumienia `codex exec --json`) dało się
zdjąć bez uruchamiania Codeksa — format jest udokumentowany:

- start wątku: `{"type":"thread.started","thread_id":"<uuid>"}` — **nie**
  `session_id`, jak zakładała pierwsza wersja parsera (naprawione, patrz 2.1);
- zużycie tokenów: `{"type":"turn.completed","usage":{"input_tokens":N,`
  `"cached_input_tokens":N,"output_tokens":N}}`;
- błędy: `{"type":"turn.failed","error":{"message":...}}` oraz stream-level
  `{"type":"error","message":...}` (nasze wykrywanie limitów działa na surowym
  tekście wyjścia, więc łapie oba);
- wznowienie: `codex exec resume <THREAD_ID> "<prompt>"` (jest też `--last`,
  ale przy dwóch przeplatających się sesjach używamy wyłącznie jawnych id);
- sesje trwają na dysku w `~/.codex/sessions/` (JSONL) — przetrwają restart
  maszyny, ale nie przeniesienie na inną.

Źródła: [event cheatsheet](https://takopi.dev/reference/runners/codex/exec-json-cheatsheet/),
[dokumentacja trybu nieinteraktywnego](https://learn.chatgpt.com/docs/non-interactive-mode),
[issue #3817 (resume w exec)](https://github.com/openai/codex/issues/3817).

Bonus znaleziony przy okazji: **`--output-schema <plik>`** — Codex może zwrócić
finalną wiadomość jako JSON zgodny z podanym schematem. To kandydat na
zastąpienie naszego kruchego wyłuskiwania bloków ```json (etap E2).

### 1.2. Dobre praktyki długotrwałych pętli agentowych → mapowanie na forge

Z lektury materiałów o "loop engineering" i długobieżnych agentach
([Osmani: Long-running agents](https://addyosmani.com/blog/long-running-agents/),
[Osmani: Code Agent Orchestra](https://addyosmani.com/blog/code-agent-orchestra/),
[OpenAI: long-horizon tasks](https://developers.openai.com/blog/run-long-horizon-tasks-with-codex/),
[loop engineering guide](https://lushbinary.com/blog/loop-engineering-ai-coding-agents-guide/)):

| Praktyka z terenu | Stan w forge |
|---|---|
| „Weryfikacja, nie generacja, jest wąskim gardłem" — bramki mechaniczne poza modelem | ✅ bramka czerwona/zielona, kontrola diffu, mapa kryteriów DONE, anty-osłabianie |
| Pamięć w plikach repo, nie w kontekście | ✅ docs/, BACKLOG, .forge/tasks/ |
| Świeży kontekst per jednostka pracy + czyste podsumowanie między sesjami | ✅ reset sesji po zadaniu; ✅ dziennik zadania (nowe, patrz 2.4) |
| Jawny plik planu + jawny plik postępu + strukturalne przekazania | ✅ task-NNN.md; ✅ task_journal.md (nowe) |
| Rozdziel generowanie od oceny (osobne konteksty) | ✅ tester ≠ koder; recenzja ≠ autor kodu |
| Pętla „nie pozwala agentowi ogłosić zwycięstwa" — twardy warunek stopu | ✅ DONE wymaga mapy kryterium→test + zielonej bramki |
| **Okresowy re-plan / refocus** — dryf pętli koryguje się świeżym planowaniem co N jednostek | ⚠️ częściowo (plan wsadowy wraca co ~5 zadań); do wzmocnienia: audyt/refocus co K batchy (etap E3) |
| Supervisor-topologia (planista deleguje wąskie zadania w dół) | ✅ Claude → para Codeksów |

Wniosek: architektura jest zgodna z tym, co w 2026 uchodzi za stan sztuki dla
takich pętli; realne braki to okresowy refocus (E3) i twardsze strukturalne
werdykty (E2).

---

## 2. Zrobione teraz, offline (commit tej serii)

### 2.1. Parser pod potwierdzony format ✔
`extract_session_id` rozumie `thread.started`/`thread_id` (+ stare `session_id`
i regex-fallback). `extract_codex_usage` sumuje `usage` ze zdarzeń
`turn.completed` (wiele tur = suma), z generycznym fallbackiem dla starszych
formatów. Testy na dokładnych kształtach z dokumentacji.

### 2.2. Raport zużycia ✔
`python3 -m forge.report [projekt]` — agregacja `.forge/usage.jsonl` per
(agent, grupa faz: plan / micro-test / micro-code / review / review-fix) z sumami
wejście / z-cache / wyjście. Orkiestrator drukuje to samo podsumowanie na końcu
każdego biegu. To zamyka „Etap 0 — pomiar" z analizy.

### 2.3. Bramka anty-osłabiania na snapshocie ✔
Pełna wersja z planu (wcześniej uproszczona): gdy koder DEKLARUJE zmiany w
testach, orkiestrator kopiuje ich bieżące wersje do tymczasowego `git worktree`
na HEAD (kod sprzed cyklu) i odpala bramkę. Zielona = testy przechodzą bez
implementacji = rozwodnione → przywrócenie wersji testera (ze snapshotu cyklu,
nie z HEAD — nowe testy cyklu nie mają wersji w HEAD). Czysto mechaniczne,
zero tokenów.

### 2.4. Dziennik zadania + odzyskiwanie sesji ✔
`.forge/task_journal.md`: orkiestrator dopisuje po każdej fazie jedną linię
(cykl, rola, werdykt). Gdy `codex exec resume` zgłosi utratę sesji (wzorce
„thread/session not found" itp.), rola dostaje świeżą sesję z ogonem dziennika
jako preambułą — zamiast restartu zadania od taga. Inne błędy agenta NIE są
połykane.

### 2.5. Żywy test dymny (gotowy do odpalenia) ✔
`forge/smoke.py` — patrz sekcja 3.

Testy: **58 przechodzi** (`python3 -m unittest discover`), w tym bramka
anty-osłabiania na prawdziwym repo git i fallback sesji na mockach.

---

## 3. Procedura pierwszego żywego testu (jak będziesz miał dostęp)

Krok po kroku, od zera tokenów do pierwszego zadania:

```bash
# 0) zero tokenów — binarki, wersje, wsparcie `codex exec resume`:
python3 -m forge.smoke --dry

# 1) dwa najtańsze wywołania Codeksa (effort minimal, katalog tymczasowy):
python3 -m forge.smoke
#    sprawdza: PONG w odpowiedzi, przechwycenie thread_id, ciągłość kontekstu
#    po resume, zapis usage.jsonl. Przy FAIL kroku „id wątku" skrypt wypisze
#    pierwsze linie JSONL — wklej je, dopasuję parser.

# 2) pierwszy mini-bieg pętli (1 zadanie, mały batch):
python3 -m forge.orchestrate --non-interactive --max-iters 1 --batch-size 2

# 3) obejrzyj:
#    - STATE.json: tester_session/coder_session niepuste?
#    - .forge/task_journal.md: wpisy cykli?
#    - git log: commity `tdd: ... (cykl N)`?
#    - podsumowanie tokenów na końcu biegu (albo: python3 -m forge.report game)
```

Środowiskowe pokrętła smoke: `FORGE_SMOKE_SANDBOX` (domyślnie workspace-write),
`FORGE_SMOKE_EFFORT` (minimal), `FORGE_SMOKE_TIMEOUT` (300 s).

**Kryteria sukcesu smoke:** wszystkie kroki PASS. Najbardziej prawdopodobne
porażki i co robić:
- *id wątku nieprzechwycone* → format JSONL inny niż udokumentowany; skrypt
  wypisuje surowe linie — poprawka w `agents.extract_session_id`;
- *resume nie pamięta PONG* → wersja CLI bez `exec resume` (krok --dry to
  wyłapie) albo sesje czyszczone; fallback dziennika i tak zadziała w pętli,
  ale warto zaktualizować Codeksa;
- *usage.jsonl pusty* → zdarzenia bez `turn.completed`; nieblokujące (pomiar
  jest best-effort), poprawka w `extract_codex_usage`.

---

## 4. Plan dalszych etapów

**E1 — kalibracja po smoke (wymaga dostępu).** Mini-bieg 5–10 zadań na nowym
modelu (opcjonalnie ten sam brief na `--legacy` dla porównania) → raport
tokenów rozstrzyga: effort testera (minimal/low?), recenzja w sesji ciągłej vs
świeżej, rozmiar batcha. Czysta zmiana konfiguracji, zero kodu.

**E2 — strukturalne werdykty przez `--output-schema`.** Zamiast wyłuskiwać
ostatni blok ```json z tekstu, podać Codeksowi schemat werdyktu per rola
(wrote_test/no_test/done; made_green+test_changes; verdict+notes). Usuwa całą
klasę błędów „agent nie zwrócił poprawnego JSON-a". Wymaga jednej weryfikacji
na żywo (czy `--output-schema` współgra z `exec resume` i `-o`). Szkic:
pole `Config.codex_output_schema_dir`, generowanie plików schematów z
`prompts.py`, fallback na obecny parser.

**E3 — okresowy refocus (anty-dryf).** Zgodnie z praktyką „periodic replanning":
co K batchy (start: K=3) planista dostaje rozszerzony prompt audytu — przegląd
ostatnich commitów, weryfikacja że BACKLOG nadal prowadzi do MVP, aktualizacja
DESIGN.md, ewentualne zadanie refaktoryzacyjne. To jest też odpowiedź na otwarte
pytanie nr 1 z analizy (audyt Claude'a) — wpięta w istniejącą fazę planowania,
więc bez nowych wywołań poza nieco droższym co-K-tym planem.

**E4 — recenzja w świeżej sesji (decyzja po danych z E1).** Jeśli raport pokaże,
że recenzja w ciągłej sesji jest pobłażliwa (mało `changes`, dużo problemów
wyłapywanych dopiero przez człowieka), przełączyć `review_task_prompt` na świeżą
sesję z `git diff <tag>..HEAD` jako wejściem. Jedno-liniowa zmiana w
`_run_review_loop` (nie podawać session_id) + prompt bez założenia o kontekście.

**E5 — równoległość między zadaniami (worktree pipelining).** Tylko jeśli po
E1–E4 wąskim gardłem będzie czas zegarowy, nie tokeny. Wymaga kolejki z
niezależnymi zadaniami (planista musiałby oznaczać zależności) i osobnych
worktree per rola. Świadomie ostatnie — złożoność > zysk, dopóki limity
subskrypcji są ciaśniejsze niż doba.

**Checklist przed pierwszym długim biegiem (nocnym):**
- [ ] smoke PASS (sekcja 3),
- [ ] mini-bieg 1–2 zadań obejrzany ręcznie (jakość testów testera!),
- [ ] `FORGE_GIT_PUSH` ustawione świadomie (push po zadaniu, nie po cyklu),
- [ ] limity: `--batch-size`, `--max-micro-cycles` (12), `FORGE_MAX_GREEN_RETRIES` (2),
- [ ] plik `STOP` jako wyłącznik awaryjny działa (utwórz w katalogu projektu).

---

## 5. Ryzyka, które zostają (świadomie)

1. **Jakość testów testera** to nadal najsłabsze ogniwo — bramki wymuszają
   *istnienie* failującego testu, nie jego *sens*. Backstopy: recenzja d),
   audyt E3, Twoje oko na mini-biegu.
2. **Dryf dwóch sesji** w obrębie długiego zadania (tester i koder mogą
   rozjechać się w rozumieniu kontraktu) — mitygacja: kontrakt API w pliku
   zadania jest jedynym źródłem prawdy, a limit mikro-cykli ogranicza długość
   dryfu.
3. **Zmiany formatu JSONL w przyszłych wersjach Codeksa** — parser ma fallbacki
   (regex, generyczny skan usage), a smoke wykryje niezgodność za darmo przy
   aktualizacji CLI.
