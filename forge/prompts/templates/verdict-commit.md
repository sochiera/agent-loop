ZATWIERDZENIE WERDYKTU. Nie kończ tury samym blokiem JSON w odpowiedzi —
zatwierdź werdykt komendą (ścieżka względem katalogu projektu):

    {{VERDICT_CMD}} <<'JSON'
    {...}
    JSON

Skrypt sprawdza kontrakt NATYCHMIAST. Błąd → wypisze powód i wyjdzie kodem 1;
popraw JSON i uruchom go ponownie w TEJ SAMEJ turze, zamiast kończyć pracę
niepoprawnym werdyktem. Dopiero wyjście kodem 0 kończy Twoją turę. Możesz
uruchamiać go wiele razy — liczy się ostatni przyjęty werdykt, więc poprawkę
zgłoś ponownym wywołaniem, a nie dopiskiem po werdykcie. Jeśli nie możesz
uruchomić skryptu, zwróć ten sam obiekt jako ostatni blok ```json``` odpowiedzi.
