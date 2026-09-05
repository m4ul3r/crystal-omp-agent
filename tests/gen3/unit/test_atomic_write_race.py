"""Concurrent frame publishes must not eat each other's temp file.

The temp sibling used to be a fixed `.<name>.tmp`, so two publishers racing --
the emulator's tick observer and a driver publishing directly -- had one
`os.replace` consume the file the other was still writing. The loser raised
FileNotFoundError and dropped its frame, which shows up in the widget as a
flash. Seen live in the collector's log against `live/.default.png.tmp`.
"""

import threading

import pytest

from pokeagent.live import _atomic_write


@pytest.mark.unit
def test_concurrent_writes_never_lose_a_temp(tmp_path):
    target = tmp_path / "default.png"
    payloads = [bytes([i]) * 4096 for i in range(1, 9)]
    errors = []

    def publish(data):
        try:
            for _ in range(25):
                _atomic_write(target, data)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=publish, args=(p,)) for p in payloads]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"racing publishers raised {errors[:3]}"
    # Whoever won last, the file must be one writer's payload in full --
    # never a partial or a mix.
    assert target.read_bytes() in payloads
    # And no temp litter is left behind.
    assert [p.name for p in tmp_path.iterdir()] == ["default.png"]


@pytest.mark.unit
def test_failed_write_leaves_no_temp(tmp_path):
    target = tmp_path / "sub" / "default.png"   # parent does not exist
    with pytest.raises(OSError):
        _atomic_write(target, b"x")
    assert list(tmp_path.iterdir()) == []
