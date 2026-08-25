import threading
from pathlib import Path

from forge.artifacts import atomic_write


def test_atomic_write_uses_unique_temporary_files_across_threads(tmp_path: Path):
    target = tmp_path / "state.json"
    payloads = [f'{{"writer": {index}, "body": "{"x" * 2000}"}}\n' for index in range(24)]
    barrier = threading.Barrier(len(payloads))

    def write(payload: str) -> None:
        barrier.wait()
        atomic_write(target, payload)

    threads = [threading.Thread(target=write, args=(payload,)) for payload in payloads]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert all(not thread.is_alive() for thread in threads)
    assert target.read_text(encoding="utf-8") in payloads
    assert list(tmp_path.glob(".state.json.*.tmp")) == []
