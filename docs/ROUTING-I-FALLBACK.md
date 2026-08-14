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

Nazwany zestaw tych wyborów to **profil**, a każdy bieg wskazuje własny — patrz
[`PROFILE-MODELI.md`](PROFILE-MODELI.md). Profile nazwane leżą w
`~/.config/forge/profiles/<slug>.json` i mają dokładnie ten sam format, co plik
opisany niżej; `routing.json` pozostaje profilem wspólnym.

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
      "slots": {
        "simple":  {"agent": "opencode", "model": "openai/gpt-5.6-luna"},
        "complex": {"agent": "claude", "model": "opus", "effort": "high"}
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
- **`agent` w slocie** obowiązuje tylko tę trudność i ma pierwszeństwo przed
  `agent` całej roli. Wybór modelu przesądza o narzędziu, a model wybiera się
  per trudność: koder na zadaniu prostym bywa modelem tanim, a na złożonym
  najmocniejszym dostępnym. `agent` roli zostaje wartością wspólną dla slotów,
  które własnego nie mają.
- **Sam `effort`**, bez modelu, dostraja model wybrany przez politykę.
- **Zmiana narzędzia zeruje model** odziedziczony z pól `*_model`: nazwa
  `zai-coding-plan/…` nic nie znaczy dla Claude'a, więc po przełączeniu na inne
  CLI wraca polityka poziomu.
- Plik jest czytany POBŁAŻLIWIE: nieznana rola, nieznana trudność i wartość
  nie do przekazania w argv są pomijane. Literówka w ręcznej edycji cofa cię do
  polityki projektu, a nie wywala biegu.
- `FORGE_ROUTING_FILE` wskazuje inny plik; `none`/`off` wyłącza całą warstwę.
  Bez tej zmiennej pliku szuka wybór profilu: `--routing-profile <nazwa>` albo
  `FORGE_ROUTING_PROFILE=<slug>`, a w ich braku — profil wspólny. Wskazanie
  profilu, którego nie ma, zatrzymuje start; to jedyne miejsce tej warstwy,
  które NIE jest pobłażliwe, bo cicha praca na polityce domyślnej kosztowałaby
  cały bieg wykonany nie tymi modelami.
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

`python3 -m forge.gui` jest edytorem tego pliku. Każda rola ma kartę: model per
trudność (albo jeden wspólny) i łańcuch zapasowy.

**Wybiera się MODEL, nie narzędzie.** Większość modeli ma dokładnie jedną drogę
uruchomienia, więc osobne pytanie o nią byłoby pustym klikiem. Pokrętło dostawcy
pojawia się tylko przy modelu, który tras ma więcej:

| Model | Trasy |
|---|---|
| `gpt-5.6-luna` | `codex` • `opencode · openai` |
| `sonnet` | `claude` • `kiro` |
| `glm-5.2` | tyle, ilu dostawców OpenCode go serwuje |
| `grok-4.6`, `haiku`, `qwen3.8-max`, … | jedna — pokrętła nie ma |

Dostawcy OpenCode są tu osobnymi trasami, bo to oni różnią się ceną, limitem i
opóźnieniem. Kolejność tras stawia natywne CLI przed mostem OpenCode: mają
telemetrię zużycia, a Codex także wznawianie sesji.

Lista modeli (z wyszukiwaniem) składa się z modeli znanych polityce i modeli
znalezionych w `~/.config/opencode/opencode.json`; nowy model spoza katalogu
wpisujesz ręcznie — wtedy pokrętło dostawcy jest widoczne zawsze, bo nazwy
własnej nie da się przypisać do narzędzia. Pozycje `wg polityki: <narzędzie>`
zapisują sam `agent`, czyli „ten sam poziom, inne CLI" — najczęściej przydatne
we wpisie zapasowym. Pierwsza pozycja („domyślnie: …") nie nadpisuje niczego i
pokazuje wprost, co pojedzie z samej polityki.

Panel zapisuje narzędzie razem z modelem, w slocie, i nie dotyka pola `agent`
całej roli — dopóki niczego nie wybierzesz, rola dziedziczy agenta (np.
weryfikator po planiście) i słucha `FORGE_<ROLA>_AGENT`. Plik napisany ręcznie
z `agent` na poziomie roli wczytuje się bez zmiany znaczenia. Wybór zapisuje się
przy każdej zmianie pokrętła, a nie dopiero przy starcie biegu.
