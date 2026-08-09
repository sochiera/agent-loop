# Routing ról i łańcuch zapasowy

Forge ma dwie warstwy decyzji o tym, kto i czym wykona rolę.

**Polityka projektu** (`forge/config.py`) jest domyślna i wersjonowana:
`ROLE_MODEL_LEVELS` mówi, jakiego POZIOMU wymaga rola przy danej trudności
zadania (`economy … max`), a `MODEL_LEVEL_ROUTING` tłumaczy poziom na konkretny
model i effort danego narzędzia. Ta warstwa opisuje, ile rozumowania zadanie
wymaga — i dlatego nie należy do pojedynczego uruchomienia.

**Wybór operatora** (`~/.config/forge/routing.json`) leży nad polityką i jest
prywatny dla maszyny. Tu decydujesz, że koder ma jechać konkretnym modelem
konkretnego providera OpenCode, a Product Owner ma być Claude'em. Plik jest
wspólny dla GUI i uruchomień z CLI, więc zmiana dostawcy nie wymaga commita.

## Plik routingu

```json
{
  "version": 1,
  "roles": {
    "product_owner": {
      "agent": "claude",
      "slots": {"all": {"model": "opus", "effort": "high"}},
      "fallbacks": [{"agent": "opencode"}]
    },
    "coder": {
      "agent": "opencode",
      "slots": {
        "simple":  {"model": "llamacpp/qwen36-coder-fast"},
        "complex": {"model": "neuralwatt/kimi-k2.7-code-flex", "effort": "high"}
      },
      "fallbacks": [{"model": "zai-coding-plan/glm-5.2"}, {"agent": "grok"}]
    }
  }
}
```

Reguły:

- **Puste = polityka.** Brak roli, brak slotu albo pusty `model` oznacza, że
  decyduje `MODEL_LEVEL_ROUTING`. Wyżej `coder/standard` nie ma slotu, więc
  jedzie modelem z poziomu `efficient`.
- **`slots`** ma klucze `simple`/`standard`/`complex` dla ról czułych na zakres
  zadania (tester, koder, recenzent, mistrz) oraz `all` dla pozostałych —
  planista, Product Owner czy weryfikator nie oglądają „jednego zadania".
  Slot `all` działa też jako wartość wspólna dla trudności zostawionych pustych.
- **Sam `effort`**, bez modelu, dostraja model wybrany przez politykę.
- **Zmiana narzędzia zeruje model** odziedziczony z pól `*_model`: nazwa
  `neuralwatt/…` nic nie znaczy dla Claude'a, więc po przełączeniu roli na inne
  CLI wraca polityka poziomu.
- Plik jest czytany POBŁAŻLIWIE: nieznana rola, nieznana trudność i wartość
  nie do przekazania w argv są pomijane. Literówka w ręcznej edycji cofa cię do
  polityki projektu, a nie wywala biegu.
- `FORGE_ROUTING_FILE` wskazuje inny plik; `none`/`off` wyłącza całą warstwę.
- Zakazy ról obowiązują też wpisy zapasowe: mistrz nie przyjmie Codeksa ani
  jako pierwszy wybór, ani jako zapas — taki plik zatrzymuje start z
  komunikatem, zamiast cicho oddać mu rolę po pierwszej awarii.

## Łańcuch zapasowy

`fallbacks` to kolejne punkty próbowane, gdy poprzedni **wyczerpał limit**
(czyli po całym backoffie, nie przy pierwszym 429) albo **twardo padł** —
niezerowy kod wyjścia, brak binarki, timeout. Oba przypadki znaczą dla biegu to
samo: tą drogą pracy nie będzie.

- Wpis bez `model` znaczy „ten sam poziom, inne narzędzie" — model wyznacza
  polityka poziomu dla wskazanego agenta.
- Wpis bez `agent` znaczy „to samo narzędzie, inny model".
- Duplikaty znikają: zapas identyczny z poprzednikiem tylko powtórzyłby awarię.
- Ostatni punkt łańcucha rzuca oryginalny wyjątek, więc bieg kończy się
  dokładnie tak, jak bez łańcucha — zmienia się tylko liczba prób przed tym.
- Przerwanie użytkownika (`Ctrl-C`) NIE uruchamia zapasu. Fallback broni przed
  awarią dostawcy, nie przed decyzją operatora.
- Preflight sprawdza także agentów ukrytych w łańcuchach: zapas, który okazuje
  się nieobecny dopiero w chwili awarii, byłby drugą awarią zamiast
  zabezpieczenia.

Przełączenie widać w logu i w GUI (kolor ostrzeżenia):

```
rola[coder]: opencode/zai-coding-plan/glm-5.2 — limit; przełączam na zapas grok (1/2).
```

## GUI

`python3 -m forge.gui` jest edytorem tego pliku. Każda rola ma kartę:
narzędzie, model per trudność (albo jeden wspólny) i łańcuch zapasowy. Lista
modeli to podpowiedź złożona z modeli znanych polityce oraz providerów
znalezionych w `~/.config/opencode/opencode.json` — nowy model spoza katalogu
wpisujesz ręcznie. Wybór zapisuje się przy każdej zmianie pokrętła, a nie
dopiero przy starcie biegu.

Pole `agent` trafia do pliku dopiero wtedy, gdy realnie zmienisz narzędzie:
pokrętło pokazujące domyślnego agenta polityki nie jest decyzją operatora,
a zapisane jako nadpisanie odcięłoby rolę od dziedziczenia (np. weryfikator
po planiście) i od `FORGE_<ROLA>_AGENT`.
