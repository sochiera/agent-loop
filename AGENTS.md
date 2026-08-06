# Zasady pracy agentów

## Koszt całego procesu, nie pojedynczego promptu

Optymalizuj oczekiwany całkowity koszt doprowadzenia zadania do poprawnego
wyniku, a nie samą liczbę tokenów w pojedynczym promptcie lub wywołaniu.
Każde kolejne wywołanie API agenta z kontekstem repozytorium, definicjami
narzędzi i wynikami ich użycia jest dużym kosztem — często o rzędy wielkości
większym niż wielokrotne przekazanie krótkiej, trwałej notatki.

Przed skróceniem pamięci lub kontekstu porównaj:

- pewną oszczędność: usunięte tokeny × liczba przyszłych przekazań;
- oczekiwaną stratę: wzrost prawdopodobieństwa dodatkowej tury API × pełny
  koszt tej tury, wraz z ryzykiem gorszej decyzji lub regresji jakości.

Nie przycinaj, nie streszczaj ani nie usuwaj automatycznie trwałych ustaleń
tylko dlatego, że ich zachowanie kosztuje kilkanaście lub kilkaset tokenów.
Jeśli utrata informacji może wymusić ponowne przeszukiwanie repozytorium,
uruchamianie narzędzi, testów albo odtwarzanie toku rozumowania, mała lokalna
oszczędność może spowodować stratę setek tysięcy tokenów.

Preferuj optymalizacje bezstratne: dokładną deduplikację, usuwanie wyników
chwilowych już niesionych przez stan procesu, celowane i ograniczone wyniki
narzędzi, cache dla stabilnego kontekstu oraz atomową ochronę wartościowych
notatek. Próg rozmiaru powinien najpierw wywoływać audyt jakości treści, a nie
automatyczne obcięcie. Optymalizacja jest poprawna tylko wtedy, gdy obniża
oczekiwany koszt całego procesu bez pogorszenia jakości wyniku.
