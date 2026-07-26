# Bootstrap i synchronizacja zmian briefu — propozycja

Status: opis problemu i kierunku. Ten dokument nie oznacza, że mechanizm
diff-bootstrapu został już zaimplementowany.

## Problem obecnego bootstrapu

Bootstrap jest dziś jednorazową operacją sterowaną wyłącznie flagą
`State.bootstrapped`. Przy pierwszym uruchomieniu agent czyta cały główny plik
briefu, tworzy szkielet projektu, backlog, dokumentację i test, dobiera komendy
weryfikacji, a po review Forge commituje wynik i ustawia flagę na `true`.

Po tym momencie główny brief przestaje uczestniczyć w procesie. Forge:

- nie zapisuje skrótu ani wersji briefu;
- nie wykrywa jego późniejszych zmian;
- nie przekazuje nowych wymagań planistce;
- nie odróżnia inicjalizacji repozytorium od synchronizacji wymagań;
- nie ma kontrolowanego sposobu aktualizacji obrazu projektu po zmianie briefu.

Usunięcie stanu albo ręczne wyzerowanie flagi nie jest rozwiązaniem.
Uruchomienie pełnego bootstrapu na istniejącym projekcie jest nieidempotentne
i może ponownie modyfikować kod, testy, architekturę oraz konfigurację.

## Proponowany podział odpowiedzialności

### Bootstrap nowego projektu

Dla rzeczywiście nowego projektu bootstrap zachowuje obecną odpowiedzialność
za utworzenie minimalnego, działającego szkieletu. Oprócz `BACKLOG.md` powinien
jednak materializować niewielki, trwały kontekst projektu przeznaczony dla
planistki:

- opis projektu i jego odbiorcy;
- ogólny cel oraz kryterium docelowego sukcesu;
- najważniejsze ograniczenia i priorytety;
- klimat, ton, kierunek wizualny lub projektowy;
- sugestie autora briefu, które nie są jeszcze twardymi wymaganiami;
- jawne rozróżnienie wymagań, preferencji i pomysłów opcjonalnych.

Dokładny układ plików pozostaje decyzją implementacyjną. Preferowany jest mały,
indeksowany artefakt, który planistka może czytać taniej niż cały historyczny
brief, na przykład `docs/PROJECT.md` albo pliki pod `docs/PROJECT/` z krótkim
`00-INDEX.md`.

Bootstrap powinien również zapisać w runtime:

- skrót treści źródłowego briefu;
- kopię briefu albo inną podstawę do wyliczenia późniejszego diffu;
- wersję formatu wygenerowanych opisów projektu.

### Diff-bootstrap po zmianie briefu

Zmiana głównego pliku `.md` nie uruchamia pełnego bootstrapu. Na bezpiecznej
granicy między zadaniami Forge porównuje aktualny skrót briefu z ostatnią
zaakceptowaną wersją. Jeśli treść się zmieniła, uruchamia osobny
`diff-bootstrap`.

Diff-bootstrap otrzymuje:

- poprzednią i nową wersję briefu albo ich zwarty diff;
- aktualny backlog;
- aktualny opis projektu, cel, ograniczenia, klimat i sugestie;
- informację o zadaniach już zaplanowanych lub ukończonych, potrzebną do
  uniknięcia duplikatów.

Jego zakres zapisu jest ograniczony wyłącznie do:

- `BACKLOG.md`;
- artefaktów opisujących projekt, ogólny cel, priorytety, klimat i sugestie;
- ewentualnych małych indeksów tych artefaktów.

Nie wolno mu modyfikować kodu, testów, konfiguracji wykonawczej ani dokumentów
architektury będących wynikiem zrealizowanych zadań. Ograniczenie powinno być
sprawdzane deterministycznie przez Forge na podstawie manifestu zmienionych
plików, a nie tylko zapisane w promptcie.

## Semantyka aktualizacji

Diff-bootstrap powinien traktować główny brief jako źródło intencji, ale nie
usuwać bezrefleksyjnie historii projektu.

- Nowe wymaganie tworzy albo podnosi priorytet odpowiedniego wpisu backlogu.
- Zmienione wymaganie aktualizuje opis i oznacza kolidujące, niezrealizowane
  wpisy do ponownego zaplanowania.
- Usunięcie wymagania nie cofa automatycznie ukończonego kodu. Tworzy jawną
  decyzję lub zadanie usunięcia, jeśli nowy brief rzeczywiście tego wymaga.
- Zmiana klimatu albo sugestii aktualizuje kontekst planistki, lecz sama nie
  musi tworzyć zadania.
- Już istniejące zadania nie powinny być po cichu kasowane. Należy je zachować,
  przeplanować albo oznaczyć jako nieaktualne z podanym powodem.

Nowy skrót i snapshot briefu wolno zapisać dopiero po poprawnym zakończeniu
diff-bootstrapu i walidacji zakresu zmian. Awaria pozostawia poprzednią wersję
jako punkt odniesienia, dzięki czemu operację można bezpiecznie wznowić.

## Relacja z planistką

Planistka powinna stale czytać zwarty opis projektu i ogólny cel, natomiast
pełny brief tylko podczas pierwszego bootstrapu albo kontrolowanej
synchronizacji. Pozwala to zachować kierunek projektu bez ponownego wysyłania
dużego dokumentu w każdym wsadzie.

Po diff-bootstrapie nowe pilne wymagania powinny trafić do planowania przed
zwykłym backlogiem, ale bez przerywania aktywnego zadania. Sposób scalania ich
z istniejącą kolejką wymaga osobnej decyzji: zachowanie kolejki i wstawienie
zadań pilnych na jej początek jest bezpieczniejsze niż jej zastąpienie.

## Otwarte decyzje przed implementacją

1. Czy opisy projektu mają być jednym `docs/PROJECT.md`, czy małym katalogiem
   z indeksem?
2. Jak długo przechowywać snapshoty briefu i czy mają być częścią repozytorium,
   czy wyłącznie `.forge/`?
3. Czy każdy diff-bootstrap wymaga osobnego read-only review?
4. Jak dokładnie przeplanowywać niezaczęte zadania, których założenia zmienił
   brief?
5. Czy zmiana samych sugestii lub klimatu ma uruchamiać planowanie od razu,
   czy tylko aktualizować kontekst następnego zwykłego wsadu?

## Poza zakresem tej zmiany

Ten dokument nie implementuje wykrywania zmian briefu, nowych pól stanu,
diff-bootstrapu, migracji istniejących projektów ani priorytetyzacji kolejki.
