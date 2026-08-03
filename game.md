# Brief gry — Total Battle Brothers (nazwa robocza)

> Wejście dla bootstrapu orkiestratora. Ma być jasne i jednoznaczne — agent
> rozwinie to w `docs/DESIGN.md` i pokroi na małe zadania TDD. Sekcje „Założenia
> MVP" i „Otwarte pytania" oznaczają, gdzie decyzja nie jest jeszcze przesądzona.

## Pitch
Single-player **sandbox** (bez scenariuszowej kampanii): strategia turowa łącząca
zarządzanie osadami i armiami z taktycznymi bitwami na heksach w stylu **Battle
for Wesnoth / Battle Brothers**. Grasz jednym księstwem przeciw księstwom
sterowanym przez **AI**. Skala kameralna: małe osady, nieliczne wojska, każda
jednostka się liczy.

## Klimat
Średniowiecze **bez magii i fantastyki**. Surowy, realistyczny ton.

## Strony i start
- **Single player vs AI.** Każde księstwo (gracza i AI) startuje z **1–3 osadami**
  w różnym stopniu rozwoju.
- Brak neutralnych band — przeciwnikami są księstwa AI.

## Warstwa strategiczna (turowa, sandbox)
- **Mapa:** w stylu **Total War** — regiony/prowincje z osadami; party
  przemieszcza się po mapie (punkty ruchu / koszt w turach), a bitwa startuje przy
  kontakcie z wrogą osadą lub party.
- **Czas:** jedna tura = **1 miesiąc**. Rok = **13 miesięcy po 4 tygodnie**.
  Trening i wyposażenie liczą się w miesiącach.
- **Bohater:** dokładnie jeden na księstwo — król i dowódca w jednym. Armia rusza
  się tylko razem z bohaterem; bez niego jednostki stoją (mogą zostać w osadzie
  jako **garnizon** — obrona).
- **Party:** bohater prowadzi maksymalnie **12 jednostek**.
- **Następstwo:** gdy bohater ginie, przejmuje **wyznaczony dziedzic** — osady i
  wojownicy tracą wtedy morale, ale gra toczy się dalej.
- **Przegrana:** utrata **wszystkich** osad **oraz** śmierć bohatera (nie ma
  dziedzica ani osady, z której by go wystawić).

## Osady, populacja i ekonomia
- **Surowce:** **pszenica** i **złoto** (dwa, celowo prosto).
- **Populacja** to kluczowy wskaźnik osady. Rośnie przez **urodzenia** i
  **imigrantów**.
- Populacja to pula ludzi zajmowana przez:
  - **rekrutację jednostek** — jednostki pochodzą z populacji osady;
  - **obsadę budynków** — np. kowal musi być mieszkańcem tej osady, więc zbyt
    mała populacja **nie pozwala uruchomić** warsztatu kowala.
- **Zwolnienie populacji:** zamknięcie/opuszczenie budynku (np. karczmy) oddaje
  1 populację z powrotem do puli.
- Gracz rozwija osady (budynki), zakłada nowe, może podbijać osady AI.

## Jednostki i progresja
Jakość jednostki wynika z trzech niezależnych filarów:
- **Trening** — czas + odpowiednie budynki. Silny zysk na początku, potem
  malejący (najszybciej się „nasyca" z trzech).
- **Uzbrojenie** — surowce + czas/budynki. Podobnie malejący zysk.
- **Doświadczenie** — wyłącznie z walki. Wpływ nieco słabszy niż dwa powyższe.

## Warstwa bitwy (styl Wesnoth / Battle Brothers)
- Turowa, na siatce **heksów**, sterujesz pojedynczymi jednostkami.
- **Teren** ma znaczenie (modyfikatory).
- **Jednostki dystansowe** obecne (model jak w Wesnoth / Battle Brothers).
- **Morale** wpływa wyłącznie na **celność** (bonus/kara do trafienia) — nie
  powoduje ucieczek.
- **Śmierć permanentna**, ale zamiast zginąć jednostka może zostać **ogłuszona**
  i odnieść ranę — **trwałą lub czasową**.

## Założenia MVP (propozycja — do potwierdzenia)
Najmniejsza grywalna pętla, single-player vs jedno księstwo AI:
1. Twoje księstwo: 1 osada z populacją, pszenicą i złotem; przeciwne księstwo AI.
2. Rozwój: rekrutuj jednostki z populacji, trenuj i wyposażaj (surowce + miesiące).
3. Bohater prowadzi party do wrogiej osady/party (garnizon może zostać w obronie).
4. Bitwa na heksach: teren, walka wręcz + dystans, morale→celność, ogłuszenia/rany,
   permanentna śmierć.
5. Cel sandboxa: pokonać księstwo AI (utrata jego osad + bohatera).

## Poza zakresem (na start)
Scenariuszowa kampania/fabuła, multiplayer sieciowy, magia/fantastyka, oddziały
masowe (np. 60 ludzi w jednostce), grafika AAA/dźwięk, edytor map.

## Warstwa wizualna (zmiana zakresu — dopisana po starcie projektu)
> Poprzednia wersja tego briefu w ogóle nie planowała grafiki poza rdzeniem
> logiki. To poniższe jest świadomym rozszerzeniem zakresu, nie sprzecznością
> do zignorowania — potraktuj jako nowy wymóg, nie sugestię.

Rdzeń logiki (strategia + bitwa) zostaje jak dotąd oddzielony od prezentacji.
Niemniej gra ma dostać **minimalną, ale realną warstwę wizualną** — nie tylko
tekstowy/headless output:
- Mapa strategiczna: widok regionów/osad/party w 2D (może być prosty,
  schematyczny — nie chodzi o AAA, chodzi o to, żeby dało się grać patrząc,
  a nie czytając logi).
- Bitwa: siatka heksów renderowana wizualnie, z jednostkami i terenem
  widocznymi na ekranie, sterowanie myszą/klawiaturą.
- Silnik/biblioteka do wyboru przez agenta bootstrapu (np. pygame, arcade,
  albo web/canvas) — ma uzasadnić decyzję w `docs/ARCHITECTURE.md`, tak samo
  jak wybór języka.
- To ma być **realne zadanie w BACKLOG.md**, nie punkt odłożony bezterminowo
  na „później". Jeśli rdzeń (logika strategii + bitwy) jest już w dużej
  mierze gotowy, warstwa wizualna to następny priorytet, nie ostatni.

## Kwestie techniczne
- Rdzeń logiki (strategia + bitwa) **oddzielony od prezentacji**, żeby dało się go
  rozwijać w TDD. Wybór języka/silnika zostawiamy agentowi bootstrapu — ma
  uzasadnić decyzję w `docs/ARCHITECTURE.md`.
- Można wykorzystać kod/zasoby z Battle for Wesnoth, ale to opcjonalne.



-------------------------------
## ZMIANA / DECYZJA: KLIENT GODOT NA LINUX (obowiązuje TERAZ)

> Świadoma zmiana zakresu, nie sugestia. Nadpisuje luźniejsze zapisy o
> warstwie wizualnej powyżej oraz dotychczasową praktykę „czytelnego dumpa
> HTML". Planista w następnym przebiegu ma wpisać to do `docs/DESIGN.md` i
> ustawić jako bieżący priorytet w `BACKLOG.md`.

**Decyzja:** warstwa wizualna to natywna gra 2D w **Godot 4**, dystrybuowana
jako pojedyncza aplikacja na **Linux x86-64** — gracz nie odpala terminala,
Pythona ani Godota-developera ręcznie. Gotowe open source assety (CC0, np.
Kenney/OpenGameArt) są OK.

**Priorytet:** budowa klienta Godota jest priorytetem, nie zadaniem
odłożonym na „później". Jeśli rdzeń logiki (kampania + bitwa) jest już w
dużej mierze gotowy, kolejne zadania mają rozwijać **widoczną, grywalną
grę** — nie tylko dokładać kolejne reguły/mechaniki bez warstwy, w której
dałoby się ich użyć. Planista nie może w nieskończoność priorytetyzować
mechaniki kosztem grafiki/klienta.

**Niezmienniki (nie do negocjacji):**
- Rdzeń `tbb` pozostaje **jedynym źródłem reguł gry** (stan kampanii,
  ekonomia, bitwa, zapis/odczyt). Godot nie duplikuje logiki gry; Python nie
  zależy od Godota ani żadnego UI.
- Komunikacja Godot↔Python przez jawny, testowalny interfejs (stan gry jako
  JSON). Konkretny transport/kształt API (HTTP, socket, inny), podział na
  sceny/węzły Godota i kolejność prac **wybiera i uzasadnia agent
  bootstrapu/planista** w `docs/ARCHITECTURE.md` — jak dotąd z wyborem
  języka/silnika.
- Istniejący klient HTML/SVG zostaje wyłącznie jako narzędzie diagnostyczne —
  nie jest już docelowym klientem gry.
- Gotowe dopiero, gdy użytkownik uruchamia natywną aplikację na Linuksie i bez
  terminala może: zarządzać osadą, przemieszczać armię, rozegrać bitwę,
  zapisać i wczytać stan.

Szczegółowe, **niewiążące** notatki projektowe (przykładowe node'y Godota,
szkic API, sugerowany podział scen i kolejność prac) — w `godot-notes.md`.
To inspiracja, nie specyfikacja: agent może je wykorzystać, zmienić albo
zignorować, jeśli uzasadni lepsze rozwiązanie w `ARCHITECTURE.md`.


---------------
Nowa zmiana: w agent-loop zostało dodane review dla kroku bootstrap oraz podział na trudność zadań przy planowaniu. Warto uwzględnić.


---------------
Nowa zmiana: w agent-loop dużo bugów i usprawnień zostało zaimplementowanych. Uwzględnij to. Warto chyba posprzątać repo gry też.


-----------
UWAGA - feedback dla agentów - prawdziwe MVP będzie wtedy, kiedy będą assety i tekstury. Nie musi być dużo budynków/rodzajów jednostek/terenu itp, ale żeby były jakieś sensowne prawdziwe assety.


## STAŁA REGUŁA PLANOWANIA: PRIORYTET OPRAWY GRAFICZNEJ

To jest obowiązująca reguła wykonawcza, a nie sugestia.

Dopóki nie zostanie osiągnięty opisany niżej próg jakości wizualnej, przy KAŻDYM
wywołaniu planisty i przy KAŻDYM nowym batchu Forge:

- batch musi zawierać co najmniej 4 zadania graficzne;
- batch może zawierać najwyżej 2 zadania mechaniczne;
- łączna liczba zadań w batchu nie może przekroczyć 6;
- batch bez co najmniej 4 zadań graficznych jest nieprawidłowy i musi zostać
  przeplanowany.

Zadania mechaniczne wolno planować wyłącznie wtedy, gdy są bezpośrednią,
niezbędną zależnością aktualnych zadań graficznych. Nie wolno planować
niezależnych mechanik kosztem wymaganej liczby zadań graficznych.

Jeśli backlog nie zawiera wystarczającej liczby zadań graficznych, planista nie
może zwrócić `no_more_tasks`. Ma najpierw utworzyć konkretne małe zadania
graficzne wynikające z tego wymagania i dopiero potem zbudować batch.

### Co jest zadaniem graficznym

Zadanie graficzne musi dostarczać widoczny efekt w natywnym kliencie Godot.
Może obejmować:

- dodanie nowych prawdziwych assetów CC0 lub CC-BY;
- podmianę istniejących placeholderów na lepsze assety;
- spójne kafle mapy, osady, budynki, tło, ikony rozkazów i sylwetki jednostek;
- poprawę prezentacji zaznaczenia, celu, właściciela, armii i stanu gry;
- podłączenie assetów do scen Godota;
- poprawę kompozycji, skali, kontrastu i czytelności istniejących widoków;
- aktualizację `CREDITS.md`, importu Godota i testów ładowania/prezentacji.

Samo dodanie testu, dokumentacji albo refaktoru bez widocznego efektu w grze
nie liczy się jako zadanie graficzne.

Każde zadanie graficzne musi:

- wskazywać konkretne assety lub elementy oprawy;
- wskazywać miejsce ich użycia w grze;
- kończyć się widocznym rezultatem po uruchomieniu Godota;
- zawierać sprawdzenie rzeczywistego wyglądu przez screenshot albo ludzkie
  review;
- zapisywać źródło i licencję assetów w `CREDITS.md`.

### Obowiązujący zakres wizualny

K87 uznaj za techniczne minimum, a nie za zakończenie rozwoju grafiki.
Istnienie obecnych tekstur nie zwalnia planisty z dalszego rozwijania oprawy.

Kolejne przyrosty mają kolejno poprawić:

1. spójność i różnorodność kafli mapy;
2. wygląd osad i budynków;
3. tło oraz kompozycję mapy strategicznej;
4. ikony i prezentację rozkazów;
5. sylwetki jednostek oraz czytelność ich stron;
6. prezentację zaznaczenia celu i stanu gry;
7. spójność stylistyczną obu widoków.

Celem nie jest grafika AAA, lecz spójna, czytelna i wyraźnie mniej prototypowa
gra 2D o średniowiecznym, realistycznym charakterze.

### Bezwzględnie wykluczone

Do czasu osiągnięcia progu jakości wizualnej nie planuj niezależnych zadań
dotyczących:

- nowych reguł gry;
- AI;
- ekonomii;
- walki;
- ruchu;
- nowych rozkazów;
- protokołu, snapshotu lub mostu;
- zmian rdzenia Python;
- zapisu i odczytu;
- porządkowania repozytorium;
- dokumentacji niezwiązanej bezpośrednio z oprawą.

Zmiany rdzenia, mostu albo protokołu są dozwolone wyłącznie wtedy, gdy są
bezpośrednią i niezbędną zależnością konkretnego zadania graficznego.

### Warunek zakończenia priorytetu graficznego

Priorytet graficzny można uznać za zakończony dopiero wtedy, gdy:

- mapa strategiczna, osady i armie używają spójnych prawdziwych assetów;
- widok bitwy ma czytelne kafle, jednostki i strony;
- interfejs nie opiera się na przypadkowych placeholderach;
- wszystkie nowe assety mają źródła i licencje;
- człowiek zaakceptuje wygląd uruchomionej gry na podstawie screenshotów;
- `docs/PROJECT.md` i `BACKLOG.md` opisują ten stan jako faktycznie osiągnięty.

Do tego momentu zasada „minimum 4 zadania graficzne w każdym batchu” obowiązuje
bez wyjątków.