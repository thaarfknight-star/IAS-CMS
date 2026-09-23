# -*- coding: utf-8 -*-
"""Headless regression test for IAS-CMS 2.0.35-beta (delivery stability).

- WAL tuning on SQLite connections
- daily DB backup + pruning to 7
- MemoryGuard cadence (gc gen-1 only)
- startup maintenance runs once per day
- stores still import and open tuned connections
"""
import os
import sqlite3
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# stub سنگین‌ها برای محیط headless (طبق AGENTS.md)
import types  # noqa: E402

for _mod in ("cv2", "numpy"):
    if _mod not in sys.modules:
        sys.modules[_mod] = types.ModuleType(_mod)

from db_maintenance import (  # noqa: E402
    MemoryGuard,
    backup_databases,
    maybe_collect,
    run_startup_maintenance,
    tune_connection,
)


def _tmpdir():
    return tempfile.mkdtemp(prefix="ias235_")


def test_01_tune_sets_wal():
    d = _tmpdir()
    p = os.path.join(d, "t.db")
    conn = sqlite3.connect(p)
    tune_connection(conn)
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    conn.close()
    assert mode.upper() == "WAL", f"journal_mode={mode}"
    print("PASS 01: WAL mode applied")


def test_02_tune_busy_timeout():
    d = _tmpdir()
    p = os.path.join(d, "t.db")
    conn = sqlite3.connect(p)
    tune_connection(conn)
    to = conn.execute("PRAGMA busy_timeout;").fetchone()[0]
    conn.close()
    assert to >= 5000, f"busy_timeout={to}"
    print("PASS 02: busy_timeout set")


def test_03_backup_creates_files():
    d = _tmpdir()
    for n in ("persons.db", "plates.db"):
        c = sqlite3.connect(os.path.join(d, n))
        c.execute("CREATE TABLE t(x)")
        c.close()
    made = backup_databases(d)
    assert len(made) == 2, f"made={made}"
    assert all(os.path.isfile(m) for m in made)
    print("PASS 03: backup creates one file per db")


def test_04_backup_prunes_to_keep():
    d = _tmpdir()
    c = sqlite3.connect(os.path.join(d, "a.db"))
    c.execute("CREATE TABLE t(x)")
    c.close()
    bd = os.path.join(d, "backups")
    os.makedirs(bd, exist_ok=True)
    for i in range(10):
        open(os.path.join(bd, f"2020010{i}_a.db"), "w").write("x")
    backup_databases(d, keep=7)
    left = [f for f in os.listdir(bd) if f.endswith(".db")]
    assert len(left) == 7, f"left={len(left)}"
    print("PASS 04: old backups pruned to 7")


def test_05_startup_runs_once_per_day():
    d = _tmpdir()
    c = sqlite3.connect(os.path.join(d, "a.db"))
    c.execute("CREATE TABLE t(x)")
    c.close()
    first = run_startup_maintenance(d)
    second = run_startup_maintenance(d)
    assert len(first) == 1, f"first={first}"
    assert second == [], f"second={second}"
    print("PASS 05: startup maintenance once per day")


def test_06_memory_guard_cadence():
    g = MemoryGuard(every_frames=600)
    fired = [g.note_frame() for _ in range(599)]
    assert not any(fired), "gc fired too early"
    assert g.note_frame() is True, "gc did not fire at 600"
    assert g.note_frame() is False, "gc fired right after reset"
    print("PASS 06: MemoryGuard fires every 600 frames")


def test_07_maybe_collect_func():
    assert maybe_collect(600) is True
    assert maybe_collect(599) is False
    assert maybe_collect(1200) is True
    print("PASS 07: maybe_collect cadence")


def test_08_person_store_tuned():
    d = _tmpdir()
    import person_store
    st = person_store.PersonStore(db_path=os.path.join(d, "persons.db"))
    mode = st._conn.execute("PRAGMA journal_mode;").fetchone()[0]
    st.close() if hasattr(st, "close") else st._conn.close()
    assert mode.upper() == "WAL", f"mode={mode}"
    print("PASS 08: PersonStore connection is WAL-tuned")


def test_09_plate_store_tuned():
    d = _tmpdir()
    import plate_store
    st = plate_store.PlateStore(db_path=os.path.join(d, "plates.db"))
    mode = st._conn.execute("PRAGMA journal_mode;").fetchone()[0]
    try:
        st.close()
    except Exception:
        st._conn.close()
    assert mode.upper() == "WAL", f"mode={mode}"
    print("PASS 09: PlateStore connection is WAL-tuned")


def test_10_report_store_connect_tuned():
    d = _tmpdir()
    import report_store
    rs = report_store.ReportStore(db_path=os.path.join(d, "reports.db"))
    conn = rs._connect()
    mode = conn.execute("PRAGMA journal_mode;").fetchone()[0]
    conn.close()
    assert mode.upper() == "WAL", f"mode={mode}"
    print("PASS 10: ReportStore connections are WAL-tuned")


if __name__ == "__main__":
    test_01_tune_sets_wal()
    test_02_tune_busy_timeout()
    test_03_backup_creates_files()
    test_04_backup_prunes_to_keep()
    test_05_startup_runs_once_per_day()
    test_06_memory_guard_cadence()
    test_07_maybe_collect_func()
    test_08_person_store_tuned()
    test_09_plate_store_tuned()
    test_10_report_store_connect_tuned()
    print("ALL 2.0.35-beta TESTS PASSED (10/10)")
