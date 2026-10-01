# Runbook: ukryte wejście https://sochiera.pl/forge/ (control room na laptopie)

Cel: `https://sochiera.pl/forge/` obsługuje żywe Forge UI (`python3 -m forge
ui`, laptop Jana), wzorem `/biblioteka/` — proxy nginx do żywego backendu, nie
kopia HTML. Wejście bez linku w publicznym menu; UI i API za jednym hasłem z
opisu karty (tantum: nigdy nieCommitujemy tego hasła — nie w tym repo, nie w
PR, nie w raporcie).

Podział ról: Forge, runnery i CLIs zostają na laptopie; VPS (ubuntu@
51.83.199.206, klucz `~/.ssh/pbn_vps`) jest tylko proxy/tunelem. Wariant
„działa przy wyłączonym laptopie” jest świadomie poza zakresem.

## 1. Laptop: Forge z gate'iem

```bash
cd /home/jan/Sources/agent-loop
# sekret z opisu karty w pliku 0600 (lokalnie, nigdy w repo):
chmod 600 /home/jan/.config/sochiera/forge-gate-secret
FORGE_UI_PASSWORD_FILE=/home/jan/.config/sochiera/forge-gate-secret \
  python3 -m forge ui --no-browser
# lub: python3 -m forge ui --no-browser --access-gate-file /home/jan/.config/sochiera/forge-gate-secret
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8787/api/health          # -> 401 (challenge Basic Auth)
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forge-Access: <sekret>' http://127.0.0.1:8787/api/health  # -> 200
```

## 1a. Wejście z przeglądarki (bez wtyczki)

Serwer na każde odrzucone żądanie odpowiada 401 z nagłówkiem
`WWW-Authenticate: Basic realm="Forge Control Room", charset="UTF-8"`, więc
zwykła przeglądarka sama pokazuje natywne okno logowania. W polu użytkownika
wpisz cokolwiek (np. `jan`), w polu hasła — sekret z pliku 0600. Po zalogowaniu
przeglądarka dokleja `Authorization: Basic …` do HTML, statyki i każdego
 żądania UI do `/api/…`, więc app.js nie musi (i nie dokleja) żadnych
nagłówków. Kanał `X-Forge-Access` pozostał nietknięty (curl/skrypty).

Panel działa też pod prefiksem `/forge/`, bo statyka i wywołania API w UI są
względne (`style.css`, `app.js`, `api/…`), a proxy nginx zdejmuje prefiks
(`proxy_pass http://127.0.0.1:8791/`), więc backend widzi korzeń.

## 2. Laptop: tunel do VPS

```bash
deploy/tunnelforge.sh up      # foreground; lub systemd user unit deploy/forge-tunnel.service
```

Na VPS musi powstać nasłuch `127.0.0.1:8791`. Weryfikacja z drugiej końcówki:

```bash
ssh -i ~/.ssh/pbn_vps ubuntu@51.83.199.206 -- 'curl -s -m 5 http://127.0.0.1:8791/api/health'
# bez nagłówka -> 403; z nagłówkiem i sekretem -> {"ok": true, ...}
```

## 3. VPS: fragment nginx (wymaga ship-it — produkcyjny vhost)

Wzorzec jak `/biblioteka/`: skopiuj `deploy/nginx-forge.conf` do
`/etc/nginx/snippets/forge.conf` i wstaw w vhost `sochiera.pl` przed
catch-allem:

```nginx
include /etc/nginx/snippets/forge.conf;
```

Konfiguracja nie zawiera sekretów; gate robi proces Forge. ewentualne drugie
zabezpieczenie (proxy-auth na VPS tym samym sekretem) jest opcjonalne i
dopuszczalne, ale niesie ryzyko duplikacji materiału hasła na serwerze —
nie jest wymagane przy tym projekcie.

```bash
sudo cp /etc/nginx/sites-available/sochiera /etc/nginx/sites-available/sochiera.forge.<timestamp>.bak
sudo nginx -t && sudo systemctl reload nginx
```

## 4. Weryfikacja po wdrożeniu (tylko po ship-it)

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://sochiera.pl/forge/            # -> 401 bez sekretu (challenge)
curl -s -o /dev/null -w '%{http_code}\n' -u 'jan:<sekret>' https://sochiera.pl/forge/    # -> 200 (Basic)
curl -s -o /dev/null -w '%{http_code}\n' -H 'X-Forge-Access: <sekret>' https://sochiera.pl/forge/  # -> 200
curl -s -o /dev/null -w '%{http_code}\n' https://sochiera.pl/                  # -> 200 (strona główna nietknięta)
curl -s -o /dev/null -w '%{http_code}\n' https://sochiera.pl/malowanie-po-numerach/  # -> 200
sudo -n nginx -t   # poprawny config
```

## 5. Rollback

```bash
sudo cp /etc/nginx/sites-available/sochiera.forge.<timestamp>.bak \
  /etc/nginx/sites-available/sochiera
sudo nginx -t && sudo systemctl reload nginx
deploy/tunnelforge.sh down
```

## Stan i ograniczenia

- Brak osobnego środowiska testowego dla `sochiera.pl` — wdrożenie(fm)
  dotyka produkcyjnego vhostu, więc czeka na jawną decyzję Jan (ship-it).
- Ten runbook nie edytuje nginx na VPS samodzielnie.
- Runnerów/systemd Forge nie przenosi się na VPS bez osobnej zgody.
- Nie ruszaj współdzielonych ścieżek `/test/` homepage/PBN ani `/poker/`.
