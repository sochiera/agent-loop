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
