"""Jeden bieg Forge na katalog projektu.

Dwa procesy orkiestratora na jednym drzewie to dwa ``STATE.json`` nadpisujące
się nawzajem i dwa ``git reset``/``commit`` na tym samym repozytorium — czyli
zniszczony przebieg obu. Panel z kilkoma biegami czyni z tego pomyłkę jednego
kliknięcia, więc zamek pilnuje tego po stronie procesu, a nie interfejsu:
obowiązuje tak samo uruchomienia z GUI, jak i z linii poleceń.

Nośnikiem jest ``flock`` na ``<projekt>/.forge/run.lock``. Jądro zwalnia go
samo przy śmierci procesu, więc SIGKILL nie zostawia zamku osieroconego i nie
potrzeba heurystyki „czy ten PID jeszcze żyje". Treść pliku (PID i czas startu)
służy WYŁĄCZNIE do napisania czytelnego komunikatu.
"""
from __future__ import annotations

import errno
import fcntl
import json
import os
import time
from collections.abc import Callable
from pathlib import Path
from types import TracebackType

LOCK_NAME = "run.lock"


class RunLocked(RuntimeError):
    """Inny proces prowadzi już bieg na tym projekcie."""


def lock_path(project: str, runtime_dir: str = ".forge") -> Path:
    return Path(project, runtime_dir, LOCK_NAME)


def _holder(fd: int) -> str:
    """Opis właściciela zamku z treści pliku; ``""`` gdy nieczytelna.

    Pusty opis nie jest powodem do wpuszczenia drugiego biegu — o tym rozstrzyga
    samo ``flock``. Brak treści oznacza tylko uboższy komunikat."""
    try:
        raw = os.pread(fd, 4096, 0).decode("utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(data, dict):
        return ""
    parts = []
    pid = data.get("pid")
    if isinstance(pid, int):
        parts.append(f"PID {pid}")
    started = data.get("started_at")
    if isinstance(started, (int, float)) and not isinstance(started, bool):
        parts.append("start " + time.strftime("%H:%M:%S", time.localtime(started)))
    return ", ".join(parts)


class RunLock:
    """Zamek trzymany przez CAŁE życie procesu biegu.

    Deskryptor zostaje otwarty celowo: ``flock`` wisi na otwartym pliku, więc
    zamknięcie deskryptora natychmiast zwolniłoby zamek.

    ``shared=True`` daje dzierżawę współdzieloną (``LOCK_SH``): dowolnie wielu
    czytelników naraz, ale nikt nie weźmie zamku wyłącznego. Tak trzyma się
    migawkę kodu — kilka biegów może pracować na tej samej kopii, a sprzątacz
    nie może jej wtedy usunąć.

    ``busy_message`` zamienia opis właściciela na komunikat dziedziny; domyślny
    mówi o katalogu projektu."""

    def __init__(self, path: Path,
                 busy_message: Callable[[str], str] | None = None,
                 shared: bool = False):
        self.path = Path(path)
        self.shared = shared
        self._busy_message = busy_message or self._project_busy
        self._fd: int | None = None

    def _project_busy(self, holder: str) -> str:
        detail = f" ({holder})" if holder else ""
        return (f"Projekt {self.path.parent.parent} prowadzi już bieg Forge"
                f"{detail}. Zatrzymaj tamten bieg albo wskaż inny katalog "
                f"projektu.")

    def acquire(self) -> "RunLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o644)
        mode = fcntl.LOCK_SH if self.shared else fcntl.LOCK_EX
        try:
            fcntl.flock(fd, mode | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK):
                os.close(fd)
                raise
            holder = _holder(fd)
            os.close(fd)
            raise RunLocked(self._busy_message(holder)) from exc
        if not self.shared:
            try:
                os.ftruncate(fd, 0)
                os.pwrite(fd, json.dumps(
                    {"pid": os.getpid(), "started_at": time.time()},
                    ensure_ascii=False).encode("utf-8"), 0)
            except OSError:
                # Nieudany opis właściciela nie jest powodem do rezygnacji
                # z zamku — chroni sam ``flock``, a treść jest tylko dla
                # komunikatu. Przy zamku współdzielonym w ogóle nie piszemy:
                # nadpisalibyśmy opis innego, równie uprawnionego czytelnika.
                pass
        self._fd = fd
        return self

    def release(self) -> None:
        if self._fd is None:
            return
        fd, self._fd = self._fd, None
        try:
            # Zamykamy bez kasowania pliku: usunięcie ścieżki spod innego
            # procesu, który właśnie ją otworzył, wpuściłoby dwa biegi naraz.
            os.close(fd)
        except OSError:
            pass

    def __enter__(self) -> "RunLock":
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None,
                 traceback: TracebackType | None) -> None:
        self.release()


def acquire(project: str, runtime_dir: str = ".forge") -> RunLock:
    return RunLock(lock_path(project, runtime_dir)).acquire()


def is_held(path: Path) -> bool:
    """Czy ktoś trzyma ten zamek — bez przejmowania go na własność.

    Do sprzątania cudzych zasobów: pytamy o zamek wyłączny i natychmiast go
    oddajemy. Nieczytelny plik traktujemy jako ZAJĘTY — pomyłka w tę stronę
    kosztuje niesprzątnięty katalog, a w drugą kod wyjęty spod pracującego
    procesu."""
    try:
        fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return True
    finally:
        os.close(fd)
    return False


def busy_reason(project: str, runtime_dir: str = ".forge") -> str:
    """Powód, dla którego start na tym projekcie nie ma sensu; ``""`` = wolny.

    Dla warstwy uruchamiającej (GUI), która chce odmówić PRZED zapłaceniem za
    proces. Rozstrzygający jest zamek brany przez sam orkiestrator — ten podgląd
    ma tylko oszczędzić operatorowi startu, który i tak by się nie udał."""
    if not Path(project).is_dir():
        # Podgląd nie zakłada katalogów: literówka w ścieżce zostawiałaby po
        # sobie puste ``<literówka>/.forge`` przy każdym sprawdzeniu.
        return ""
    try:
        acquire(project, runtime_dir).release()
    except RunLocked as exc:
        return str(exc)
    except OSError:
        # Niedostępny katalog rozpozna preflight; zamek nie jest od tego.
        return ""
    return ""
