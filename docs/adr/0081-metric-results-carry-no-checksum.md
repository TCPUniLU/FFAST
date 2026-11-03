# Metric results carry no checksum

Status: Accepted 2026-11-03. Follows ADR 0080's measurement. Amends ADR 0022.

## The problem

Every metric run hashed its whole answer with SHA-256 and kept the hex string
on the Metric Result (`checksum`). MeluXina, 2026-11-03 (ADR 0080
"Measured"): 4.7 s for the 5.7 GB per-atom force difference inside the
colour-by-force-error click, 1.6 s for the 1.9 GB per-structure one in Basic
Errors. One thread, every byte, every run.

Nothing in the app reads it. The server sends it with each result; neither
client looks at it. ADR 0022 planned it as a fast path for the desktop plots'
redraw check, but the desktop hashes the drawn arrays instead
(`UI/Plots.py` `_dataSignature`, on purpose: the energy shift toggle lives in
no checksum). The metric cache finds results by metric id, implementation
hash, parameters and input fingerprints, before computing, so the checksum
plays no part in avoiding a repeat. Its only readers are two commands:
`ffast-cli metrics test` (run a metric twice, compare) and
`ffast-cli metrics run --json` (print it).

## The decision

1. **A Metric Result has no `checksum`.** The executor does not hash results.
2. **The two commands hash for themselves**: `metrics test` compares the two
   runs' digests, `metrics run --json` prints one, both the same SHA-256 of
   the values as before.
3. **The wire field stays optional and is ignored**, so a client still reads
   results from an older server it reconnects to (ADR 0024). New servers do
   not send it.

## Why

- Tools that cache by inputs do not hash outputs on the normal path (joblib's
  cache, Dask's task keys). Output hashes are for content-addressed storage
  (Git, DVC), which FFAST does not do.
- If reconnecting ever re-uses results the client already holds, the cache key
  already names a result exactly for as long as the cache lives (it lives in
  one server process; code cannot change inside it). It can serve as that tag
  without hashing bytes.

## Consequences

- The colour-by-force-error click and Basic Errors lose 4.7 s and 1.6 s of
  hashing on shard_03.
- ADR 0022's "if the backing Metric Result's checksum is unchanged the hash is
  skipped" does not hold; the drawn-array hash alone decides.
- If the metric cache is ever kept across server restarts, its key must cover
  more than the metric function's own source (helpers it calls are not
  hashed). That is a cache-key question, not a reason for a checksum.

## Considered and rejected

- **A fast non-cryptographic hash (xxh3).** About 0.5 s for 5.7 GB instead of
  4.7 s, but a new dependency to compute a value nobody reads.
- **Hash in the background, off the click's path.** Still spends a core on
  every large result while other metrics wait for cores.
