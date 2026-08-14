"""Nazwane profile routingu: osobny zestaw modeli dla każdego biegu.

Warstwa nadpisań operatora (``routing.py``) opisuje JEDEN wybór modeli. Dopóki
panel prowadził jeden bieg, to wystarczało; przy kilku projektach naraz jeden
wspólny wybór wymusza kompromis, którego nie da się obronić — projekt na
darmowym limicie i projekt, na który wolno wydać Opusa, potrzebują innych
modeli w tych samych rolach. Ten moduł dokłada nad routingiem warstwę PROFILI:

- profil to nazwany zestaw nadpisań ról, czyli po prostu ``Routing`` z etykietą;
- bieg wskazuje profil po SLUGU, a nie po ścieżce — zmiana nazwy nie może
  osierocić wiersza w panelu;
- profil ``SHARED_SLUG`` (pusty slug) to dotychczasowy ``routing.json``. Nic się
  dla niego nie zmienia: uruchomienie z CLI bez żadnej zmiennej dalej czyta ten
  sam plik, więc dzisiejsze skróty i jednostki systemd działają bez zmian.

Profile nazwane leżą w ``<konfiguracja>/forge/profiles/<slug>.json`` i mają
DOKŁADNIE ten sam schemat, co ``routing.json`` (plus pole ``name`` z etykietą,
którą parser routingu i tak ignoruje). To celowe: ``FORGE_ROUTING_FILE``
wskazujący na plik profilu działa bez jednej linii kodu po stronie
orkiestratora, a migawka routingu biegu pozostaje zwykłym plikiem routingu.

Wybór źródła jest jednoznaczny i nie ma w nim cichych awarii:

- ``FORGE_ROUTING_FILE`` ustawione → zachowanie jak dotąd (plik albo wyłączenie);
- inaczej ``FORGE_ROUTING_PROFILE`` → ten profil, a jego brak jest BŁĘDEM;
- inaczej profil wspólny.

Brak wskazanego profilu podnosi wyjątek zamiast po cichu wrócić do polityki
domyślnej. Cicha podmiana kosztowałaby cały bieg wykonany nie tymi modelami,
co trzeba — a to godziny pracy i rachunek, którego nikt nie zamawiał.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from . import routing as routing_module

# Pusty slug = profil wspólny, czyli plik ``routing.json`` sprzed profili.
SHARED_SLUG = ""
SHARED_LABEL = "Wspólny"
DIRECTORY_NAME = "profiles"
PROFILE_ENV = "FORGE_ROUTING_PROFILE"
ROUTING_FILE_ENV = "FORGE_ROUTING_FILE"
NEW_PROFILE_NAME = "Nowy profil"

_MAX_SLUG_LEN = 60
_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Litery, których rozkład NFKD nie rozbija na znak bazowy i znak diakrytyczny.
# Bez tej tabeli „Zawiłe" i „Zawie" dałyby ten sam slug „zawie".
_TRANSLITERATION = str.maketrans({"ł": "l", "Ł": "L", "ß": "ss",
                                  "ø": "o", "Ø": "O", "đ": "d", "Đ": "D"})


class UnknownProfile(ValueError):
    """Wskazany profil nie istnieje.

    Osobny typ, bo warstwa uruchamiająca traktuje go inaczej niż zwykły błąd
    konfiguracji: to nie literówka w wartości, tylko brak całego zestawu modeli,
    którym bieg miał pracować."""


def slugify(name: str) -> str:
    """Nazwa profilu → bezpieczna nazwa pliku.

    Ogonki idą do postaci bazowej (``Tylko GPT`` i ``tylko-gpt`` mają dawać ten
    sam plik), reszta znaków spoza ``[a-z0-9]`` staje się myślnikiem. Pusty
    wynik dostaje nazwę zastępczą — plik ``.json`` bez nazwy byłby ukryty."""
    normalized = unicodedata.normalize("NFKD", (name or "").translate(
        _TRANSLITERATION))
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")[:_MAX_SLUG_LEN]
    return slug.strip("-") or "profil"


def valid_slug(slug: str) -> bool:
    """Czy slug nadaje się na nazwę pliku w katalogu profili.

    Sprawdzenie jest białą listą, nie czarną: slug bierze się z pliku ustawień
    GUI albo ze zmiennej środowiskowej, więc ``../../.ssh/config`` musi odpaść
    zanim dotknie systemu plików."""
    return bool(_SLUG_RE.match(slug or ""))


def shared_path(environ: dict[str, str] | None = None) -> Path:
    """Plik profilu wspólnego — ten sam, co przed wprowadzeniem profili."""
    environ = os.environ if environ is None else environ
    return (routing_module.configured_path(environ)
            or routing_module.default_path(environ))


def directory(environ: dict[str, str] | None = None) -> Path:
    """Katalog profili nazwanych — obok pliku wspólnego."""
    return shared_path(environ).parent / DIRECTORY_NAME


def path_for(slug: str, environ: dict[str, str] | None = None) -> Path:
    """Ścieżka pliku profilu; ``SHARED_SLUG`` wskazuje profil wspólny."""
    if slug == SHARED_SLUG:
        return shared_path(environ)
    if not valid_slug(slug):
        raise UnknownProfile(f"niepoprawna nazwa profilu: {slug!r}")
    return directory(environ) / f"{slug}.json"


def read_label(path: Path, slug: str) -> str:
    """Etykieta z pliku profilu; przy braku — slug.

    Etykietę trzyma sam plik, więc zmiana nazwy nie rusza ani nazwy pliku, ani
    odwołań z panelu. Nieczytelny plik nie jest powodem do ukrycia profilu:
    routing i tak zostanie z niego wczytany pobłażliwie."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return slug
    name = data.get("name") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else slug


@dataclass
class Profile:
    """Jeden zestaw modeli: etykieta operatora, slug w pliku i nadpisania ról."""

    slug: str
    name: str
    routing: routing_module.Routing = field(
        default_factory=routing_module.Routing)

    @property
    def shared(self) -> bool:
        return self.slug == SHARED_SLUG


def load_named(
    slug: str,
    difficulties: tuple[str, ...] = (),
    environ: dict[str, str] | None = None,
) -> routing_module.Routing:
    """Routing profilu o tym slugu; brak pliku = ``UnknownProfile``."""
    if slug == SHARED_SLUG:
        return routing_module.load(shared_path(environ), difficulties)
    path = path_for(slug, environ)
    if not path.is_file():
        raise UnknownProfile(
            f"nie znam profilu routingu {slug!r}: brak pliku {path}. "
            f"Dostępne profile: {', '.join(available(environ)) or '(żadnego)'}.")
    return routing_module.load(path, difficulties)


def slugs_in(folder: Path) -> list[str]:
    """Slugi profili leżących w tym katalogu, alfabetycznie.

    Nazwy niebędące poprawnym slugiem są pomijane, a nie naprawiane: katalog
    konfiguracji bywa edytowany ręcznie, a plik, którego nie umiemy jednoznacznie
    zaadresować, lepiej zignorować niż pokazać pod zmyśloną nazwą."""
    try:
        found = sorted(path.stem for path in Path(folder).glob("*.json"))
    except OSError:
        return []
    return [slug for slug in found if valid_slug(slug)]


def available(environ: dict[str, str] | None = None) -> list[str]:
    """Slugi profili nazwanych widocznych dla tego procesu."""
    return slugs_in(directory(environ))


def load_from_env(
    environ: dict[str, str] | None = None,
    difficulties: tuple[str, ...] = (),
) -> routing_module.Routing:
    """Routing dla tego procesu: plik, profil albo wybór wspólny.

    ``FORGE_ROUTING_FILE`` zachowuje pierwszeństwo, bo tym kanałem panel podaje
    biegowi jego MIGAWKĘ routingu — pytanie o profil byłoby wtedy pytaniem o coś,
    co zostało już rozstrzygnięte w chwili startu."""
    environ = os.environ if environ is None else environ
    if (environ.get(ROUTING_FILE_ENV) or "").strip():
        return routing_module.load_from_env(environ, difficulties)
    slug = (environ.get(PROFILE_ENV) or "").strip()
    if not slug:
        return routing_module.load_from_env(environ, difficulties)
    return load_named(resolve(slug, environ), difficulties, environ)


def resolve(name: str, environ: dict[str, str] | None = None) -> str:
    """Zamień to, co wpisał operator, na slug istniejącego profilu.

    Przyjmujemy zarówno slug, jak i etykietę („Tylko GPT" ≡ ``tylko-gpt``), bo
    w wierszu poleceń naturalne jest przepisanie nazwy widzianej w panelu."""
    text = (name or "").strip()
    if not text or text == SHARED_SLUG:
        return SHARED_SLUG
    slugs = available(environ)
    if text in slugs:
        return text
    candidate = slugify(text)
    if candidate in slugs:
        return candidate
    for slug in slugs:
        if read_label(path_for(slug, environ), slug).casefold() == text.casefold():
            return slug
    raise UnknownProfile(
        f"nie znam profilu routingu {text!r}. "
        f"Dostępne profile: {', '.join(slugs) or '(żadnego)'}.")


class Store:
    """Profile widziane przez panel: wczytanie, edycja i zapis na dysk.

    Źródłem prawdy jest DYSK, a pamięć tylko jego kopią roboczą — panel zapisuje
    każdą zmianę pokrętła od razu, tak samo jak robił to z jednym plikiem
    routingu. Klasa nie zna GTK, więc cała logika profili daje się sprawdzić
    bez sesji graficznej."""

    def __init__(self, shared: Path, folder: Path, profiles: list[Profile]):
        self._shared = Path(shared)
        self._folder = Path(folder)
        self._profiles = profiles

    @classmethod
    def load(cls, shared: Path, folder: Path,
             difficulties: tuple[str, ...] = ()) -> "Store":
        shared, folder = Path(shared), Path(folder)
        entries = [Profile(SHARED_SLUG, SHARED_LABEL,
                           routing_module.load(shared, difficulties))]
        named = [
            Profile(slug,
                    read_label(folder / f"{slug}.json", slug),
                    routing_module.load(folder / f"{slug}.json", difficulties))
            for slug in slugs_in(folder)
        ]
        named.sort(key=lambda profile: profile.name.casefold())
        return cls(shared, folder, [*entries, *named])

    # --- odczyt -----------------------------------------------------------
    def profiles(self) -> list[Profile]:
        """Profil wspólny zawsze pierwszy — to on jest wyborem domyślnym."""
        return list(self._profiles)

    def slugs(self) -> list[str]:
        return [profile.slug for profile in self._profiles]

    def has(self, slug: str) -> bool:
        return any(profile.slug == slug for profile in self._profiles)

    def get(self, slug: str) -> Profile:
        for profile in self._profiles:
            if profile.slug == slug:
                return profile
        raise UnknownProfile(
            f"nie znam profilu routingu {slug!r}. Wybierz istniejący profil "
            f"dla tego biegu — jego modele nie są nigdzie zapisane.")

    def routing(self, slug: str) -> routing_module.Routing:
        return self.get(slug).routing

    def label(self, slug: str) -> str:
        try:
            return self.get(slug).name
        except UnknownProfile:
            return slug

    def path(self, slug: str) -> Path:
        if slug == SHARED_SLUG:
            return self._shared
        if not valid_slug(slug):
            raise UnknownProfile(f"niepoprawna nazwa profilu: {slug!r}")
        return self._folder / f"{slug}.json"

    # --- zapis ------------------------------------------------------------
    def set_routing(self, slug: str, routing: routing_module.Routing) -> None:
        """Zapamiętaj i zapisz nadpisania ról tego profilu."""
        profile = self.get(slug)
        profile.routing = routing
        self._write(profile)

    def create(self, name: str = NEW_PROFILE_NAME,
               source: str = SHARED_SLUG) -> Profile:
        """Nowy profil jako KOPIA istniejącego.

        Kopia, nie pustka: profil zaczynający od czystej polityki wyglądałby
        w panelu identycznie jak wspólny, a operator zakłada go zwykle po to,
        żeby zmienić dwie role z dziesięciu."""
        base = (name or NEW_PROFILE_NAME).strip() or NEW_PROFILE_NAME
        # Kopia mapy ról, nie ta sama instancja: ``Routing`` jest wprawdzie
        # traktowany jak wartość, ale współdzielony słownik zamieniłby przyszłą
        # zmianę w miejscu w cichą edycję dwóch profili naraz.
        profile = Profile(self._unique_slug(slugify(base)),
                          self._unique_name(base),
                          routing_module.Routing(
                              roles=dict(self.get(source).routing.roles)))
        self._profiles.append(profile)
        self._sort()
        self._write(profile)
        return profile

    def rename(self, slug: str, name: str) -> Profile:
        """Zmień etykietę; slug (a więc i plik) zostaje bez zmian.

        Wiersze biegów wskazują slug, więc przemianowanie nie może ich
        osierocić — nazwa jest wyłącznie dla oczu operatora."""
        profile = self.get(slug)
        if profile.shared:
            raise ValueError("Profil wspólny ma stałą nazwę.")
        text = (name or "").strip()
        if not text:
            raise ValueError("Nazwa profilu nie może być pusta.")
        if text != profile.name:
            profile.name = self._unique_name(text, ignore=slug)
            self._sort()
            self._write(profile)
        return profile

    def delete(self, slug: str) -> None:
        """Usuń profil nazwany; profilu wspólnego nie da się usunąć."""
        profile = self.get(slug)
        if profile.shared:
            raise ValueError("Profil wspólny jest zawsze dostępny.")
        self._profiles.remove(profile)
        try:
            self.path(slug).unlink()
        except OSError:
            # Plik mógł zniknąć spod nas (drugie okno, ręczne sprzątanie).
            # Cel operacji — profil nie jest już do wyboru — jest osiągnięty.
            pass

    # --- szczegóły --------------------------------------------------------
    def _sort(self) -> None:
        shared = [item for item in self._profiles if item.shared]
        named = sorted((item for item in self._profiles if not item.shared),
                       key=lambda profile: profile.name.casefold())
        self._profiles = [*shared, *named]

    def _unique_slug(self, base: str) -> str:
        # Także pliki, których jeszcze nie wczytaliśmy: profil założony w drugim
        # oknie panelu nie może zostać nadpisany przez kolizję nazwy.
        taken = set(self.slugs()) | set(slugs_in(self._folder))
        if base not in taken:
            return base
        for number in range(2, 1000):
            candidate = f"{base}-{number}"
            if candidate not in taken:
                return candidate
        raise ValueError("Za dużo profili o tej nazwie.")

    def _unique_name(self, base: str, ignore: str = "\0") -> str:
        taken = {profile.name.casefold() for profile in self._profiles
                 if profile.slug != ignore}
        if base.casefold() not in taken:
            return base
        for number in range(2, 1000):
            candidate = f"{base} {number}"
            if candidate.casefold() not in taken:
                return candidate
        raise ValueError("Za dużo profili o tej nazwie.")

    def _write(self, profile: Profile) -> None:
        path = self.path(profile.slug)
        extra = None if profile.shared else {"name": profile.name}
        routing_module.save(profile.routing, path, extra)
