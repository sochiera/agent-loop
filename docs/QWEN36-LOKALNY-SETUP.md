# Qwen3.6-35B-A3B lokalnie — uruchomienie i strojenie

Data: 2026-08-05, uzupełnione 2026-08-06. Sprzęt: RTX 3060 12 GB, Ryzen 5 5600 (6 rdzeni / 12 wątków), 30 GB RAM.

Model działa lokalnie na GPU z offloadem ekspertów do RAM, podpięty do opencode.

**Dostępne są dwa modele tej samej architektury 35B-A3B**, wykluczające się na porcie 8080:

| Usługa | Model | Zastosowanie | tg |
|---|---|---|---|
| `llama-qwen36-coder.service` | **Qwen3.6-35B-A3B oficjalny**, UD-Q4_K_XL | **domyślny — programowanie** | 39.8 t/s |
| `llama-qwen36.service` | HauhauCS Uncensored Aggressive, IQ4_XS | treści odcenzurowane | 43.0 t/s |

Sekcje 1–9 opisują pierwotną konfigurację z modelem odcenzurowanym.
**Sekcja 10 opisuje model do programowania i to on jest domyślny.**

---

## 1. Wynik końcowy

| Element | Wartość |
|---|---|
| Backend | llama.cpp `b10284`, build **Vulkan** |
| Ścieżka binarek | `~/.local/opt/llamacpp/llama-b10284/` |
| Model (GGUF) | `~/.local/opt/llamacpp/models/qwen36-hauhau-IQ4_XS.gguf` (symlink na blob ollamy) |
| Kwantyzacja | IQ4_XS — 17.43 GiB, 4.25 bpw |
| Endpoint | `http://127.0.0.1:8080/v1` (OpenAI-compatible) |
| Usługa | `systemd --user`: `llama-qwen36.service` |
| Integracja | opencode, provider `llamacpp`, dwa modele |

Parametry produkcyjne:

```
-ngl 999 --n-cpu-moe 23 -c 65536 -fa on --jinja
```

---

## 2. Dlaczego llama.cpp, a nie ollama

Ollama została zaktualizowana (0.23.2 → 0.32.5) i **model się na niej uruchamia**, ale jest
znacznie wolniejsza. Pomiar tym samym obciążeniem (prompt 1470 tok., generacja 300 tok., kontekst 32k):

| Backend | Prompt | Generacja |
|---|---|---|
| **llama.cpp Vulkan** (`--n-cpu-moe 20`) | **489 t/s** | **41.5 t/s** |
| ollama 0.32.5 (CUDA) | 67.9 t/s | 29.1 t/s |

Przyczyna jest widoczna w `ollama ps`:

```
PROCESSOR: 50%/50% CPU/GPU
```

Ollama dzieli model **całymi warstwami** — połowa warstw trafia na CPU razem z atencją.
llama.cpp z `--n-cpu-moe` trzyma atencję **wszystkich 40 warstw** na GPU i przenosi do RAM
wyłącznie tensory ekspertów. Przy MoE to zasadnicza różnica: eksperty są duże, ale na token
aktywuje się tylko 8 z 256, więc ich odczyt z RAM jest tani. Atencja natomiast dotyka całego
kontekstu przy każdym tokenie i na CPU kosztuje ogromnie — stąd 7-krotna przewaga w prompt
processingu.

**CUDA nie ratuje ollamy**, bo problem leży w strategii podziału, nie w backendzie obliczeniowym.
Vulkan wystarczył, żeby ją pokonać z dużym zapasem.

---

## 3. Charakterystyka modelu

Odczytane z nagłówka GGUF:

| Parametr | Wartość |
|---|---|
| Architektura | `qwen35moe` |
| Parametry | 34.66 B total / ~3 B aktywnych |
| Warstwy | 40 |
| Eksperty | 256, aktywnych 8 |
| Głowice atencji | 16 (KV: 2 — GQA) |
| Kontekst natywny | 262 144 |
| KV cache | ~20 KB / token — **nie 40**, patrz sekcja 11.7 |

Model jest multimodalny (ollama pobrała też projektor CLIP 447 M), ale **wizja nie jest używana** —
zgodnie z ustaleniem konfiguracja jest tekstowa.

Model **rozumuje** — domyślnie generuje długi blok myślenia. Szablon czatu wspiera
`enable_thinking`, więc myślenie da się wyłączyć i to jest największa oszczędność czasu
w pracy agentowej (patrz sekcja 6).

---

## 4. Strojenie: jak dobrano `--n-cpu-moe`

`--n-cpu-moe N` = eksperty z `N` warstw idą do RAM, reszta zostaje na GPU.
Im niższe `N`, tym szybciej — aż do wyczerpania VRAM.

### Sweep przy mikro-kontekście (`llama-bench`, pp512/tg128)

| ncmoe | pp t/s | tg t/s |
|---|---|---|
| 40 (wszystko na CPU) | 359 | 38.5 |
| 28 | 460 | 42.6 |
| 24 | 508 | 44.3 |
| 22 | 529 | 45.4 |
| 20 | 543 | 46.9 |
| 18 | 584 | 48.2 |
| 16 | — | **OOM** |

Te liczby są **zawyżone** — benchmark alokuje KV tylko na ~640 tokenów. Realny kontekst
zjada VRAM, którego przy `ncmoe 18` już nie ma.

### Sweep w warunkach docelowych (`llama-server`, prompt 1470 tok.)

| ncmoe | ctx | KV | VRAM | zapas | pp t/s | tg t/s |
|---|---|---|---|---|---|---|
| 18 | 32k | f16 | — | — | **OOM** | — |
| 20 | 32k | f16 | 11153 MiB | 1.1 GB | 489 | 41.5 |
| 22 | 32k | f16 | 10336 MiB | 1.9 GB | 488 | 39.4 |
| 22 | 65k | f16 | 11007 MiB | 1.3 GB | 468 | 39.7 |
| **23** | **65k** | **f16** | **10599 MiB** | **1.7 GB** | **424** | **39.6** |
| 24 | 65k | f16 | 10191 MiB | 2.1 GB | 458 | 38.6 |
| 26 | 98k | f16 | 10047 MiB | 2.2 GB | 406 | 37.3 |

Pomiary `pp` mają ok. ±10% szumu; `tg` jest stabilne.

### Wybór: `ncmoe 23` przy 64k

Wariant „najszybszy" (`ncmoe 20` / 32k) daje **41.5 t/s**, czyli o 5% więcej — ale zostawia
tylko 1.1 GB zapasu VRAM. Pulpit (GNOME + Firefox + Steam) potrafi zająć od 490 MiB do 1.2 GB
i te wahania obserwowano w trakcie pracy. Przy tak wąskim marginesie otwarcie kilku kart
mogłoby wywalić serwer w środku generacji. Za 5% prędkości dostajemy **dwukrotnie większy
kontekst i 60% więcej zapasu** — dla pracy agentowej to opłacalna zamiana.

**Jeśli zdarzy się OOM przy obciążonym pulpicie**, podnieś `--n-cpu-moe` do `24` (koszt: ~1 t/s).

---

## 5. Co sprawdzono i odrzucono

### Kwantyzacja KV cache (`q8_0`) — odrzucona

Miała zwolnić VRAM i pozwolić na niższe `ncmoe`. Efekt odwrotny do zamierzonego:

| Konfiguracja | pp t/s | tg t/s |
|---|---|---|
| ncmoe 22 / 65k / **f16** | 468 | 39.7 |
| ncmoe 22 / 65k / **q8_0** | **231** | 39.7 |

Na Vulkanie `q8_0` **ścina prompt processing o połowę**, a generacji nie poprawia w ogóle.
Dodatkowo `ncmoe 18` + `q8_0` co prawda się ładuje (11668 MiB), ale zapytanie zwraca zero
tokenów — pada w trakcie liczenia. Zostajemy przy `f16`.

### Liczba wątków — zostawiona domyślna

Ryzen 5 5600 to 6 rdzeni fizycznych / 12 logicznych. Domyślne `-t 6` w llama.cpp odpowiada
liczbie rdzeni fizycznych, czyli jest już optymalne. Podbijanie do 12 na SMT zwykle szkodzi
przy obciążeniu pamięciowym, jakim jest odczyt ekspertów.

### CUDA dla llama.cpp — niedostępna bez roota

llama.cpp nie publikuje prebuiltów CUDA dla Linuksa (tylko dla Windows), a budowa ze źródeł
wymaga `nvcc` i `cmake`, których nie ma w systemie — instalacja obu potrzebuje sudo.
Vulkan okazał się wystarczający: bije ollamę z natywnym CUDA siedmiokrotnie na prompcie.

---

## 6. Integracja z opencode

Dopisany provider w `~/.config/opencode/opencode.json`
(kopia zapasowa: `opencode.json.bak-20260805-173431`):

```json
"llamacpp": {
  "npm": "@ai-sdk/openai-compatible",
  "name": "llama.cpp (lokalny)",
  "options": {
    "baseURL": "http://127.0.0.1:8080/v1",
    "apiKey": "local"
  },
  "models": {
    "qwen36-hauhau":      { "reasoning": true,  "tool_call": true, ... },
    "qwen36-hauhau-fast": { "reasoning": false, "tool_call": true,
      "options": { "chat_template_kwargs": { "enable_thinking": false } } }
  }
}
```

Oba modele wskazują **ten sam proces serwera** — `llama-server` ignoruje pole `model`
w zapytaniu, więc jeden załadowany model obsługuje obydwa wpisy. Różnią się wyłącznie
tym, czy wysyłają `enable_thinking: false`.

Użycie:

```bash
opencode run -m llamacpp/qwen36-hauhau-fast "..."   # bez myślenia — do pracy agentowej
opencode run -m llamacpp/qwen36-hauhau "..."        # z myśleniem — do trudniejszych zadań
```

### Który wariant wybrać

Do opencode domyślnie **`-fast`**. Model rozumujący potrafi spalić 1200+ tokenów na samo
myślenie zanim odpowie (w teście `finish_reason: length` przy limicie 1200 tokenów, treść
odpowiedzi wciąż pusta). Przy 39 t/s to pół minuty zanim cokolwiek się pojawi — w pętli
agentowej z kilkoma krokami robi się z tego kilka minut.

### Zweryfikowane działanie

| Test | Wynik | Czas |
|---|---|---|
| Prosty prompt (`17*23`) | 391 — poprawnie | 37 s |
| Tool call (`get_weather`) | `{"city":"Kraków"}`, `finish_reason: tool_calls` | — |
| Zapis pliku przez narzędzie `Write` | plik utworzony, kod wykonuje się poprawnie | 14 s |
| Pętla wieloetapowa (znajdź i popraw błąd) | odczyt → diagnoza → diff → potwierdzenie | 43 s |
| Wariant z myśleniem (`12% z 850`) | 102 — poprawnie | 35 s |

Tool calling działa poprawnie, łącznie z polskimi znakami w argumentach.

---

## 7. Obsługa serwera

```bash
systemctl --user start   llama-qwen36.service
systemctl --user stop    llama-qwen36.service
systemctl --user status  llama-qwen36.service
journalctl --user -u llama-qwen36.service -f
```

**Autostart jest celowo NIE włączony.** Serwer trzyma ~10.5 GB VRAM i kilka GB RAM przez cały
czas działania, więc uruchamianie go przy każdym logowaniu byłoby marnotrawstwem, jeśli
akurat nie korzystasz z modelu. Jeśli chcesz autostart:

```bash
systemctl --user enable llama-qwen36.service
```

Czas startu: ~20–30 s (wczytanie 17.4 GiB i upload na GPU).

---

## 8. Rzeczy, o których warto wiedzieć

**Pierwsze zapytanie po starcie serwera jest bardzo wolne.** Systemowy prompt opencode ma
8384 tokeny i przy zimnym cache liczy się ~35 s, a równolegle leci osobne zapytanie
generujące tytuł sesji — oba konkurują o sloty. Pierwsza próba testu agentowego przekroczyła
przez to 500 s i została przerwana; kolejne, z ciepłym cache, kończyły się w 14–43 s.
**Nie oceniaj wydajności po pierwszym uruchomieniu.**

**`free -h` nie pokazuje pamięci zajętej przez model — i tak ma być.** llama.cpp wczytuje
GGUF przez `mmap`, więc eksperty zrzucone na CPU są czytane wprost ze zmapowanego pliku,
a nie kopiowane do pamięci anonimowej. Jądro liczy takie strony jako **page cache**, czyli
kolumnę `buff/cache`, nie `used`. Dowód z `/proc/<pid>/status` przy działającym serwerze:

```
VmRSS:    11 914 920 kB   ← realnie zajęte
RssAnon:     736 500 kB   ← tylko 0.7 GB pamięci anonimowej
RssFile:  11 178 420 kB   ← 11.2 GB to zmapowany GGUF
```

To zachowanie pożądane: przy niedoborze pamięci jądro te strony odrzuci i doczyta z NVMe,
zamiast wywołać OOM-killera. Do sprawdzenia realnego zużycia używaj `VmRSS`, nie `free`.

**Model zależy od blobu ollamy.** GGUF nie został zduplikowany — `~/.local/opt/llamacpp/models/`
zawiera symlink do `/usr/share/ollama/.ollama/models/blobs/sha256-c26708a7...`. Oszczędza to
18.7 GB dysku, ale **usunięcie modelu z ollamy zepsuje serwer llama.cpp**. Jeśli chcesz
uniezależnić:

```bash
cp /usr/share/ollama/.ollama/models/blobs/sha256-c26708a77a26d6c0416502832a200de4135e91af8279b5e93c67fe4e4e081aae \
   ~/.local/opt/llamacpp/models/qwen36-hauhau-IQ4_XS.gguf
```

**Jakość polszczyzny bywa niedoskonała.** To odcenzurowany finetune i widać na nim ślady
tuningu — w testach pojawiły się zlepki w rodzaju „Dwie two asystentki" czy „zwrita".
Kod i logika są poprawne, ale do tekstu po polsku model bywa niechlujny.

**Ollama zachowana i sprawna.** Aktualizacja do 0.32.5 nie ruszyła istniejących modeli
(dolphin3, bielik, llama3.2 działają). Model Qwen3.6 jest w niej dostępny jako
`hf.co/HauhauCS/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive:IQ4_XS`, gdyby był potrzebny
poza llama.cpp — tylko wolniej.

---

## 9. Dane operacyjne z instalacji

| Operacja | Zmierzony czas |
|---|---|
| Pobranie modelu (18.7 GB z HF przez ollama) | **22 min 50 s** (24 MB/s na starcie, 12 MB/s pod koniec) |
| Pobranie llama.cpp Vulkan (31 MB) | < 10 s |
| Start serwera (zimny) | 20–30 s |
| Sweep `llama-bench`, 6 konfiguracji | 97 s (ciepły page cache) |

Przy kolejnym pobieraniu modelu tej wielkości z HuggingFace zakładaj **~20–25 min**, nie 10.

---

## 10. Model do programowania — Qwen3.6-35B-A3B oficjalny (2026-08-06)

### Dlaczego akurat ten

Model z sekcji 1–9 to **odcenzurowany finetune** bazy `Qwen/Qwen3.6-35B-A3B`. Takie tuningi
konsekwentnie psują instruction-following, a cenzura i tak nie dotyka kodu — do programowania
to strata bez żadnego zysku. Oficjalne wagi (Apache 2.0, kwiecień 2026) dają:

| Benchmark | Wynik |
|---|---|
| SWE-bench Verified | 73.4 |
| SWE-bench Pro | 49.5 |
| LiveCodeBench v6 | 80.4 |
| Terminal-Bench 2.0 | 51.5 |

**Alternatywy odrzucone.** `Qwen3.6-27B` jest gęsty — Q4 to ~16 GB, więc atencja musiałaby
trafić na CPU, czyli dokładnie ta patologia, przez którą przegrała ollama (sekcja 2).
MoE z 3 B aktywnych bije to bezkonkurencyjnie na tym sprzęcie.

### Plik

```
~/.local/opt/llamacpp/models/Qwen3.6-35B-A3B-UD-Q4_K_XL.gguf
```

22 360 456 160 B (20.82 GiB), z `unsloth/Qwen3.6-35B-A3B-GGUF`. Kopia własna, **bez zależności
od ollamy** — w przeciwieństwie do modelu z sekcji 1.

Wybrano UD-Q4_K_XL zamiast IQ4_XS, bo Unsloth Dynamic trzyma wrażliwe tensory w wyższej
precyzji. Cena: plik o 19% większy, więc więcej ekspertów ląduje w RAM.

### Strojenie `--n-cpu-moe` (65 536 ctx, prompt 3579 tok., generacja wymuszona 300 tok.)

| ncmoe | VRAM | zapas | pp | tg |
|---|---|---|---|---|
| 24 | — | — | **OOM przy ładowaniu** | — |
| 26 | 11 586 MiB | 702 MiB | — | **0 tokenów** — pada w liczeniu |
| 27 | 11 069 MiB | 1219 MiB | 427 t/s | 39.4 t/s |
| **28** | **10 582 MiB** | **1706 MiB** | **418 t/s** | **39.8 t/s** |
| 29 | 10 078 MiB | 2210 MiB | 381 t/s | 36.3 t/s |
| 30 | 9 603 MiB | 2685 MiB | 341 t/s | 33.9 t/s |

`ncmoe 26` powtarza patologię z sekcji 5: ładuje się, ale zapytanie zwraca zero tokenów.
Sam start serwera **nie jest** dowodem, że konfiguracja działa — zawsze wyślij zapytanie.

**Wybór: `28`.** Jest tak samo szybkie jak `27` (różnica w granicach szumu), a daje
487 MiB więcej zapasu VRAM.

> **Uwaga metodologiczna.** Liczby w tej sekcji mierzono promptem 3579-tokenowym i generacją
> wymuszoną przez `ignore_eos`, więc **nie porównuj ich wprost z sekcją 4** (prompt 1470 tok.).
> Pierwsze podejście bez `ignore_eos` dało niemonotoniczną krzywą (ncmoe 29 „szybsze" od 27),
> bo generacja urywała się na ~110 tokenach — próbka za mała.

### Koszt względem modelu odcenzurowanego

Ten sam prompt, oba modele:

| Model | ncmoe | pp | tg |
|---|---|---|---|
| HauhauCS IQ4_XS (17.43 GiB) | 23 | 482 t/s | **43.0 t/s** |
| Oficjalny UD-Q4_K_XL (20.82 GiB) | 28 | 418 t/s | **39.8 t/s** |

**−7.4% generacji i −13% promptu.** Świadoma zamiana: 7% prędkości za oficjalne wagi.

### Parametry produkcyjne

```
-ngl 999 --n-cpu-moe 28 -c 65536 -fa on --jinja
--temp 0.6 --top-p 0.95 --top-k 20 --min-p 0.0
```

Sampling zgodny z zaleceniami Qwen dla zadań programistycznych.

### Usługa

```bash
systemctl --user start llama-qwen36-coder.service
```

Obie usługi mają wzajemny `Conflicts=`, więc start jednej zatrzymuje drugą — nie da się
przypadkiem uruchomić dwóch serwerów na porcie 8080 i wyczerpać VRAM.

### opencode

Dopisane modele (kopia zapasowa konfiguracji: `opencode.json.bak-20260806-*`):

```bash
opencode run -m llamacpp/qwen36-coder-fast "..."   # bez myślenia — domyślny
opencode run -m llamacpp/qwen36-coder      "..."   # z myśleniem
```

Wpisy `qwen36-hauhau*` nadal istnieją, ale `llama-server` ignoruje pole `model` w zapytaniu —
**o tym, który model faktycznie odpowiada, decyduje uruchomiona usługa, a nie nazwa w opencode.**

### Zweryfikowane działanie

| Test | Wynik | Czas |
|---|---|---|
| `merge_intervals` bez myślenia | kod poprawny | 12 s |
| Tool call (`read_file`) | `{"path":"src/main.py"}`, `finish_reason: tool_calls` | 4 s |
| Pętla agentowa w opencode (znajdź i popraw błąd) | odczyt → diagnoza → edycja → uruchomienie → potwierdzenie | **29 s** |
| Reasoning (złożoność wyszukiwania anagramów) | O(N·L), poprawnie | 32 s |
| Polszczyzna (mutex vs semafor) | czysta, bez zlepków | — |

Pętla agentowa jest **szybsza niż na modelu odcenzurowanym** (29 s vs 43 s) mimo niższego
tg — model marnuje mniej tokenów.

### Rzeczy, o których warto wiedzieć

**Reasoning wymaga budżetu ≥ 4000 tokenów wyjścia.** Przy limicie 900 tokenów samo myślenie
zjadło całość i odpowiedź była **pusta** (`finish_reason: length`) — to wygląda jak zepsuty
model, a jest tylko za ciasny limit. Przy 4000 tokenów: 1216 wygenerowanych, odpowiedź
poprawna, 32 s. Do pracy agentowej i tak domyślny jest wariant `-fast`.

**`opencode run` wiesza się bez `</dev/null`.** Przy zadaniu wymagającym narzędzi (edycja
pliku, uruchomienie polecenia) czeka na potwierdzenie uprawnień na stdin. Objaw mylący:
serwer nie dostaje **żadnego** zapytania, więc wygląda to na zawieszony model. W skryptach:

```bash
opencode run -m llamacpp/qwen36-coder-fast "..." </dev/null
```

**Mieszanie języków w dłuższych wypowiedziach po polsku.** W teście agentowym w polskim
zdaniu pojawiło się rosyjskie „который пропускał". Kod i logika są bez zarzutu, ale do
tekstu po polsku model bywa niechlujny — podobnie jak jego odcenzurowany krewniak.

**Pobieranie przeżywa przerwanie sesji.** `curl -C -` wznawia od miejsca przerwania;
pobranie 20.82 GiB zajęło łącznie ~21 min przy 12–24 MB/s.

---

## 11. Porównanie z modelami chmurowymi i budżet pamięci (2026-08-06)

### 11.1. Najważniejsze: to jest ten sam model, co `neuralwatt/qwen3.6-35b`

Nagłówek GGUF nie zostawia wątpliwości:

```
general.base_model.0.repo_url = https://huggingface.co/Qwen/Qwen3.6-35B-A3B
general.quantized_by          = Unsloth
general.license               = apache-2.0
```

Lokalny model **nie jest podobny** do `neuralwatt/qwen3.6-35b` — to są te same wagi.
Cała różnica to kwantyzacja (5.16 bpw lokalnie vs pełna precyzja w chmurze), kontekst
(65 536 vs 131 056) i prędkość. Pytanie „z czym się równa" ma więc jedną odpowiedź wprost:
**równa się dokładnie sobie u dostawcy**, minus straty kwantyzacji.

### 11.2. Zestawienie

Ceny z `~/.config/opencode/opencode.json` ($/M tokenów). Prędkości lokalne zmierzone
(sekcja 10), chmurowe — nie mierzone.

| Model | Gdzie | Klasa | Ctx | in / out | Relacja do lokalnego |
|---|---|---|---|---|---|
| **Qwen3.6-35B-A3B UD-Q4_K_XL** | lokalnie | 35B-A3B | 65k | 0 / 0 | — |
| `qwen3.6-35b` / `-fast` | NeuralWatt | 35B-A3B | 131k | 0.29 / 1.15 | **te same wagi**, pełna precyzja, 2× ctx |
| `gemma-4-31b` | NeuralWatt | 31B gęsty | 262k | 0.144 / 0.42 | **ta sama półka**, inne mocne strony |
| `deepseek-v4-flash` | NeuralWatt | mały MoE | 1M | 0.10 / 0.21 | **porównywalny**, dużo tańszy w czasie |
| `kimi-k2.6` / `-fast` / `-flex` | NeuralWatt | frontier MoE | 262k | 0.69 / 3.22 | wyraźnie wyżej |
| `qwen3.5-397b` / `-fast` | NeuralWatt | 397B MoE | 262k | 0.69 / 4.14 | wyraźnie wyżej |
| `kimi-k2.7-code` / `-flex` | NeuralWatt | frontier, kod | 262k | 0.95 / 4.00 | wyraźnie wyżej |
| `glm-5.2` (4 warianty) | NeuralWatt | frontier MoE | 1M | 1.45 / 4.50 | wyraźnie wyżej |
| `grok-4.5` | xAI (forge) | frontier | — | — | wyraźnie wyżej |
| Claude Opus 5 | Anthropic | frontier | — | — | wyraźnie wyżej |

> Podział na półki dla modeli chmurowych to **ocena po klasie parametrów i cenniku**,
> nie pomiar. Nie uruchamiano tu benchmarków porównawczych. Cennik jest jednak dobrym
> sygnałem: dostawca wycenia `qwen3.6-35b` na 1.15 $/M wyjścia, a `glm-5.2` na 4.50 $/M —
> czterokrotna różnica ceny nie bierze się z niczego.

### 11.3. Z czym się „jako tako" równa

**Równa się w pełni:** `neuralwatt/qwen3.6-35b` i `qwen3.6-35b-fast`. To ten sam model.
Q4_K_XL z imatrixem Unslotha (`quantize.imatrix.entries_count = 510`, 76 chunków kalibracji)
kosztuje ułamek procenta jakości na kodzie. Do zadań, w których i tak wybierałbyś
`qwen3.6-35b`, **lokalny wariant jest pełnoprawnym zamiennikiem za darmo.**

**Równa się z grubsza:** `gemma-4-31b` i `deepseek-v4-flash`. Ta sama półka wielkościowa.
Qwen ma przewagę na kodzie i pracy agentowej (SWE-bench Verified 73.4, LiveCodeBench v6 80.4
— sekcja 10); Gemma ma wizję, 262k kontekstu i zwykle czystszy tekst naturalny, co przy
znanej słabości Qwena do mieszania języków po polsku (sekcja 10) bywa istotne.

**Nie równa się:** Kimi K2.6/K2.7, GLM-5.2, Qwen3.5-397B, Grok-4.5, Opus. To modele o rząd
wielkości większe. Na krótkich, dobrze zdefiniowanych zadaniach lokalny model często wystarczy,
ale na długich pętlach agentowych, wielopunktowym refaktorze i zadaniach wymagających
trzymania planu przez kilkadziesiąt kroków różnica jest widoczna i nie da się jej nadrobić.

**Realny koszt lokalnego modelu to nie pieniądze, tylko czas.** Prompt processing 418 t/s
oznacza, że systemowy prompt opencode (8384 tokeny) liczy się ~20 s przy zimnym cache —
w chmurze to ułamek sekundy. Przy 39.8 t/s wygenerowanie 1 M tokenów zajmuje ~7 h.
Do pętli agentowej z dziesiątkami kroków to jest ograniczenie twardsze niż jakość modelu.

**Praktyczny podział:** lokalnie wszystko, co nie wymaga półki frontier i gdzie liczy się
prywatność albo zero kosztu; NeuralWatt do zadań, które lokalny model ledwo ciągnie;
frontier tam, gdzie zadanie faktycznie tego wymaga.

### 11.4. Dlaczego plik ma 20.82 GiB

Rozmiar to **liczba parametrów × bity na parametr**, a nie wymóg VRAM.

```
22 360 456 160 B × 8 / 34.66e9 param = 5.16 bpw
```

Q4_K_XL to nie „równe 4 bity". Unsloth Dynamic trzyma embeddingi, wyjście i tensory atencji
w wyższej precyzji, a kwantyzuje agresywnie tylko eksperty — stąd 5.16 bpw średniej.
Dla porównania IQ4_XS z sekcji 1 ma 4.32 bpw, czyli 17.43 GiB. Te 19% różnicy to właśnie
cena dokładności na wrażliwych tensorach.

**Gdzie te bajty siedzą.** Z metadanych GGUF: 256 ekspertów × 3 macierze × 2048 × 512 × 40 warstw
= **32.21 mld parametrów w samych ekspertach**, czyli 93% modelu. Reszta — atencja, SSM,
embeddingi — to ~1.7 GiB.

Wynik zgadza się z pomiarem: w sweepie z sekcji 10 każda warstwa przeniesiona z GPU do RAM
zmienia zużycie VRAM o **489 MiB** (487 / 504 / 475 dla przejść 27→28→29→30). 40 × 489 MiB
= 19.1 GiB, wobec 19.3 GiB policzonych z metadanych. **Dwa niezależne wyliczenia zgadzają się
w granicach 1%.**

Dlatego 20.82 GiB nie musi zmieścić się w 12 GB VRAM — mieści się w **VRAM + RAM naraz**,
a `--n-cpu-moe` decyduje o proporcji.

### 11.5. Zmierzony budżet pamięci — czy warto sięgnąć wyżej

Pomiar na działającym serwerze (ncmoe 28, ctx 65 536):

| Wielkość | Wartość |
|---|---|
| `VmRSS` | 17.07 GB (16.28 GiB) |
| `RssFile` (zmapowany GGUF) | 15.84 GB (14.75 GiB) |
| `RssAnon` | 1.23 GB |
| `VmSwap` | **248 MB — proces już jest częściowo wyswapowany** |
| VRAM (cały GPU) | 11 163 / 12 288 MiB |
| RAM: total / apps / swap used | 30.3 GiB / 7.5 GiB / **2.2 GiB** |

Rozkład na GPU przy ncmoe 28: 12 warstw ekspertów (5.73 GiB) + tensory nie-ekspertowe (1.7 GiB)
= 7.43 GiB wag, reszta do 10.33 GiB to KV i bufory obliczeniowe (~2.9 GiB).

#### Sufit sprzętowy

RAM 30.3 GiB − 7.5 GiB na aplikacje = **22.8 GiB** realnie dostępne na page cache modelu.
VRAM na wagi ≈ **7.4 GiB** (reszta na KV, bufory i pulpit). Sufit na wagi: **~30 GiB**.

| Kwantyzacja | bpw | Rozmiar | ncmoe | RAM modelu | Prognoza tg | Werdykt |
|---|---|---|---|---|---|---|
| IQ4_XS | 4.32 | 17.4 GiB | ~26 | 11.3 GiB | ~43 t/s | szybsze, gorsze wagi |
| **UD-Q4_K_XL (obecne)** | **5.16** | **20.8 GiB** | **28** | **14.8 GiB** | **39.8 t/s** | **obecne** |
| UD-Q5_K_XL | ~5.95 | ~24 GiB | ~30 | ~16.1 GiB | **~32 t/s** | nie warto |
| Q6_K | ~6.6 | ~26.6 GiB | ~32 | ~19 GiB | ~27 t/s | na granicy thrashingu |
| Q8_0 | ~8.5 | ~34.3 GiB | — | ~26 GiB | — | **nie mieści się** |

Prognozy tg wynikają z tego, że przy MoE z offloadem generacja jest ograniczona
**przepustowością RAM**, a nie mocą obliczeniową. Ruch na token rośnie dwukrotnie: raz przez
większe wagi (5.95/5.16 = 1.15×), drugi raz przez to, że mniej warstw mieści się na GPU
(30/28 = 1.07×). Razem 1.23× więcej bajtów na token → 39.8 / 1.23 ≈ 32 t/s. Zgadza się to
z pomiarem z sekcji 10, gdzie samo podniesienie ncmoe do 30 dało 33.9 t/s.

#### Werdykt: **nie warto**

1. **Zysk jakości jest znikomy.** Q4_K_XL z imatrixem to już nie jest „stratna czwórka" —
   wrażliwe tensory siedzą wyżej, a różnica Q4→Q5 na modelach MoE mieści się poniżej procenta
   perplexity. Płacisz 20% prędkości za coś, czego nie zmierzysz w codziennej pracy.
2. **RAM już teraz jest napięty.** Proces ma **248 MB w swapie**, a system 2.2 GiB.
   Przy 14.75 GiB page cache. Podniesienie do 16–19 GiB przy 7.5 GiB zajętych przez aplikacje
   oznacza wypychanie do swapu w trakcie generacji — a to nie kosztuje 20%, tylko rzędy wielkości.
3. **Wąskim gardłem nie są bity, tylko 3 mld aktywnych parametrów.** Piąty bit tego nie zmieni.
   Jakościowy skok wymagałby większego modelu, a nic sensownego się nie mieści: MoE klasy 100B
   nawet w Q3 to ~45 GiB, wobec sufitu 30 GiB.

**Odwrotny kierunek warto rozważyć.** Jeśli częściej brakuje Ci prędkości i kontekstu niż
jakości, oficjalne wagi w IQ4_XS (~17.4 GiB) dałyby ncmoe ~26, czyli ~43 t/s i ~1.5 GiB
więcej zapasu VRAM — przy tych samych oficjalnych wagach, bez odcenzurowanego finetune'u.

### 11.6. Co zrobić z zasobami zamiast zwiększać kwantyzację

Trzy rzeczy dają więcej niż piąty bit, w kolejności opłacalności.

#### (a) `--no-mmap` — llama.cpp sam to podpowiada, a nie jest włączone

W logu startowym serwera, linia 9:

```
W llama_model_loader: tensor overrides to CPU are used with mmap enabled
                      - consider using --no-mmap for better performance
```

To jest ostrzeżenie skierowane dokładnie do naszej konfiguracji. Przy `--n-cpu-moe` eksperty
czytane są z pliku zmapowanego przez `mmap` (sekcja 8 opisuje to jako zaletę — i jest zaletą
dla odporności na OOM, ale **nie dla prędkości**). Z `--no-mmap` trafiają do zwykłej pamięci
anonimowej, co eliminuje narzut page-faultów przy każdym odczycie eksperta.

Bilans jest realny w obie strony:

| | mmap (obecnie) | `--no-mmap` |
|---|---|---|
| Prędkość odczytu ekspertów | z page cache, przez page fault | wprost z RAM |
| Zachowanie przy braku RAM | jądro odrzuca strony, doczytuje z NVMe | **OOM-killer** |
| `free -h` pokazuje | `buff/cache` | `used` |
| Start serwera | szybki | wolniejszy (pełny odczyt 20.8 GiB) |

Przy 7.5 GiB zajętych przez aplikacje i 14.8 GiB modelu margines wynosi ~8 GiB — powinno
wystarczyć, ale utrata siatki bezpieczeństwa jest prawdziwa. **Do zmierzenia, nie do wdrożenia
w ciemno.** Jeśli zysk jest w granicach szumu, zostaw mmap dla bezpieczeństwa.

#### (b) Kontekst 65k → 128k kosztuje mniej, niż się wydaje

Patrz 11.7 — atencja pełna działa tylko w 10 z 40 warstw, więc podwojenie kontekstu to
+1.25 GiB KV, nie +2.5 GiB. Przy zapasie 1706 MiB (ncmoe 28) trzeba by zejść na ncmoe 29
(zapas 2210 MiB, tg 36.3 t/s) i wyszłoby ~930 MiB zapasu przy 131k kontekstu.
**Dwukrotny kontekst za 9% prędkości** — lepszy interes niż piąty bit za 20%.

#### (c) `-np 2` zamiast domyślnych 4 slotów

Log potwierdza patologię z sekcji 8 pomiarem. Przy dwóch równoległych zadaniach:

```
slot 1 | prompt processing, n_tokens = 2048 | 348.82 t/s
slot 2 | prompt processing, n_tokens =   47 |   2.54 t/s
```

Slot 2 dostał **2.54 t/s** — 137 razy mniej niż slot 1. Cztery sloty przy jednym użytkowniku
nie dają współbieżności, tylko głodzenie i dodatkowy stan rekurencyjny w VRAM
(~63 MiB na slot). `-np 2` zwalnia ~125 MiB i ogranicza problem.

### 11.7. Korekta sekcji 3: KV cache to ~20 KB/token, nie 40

To model **hybrydowy**, co sekcja 3 przeoczyła. Metadane:

```
qwen35moe.full_attention_interval = 4      ← pełna atencja co 4. warstwę
qwen35moe.ssm.state_size          = 128    ← pozostałe warstwy: stan SSM
qwen35moe.ssm.conv_kernel         = 4
qwen35moe.ssm.inner_size          = 4096
qwen35moe.attention.head_count_kv = 2
qwen35moe.attention.key_length    = 256
qwen35moe.attention.value_length  = 256
```

Pełną atencję ma **10 z 40 warstw**; pozostałe 30 to warstwy rekurencyjne o stanie
**stałym niezależnie od długości kontekstu**. Stąd:

```
2 głowice KV × (256 + 256) × 2 B (f16) = 2 KiB / token / warstwę
× 10 warstw pełnej atencji            = 20 KiB / token
× 65 536 tokenów                      ≈ 1.25 GiB
```

plus ~63 MiB stanu rekurencyjnego na slot (×4 sloty ≈ 250 MiB). Razem ~1.5 GiB, wobec
~2.9 GiB zmierzonych na KV i bufory — reszta to bufory obliczeniowe, więc rząd wielkości
się zgadza.

**Konsekwencja praktyczna:** kontekst w tym modelu jest tani i skaluje się liniowo tylko
w ¼ warstw. Wniosek z sekcji 5, że kwantyzacja KV (`q8_0`) nie opłaca się na Vulkanie,
zyskuje drugi argument — nie ma tu wiele do zaoszczędzenia, bo KV i tak jest małe.

> Liczby w tej sekcji są wyprowadzone z metadanych GGUF i skonfrontowane ze zmierzonym
> zużyciem VRAM, ale **nie odczytane wprost z logu alokacji** — llama.cpp b10284 przy
> obecnym poziomie logowania nie wypisuje linii `llama_kv_cache: ... KV buffer size`.
> Żeby potwierdzić, uruchom serwer z `-lv 5` i sprawdź fazę ładowania.
