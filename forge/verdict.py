"""Kontrakt werdyktów ról i skrypt, którym rola zatwierdza swój werdykt.

Ten plik jest KOPIOWANY do katalogu runtime projektu (``.forge/verdict.py``) i
uruchamiany przez agenta jako zwykłe narzędzie powłoki. Dlatego nie wolno mu
importować niczego z pakietu ``forge`` ani spoza biblioteki standardowej — kopia
musi działać w cudzym repozytorium, bez PYTHONPATH i bez instalacji Forge.

Po co on jest: dotąd niepoprawny werdykt wychodził na jaw dopiero PO turze, w
parserze orkiestratora, a jedyną naprawą było powtórzenie całej tury. Pomiar z
2026-08-10: tura testera to 102 kroki modelu, 11,1 M tokenów i 40 minut — cena
za literówkę w strukturze JSON. Skrypt daje ten sam werdykt do sprawdzenia w
trakcie sesji, kosztem jednego kroku narzędziowego, więc rola poprawia się
sama, zanim tura się skończy.

Ten sam moduł jest jedynym źródłem prawdy o kontrakcie dla parserów Forge
(``task_pipeline``). Rozdzielenie reguł na „walidator w projekcie" i „parser w
orkiestratorze" wróciłoby dokładnie tym błędem, który skrypt ma usunąć: skrypt
mówi OK, orkiestrator odrzuca, tura ginie.
"""
from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

TASK_PHASES = ("tester", "coder", "review", "corrections", "commit")
TESTER_STATUSES = ("red", "code", "review", "finalize", "blocked")
CODER_STATUSES = ("green", "test_changes_needed", "tester_input_needed")
REVIEW_VERDICTS = ("approve", "suggestions", "request_changes")

# Role, które zatwierdzają werdykt skryptem. Nazwa jest zarazem nazwą pliku
# werdyktu, więc musi być stabilna: pojawia się w promptach ról.
ROLES = ("tester", "coder", "review")

_VERDICT_DIR = "verdict"
_SCRIPT_NAME = "verdict.py"


class InvalidDecision(ValueError):
    """Agent nie zwrócił poprawnego kontraktu decyzji."""


# --- Kontrakt ---------------------------------------------------------------

def _require_text(data: dict, field: str, context: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvalidDecision(f"{context} wymaga niepustego `{field}`")
    return value.strip()


def _statuses(allowed, default: tuple[str, ...]) -> tuple[str, ...]:
    if not allowed:
        return default
    # Zawężamy do kontraktu roli: plik kontraktu tury nie może ROZSZERZYĆ
    # zbioru statusów, bo parser i tak przyjmuje wyłącznie te znane tutaj.
    narrowed = tuple(name for name in default if name in set(allowed))
    return narrowed or default


def validate_tester(data: dict, *, statuses=None) -> dict:
    allowed = _statuses(statuses, TESTER_STATUSES)
    status = data.get("status")
    if status == "finalize" and "finalize" not in allowed:
        raise InvalidDecision(
            "`finalize` jest dozwolone tylko po werdykcie suggestions")
    if status not in allowed:
        raise InvalidDecision(
            f"niedozwolona decyzja testera: {status!r}; "
            f"dozwolone: {'|'.join(allowed)}")
    if status in {"red", "code"}:
        data["command"] = _require_text(
            data, "command", f"decyzja testera {status!r}")
    if status == "finalize":
        data["reason"] = _require_text(
            data, "reason", "decyzja testera 'finalize'")
    return data


def validate_coder(data: dict, *, statuses=None) -> dict:
    allowed = _statuses(statuses, CODER_STATUSES)
    status = data.get("status")
    if status not in allowed:
        raise InvalidDecision(
            f"niedozwolona decyzja kodera: {status!r}; "
            f"dozwolone: {'|'.join(allowed)}")
    return data


def validate_review(data: dict, *, statuses=None) -> dict:
    allowed = _statuses(statuses, REVIEW_VERDICTS)
    verdict = data.get("verdict")
    if verdict not in allowed:
        raise InvalidDecision(
            f"niedozwolony werdykt review: {verdict!r}; "
            f"dozwolone: {'|'.join(allowed)}")
    notes = as_strings(data.get("notes"))
    data["notes"] = notes
    data["nits"] = as_strings(data.get("nits"))
    if verdict == "approve" and notes:
        raise InvalidDecision(
            "werdykt 'approve' wymaga pustego `notes` "
            "(kosmetykę zapisz w `nits`)")
    if verdict in {"suggestions", "request_changes"} and not notes:
        raise InvalidDecision(
            f"werdykt {verdict!r} wymaga co najmniej jednej notatki w `notes`")
    return data


def as_strings(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    elif not isinstance(value, (list, tuple)):
        value = [value]
    notes = []
    for item in value:
        text = item if isinstance(item, str) else str(item)
        if text.strip():
            notes.append(text.strip())
    return notes


VALIDATORS = {
    "tester": validate_tester,
    "coder": validate_coder,
    "review": validate_review,
}

_SCHEMAS = {
    "tester": '{{"status":"{statuses}","command":"...","test_files":[],'
              '"reason":"...","notebook":"..."}}',
    "coder": '{{"status":"{statuses}","summary":"...","reason":"...",'
             '"notebook":"..."}}',
    "review": '{{"verdict":"{statuses}","notes":[],"nits":[]}}',
}
_DEFAULTS = {
    "tester": TESTER_STATUSES,
    "coder": CODER_STATUSES,
    "review": REVIEW_VERDICTS,
}


def schema(role: str, statuses=None) -> str:
    """Oczekiwany kształt werdyktu — z dokładnie tymi statusami, które przejdą."""
    allowed = _statuses(statuses, _DEFAULTS[role])
    return _SCHEMAS[role].format(statuses="|".join(allowed))


def validate(role: str, data: dict, *, statuses=None) -> dict:
    """Sprawdź werdykt roli; zwróć znormalizowane dane albo rzuć wyjątek."""
    if role not in VALIDATORS:
        raise InvalidDecision(
            f"nieznana rola {role!r}; dozwolone: {'|'.join(ROLES)}")
    if not isinstance(data, dict):
        raise InvalidDecision("werdykt musi być obiektem JSON, nie "
                              f"{type(data).__name__}")
    return VALIDATORS[role](data, statuses=statuses)


# --- Ścieżki i strona orkiestratora ----------------------------------------

def command(runtime_dir: str, role: str) -> str:
    """Komenda, którą rola zatwierdza werdykt (ścieżka względna projektu).

    Ścieżka jest cytowana, bo trafia wprost do powłoki agenta, a
    ``runtime_dir`` to dowolny string konfiguracji: spacja rozbiłaby ją na dwa
    argumenty i skrypt w ogóle by nie wystartował. Dla domyślnego `.forge`
    cytowanie nic nie zmienia, więc prompt zostaje bajtowo taki sam."""
    return f"python3 {shlex.quote(f'{runtime_dir}/{_SCRIPT_NAME}')} {role}"


def _dir(runtime: Path) -> Path:
    return runtime / _VERDICT_DIR


def verdict_path(runtime: Path, role: str) -> Path:
    return _dir(runtime) / f"{role}.json"


def contract_path(runtime: Path, role: str) -> Path:
    return _dir(runtime) / f"{role}.contract.json"


def install(project: str, runtime_dir: str) -> Path:
    """Skopiuj ten moduł do runtime projektu; zwróć ścieżkę kopii.

    Kopia, a nie import: agent pracuje w cudzym repozytorium i nie ma jak
    zaimportować pakietu Forge. Zapis tylko przy różnicy treści, żeby nie
    dotykać mtime przy każdej turze."""
    dest = Path(project, runtime_dir, _SCRIPT_NAME)
    source = Path(__file__).read_bytes()
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dest.read_bytes() == source:
            return dest
    except OSError:
        pass
    dest.write_bytes(source)
    return dest


def prepare(project: str, runtime_dir: str, role: str, *, statuses=None) -> None:
    """Przygotuj turę roli: świeży skrypt, kontrakt tury, brak starego werdyktu.

    Kasowanie jest obowiązkowe z tego samego powodu co przy ``codex_last.txt``:
    tura, która nic nie zatwierdzi, nie może odziedziczyć werdyktu poprzedniej."""
    install(project, runtime_dir)
    runtime = Path(project, runtime_dir)
    _dir(runtime).mkdir(parents=True, exist_ok=True)
    verdict_path(runtime, role).unlink(missing_ok=True)
    contract = contract_path(runtime, role)
    if statuses:
        contract.write_text(
            json.dumps({"statuses": list(statuses)}, ensure_ascii=False),
            encoding="utf-8")
    else:
        contract.unlink(missing_ok=True)


def read(project: str, runtime_dir: str, role: str) -> dict | None:
    """Werdykt zatwierdzony w tej turze albo ``None``."""
    path = verdict_path(Path(project, runtime_dir), role)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


# --- Skrypt dla roli --------------------------------------------------------

_USAGE = """\
Zatwierdź werdykt roli. Kontrakt jest sprawdzany NATYCHMIAST: przy błędzie
skrypt wypisuje powód i wychodzi kodem 1, więc możesz poprawić JSON i uruchomić
go ponownie w tej samej turze. Dopiero wyjście kodem 0 kończy Twoją pracę.

  python3 {runtime}/verdict.py <rola> <<'JSON'
  {{"status":"..."}}
  JSON

  python3 {runtime}/verdict.py <rola> sciezka/do/werdykt.json

Role: {roles}
"""


def _load_contract(runtime: Path, role: str):
    try:
        data = json.loads(contract_path(runtime, role).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    statuses = data.get("statuses") if isinstance(data, dict) else None
    return statuses if isinstance(statuses, list) and statuses else None


def _describe(role: str, data: dict) -> str:
    key = "verdict" if role == "review" else "status"
    return f"{key}={data.get(key)!r}"


def main(argv: list[str] | None = None, *, stdin=None, stdout=None, stderr=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    stderr = sys.stderr if stderr is None else stderr
    runtime = Path(__file__).resolve().parent
    usage = _USAGE.format(runtime=runtime.name, roles=", ".join(ROLES))
    if not argv or argv[0] in {"-h", "--help"}:
        print(usage, file=stdout)
        return 0 if argv else 1
    role = argv[0]
    if role not in ROLES:
        print(f"BŁĄD: nieznana rola {role!r}; dozwolone: {', '.join(ROLES)}",
              file=stderr)
        return 1
    if len(argv) > 1 and argv[1] != "-":
        try:
            raw = Path(argv[1]).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"BŁĄD: nie mogę przeczytać {argv[1]!r}: {exc}", file=stderr)
            return 1
    else:
        raw = stdin.read()
    if not raw.strip():
        print("BŁĄD: pusty werdykt — podaj JSON na stdin (heredoc) albo "
              f"ścieżkę pliku.\n\n{usage}", file=stderr)
        return 1
    allowed = _load_contract(runtime, role)
    hint = f"Popraw i uruchom ponownie. Oczekiwany kształt: {schema(role, allowed)}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        context = raw[max(0, exc.pos - 60):exc.pos + 60].replace("\n", " ")
        print(f"BŁĄD: to nie jest poprawny JSON: {exc.msg} "
              f"(linia {exc.lineno}, kolumna {exc.colno}); kontekst: …{context}…\n"
              + hint, file=stderr)
        return 1
    try:
        data = validate(role, data, statuses=allowed)
    except InvalidDecision as exc:
        print(f"BŁĄD: {exc}\n" + hint, file=stderr)
        return 1
    path = verdict_path(runtime, role)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"OK: werdykt roli {role} przyjęty ({_describe(role, data)}). "
          "Możesz zakończyć turę; ponowne uruchomienie nadpisuje werdykt.",
          file=stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
