# Notatki: klient Godot — materiał pomocniczy, NIE specyfikacja

> To są **niewiążące** notatki projektowe do decyzji w `game.md` ("KLIENT
> GODOT NA LINUX"). Agent bootstrapu/planista może je wykorzystać jako punkt
> wyjścia, zmienić albo całkowicie zignorować — pod warunkiem, że wybraną
> architekturę uzasadni w `docs/ARCHITECTURE.md`. Ten plik **nie** jest
> częścią śledzonego briefu i nie trzeba go transkrybować do `DESIGN.md`.

## Podział odpowiedzialności (przykład)

**Godot** mógłby odpowiadać za: mapę strategiczną, osady i armie, bitwy na
heksach, teren/sprite'y/animacje/kamerę, obsługę myszy i klawiatury, menu,
panele, tooltipy, karty jednostek.

**Python (`tbb`)** odpowiada za: stan kampanii, ekonomię, osady i rekrutację,
ruch armii, AI, zasady bitwy, obrażenia/morale/rany/śmierć, zapis i odczyt.

Godot nie czyta stanu z HTML, SVG, logów, `repr()` ani wewnętrznych struktur
Pythona — tylko przez jawny interfejs.

## Szkic komunikacji Godot–Python

```text
Godot
  ↓ polecenia gracza
lokalne API Python
  ↓
rdzeń `tbb`
  ↓
stan gry jako JSON
Godot renderuje wynik
```

Jeden z możliwych kształtów — lokalne HTTP API uruchamiane razem z grą,
przykładowe operacje:

```text
POST /game/new
GET  /game/state
POST /orders/end-turn
POST /orders/recruit
POST /orders/train
POST /orders/equip
POST /orders/move-party
POST /battle/move
POST /battle/attack
POST /game/save
POST /game/load
```

Alternatywy warte rozważenia: unix socket / stdin-stdout zamiast HTTP (mniej
narzutu, brak konfliktów portów) — ocena należy do agenta.

## Implementacja w Godot (przykładowy zestaw)

Godot 4, GDScript, `Node2D` dla świata gry, `Control` dla UI, `Camera2D` dla
mapy i bitwy, `Sprite2D`/`AnimatedSprite2D` dla obiektów, `TileMapLayer` (albo
równoważne) dla terenu, `AnimationPlayer`/`Tween` dla animacji, Theme Godota
dla spójnego UI. Projekt Godota w katalogu `game/`.

Przykładowy podział na sceny: `MainMenu`, `StrategicMap`, `SettlementView`,
`BattleView`, `BattleResult`. Unikać upychania całego klienta w jednej scenie
lub jednym skrypcie — ale dokładny podział to decyzja implementacyjna.

## Testowalność (sugerowany zakres)

Testy jednostkowe rdzenia Python bez Godota; testy API i odpowiedzi JSON;
testy scen Godota z przykładowym stanem JSON; test integracyjny Godot–Python;
test pełnej pętli rozgrywki. Przydatne, żeby klient Godota umiał wyświetlić
zapisany stan testowy JSON bez uruchamiania pełnej kampanii.

## Dystrybucja na Linux (sugerowany przepływ)

Build Godota mógłby: uruchomić dołączony backend → poczekać na jego gotowość
→ rozpocząć komunikację → zamknąć backend przy wyjściu z gry. Backend spakowany
razem z grą, żeby użytkownik nie instalował Pythona. Przykładowy cel:
`dist/linux/TotalBattleBrothers.x86_64`.

## Możliwa kolejność prac (do przeplanowania przez planistę)

1. Projekt Godot z działającym eksportem Linux.
2. Lokalne API nad istniejącym rdzeniem.
3. Automatyczne uruchamianie backendu.
4. Pobieranie prawdziwego stanu kampanii.
5. Grywalna mapa strategiczna.
6. Widok osady.
7. Grywalna bitwa.
8. Zapis i odczyt gry.
9. Pełna pętla rozgrywki bez terminala.

To punkt wyjścia do rozkładu na zadania — planista może przeplanować kolejność
i granulację, jeśli ma lepsze uzasadnienie.
