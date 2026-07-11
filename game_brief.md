# Brief gry: Total Battle Brothers (fork Wesnotha)

> To jest WEJŚCIE dla orkiestratora. Pisz tu prosto i konkretnie — agent
> bootstrapu rozwinie to w docs/DESIGN.md. Zmień treść na własną wizję.

## Pitch
Taktyczna gra strategiczna: prowadzisz najemną kompanię braci-wojów po
proceduralnej mapie kampanii (w stylu Battle Brothers), staczając heksagonalne
bitwy taktyczne na silniku wywodzącym się z Wesnotha (Total War-owy rozmach
starć, kameralne zarządzanie drużyną Battle Brothers).

## Klimat
Ponure, niskofantastyczne średniowiecze. Śmierć jest trwała, złoto zawsze go za
mało, a każdy najemnik ma imię, cechy i historię.

## Rdzeń mechaniki (MVP)
- Warstwa kampanii: mapa, przemieszczanie kompanii, kontrakty za złoto, żołd.
- Drużyna: rekrutacja najemników z cechami (siła, celność, morale), ekwipunek,
  trwała śmierć.
- Bitwa taktyczna: siatka heksów, tury, punkty ruchu i akcji, atak/obrona z
  modyfikatorami terenu, morale i ucieczka.
- Ekonomia pętli: kontrakt → bitwa → łupy/rannych/zabitych → wydatki → kolejny
  kontrakt.

## MVP „grywalne”
Jedna kampania: zrekrutuj 3 braci, przyjmij kontrakt, wygraj jedną bitwę
taktyczną na heksach przeciw wrogiej grupie, dostań zapłatę. Reszta później.

## Poza zakresem (na start)
Grafika AAA, sieciowa gra wieloosobowa, edytor map, dźwięk.

## Preferencje techniczne
Fork/inspiracja Wesnotha jest OK, ale priorytetem jest testowalny rdzeń logiki
(kampania + bitwa) oddzielony od warstwy prezentacji, żeby dało się go rozwijać
w TDD. Wybór konkretnego języka/silnika zostawiam agentowi bootstrapu — ma
uzasadnić decyzję w docs/ARCHITECTURE.md.
