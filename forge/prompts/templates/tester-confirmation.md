TURA POTWIERDZAJĄCA po green kodera. {{SUGGESTED}}

1. uruchom celowaną bramkę i odpowiedz, czy jest zielona;
2. sprawdź, czy pozostały nieprzetestowane kryteria akceptacji, i jeśli tak —
   ROZSZERZ o nie bieżącą bramkę w TEJ rundzie (parametryzacja albo kolejne
   asercje w tym samym pliku), zamiast otwierać nowy cykl `red`. Nowy cykl
   otwieraj tylko wtedy, gdy kryterium naprawdę wymaga osobnej bramki — wtedy
   podaj powód w `reason`;
3. przejrzyj dotknięte testy i wykonaj potrzebny mały refaktor bez osłabiania pokrycia.

Pełna bramka `{{FULL_TEST_CMD}}` należy do Forge przed commitem; nie uruchamiaj
jej tutaj bez konkretnej potrzeby. Nie oceniaj jakości implementacji — to
zadanie świeżego reviewera. Jeśli wynik i pokrycie są dobre, wybierz review; w
przeciwnym razie wybierz red, code albo blocked i podaj konkretny powód. Dla
red/code zwróć faktycznie używaną komendę w `command`.
