"""Regression: reconnect must recover the Session Token from the record.

Without it the reconnect HELLO is tokenless, the server grants READ_ONLY, and
the client cannot drive metric generation — plots stay empty and phantom tasks
never complete (observed on MeluXina, job 4790271). See ADR 0012.
"""
import cluster.connection as conn


def _redirect_records(tmp_path, monkeypatch):
    monkeypatch.setattr(conn, "_SESSIONS_FILE", str(tmp_path / "sessions.json"))


def test_token_saved_then_recovered(tmp_path, monkeypatch):
    _redirect_records(tmp_path, monkeypatch)
    conn.save_session_record("4790271", "meluxina", "mel0477", 8765,
                             token="secret-plaintext")
    assert conn.recover_token_for_job("4790271") == "secret-plaintext"


def test_recover_missing_job_returns_empty(tmp_path, monkeypatch):
    _redirect_records(tmp_path, monkeypatch)
    conn.save_session_record("111", "meluxina", "mel0001", 8765, token="tok")
    assert conn.recover_token_for_job("999") == ""


def test_recover_job_without_token_returns_empty(tmp_path, monkeypatch):
    _redirect_records(tmp_path, monkeypatch)
    conn.save_session_record("222", "meluxina", "mel0002", 8765)  # no token
    assert conn.recover_token_for_job("222") == ""


def test_job_id_matches_across_int_str(tmp_path, monkeypatch):
    _redirect_records(tmp_path, monkeypatch)
    conn.save_session_record("4790271", "meluxina", "mel0477", 8765, token="t")
    # reconnect may pass the job id as int or str; recovery must not care
    assert conn.recover_token_for_job(4790271) == "t"
