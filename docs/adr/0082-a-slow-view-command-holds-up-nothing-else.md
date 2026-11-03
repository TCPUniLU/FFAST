# A slow view command holds up nothing else

Status: Accepted 2026-11-03. Follows ADR 0080's measurement.

## The problem

MeluXina, 2026-11-03, `shard_03_pred.extxyz` (1,773,605 structures). The
user clicks "Colour atoms by force error", then opens Basic Errors 2.6 s
later. Basic Errors' metric requests reach the server 13 s after the click
that sent them: at 11:10:23, the second the colour metric finished.

Two things together cause it:

1. **The server handles a window's messages one at a time, in order**
   (`server.py` `handle_loop`). Only `REQUEST_METRIC` is moved aside to run in
   parallel, and only once the loop reaches it.
2. **A view command waits for its scene.** Colour by is a `VIEW_COMMAND`. Its
   handler rebuilds the 3D scene and waits for it
   (`server_session.py` `_on_view_command`). Building the scene computes the
   colour metric over the whole dataset (`get_metric` →
   `generateMetric`): 13.1 s for the per-atom force error on shard_03.

So every message behind the colour click waited: Basic Errors' requests, and
any slider move or camera turn. One slow metric stopped the whole window.

## The decision

Two parts, built and committed separately, (a) first.

### (a) Draw first, colour later

1. **A scene never computes a whole-dataset colour metric itself.** When the
   metric is in the cache, the scene uses it, as today. When it is not, the
   scene colours the atoms from the frame on screen alone, the fallback that
   already exists for a metric that cannot be computed (`color_values.py`
   `_whole_dataset_values` → None).
2. **The scene says its colours are provisional.** The colour bar shows
   "computing colours over all structures…" until the final colours arrive,
   so the frame-only range is not taken for the dataset's.
3. **The server computes the whole-dataset metric on the side**, through the
   same compute threads and the same "compute once" sharing as a 2D plot's
   request (`_compute_once`), so a plot asking for it meanwhile waits for the
   same run.
4. **When it is done, the view is rebuilt and sent again**, but only if the
   view still colours by the same metric, parameters, dataset and prediction.
   Otherwise the result just stays in the cache. The rebuild uses the view as
   it is then (its frame may have moved).
5. **If it fails**, the frame-only colours stay and the colour bar says it
   failed and why, as a failed 2D panel does (ADR 0066).

### (b) View commands run beside the queue

6. **A `VIEW_COMMAND` runs beside the message queue**, as `REQUEST_METRIC`
   does: the loop starts it and goes on to the next message.
7. **One view's commands still run one at a time, in the order sent.** Each
   view has its own small queue. Different views do not wait for each other.
8. **Latest wins for frame and camera moves.** A `SetFrame` or `SetCamera`
   still waiting in a view's queue is dropped when a newer one of the same
   kind arrives. Every other command (parameter, feature toggle, selection,
   edit, undo, redo) always runs.
9. **Other messages stay in the main queue.** Loading, deleting, saving a
   session and the rest keep running one at a time, in order. They may now
   run while a view rebuilds: a scene whose dataset or prediction was deleted
   meanwhile already degrades to an empty scene (ADR 0044). Saving a session
   already runs in its own worker thread, outside the queue
   (`_queue_session_op`); it reads datasets and a copy of the cache, never a
   view, so a rebuild running beside it changes nothing for it.

## Why

- Draw what is ready, fill in the rest when it comes: map and 3D viewers draw
  progressively, and FFAST's own 2D panels already appear one by one as their
  metrics finish.
- (a) removes today's cause. (b) keeps the next slow command, whatever it is
  (a huge frame, a slow file read), from stopping the window.
- (b) repeats a pattern the server already has (metric requests beside the
  queue, ADR 0077).
- Separate commits keep each part easy to revert alone.

## Consequences and risks

- **Colours change twice** after "colour by" on a large dataset: frame-only,
  then dataset-wide. The colour bar's numbers jump when the final colours
  arrive. The "computing…" note is what keeps the first picture from
  misleading.
- **Two scene updates for one command** where there was one; tests that
  expect one need changing.
- **A late result must not overwrite a newer choice**: the check in rule 4.
- **Order within a view** now depends on the per-view queue (rule 7). A bug
  there could leave a view on an old frame.
- **More work at once on the server**: a view rebuild can run beside metric
  requests and other views' rebuilds, in threads.

Expected on shard_03, with the checksum removal (ADR 0081): Basic Errors'
metrics start when it is clicked, not 13 s later; the colour click answers at
once with frame-only colours, and the final colours follow in ~7 s.

## Considered and rejected

- **Only (b).** Nothing else waits, but colour by still shows nothing new for
  13 s, and holds a thread that long.
- **Only (a).** Removes today's cause; the next slow view command blocks the
  window again.
- **Make the colour metric faster.** Worth doing (ADR 0081 removed 6 s of it),
  but any metric can be slow on a large enough file; it does not stop one
  command holding up the window.
- **Run every message beside the queue.** Loading, deleting and saving
  depend on order; letting them interleave is a larger change than this
  problem needs.
