# Plan: dwa projekty jednocześnie z jednego GUI

> **Stan: wdrożone** (11.08.2026). Co powstało i gdzie — sekcja 7 na końcu.

Cel: jedno okno `python3 -m forge.gui`, dwa (docelowo N) niezależne biegi —
każdy ma własne repozytorium, własny brief i własny proces `forge.orchestrate`.
Routing ról pozostaje wspólny (modele mogą być te same).

Punkt wyjścia: [`AWARIE-2026-08-11.md`](AWARIE-2026-08-11.md). Obie nocne awarie
wzięły się z **współdzielenia zasobu, który nie znosi współdzielenia**. Wersja
z tokenem OAuth (`1ce6a01`) usunęła jedną z nich; ten plan domyka resztę.

---

## 1. Co już jest bezpieczne — i czego nie ruszamy

Zanim cokolwiek zmienimy: większość stanu Forge jest już związana z projektem,
nie z procesem. Nie ma potrzeby tego przebudowywać.

| zasób | zakres | wniosek |
|---|---|---|
| `STATE.json`, `ledger.md`, `usage.jsonl`, `tasks/`, `verification/`, `parked.md`, notatniki | `<projekt>/.forge/` | rozdzielone z definicji |
| surowa telemetria tur | `~/.cache/forge/<nazwa>-<sha256(ścieżka)[:10]>/logs` ([`orchestrate.py:1858`](../forge/orchestrate.py#L1858)) | klucz zawiera ścieżkę projektu — rozdzielone |
| log sesji GUI | `<projekt>/.forge/gui_run.log` | rozdzielone |
| raport zużycia (`report.usage_summary`) | per projekt | rozdzielone |
| dowiązania w izolowanym domu (`_prepare_isolated_home`) | zapis przez `uuid` + `os.replace` | atomowy, odporny na wyścig |
| `routing.json` | czytany RAZ, przy budowie `Config` ([`orchestrate.py:2120`](../forge/orchestrate.py#L2120)) | zmiana pokrętła w trakcie biegu nie wpływa na bieg już uruchomiony |
| operacje git | zawsze `git -C <projekt>` | rozdzielone, dopóki projekty są różne |

Wniosek: **problem jest w warstwie uruchamiającej, nie w logice Forge.**

---

## 2. Co się zderza

### 2.1. GUI umie prowadzić dokładnie jeden bieg

[`ForgeWindow`](../forge/gui.py#L536) trzyma pojedynczy `self.process`,
`self.started_at`, `self.stop_requested`, jeden `log_buffer`, jeden uchwyt
`_session_log_fh`, jedno pole `brief` i jedno `project`. Ustawienia
(`~/.config/forge/gui.json`) mają płaskie klucze `brief`/`project`. To jest
główna praca do wykonania.

### 2.2. Nic nie broni uruchomienia dwóch biegów na TYM SAMYM projekcie

Dziś nie ma żadnej blokady — ani w GUI, ani w orkiestratorze (`grep flock` nie
zwraca nic). Dwa procesy na jednym katalogu to dwa `STATE.json` nadpisujące się
nawzajem, dwa `git reset`/`commit` na jednym drzewie i zniszczony przebieg obu.
Przy panelu z dwoma wierszami wpisanie tej samej ścieżki dwa razy będzie
pomyłką jednego kliknięcia.

### 2.3. Sesja Claude Code w trybie plikowym nadal jest miną

Tryb plikowy działa dalej jako domyślny ([`agents.py:120`](../forge/agents.py#L120)).
Dwa biegi + jeden rotujący refresh token = dokładnie awaria A z 11.08, razem
z ubiciem interaktywnej sesji operatora. Preflight owszem sprawdza sesję, ale
**przed** startem — wyścig zdarza się po godzinach pracy.

### 2.4. Kod Forge jest wspólny dla obu biegów (awaria B)

Oba procesy startują z `cwd=ROOT` i wykonują ten sam `forge/`. Commit
w agent-loop pod działającymi pętlami rozjeżdża kod w pamięci procesu
z szablonem promptu wczytywanym z dysku — i wywala bieg po trzech godzinach,
już po zapłaceniu za turę. Przy dwóch równoległych biegach ekspozycja jest
podwójna, a scenariusz „forge buduje forge" (instancja B pracowała na tym
repozytorium) trafia w to celująco.

To jest [propozycja 6.1 z AWARII](AWARIE-2026-08-11.md#61-snapshot-kodu-na-przebieg-naprawa-awarii-b),
świadomie odłożona jako decyzja operacyjna. Praca dwóch biegów naraz zamienia ją
w wymaganie.

### 2.5. Drobne

- `_isolated_agent_env` dla `grok` zapisuje `config.toml` **nieatomowo**
  ([`agents.py:158`](../forge/agents.py#L158)) — przy dwóch procesach drugi może
  odczytać plik w połowie zapisu. Trzy linijki naprawy.
- Ścieżki względne (`--project game`) rozwiązują się względem `cwd` procesu.
  Po przejściu na migawkę kodu `cwd` przestaje być `ROOT`, więc GUI musi
  przekazywać ścieżki **absolutne**.

---

## 3. Plan wdrożenia

Etapy są ułożone tak, żeby każdy dawał wartość osobno; etap 0 chroni nawet
dzisiejsze ręczne uruchomienia z CLI.

### Etap 0 — blokada projektu (`forge/runlock.py`, nowy plik)

`fcntl.flock(LOCK_EX | LOCK_NB)` na `<projekt>/.forge/run.lock`, trzymany przez
całe życie procesu orkiestratora; do pliku wpisujemy PID i czas startu. Zajęty
zamek = natychmiastowe, czytelne zatrzymanie:

```
Ten projekt prowadzi już bieg Forge (PID 12345, start 21:47). Zatrzymaj go
albo wskaż inny katalog.
```

Zamek `flock` znika sam przy śmierci procesu, więc nie ma problemu wpisów
osieroconych po SIGKILL — nie trzeba żadnej heurystyki „czy PID żyje".

- pliki: `forge/runlock.py`, wpięcie w `orchestrate.main` tuż przed preflightem;
- testy: zajęcie zamku w podprocesie → drugi start kończy się komunikatem;
  zwolnienie po zakończeniu procesu; brak `.forge/` → katalog powstaje.
- koszt: ~40 linii + testy.

### Etap 1 — GUI prowadzi N biegów (rdzeń pracy)

Rozbicie [`ForgeWindow`](../forge/gui.py#L536) na okno i **kontroler biegu**:

```
ForgeWindow
├── RunPanel  ×N      (brief, katalog projektu, Start/Stop, pigułka statusu, czas)
├── sekcja Role       (bez zmian — wspólny routing dla wszystkich biegów)
└── Gtk.Notebook      (jedna zakładka logu na bieg)
```

- `RunController` — przenosimy do niego `process`, `started_at`,
  `stop_requested`, `log_buffer`, `_session_log_fh`, `_read_process`,
  `_process_finished`, `_stop`, `_escalate_stop`, `_update_elapsed`,
  `_open_session_log`, `_load_previous_run`. Kod jest przenoszony 1:1, zmienia
  się tylko właściciel stanu.
- `RunPanel` — wiersz konfiguracji jednego biegu; przyciski wyboru pliku
  i katalogu dokładnie jak dziś, tylko zwielokrotnione.
- nagłówek: „▶ Start wszystkie" / „Zatrzymaj wszystkie" + pigułka zbiorcza
  („2 biegi pracują"); sterowanie pojedynczym biegiem zostaje w jego wierszu.
- `+ Dodaj bieg` / usunięcie wiersza; limit 4 (dwa biegi to już podwojony
  rachunek za tokeny i realne ryzyko limitów dostawcy).
- blokowanie pól: dziś `_set_running` wygasza wszystko; po zmianie wygasza
  **tylko wiersz pracującego biegu**. Karty ról zostają aktywne — routing
  czytany jest raz przy starcie procesu, więc zmiana dla biegu B nie dotknie
  pracującego biegu A.
- `close-request`: zatrzymanie wszystkich, `destroy()` po ostatnim.
- ustawienia: `{"runs": [{"brief": ..., "project": ...}, ...], "window": {...}}`
  z migracją ze starych płaskich kluczy (`brief`/`project` → jednoelementowa
  lista). Brak migracji = utrata ostatniego wyboru operatora przy pierwszym
  uruchomieniu nowej wersji.
- walidacja przy starcie: dwa biegi nie mogą wskazywać tego samego katalogu
  (`Path.resolve()`); to samo dla „ten sam projekt, inny zapis ścieżki".

Testy (bez pętli GTK, tak jak dotychczasowe w `tests/test_gui.py`):
`test_settings_migrate_single_run_to_list`, `test_two_runs_round_trip`,
`test_same_project_twice_is_rejected`, `test_each_run_has_its_own_log_buffer`,
`test_stopping_one_run_leaves_the_other_running`.

- koszt: największy blok, ~2–3 h; głównie przenoszenie kodu.

### Etap 2 — migawka kodu na bieg (naprawa awarii B)

`forge/snapshot.py`: `make_snapshot(root, dest) -> Path` kopiuje pakiet
`forge/` (bez `__pycache__`) i zapisuje obok `SNAPSHOT.json` z `git rev-parse
HEAD` oraz znacznikiem brudnego drzewa. Cel: `~/.cache/forge/runs/<klucz
projektu>/code` — ten sam schemat klucza co
[`_transcript_log_dir`](../forge/orchestrate.py#L1858).

`build_launch` zmienia się w trzech miejscach:

1. `--brief` i `--project` przekazywane jako ścieżki **absolutne**
   (`resolve_project` przenosi się do budowania komendy);
2. `cwd` procesu = katalog migawki, `PYTHONPATH` wskazuje na niego;
3. pierwsza linia logu biegu: `bieg używa migawki kodu <sha>[+brudne] z <ścieżka>`.

Skutek: commit albo edycja w agent-loop w trakcie pracy nie dotyka żadnego
pracującego biegu. Szablony promptów rozwiązują się przez `__file__`
([`prompts/render.py:8`](../forge/prompts/render.py#L8)), więc migawka jest
spójna z kodem automatycznie; `verdict.py` kopiowany do projektu też pochodzi
z migawki.

Cena, którą trzeba znać: poprawka wprowadzona w trakcie biegu **nie** działa aż
do restartu. To jest zamierzone — dokładnie to zabiło instancję B.

- testy: `test_launch_runs_from_a_code_snapshot`,
  `test_snapshot_records_head_sha`, `test_editing_the_repo_does_not_touch_a_snapshot`,
  `test_paths_passed_to_orchestrator_are_absolute`.
- koszt: ~1 h.

Wariant tańszy, gdyby migawka miała okazać się kłopotliwa: sam strażnik
z [6.2](AWARIE-2026-08-11.md#62-strażnik-tożsamości-kodu) — zapis HEAD przy
starcie i czyste zatrzymanie na granicy iteracji, gdy HEAD się zmienił.
Zamienia losowy `ValueError` po trzech godzinach na świadomy restart, ale nie
pozwala bezpiecznie commitować pod pracującą pętlą.

### Etap 3 — strażnik sesji Claude przy wielu biegach

Przy próbie uruchomienia **drugiego** biegu, gdy `claude` jest w routingu
(pierwszy wybór lub zapas dowolnej roli) i `FORGE_CLAUDE_OAUTH_TOKEN` /
`CLAUDE_CODE_OAUTH_TOKEN` są puste — twarde zatrzymanie z instrukcją:

```
Drugi bieg z Claude Code na współdzielonym pliku sesji unieważni sesję obu
(patrz docs/AWARIE-2026-08-11.md). Uruchom `claude setup-token` i ustaw
CLAUDE_CODE_OAUTH_TOKEN, albo wskaż dla tego biegu inne modele.
```

Furtka `FORGE_ALLOW_SHARED_CLAUDE=1` dla świadomej decyzji. Sprawdzenie tokenu
jest gotowe ([`claude_oauth_token`](../forge/agents.py#L44)); token dobierany
jest też z plików `*.env` przez preflight, więc pusta powłoka nie daje fałszywego
alarmu tylko wtedy, gdy GUI użyje tej samej ścieżki wczytywania.

Przy okazji: atomowy zapis `config.toml` dla grok (temp + `os.replace`).

- testy: `test_second_claude_run_without_token_is_refused`,
  `test_token_allows_two_claude_runs`, `test_runs_without_claude_are_unaffected`.
- koszt: ~30 min.

### Etap 4 — opcjonalnie: migawka routingu na bieg

Przy starcie zapis bieżącego routingu do `<projekt>/.forge/routing-run.json`
i `FORGE_ROUTING_FILE` wskazujący na ten plik. Dziś nic nie psuje się bez tego
(routing czytany raz), ale bieg zyskuje zapis tego, czym naprawdę pracował —
przydatne przy porównywaniu kosztu dwóch projektów. ~10 linii.

---

## 4. Czego świadomie NIE robimy

- ~~**Osobny routing per bieg w GUI.** Modele mają być te same; osobne panele ról
  to podwojony interfejs bez odbiorcy. Gdyby kiedyś było potrzebne, etap 4 jest
  naturalnym punktem zaczepienia.~~ **Nieaktualne od 14.08.2026**: każdy bieg
  wskazuje własny PROFIL modeli — patrz [`PROFILE-MODELI.md`](PROFILE-MODELI.md).
  Etap 4 rzeczywiście okazał się punktem zaczepienia, a „podwojony interfejs"
  udało się ominąć: profil wybiera się w wierszu biegu, a karty ról pozostają
  jednym kompletem, który edytuje jeden profil naraz.
- **Osobne izolowane domy CLI per bieg** (`~/.config/forge/claude-<projekt>`).
  Dwa równoległe procesy `claude` na jednym katalogu konfiguracyjnym to
  normalny tryb pracy narzędzia (sesje trzymane są per projekt), a jedyny realny
  wyścig — o poświadczenia — znika wraz z tokenem. Dokładanie osobnych domów
  oznaczałoby osobne logowanie MCP dla każdego biegu.
- **Kolejkowanie biegów / harmonogram.** Dwa procesy uruchamiane ręcznie to dwa
  procesy; zarządzanie kolejką rozwiązywałoby problem, którego nie ma.
- **Wspólny widok kosztu obu projektów.** `report.usage_summary` liczy per
  projekt i to wystarcza; zbiorczy raport można dołożyć później bez zmian
  w architekturze.

---

## 5. Ryzyka operacyjne dwóch biegów naraz

1. **Rachunek za tokeny podwaja się** i limity dostawcy przychodzą dwa razy
   szybciej. Łańcuchy zapasowe już to obsługują, ale przy tych samych modelach
   w obu biegach oba przełączą się na zapas w tym samym momencie — warto dać
   drugiemu biegowi inną kolejność zapasów. Od 14.08.2026 jest na to narzędzie:
   osobny profil modeli na bieg ([`PROFILE-MODELI.md`](PROFILE-MODELI.md)).
2. **Rywalizacja o CPU/IO** przy uruchamianiu testów obu projektów naraz;
   jeśli któryś projekt ma ciężką suitę, czasy tur wzrosną w obu.
3. **Projekt = to repozytorium.** Bieg pracujący na agent-loop commituje do
   drzewa, z którego (bez etapu 2) startują oba biegi. Etap 2 jest tu warunkiem
   koniecznym, nie ulepszeniem.

---

## 6. Kolejność i podsumowanie kosztu

| etap | zakres | koszt | ryzyko bez niego |
|---|---|---|---|
| 0 | blokada projektu | ~40 linii | zniszczenie stanu obu biegów jednym literówkowym kliknięciem |
| 1 | GUI × N biegów | ~2–3 h | funkcja nie istnieje |
| 2 | migawka kodu | ~1 h | powtórka awarii B, podwójna ekspozycja |
| 3 | strażnik sesji Claude | ~30 min | powtórka awarii A + utrata sesji operatora |
| 4 | migawka routingu | ~10 linii | brak zapisu, czym bieg pracował |

Etapy 0 i 3 mają sens także bez etapu 1 — chronią dzisiejsze uruchomienia
z linii poleceń.

---

## 7. Stan wdrożenia

Wszystkie pięć etapów jest w drzewie. `python3 -m pytest -q`: **569 przechodzi**
(przed pracą 506), 9,7 s.

| etap | co powstało |
|---|---|
| 0 | [`forge/runlock.py`](../forge/runlock.py) — `flock` na `<projekt>/.forge/run.lock` brany w [`orchestrate.main`](../forge/orchestrate.py) przed czymkolwiek, co dotyka drzewa; zajęty projekt = `rc=4` i komunikat z PID-em oraz godziną startu. Zamek zwalnia jądro, więc SIGKILL nie zostawia sieroty. `busy_reason` daje GUI podgląd bez zakładania katalogów. |
| 1 | [`forge/gui.py`](../forge/gui.py): klasa `Run` (własny proces, bufor logu, plik sesji, przyciski, pigułka statusu) i przebudowane `ForgeWindow` — lista biegów po lewej, `Gtk.Notebook` z zakładką logu na bieg po prawej, „+ Dodaj bieg" do `MAX_RUNS`, start/stop per bieg oraz zbiorczy w nagłówku. Ustawienia migrują z płaskich `brief`/`project` do listy `runs`. |
| 2 | [`forge/snapshot.py`](../forge/snapshot.py) + `build_launch` zwracające `Launch(command, env, cwd)`: proces startuje z `~/.cache/forge/code/forge-<odcisk>`, dostaje `PYTHONPATH` i **absolutne** ścieżki briefu oraz projektu. Pierwsza linia logu biegu mówi, z jakiej migawki i z jakiego commita pracuje. Uruchomienie z powłoki przenosi się na migawkę samo (`_reexec_from_snapshot`), a pracujący bieg trzyma na niej dzierżawę, więc sprzątacz jej nie usunie. |
| 3 | `preflight.claude_file_session_lock` — jeden zamek na maszynę na PLIKOWY tryb sesji Claude Code, brany przez orkiestrator (token dobierany także z plików `*.env`; furtka `FORGE_ALLOW_SHARED_CLAUDE=1`). Przy okazji atomowy zapis `config.toml` grokowego domu. |
| 4 | `Run.routing_snapshot` — routing biegu ląduje w `<projekt>/.forge/routing/run-<stamp>.json` (nazwa jednorazowa) i to jego dostaje proces; karty ról zostają edytowalne w trakcie pracy, bo pracujący bieg czyta już własną kopię. |

Sprawdzone poza testami jednostkowymi: bieg uruchomiony z migawki importuje
kod z `~/.cache/forge/code/...`, dostaje absolutne ścieżki i zatrzymuje się na
zamku (`rc=4`) bez wywołania choćby jednej roli; proces w migawce bierze
dzierżawę i nie próbuje przenosić się drugi raz; sprzątanie omija kopię
z żywą dzierżawą i usuwa ją dopiero po zwolnieniu.

### 7.1. Poprawki po przeglądzie

Cztery znaleziska z przeglądu dotyczyły tego samego wzorca: strażnik pilnował
mniej, niż obiecywał.

1. **Sesja Claude Code była pilnowana tylko w obrębie jednego okna GUI.**
   Warunek „czy w tym oknie pracuje inny bieg" nie widział ani drugiego okna,
   ani powłoki, a orkiestrator w ogóle nie pytał. Sprawdzenie zamieniło się
   w procesowy zamek `flock` na `~/.cache/forge/claude-file-session.lock`,
   brany przez orkiestrator; GUI tylko w niego zagląda.
2. **Migawka routingu miała stałą nazwę.** Zapis wyprzedza zamek projektu (a
   `Config` czyta plik jeszcze przed jego przejęciem), więc dwa starty z różnych
   okien mogły podmienić sobie plik i zwycięzca ruszyłby z cudzym routingiem.
   Nazwa jest teraz jednorazowa, a sprzątanie zostawia ostatnie pięć zapisów
   i nigdy nie rusza pliku właśnie zapisanego.
3. **Sprzątanie mogło usunąć kod pracującej pętli.** Wiek katalogu nie widzi
   biegu, który pracuje trzeci tydzień. Bieg trzyma dzierżawę współdzieloną,
   a `prune` pomija katalogi, na których ktoś ją ma.
4. **Ochrona migawką kończyła się na GUI.** `python3 -m forge.orchestrate`
   nadal ładował kod i szablony z drzewa roboczego — czyli dokładnie tak, jak
   padła instancja B. Orkiestrator przenosi się teraz na migawkę sam;
   `FORGE_CODE_SNAPSHOT=0` zostawia dotychczasowe zachowanie.

Czego wdrożenie NIE zmienia: routing pozostaje wspólny dla wszystkich biegów
(punkt 4 z sekcji „Czego świadomie nie robimy"), izolowane domy CLI dalej są
współdzielone, a rachunek za tokeny przy dwóch biegach jest podwójny —
ryzyka operacyjne z sekcji 5 obowiązują bez zmian.
