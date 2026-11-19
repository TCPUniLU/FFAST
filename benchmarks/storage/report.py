"""Tables, verdicts and the go/no-go of ADR 0109 §5, from the Parquet rows."""

import json
import sys
from pathlib import Path

from benchmarks.ingest.compare import ALARM, verdict
from benchmarks.storage import variants

# (column, label, unit divisor, unit name)
MEASURES = (
    ("write_s", "write wall", 1, "s"), ("write_cpu_s", "write CPU", 1, "s"),
    ("scan_s", "scan wall", 1, "s"), ("scan_cpu_s", "scan CPU", 1, "s"),
    ("frame_p50_ms", "frame p50", 1, "ms"), ("frame_p95_ms", "frame p95", 1, "ms"),
    ("frames_total_s", "2,000 frames total", 1, "s"), ("reopen_s", "reopen", 1 / 1000, "ms"),
    ("disk_bytes", "bytes on disk", 2**20, "MiB"), ("n_files", "files", 1, ""),
    ("peak_rss_bytes", "peak memory", 2**20, "MiB"),
)
# The measures that rule a variant out when it is worse than the baseline by the alarm (§5).
RULE_MEASURES = ("write_s", "scan_s", "frame_p95_ms", "reopen_s", "peak_rss_bytes")
# Measures for the "baseline no worse on every measure" test (§5, dominated).
DOMINATED_MEASURES = RULE_MEASURES + ("disk_bytes",)
MIN_DISK_SAVING = 0.25
MIN_COLD_SCAN_GAIN = 0.10


class ReportError(Exception):
    """The rows cannot be reported; the message says why."""


def _load(output):
    import duckdb

    files = sorted((Path(output) / "rows").glob("*.parquet"))
    if not files:
        raise ReportError(f"no rows in {Path(output) / 'rows'}")
    con = duckdb.connect()
    listed = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)
    con.execute(f"CREATE TABLE r AS SELECT * FROM read_parquet([{listed}], union_by_name=true)")
    return con


def _stats(con):
    """``{(case, cache, variant, measure): (median, q25, q75, p95, n)}`` over completed runs."""
    columns = ", ".join(m for m, *_ in MEASURES)
    rows = con.execute(f"""
        WITH ok AS (SELECT "case", cache, variant, {", ".join(f"{m}::DOUBLE AS {m}" for m, *_ in MEASURES)}
                    FROM r WHERE completed),
        long AS (SELECT * FROM ok UNPIVOT (value FOR measure IN ({columns})))
        SELECT "case", cache, variant, measure, median(value), quantile_cont(value, 0.25),
               quantile_cont(value, 0.75), quantile_cont(value, 0.95), count(value)
        FROM long WHERE value IS NOT NULL GROUP BY ALL""").fetchall()
    return {(c, k, v, m): (med, q25, q75, q95, n) for c, k, v, m, med, q25, q75, q95, n in rows}


def _cell(stats, scale, unit):
    med, q25, q75 = (x / scale for x in stats[:3])
    digits = 0 if unit == "" else 1 if unit == "MiB" else 3
    return f"{med:.{digits}f} [{q25:.{digits}f}–{q75:.{digits}f}]"


def _order(names, baseline):
    ordered = [n for n in variants.all_names() if n in names]
    return [baseline] + [n for n in ordered + sorted(set(names) - set(ordered)) if n != baseline]


def analyse(stats, baseline, real_cases):
    """Rule-outs and the go/no-go; returns a dict used by the report and the tests."""
    groups = sorted({(c, k) for c, k, *_ in stats})
    # npz-copy (the A/A check) is the same code as the baseline, not a candidate.
    names = {v for _, _, v, _ in stats if not v.startswith("npz")} - {baseline}
    ruled, reasons = set(), {v: [] for v in names}
    for case, cache in groups:
        for name in sorted(names):
            slower = []
            for measure in DOMINATED_MEASURES:
                base, cand = (stats.get((case, cache, v, measure)) for v in (baseline, name))
                if not (base and cand):
                    continue
                if measure in RULE_MEASURES:
                    ratio, word = verdict(base[:3], cand[:3], ALARM)
                    if word == "worse":
                        ruled.add(name)
                        reasons[name].append(
                            f"{measure} worse than {baseline} by x{ratio:.2f} on {case} ({cache})")
                slower.append(base[0] <= cand[0])
            # Dominated (the baseline is no worse on every measure): judged on real data,
            # where the compression gain would show; random synthetic positions hide it.
            if case in real_cases and slower and all(slower):
                ruled.add(name)
                reasons[name].append(f"dominated by {baseline} on {case} ({cache})")
    survivors = sorted(names - ruled, key=lambda n: _order(names | {baseline}, baseline).index(n))
    return {"ruled_out": {n: reasons[n] for n in sorted(ruled)}, "survivors": survivors,
            "go": _go(stats, baseline, survivors, real_cases)}


def _any_cache(stats, case, variant, measure):
    """A measure that does not depend on the cache state (disk bytes): from any group."""
    return next((s for (c, _, v, m), s in sorted(stats.items())
                 if (c, v, m) == (case, variant, measure)), None)


def _go(stats, baseline, survivors, real_cases):
    """Per survivor: the gain on the largest real file (§5): disk saving or cold scan."""
    out = {}
    cases = sorted(real_cases, key=lambda c: max((s[0] for k, s in stats.items()
                                                   if k[0] == c and k[3] == "disk_bytes"), default=0))
    if not cases:
        return {n: {"go": False, "why": "no real file in the rows"} for n in survivors}
    case = cases[-1]
    for name in survivors:
        base, cand = (_any_cache(stats, case, v, "disk_bytes") for v in (baseline, name))
        saving = None if not (base and cand and base[0]) else 1 - cand[0] / base[0]
        cold = None
        base_s, cand_s = (stats.get((case, "cold", v, "scan_s")) for v in (baseline, name))
        if base_s and cand_s:
            ratio, word = verdict(base_s[:3], cand_s[:3], MIN_COLD_SCAN_GAIN)
            cold = {"ratio": ratio, "verdict": word}
        reasons = []
        if saving is not None and saving >= MIN_DISK_SAVING:
            reasons.append(f"{saving:.0%} fewer bytes on disk")
        if cold and cold["verdict"] == "better":
            reasons.append(f"cold scan x{cold['ratio']:.2f}")
        out[name] = {"go": bool(reasons), "case": case, "disk_saving": saving, "cold": cold,
                     "why": ", ".join(reasons) or "no 25 % disk saving"
                     + ("" if cold else " and cold scan not measured")}
    return out


def build_report(output, baseline="npz"):
    """``(markdown, analysis)``."""
    output = Path(output)
    con = _load(output)
    stats = _stats(con)
    run_set = json.loads((output / "run_set.json").read_text()) if (output / "run_set.json").is_file() else {}
    facts, versions = run_set.get("host_facts", {}), run_set.get("versions", {})
    counts = con.execute(
        'SELECT "case", cache, variant, count(*), count(*) FILTER (WHERE NOT completed) '
        "FROM r GROUP BY ALL").fetchall()
    real = {c for (c,) in con.execute('SELECT DISTINCT "case" FROM r WHERE real').fetchall()}
    if not any(k[2] == baseline for k in stats):
        raise ReportError(f"baseline {baseline!r} has no completed rows")
    analysis = analyse(stats, baseline, real)

    lines = [f"# Storage bench: {baseline} against the other variants", "",
             f"Host: {facts.get('host', '?')}, {facts.get('cpu_model', '?')}, "
             f"{(facts.get('ram_bytes') or 0) / 2**30:.0f} GiB RAM, {facts.get('os', '?')}.",
             f"zarr {versions.get('zarr', '?')}, numcodecs {versions.get('numcodecs', '?')}; "
             f"Blosc on one thread (nthreads set to "
             f"{con.execute('SELECT max(blosc_nthreads) FROM r').fetchone()[0]}).", "",
             "Cells are median [25th–75th percentile] over completed runs. Ratio is the variant's "
             f"median over {baseline}'s. Verdict: *worse* needs a ratio above 1.10 and the variant's "
             f"25th percentile above {baseline}'s 75th; *better* is the mirror image; anything else is "
             "*no clear change*. p95 is over the runs and is about the maximum with 5 runs.", ""]
    failed = [(c, k, v, n, f) for c, k, v, n, f in counts if f]
    if failed:
        lines += ["Failed runs: " + "; ".join(f"{c} {v} ({k}): {f} of {n}" for c, k, v, n, f in failed), ""]
    for case, cache in sorted({(c, k) for c, k, *_ in stats}):
        lines += [f"## {case} ({cache}{', real data' if case in real else ''})", ""]
        present = {v for c, k, v, _ in stats if (c, k) == (case, cache)}
        for measure, label, scale, unit in MEASURES:
            lines += [f"### {label}" + (f" ({unit})" if unit else ""), "",
                      f"| Variant | Median [q25–q75] | p95 | n | Ratio | Verdict |",
                      "|---|---|---:|---:|---:|---|"]
            base = stats.get((case, cache, baseline, measure))
            for name in _order(present, baseline):
                cand = stats.get((case, cache, name, measure))
                if not cand:
                    continue
                if name == baseline or not base:
                    ratio_text, word = "–", "baseline" if name == baseline else "–"
                else:
                    ratio, word = verdict(base[:3], cand[:3], ALARM)
                    ratio_text = "–" if ratio is None else f"{ratio:.3f}"
                digits = 0 if unit == "" else 1 if unit == "MiB" else 3
                lines.append(f"| {name} | {_cell(cand, scale, unit)} | {cand[3] / scale:.{digits}f} | "
                             f"{cand[4]} | {ratio_text} | {word} |")
            lines.append("")
    aa = [(c, k, m) for (c, k, v, m), s in stats.items() if v == "npz-copy"
          and verdict(stats[(c, k, baseline, m)][:3], s[:3], ALARM)[1] != "no clear change"]
    if any(k[2] == "npz-copy" for k in stats):
        lines += ["## A/A check", "",
                  "`npz-copy` is the same code as `npz`: every verdict must read *no clear change*. "
                  + ("It did not: " + ", ".join(f"{m} on {c}" for c, _, m in aa) if aa else "It did."), ""]
    lines += ["## Rule-out (ADR 0109 §5)", ""]
    if analysis["ruled_out"]:
        for name, why in analysis["ruled_out"].items():
            lines.append(f"- **{name}** ruled out: " + "; ".join(why))
    else:
        lines.append("- No variant is ruled out.")
    lines += ["", "Survivors: " + (", ".join(analysis["survivors"]) or "none") + ".", "",
              "## Go or no-go (ADR 0109 §5)", ""]
    if analysis["go"]:
        for name, info in analysis["go"].items():
            lines.append(f"- **{name}**: {'go' if info['go'] else 'no go'} ({info['why']})")
    else:
        lines.append("- No survivor, so no go.")
    return "\n".join(lines) + "\n", analysis


def report(output, baseline="npz"):
    """The ``report`` subcommand."""
    try:
        text, _ = build_report(output, baseline)
    except ReportError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(text)
    (Path(output) / "report.md").write_text(text)
    return 0
