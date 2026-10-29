"""Session records (~/.ffast/sessions.json): upsert/dedup, delete, load.

These back the reconnect UI (ADR 0024): each running cluster job writes a
record so it can be found again. The functions are module-level and keyed on a
module-global path; tests redirect that path to a tmp file via monkeypatch so
they never touch the real ~/.ffast.
"""
import cluster.connection as conn


def _redirect(monkeypatch, tmp_path):
    monkeypatch.setattr(
        conn, "_SESSIONS_FILE", str(tmp_path / ".ffast" / "sessions.json")
    )


def test_save_then_load_roundtrip(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    conn.save_session_record("42", "meluxina", "node07", 8765, token="tok")
    records = conn.load_session_records()
    assert len(records) == 1
    r = records[0]
    assert r["job_id"] == "42"
    assert r["profile_name"] == "meluxina"
    assert r["node"] == "node07"
    assert r["remote_port"] == 8765
    assert r["token"] == "tok"


def test_save_is_upsert_dedups_by_job_id(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    conn.save_session_record("42", "meluxina", "node07", 8765)
    # Same job_id, new node/port → replaces, not appends.
    conn.save_session_record("42", "meluxina", "node99", 8766)
    records = conn.load_session_records()
    assert len(records) == 1
    assert records[0]["node"] == "node99"
    assert records[0]["remote_port"] == 8766


def test_distinct_jobs_coexist(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    conn.save_session_record("1", "p", "n1", 8765)
    conn.save_session_record("2", "p", "n2", 8765)
    assert {r["job_id"] for r in conn.load_session_records()} == {"1", "2"}


def test_delete_removes_only_matching_job(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    conn.save_session_record("1", "p", "n1", 8765)
    conn.save_session_record("2", "p", "n2", 8765)
    conn.delete_session_record("1")
    assert [r["job_id"] for r in conn.load_session_records()] == ["2"]


def test_load_returns_empty_when_no_file(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    assert conn.load_session_records() == []


def test_delete_missing_job_is_noop(monkeypatch, tmp_path):
    _redirect(monkeypatch, tmp_path)
    conn.save_session_record("1", "p", "n1", 8765)
    conn.delete_session_record("nonexistent")  # must not raise
    assert [r["job_id"] for r in conn.load_session_records()] == ["1"]
