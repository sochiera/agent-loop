# Profile modeli: niezależny routing dla każdego biegu

> **Stan: wdrożone** (14.08.2026). Zastępuje punkt 4 z sekcji „Czego świadomie
> NIE robimy" w [`PLAN-ROWNOLEGLE-BIEGI.md`](PLAN-ROWNOLEGLE-BIEGI.md).

Cel: dwa biegi w jednym panelu mogą pracować **innymi modelami**. Jeden projekt
jedzie wyłącznie na GPT, drugi równolegle miesza GPT, Claude'a i Groka — bez
przełączania pokręteł między startami i bez ryzyka, że konfiguracja jednego
biegu zmieni to, czym pracuje drugi.

---

## 1. Dlaczego dotychczasowy wspólny routing nie wystarczał

Plan równoległych biegów zakładał, że „te same modele w obu projektach to typowy
przypadek", i zostawiał jeden plik `~/.config/forge/routing.json` dla wszystkich.
Założenie nie przeżyło zderzenia z praktyką z trzech powodów:

1. **Projekty mają różną wartość.** Eksperyment na zabawkowym repozytorium
   i projekt, na który wolno wydać Opusa, dostawały ten sam routing, więc każdy
   wybór był kompromisem — albo przepłacone zabawki, albo osłabiony projekt.
2. **Limity dostawcy przychodzą razem.** Sekcja 5.1 planu równoległości
   przewidywała to wprost: przy tych samych modelach oba biegi przełączają się
   na zapas w tej samej chwili. Osobne profile pozwalają dać drugiemu biegowi
   inną kolejność zapasów, więc limit jednego dostawcy przestaje być zdarzeniem
   dotykającym wszystkiego naraz.
3. **Zamek na plikową sesję Claude Code obowiązywał wszystkich.** Bieg, który
   Claude'a w ogóle nie wołał, i tak rozbijał się o czyjś zamek albo blokował
   sobie drugi bieg — bo pytanie zadawano o routing OKNA, a nie o routing biegu.

---

## 2. Model pojęciowy

**Profil** to nazwany zestaw nadpisań ról — czyli po prostu `Routing`
z etykietą. Biegi wskazują profile, nie modele.

```
profil „Wspólny"        ~/.config/forge/routing.json          ← CLI bez zmiennych
profil „Tylko GPT"      ~/.config/forge/profiles/tylko-gpt.json
profil „Trzy narzędzia" ~/.config/forge/profiles/trzy-narzedzia.json

bieg alfa  → profil „Tylko GPT"      → migawka w alfa/.forge/routing/run-….json
bieg beta  → profil „Trzy narzędzia" → migawka w beta/.forge/routing/run-….json
```

Trzy decyzje, na których to stoi:

- **Profil nazwany to zwykły plik routingu**, tylko z dodatkowym polem `name`.
  Parser routingu ignoruje nieznane pola, więc `FORGE_ROUTING_FILE` wskazujący
  plik profilu działa **bez jednej linii zmiany** w orkiestratorze, a migawka
  biegu pozostaje tym, czym była.
- **Profil wspólny to dotychczasowy `routing.json`.** Uruchomienie z powłoki bez
  żadnej zmiennej czyta dokładnie ten sam plik, co przed przebudową; istniejące
  skróty, jednostki systemd i przyzwyczajenia nie tracą nic.
- **Bieg trzyma SLUG, nie ścieżkę ani kopię routingu.** Przemianowanie profilu
  nie osierocą wiersza w panelu (etykieta mieszka w pliku, nie w nazwie pliku),
  a kopia routingu w wierszu rozjeżdżałaby się z tym, co operator właśnie
  wyklikał.

---

## 3. Trudne miejsca i jak są rozstrzygnięte

### 3.1. Panel nie może mieć jedenastu kart ról na bieg

Cztery biegi × jedenaście ról × do trzech pokręteł na katalogu kilkudziesięciu
modeli to interfejs, którego nikt nie obsłuży, i koszt budowy okna, którego nikt
nie chce płacić. Rozdzielamy więc **wybór** od **edycji**:

- wiersz biegu ma jedno pokrętło „Profil modeli" i przycisk `Modele…`;
- sekcja „Modele ról" edytuje **jeden profil naraz** — ten wskazany w jej
  nagłówku.

Skutek uboczny jest zaletą: dwa biegi na tym samym profilu widzą jedną prawdę,
zamiast rozjeżdżać się przy pierwszej zmianie w jednej z dwóch kopii kart.

### 3.2. Karty ról są wspólne, więc muszą się czyścić

Przy przełączeniu profilu każda karta dostaje `apply()` — z wpisem nowego
profilu albo z pustym. Bez tego rola, której nowy profil nie nadpisuje,
zostałaby na ekranie z wartością poprzedniego, a pierwszy klik zapisałby ją jako
świadomy wybór operatora dla profilu, w którym jej nie było
(`test_a_role_untouched_by_the_new_profile_returns_to_policy`).

Podmiana zawartości kart nie jest wyborem operatora, więc `_applying_profile`
wycisza na ten czas zapis.

### 3.3. Bieg pyta o zamek Claude'a **swoim** routingiem

`blocking_problem` buduje `Config` z routingu TEGO biegu, a nie okna. Dzięki temu
bieg bez Claude'a w łańcuchu nie bierze maszynowego zamku na plikową sesję i nie
daje się nim zablokować, a bieg z Claude'em jest chroniony tak samo jak dotąd.
Rozstrzyga to nadal orkiestrator (`preflight.claude_file_session_lock`) czytający
migawkę biegu — panel tylko zagląda.

### 3.4. Zniknięty profil nie może po cichu wrócić na wspólny

Profil skasowany w drugim oknie albo plik usunięty ręcznie zostawia wiersz biegu
ze slugiem, którego nie ma. Cicha podmiana na profil wspólny byłaby najgorszym
możliwym wynikiem: bieg ruszyłby na godziny modelami, których dla tego projektu
nikt nie wybrał. Zamiast tego:

- pokrętło biegu pokazuje pozycję `<slug> — BRAK`;
- `blocking_problem` odmawia startu z nazwą brakującego profilu;
- `--routing-profile` i `FORGE_ROUTING_PROFILE` podnoszą `UnknownProfile`,
  a `main` kończy się błędem argumentu — zanim cokolwiek kosztuje.

Usunięcie profilu z panelu jest inne, bo jest świadomą decyzją: bezczynne biegi
wracają na wspólny **z wpisem w swoim logu**, a profil prowadzący bieg PRACUJĄCY
w ogóle nie daje się usunąć (wiersz opisywałby wtedy inne modele niż te,
którymi ten bieg realnie pracuje).

### 3.5. Nazwy plików pochodzą od operatora

`slugify` sprowadza nazwę do `[a-z0-9-]` (z ręczną tabelą dla `ł`, którego NFKD
nie rozkłada), a `valid_slug` jest **białą listą** sprawdzaną przed dotknięciem
systemu plików — slug bierze się z pliku ustawień i ze zmiennej środowiskowej,
więc `../../.ssh/config` musi odpaść wcześniej. Kolizje nazw dostają sufiks
liczbowy, sprawdzany także wobec plików nieznanych jeszcze temu oknu (profil
założony w drugim panelu nie może zostać nadpisany).

### 3.6. Zapis migawki może się nie udać

Gdy zapis `<projekt>/.forge/routing/run-….json` padnie (pełny dysk, prawa),
bieg dostaje **plik swojego profilu**, a nie wspólny. Wariant „awaryjnie
wspólny" oznaczałby po cichu inne modele niż wybrane — dokładnie ten błąd,
przed którym broni reszta tej sekcji.

---

## 4. Co powstało

| plik | zmiana |
|---|---|
| [`forge/profiles.py`](../forge/profiles.py) | nowy: `slugify`/`valid_slug`, `Profile`, `Store` (CRUD + zapis), `resolve`, `load_named`, `load_from_env` |
| [`forge/routing.py`](../forge/routing.py) | `save(..., extra=)` — etykieta profilu obok `roles`, bez drugiej implementacji zapisu atomowego |
| [`forge/config.py`](../forge/config.py) | źródłem routingu jest `profiles.load_from_env`: migawka → profil → wspólny |
| [`forge/orchestrate.py`](../forge/orchestrate.py) | `--routing-profile`; profil rozstrzyga się PRZED `Config`, żeby nie ominąć `__post_init__` (zakaz Codeksa dla mistrza) |
| [`forge/gui.py`](../forge/gui.py) | pokrętło profilu w wierszu biegu, pasek profilu nad kartami ról (wybór, nazwa, nowy, usuń), `run_routing(run)`, migawka i zamki per bieg |
| `tests/test_profiles.py` | 21 testów warstwy profili — bez GTK |
| `tests/test_gui.py`, `tests/test_cli.py` | profil per bieg, zamek Claude'a per bieg, wybór profilu z wiersza poleceń i ze środowiska |

`python3 -m pytest -q`: **666 przechodzi** (przed pracą 622).

---

## 5. Jak się tego używa

**Panel.** W sekcji „Modele ról" kliknij `+ Nowy profil` (powstaje jako kopia
bieżącego), nazwij go, przestaw role. W wierszu biegu wybierz profil z pokrętła;
`Modele…` pokazuje role tego biegu w sekcji poniżej.

**Wiersz poleceń.**

```bash
python3 -m forge.orchestrate --brief game.md --project game \
  --routing-profile "Tylko GPT"       # nazwa albo slug (tylko-gpt)
```

albo dla launchera bez wiersza poleceń: `FORGE_ROUTING_PROFILE=tylko-gpt`.

Pierwszeństwo źródeł: `FORGE_ROUTING_FILE` (migawka biegu) → `--routing-profile`
→ `FORGE_ROUTING_PROFILE` → profil wspólny.

---

## 6. Czego świadomie NIE robimy

- **Profil per rola per bieg poza profilami.** Zestaw ról to jedna decyzja;
  rozbicie jej na jedenaście niezależnych ustawień per bieg wróciłoby dokładnie
  do interfejsu odrzuconego w 3.1.
- **Automatycznego różnicowania łańcuchów zapasowych.** Profile *pozwalają* dać
  drugiemu biegowi inną kolejność zapasów, ale wybór zostaje przy operatorze —
  zgadywanie, który dostawca ma paść pierwszy, byłoby polityką udającą wiedzę.
- **Współdzielenia profili między maszynami.** Pliki leżą w konfiguracji
  użytkownika; synchronizacja to zadanie dla narzędzia od dotfiles, nie dla Forge.
- **Śledzenia zmian profilu w trakcie biegu.** Bieg czyta migawkę raz i tak ma
  zostać: zmiana modeli w połowie zadania rozjechałaby dowody z tym, co je
  wytworzyło.
