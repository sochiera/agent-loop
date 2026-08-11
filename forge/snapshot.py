"""Migawka kodu Forge na czas jednego biegu.

Powód jest udokumentowany w ``docs/AWARIE-2026-08-11.md`` (awaria B): kod ``.py``
wczytuje się do procesu RAZ, przy starcie, a szablony promptów przy KAŻDYM
renderowaniu. Commit w repozytorium Forge pod działającą pętlą rozjeżdża jedno
z drugim i wywraca bieg po godzinach pracy — już po zapłaceniu za turę agenta.
Przy dwóch biegach naraz ekspozycja jest podwójna, bo jednym z projektów bywa
samo repozytorium Forge.

Bieg dostaje więc własną kopię pakietu ``forge`` w katalogu cache i to z niej
jest uruchamiany. Cena jest zamierzona: poprawka wprowadzona w trakcie biegu
zacznie obowiązywać dopiero po jego restarcie.

Katalog migawki nazywamy odciskiem ZAWARTOŚCI, nie projektem. Dzięki temu:

- dwa biegi z tym samym kodem dzielą jedną kopię (jest tylko do odczytu),
- nowa migawka nigdy nie nadpisuje kopii, z której ktoś właśnie korzysta,
- sprzątanie starych kopii sprowadza się do wieku katalogu.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from . import runlock

PACKAGE = Path(__file__).resolve().parent
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo")
KEEP_DAYS = 14
# Dzierżawa leży OBOK pakietu, nie w nim: kopiujemy tylko ``forge/``, więc plik
# nigdy nie trafi do kolejnej migawki ani nie zmieni jej odcisku.
LEASE_NAME = ".in-use.lock"
_GIT_TIMEOUT_S = 5


def project_key(project: str) -> str:
    """Stabilna nazwa katalogu cache dla projektu (nazwa + skrót ścieżki).

    Sama nazwa katalogu nie wystarcza — dwa projekty ``game`` w różnych
    miejscach dzieliłyby jeden katalog logów."""
    root = Path(project).resolve()
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:10]
    return f"{root.name or 'project'}-{digest}"


def cache_root(environ: dict[str, str] | None = None) -> Path:
    environ = os.environ if environ is None else environ
    base = environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
    return Path(base) / "forge"


def _code_root(environ: dict[str, str] | None = None) -> Path:
    return cache_root(environ) / "code"


@dataclass(frozen=True)
class Snapshot:
    """Kopia pakietu gotowa do uruchomienia.

    ``path`` jest katalogiem NADRZĘDNYM (zawiera ``forge/``) — to on trafia do
    ``PYTHONPATH`` i do ``cwd`` procesu."""

    path: Path
    head: str = ""
    dirty: bool = False
    reused: bool = False

    def describe(self) -> str:
        version = (self.head[:12] + ("+brudne" if self.dirty else "")
                   if self.head else "bez gita")
        return f"migawka kodu {version} w {self.path}"


def _fingerprint(package: Path) -> str:
    """Odcisk zawartości pakietu: ścieżka, rozmiar i czas modyfikacji plików.

    Nie czytamy treści — pakiet to kilkaset kilobajtów przy każdym starcie
    biegu, a rozmiar z mtime rozróżnia dokładnie te zmiany, które nas tu
    interesują (edycja, checkout, rebase)."""
    digest = hashlib.sha256()
    for path in sorted(p for p in package.rglob("*") if p.is_file()):
        if any(part == "__pycache__" for part in path.parts) \
                or path.suffix in {".pyc", ".pyo"}:
            continue
        stat = path.stat()
        digest.update(str(path.relative_to(package)).encode("utf-8"))
        digest.update(f"{stat.st_size}:{stat.st_mtime_ns}".encode("ascii"))
    return digest.hexdigest()[:16]


def _git(source: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ("git", "-C", str(source), *args),
            capture_output=True, text=True, timeout=_GIT_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def is_snapshot(path: Path, environ: dict[str, str] | None = None) -> bool:
    """Czy ``path`` jest katalogiem migawki (a nie drzewem roboczym)?"""
    try:
        return Path(path).resolve().parent == _code_root(environ).resolve()
    except OSError:
        return False


def hold(path: Path | None = None,
         environ: dict[str, str] | None = None) -> "runlock.RunLock | None":
    """Dzierżawa migawki na czas biegu; ``None``, gdy kod nie jest migawką.

    Sam czas modyfikacji katalogu nie wystarczał: pętla potrafi pracować bez
    limitu iteracji, więc po ``KEEP_DAYS`` sprzątacz uruchomiony przez KOLEJNY
    bieg usunąłby kod spod pracującego procesu, a pierwszy nieprzeczytany
    jeszcze szablon promptu wywróciłby go — dokładnie tak, jak awaria B.
    Dzierżawa jest współdzielona, bo kilka biegów legalnie dzieli jedną kopię."""
    root = Path(path) if path is not None else PACKAGE.parent
    if not is_snapshot(root, environ):
        return None
    try:
        return runlock.RunLock(root / LEASE_NAME, shared=True).acquire()
    except (OSError, runlock.RunLocked):
        # Brak dzierżawy nie jest powodem do zatrzymania biegu: najgorszy
        # skutek to kopia sprzątnięta po dwóch tygodniach pracy tej pętli.
        return None


def prune(root: Path, keep_days: int = KEEP_DAYS) -> None:
    """Best-effort: usuń kopie stare i JEDNOCZEŚNIE przez nikogo nietrzymane.

    Dwa warunki, bo każdy sam w sobie zawodzi: wiek nie widzi biegu, który
    pracuje trzeci tydzień, a sama dzierżawa nie zwalnia kopii po biegu ubitym
    tuż po starcie."""
    cutoff = time.time() - keep_days * 86_400
    try:
        candidates = list(root.iterdir())
    except OSError:
        return
    for path in candidates:
        try:
            if not path.is_dir() or path.stat().st_mtime >= cutoff:
                continue
            if runlock.is_held(path / LEASE_NAME):
                continue
            shutil.rmtree(path, ignore_errors=True)
        except OSError:
            continue


def create(package: Path | None = None,
           environ: dict[str, str] | None = None) -> Snapshot:
    """Zwróć kopię pakietu odpowiadającą jego BIEŻĄCEJ zawartości."""
    package = PACKAGE if package is None else Path(package).resolve()
    root = _code_root(environ)
    root.mkdir(parents=True, exist_ok=True)
    prune(root)

    target = root / f"{package.name}-{_fingerprint(package)}"
    head = _git(package.parent, "rev-parse", "HEAD")
    dirty = bool(head) and bool(_git(package.parent, "status", "--porcelain"))
    if (target / package.name / "__init__.py").is_file():
        # Kopia z tym odciskiem już jest — sam ją odświeżamy jako używaną,
        # zamiast nadpisywać katalog, z którego ktoś może właśnie czytać.
        try:
            os.utime(target)
        except OSError:
            pass
        return Snapshot(target, head=head, dirty=dirty, reused=True)

    staging = root / f".{target.name}.{uuid.uuid4().hex}.tmp"
    try:
        shutil.copytree(package, staging / package.name, ignore=IGNORED)
        try:
            os.rename(staging, target)
        except OSError:
            # Przegrany wyścig o tę samą nazwę: kopia jest już na miejscu
            # i ma tę samą zawartość, więc po prostu z niej korzystamy.
            if not (target / package.name / "__init__.py").is_file():
                raise
            shutil.rmtree(staging, ignore_errors=True)
            return Snapshot(target, head=head, dirty=dirty, reused=True)
    except OSError:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return Snapshot(target, head=head, dirty=dirty)
