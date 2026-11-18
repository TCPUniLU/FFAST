"""Compare two sides of a run set: medians, spread and a verdict (ADR 0105 §5)."""

import sys
from pathlib import Path

ALARM = 0.10  # spec §8: 10 % for time, peak memory and cache size
MEASURES = (("load_s", "load seconds", "s"), ("basic_errors_s", "Basic Errors seconds", "s"),
            ("peak_rss_bytes", "peak memory (MiB)", "MiB"), ("cache_bytes", "cache on disk (MiB)", "MiB"))
SCENARIO_TITLES = {"first_open": "First open (empty FFAST caches)",
                   "reopen": "Reopen (reusing the caches)"}


class CompareError(Exception):
    """The two sides cannot be compared; the message says why."""


def verdict(base, cand, alarm=ALARM):
    """``base`` and ``cand`` are (median, q25, q75). Returns (ratio, word)."""
    if not base[0]:
        return None, "no clear change"
    ratio = cand[0] / base[0]
    if ratio > 1 + alarm and cand[1] > base[2]:
        return ratio, "worse"
    if ratio < 1 - alarm and cand[2] < base[1]:
        return ratio, "better"
    return ratio, "no clear change"


def _paths(output, extra):
    folders = [Path(output), *map(Path, extra)]
    found = []
    for folder in folders:
        files = sorted((folder / "rows").glob("*.parquet"))
        if not files:
            raise CompareError(f"no rows in {folder / 'rows'}; run `rebuild` or `run` first")
        found += [str(f) for f in files]
    return found


def _cell(stats, unit):
    median, q25, q75 = stats[:3]
    scale = 2**20 if unit == "MiB" else 1
    digits = 1 if unit == "MiB" else 2
    return (f"{median / scale:.{digits}f} [{q25 / scale:.{digits}f}–{q75 / scale:.{digits}f}]")


def build_report(output, baseline, candidate, extra=()):
    """``(markdown, results)``; raises CompareError when the sides must not be compared."""
    import duckdb

    con = duckdb.connect()
    files = ", ".join("'" + p.replace("'", "''") + "'" for p in _paths(output, extra))
    con.execute(f"CREATE TABLE r AS SELECT * FROM read_parquet([{files}], union_by_name=true)")
    sides = (baseline, candidate)
    present = {row[0] for row in con.execute("SELECT DISTINCT side FROM r").fetchall()}
    for side in sides:
        if side not in present:
            raise CompareError(f"side {side!r} has no rows; sides present: {', '.join(sorted(present))}")

    hosts = con.execute("SELECT DISTINCT host, cpu_model FROM r").fetchall()
    if len(hosts) > 1:
        raise CompareError("the run sets come from different machines: "
                           + "; ".join(f"{h} ({c})" for h, c in hosts))
    clash = con.execute(
        'SELECT "case", count(DISTINCT dataset_sha256) FROM r WHERE side IN (?, ?) '
        'AND dataset_sha256 IS NOT NULL GROUP BY "case" HAVING count(DISTINCT dataset_sha256) > 1',
        list(sides)).fetchall()
    if clash:
        raise CompareError("the sides used different data for case(s): "
                           + ", ".join(c for c, _ in clash))

    counts = {side: con.execute(
        "SELECT count(*) FILTER (WHERE scenario = 'first_open'), "
        "count(*) FILTER (WHERE NOT completed) FROM r WHERE side = ?", [side]).fetchall()[0]
        for side in sides}
    commits = {side: con.execute(
        "SELECT DISTINCT commit, dirty FROM r WHERE side = ?", [side]).fetchall() for side in sides}

    measure_list = ", ".join(m for m, _, _ in MEASURES)
    rows = con.execute(f"""
        WITH ok AS (
          SELECT "case", scenario, side, file_bytes,
                 load_s::DOUBLE AS load_s, basic_errors_s::DOUBLE AS basic_errors_s,
                 peak_rss_bytes::DOUBLE AS peak_rss_bytes, cache_bytes::DOUBLE AS cache_bytes
          FROM r WHERE completed AND side IN (?, ?)),
        long AS (SELECT * FROM ok UNPIVOT (value FOR measure IN ({measure_list})))
        SELECT "case", scenario, measure, side, max(file_bytes), count(*), median(value),
               quantile_cont(value, 0.25), quantile_cont(value, 0.75), quantile_cont(value, 0.95)
        FROM long WHERE value IS NOT NULL GROUP BY ALL
        """, list(sides)).fetchall()
    stats, order = {}, {}
    for case, scenario, measure, side, file_bytes, n, med, q25, q75, q95 in rows:
        stats[(case, scenario, measure, side)] = (med, q25, q75, q95, n)
        order[case] = (file_bytes or 0, case)

    results = []
    lines = [f"# Ingestion benchmark: {candidate} vs {baseline}", ""]
    host, cpu = hosts[0] if hosts else ("?", "?")
    lines += [f"Host: {host}, {cpu}.", ""]
    for side in sides:
        runs, failed = counts[side]
        shown = ", ".join(f"{(c or '?')[:10]}{' (dirty)' if d else ''}" for c, d in commits[side])
        lines.append(f"- **{side}**: commit {shown}; {runs} runs, {failed} failed scenario rows left out.")
    lines += ["", "Baseline is the left column. Cells are median [25th–75th percentile].",
              "Verdict: *worse* needs a ratio above 1.10 and the candidate's 25th percentile above the",
              "baseline's 75th; *better* is the mirror image; anything else is *no clear change*.",
              "The OS file cache is warm and not controlled: \"first open\" is not a cold disk read.",
              "p95 is about the maximum with 5 runs; it is not used for verdicts.",
              "`cache on disk` is measured once after both runs, so both scenarios show the same value.", ""]
    for scenario, title in SCENARIO_TITLES.items():
        lines += [f"## {title}", "", f"| Case | Measure | {baseline} | {candidate} | Ratio | Verdict |",
                  "|---|---|---|---|---:|---|"]
        for case in sorted(order, key=order.get):
            for measure, label, unit in MEASURES:
                base = stats.get((case, scenario, measure, baseline))
                cand = stats.get((case, scenario, measure, candidate))
                if not (base and cand):
                    continue
                ratio, word = verdict(base, cand)
                results.append({"case": case, "scenario": scenario, "measure": measure,
                                "ratio": ratio, "verdict": word,
                                "n_baseline": base[4], "n_candidate": cand[4]})
                shown = "–" if ratio is None else f"{ratio:.3f}"
                lines.append(f"| {case} | {label} | {_cell(base, unit)} (n={base[4]}) | "
                             f"{_cell(cand, unit)} (n={cand[4]}) | {shown} | {word} |")
        lines.append("")
    return "\n".join(lines), results


def compare(output, baseline, candidate, extra=()):
    """The ``compare`` subcommand."""
    try:
        text, _ = build_report(output, baseline, candidate, extra)
    except CompareError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(text)
    (Path(output) / f"compare-{baseline}-vs-{candidate}.md").write_text(text + "\n")
    return 0
