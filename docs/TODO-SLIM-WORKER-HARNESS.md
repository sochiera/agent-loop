# TODO — Slim Worker Harness

Status: odłożone. Nie implementować razem z Context Capsule.

## Cel

Zmniejszyć stały kontekst narzucany przez CLI agentów w rolach innych niż
mistrz, bez odbierania im narzędzi potrzebnych do pracy i bez pogorszenia
wyników TDD.

Mistrz ma już osobny tryb `thin`: własny system prompt, brak narzędzi i jedna
tura. Tester, koder, reviewer, planista i verifier są agentami roboczymi.
Potrzebują pętli tool-use, ale nie każda rola potrzebuje całego domyślnego
harnessu, wszystkich narzędzi, pluginów, pamięci, web search i subagentów.

## Dlaczego to jest osobny feature

Context Capsule optymalizuje zmienny kontekst zadania: historię, handoff,
wyniki bramek i stan rundy. Slim Worker Harness optymalizuje stały prefiks
providera: system prompt, schematy narzędzi i funkcje samego CLI.

Te zmiany trzeba mierzyć oddzielnie:

- kapsuła może dać duży efekt przy rozrośniętej historii;
- slim harness może dać duży efekt w świeżej, krótkiej turze;
- jednoczesne wdrożenie uniemożliwi przypisanie oszczędności i regresji do
  właściwej zmiany.

## Stan obecny

Historyczny pomiar pełnego `claude -p` pokazał około 15,9 tys. tokenów
cache-read samego harnessu: system prompt Claude Code, schematy narzędzi oraz
instrukcje projektu. Nie jest to uniwersalna wartość dla wszystkich providerów.

Aktualne adaptery:

| Provider | Pełny agent roboczy | Dostępne mechanizmy redukcji |
|---|---|---|
| Claude | wbudowany adapter | `--system-prompt`, `--tools`, ustawienia źródeł |
| OpenCode | generyczny adapter | własny agent, allowlista tools, `--pure` |
| Grok | generyczny adapter | system override, allowlista tools, brak memory/web/subagents |
| Codex | wbudowany adapter | brak system override i kontroli listy głównych narzędzi |
| Kiro | generyczny adapter | niezweryfikowane lokalnie |

OpenCode i Grok nie raportują obecnie usage w formacie obsługiwanym przez
Forge, dlatego nie znamy ich rzeczywistego bazowego kosztu.

## Założenia

1. Nie budujemy własnej pętli narzędziowej, jeśli CLI potrafi uruchomić
   ograniczonego agenta.
2. Nie usuwamy narzędzia tylko dlatego, że dana tura zwykle go nie używa.
   Narzędzie musi być zbędne dla całego kontraktu roli.
3. Stabilne instrukcje roli powinny tworzyć stabilny prefiks przyjazny prompt
   cachingowi.
4. Dynamiczny Context Capsule pozostaje oddzielną częścią promptu.
5. Nie optymalizujemy Codeksa, dopóki CLI nie udostępni system override oraz
   kontroli narzędzi.
6. Kiro nie dostaje domyślnego trybu slim bez lokalnej weryfikacji flag i
   pomiaru.

## Proponowane profile ról

Nazwy narzędzi są logiczne. Adapter providera tłumaczy je na własne flagi.

### Reviewer

Potrzebuje:

- odczytu plików;
- wyszukiwania nazw i fragmentów;
- listowania plików;
- poleceń tylko do inspekcji Git, diffu i ewentualnych testów.

Nie potrzebuje:

- edycji i zapisu plików;
- web search;
- pamięci między sesjami;
- subagentów;
- narzędzi todo.

To pierwszy kandydat do wdrożenia: reviewer jest zawsze świeży, często kończy
na jednej turze i już dziś nie ma prawa zmieniać worktree.

### Planista

Potrzebuje:

- odczytu repozytorium i dokumentacji;
- wyszukiwania;
- listowania;
- poleceń inspekcyjnych.

Zwykle nie potrzebuje:

- edycji kodu i dokumentacji;
- web search;
- subagentów;
- pamięci między sesjami.

Planista czyta dużo repo, więc stały harness stanowi zwykle mały procent jego
całego inputu. Odchudzanie tej roli ma niższy priorytet niż reviewer i agenci
TDD.

### Tester

Potrzebuje:

- odczytu, wyszukiwania i listowania;
- uruchamiania testów;
- edycji, tworzenia i refaktoryzacji testów oraz ich infrastruktury;
- odczytu aktualnego diffu.

Nie potrzebuje:

- web search w zwykłej pętli TDD;
- subagentów;
- cross-session memory providera;
- narzędzi todo.

### Koder

Potrzebuje:

- odczytu, wyszukiwania i listowania;
- uruchamiania testów oraz buildów;
- edycji kodu produkcyjnego i dokumentacji;
- odczytu diffu.

Nie potrzebuje:

- web search w zwykłej pętli;
- subagentów;
- cross-session memory providera;
- narzędzi todo.

### Verifier

Nie definiować jednego profilu minimalnego. Verifier może potrzebować MCP,
sieci, CI, sprzętu albo narzędzi charakterystycznych dla projektu. Jego profil
powinien wynikać z zadeklarowanych targetów weryfikacji.

## Szacowany potencjał

Poniższe wartości są hipotezą do zmierzenia, nie wynikiem benchmarku:

| Provider | Cel redukcji stałego harnessu |
|---|---:|
| Claude | około 35–60% przy zachowaniu podstawowych narzędzi |
| OpenCode | około 30–60% przez własne agenty i `--pure` |
| Grok | około 30–60% przez system override i ograniczenie funkcji |
| Codex | poniżej kilku procent przy obecnych flagach — nie wdrażać |
| Kiro | brak estymacji |

Redukcja całego inputu będzie mniejsza niż redukcja harnessu:

- przy reviewerze może być zauważalna, bo tura jest krótka;
- przy testerze i koderze zależy od rozmiaru diffu, outputu testów i historii;
- przy planiście historyczne wejście rzędu setek tysięcy tokenów sprawia, że
  kilka tysięcy oszczędności stanowi niski procent.

Tokeny odczytane z prompt cache są zwykle tańsze niż nowe tokeny, ale nadal
zajmują okno kontekstu, mogą wpływać na limity subskrypcji i są przetwarzane
przy kolejnych wywołaniach modelu.

## Wymagana obserwowalność

Nie wdrażać profili slim przed dodaniem pomiaru:

1. Rozmiar stabilnych instrukcji roli.
2. Rozmiar schematów aktywnych narzędzi, jeśli CLI go ujawnia.
3. Rozmiar dynamicznego promptu lub Context Capsule.
4. Usage pojedynczej tury: input, cached input, output i reasoning.
5. Liczba wewnętrznych tur modelu.
6. Liczba i rodzaj wywołań narzędzi.
7. Czas wykonania.

Dla generycznych adapterów należy parsować usage z ich JSON/JSONL, jeśli
provider go emituje. Gdy usage nie jest dostępne, Forge zapisuje rozmiary
promptów i liczbę tur/narzędzi bez zgadywania tokenizacji.

## Architektura

### Potrzeba roli

Rdzeń definiuje profil logiczny:

```python
WorkerProfile(
    tools=("read", "search", "list", "shell", "edit"),
    web=False,
    memory=False,
    subagents=False,
    plugins=False,
    max_turns=None,
)
```

### Tłumaczenie providera

Adapter przekłada profil na natywne możliwości:

- Claude: własny system prompt i `--tools` z allowlistą;
- OpenCode: generowany agent `forge-<role>` i mapa `tools`;
- Grok: system override, `--tools`, `--no-memory`,
  `--disable-web-search`, `--no-subagents`;
- Codex: brak adaptera slim i jawny fallback do pełnego trybu;
- custom CLI: `FORGE_AGENT_<NAME>_SLIM_CMD`.

Brak tłumaczenia nie może po cichu usuwać narzędzi. Oznacza pełny tryb i wpis
w telemetrii `slim_supported=false`.

## Plan wdrożenia

### Etap 1 — baseline

1. Dodać telemetrię per rola/provider.
2. Zmierzyć pełne wywołania na tych samych zadaniach.
3. Zapisać wersje CLI oraz aktywny profil narzędzi.

### Etap 2 — reviewer

1. Dodać `WorkerProfile` i translację dla Claude, OpenCode oraz Groka.
2. Włączyć read-only profil reviewera.
3. Porównać koszt, czas, liczbę tur i werdykty review.
4. Sprawdzić deterministycznie, że reviewer nadal nie zmienia worktree.

### Etap 3 — tester i koder

1. Dodać profile z odczytem, shellem i edycją.
2. Włączyć je najpierw dla OpenCode, potem Groka i Claude.
3. Uruchomić korpus z turą `red`, `green`, żądaniem zmiany testu, regresją
   pełnej bramki i cyklem po review.
4. Zostawić szybki rollback do pełnego harnessu.

### Etap 4 — planista

Wdrożyć tylko wtedy, gdy baseline pokaże sensowny udział harnessu w całym
inputcie. Nie ryzykować jakości planu dla oszczędności rzędu pojedynczych
procent.

### Etap 5 — verifier i pozostali providerzy

Projektować osobno po zebraniu realnych potrzeb targetów oraz po lokalnej
weryfikacji Kiro.

## Kryteria akceptacyjne

Profil slim danej roli może stać się domyślny, gdy:

- zmniejsza stały input co najmniej o 25%;
- zmniejsza całe wejście roli w reprezentatywnym korpusie;
- nie zwiększa mediany tur TDD;
- nie zwiększa liczby `InvalidDecision`, `blocked` ani `request_changes`;
- nie zwiększa powtórzonych odczytów tych samych plików;
- nie odbiera żadnego narzędzia potrzebnego w scenariuszach akceptacyjnych;
- pełny pakiet testów Forge pozostaje zielony.

Jeżeli oszczędność całego inputu jest mniejsza niż 5%, pozostawiamy pełny
harness, chyba że profil daje inną istotną korzyść, np. twardy read-only
reviewera.

## Kolejność priorytetów

1. Context Capsule i pomiar dynamicznego kontekstu.
2. Slim reviewer.
3. Slim tester/koder dla OpenCode.
4. Grok i Claude.
5. Planista tylko po pomiarze.
6. Codex dopiero po pojawieniu się natywnych flag.
7. Kiro po instalacji i weryfikacji.

## Decyzja

Feature pozostaje w backlogu. Obecnie nie zmieniamy wywołań agentów roboczych.
Do implementacji wracamy po wdrożeniu Context Capsule i zebraniu baseline'u,
zaczynając od read-only reviewera.
