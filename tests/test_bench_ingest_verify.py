"""The ``verify`` benchmark command is wired in (ADR 0111, step 5)."""
from benchmarks.ingest import verify
from benchmarks.ingest.__main__ import build_parser


def test_the_verify_command_takes_the_bundle_style_arguments():
    args = build_parser().parse_args(["verify", "--data-dir", "d", "--output", "o", "--repeats", "3"])
    assert args.command == "verify" and args.repeats == 3 and args.sizes == ["1GB"]


def test_the_child_reports_its_stats_after_the_command(tmp_path, monkeypatch):
    monkeypatch.setenv("FFAST_FRAME_ARRAYS_DIR", str(tmp_path / "p"))
    monkeypatch.setenv("FFAST_FRAME_INDEX_DIR", str(tmp_path / "i"))
    source = tmp_path / "x.xyz"
    source.write_text("1\n\nH 0 0 0\n")
    seconds, out, stats = verify._cli(dict(__import__("os").environ), "cache", "verify", str(source))
    assert stats["code"] == 2 and stats["peak_rss"] > 0 and "nothing could be checked" in out
