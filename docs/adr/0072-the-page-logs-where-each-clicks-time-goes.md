# The page logs where each click's time goes

Status: Accepted 2026-10-30. Builds on ADR 0071 (the launcher talks to the
page over HTTP) and the server trace (`ffast-server --trace`).

## The problem

On a cluster, opening a tab's metrics, energy shift and making a subset from a
plot selection all feel slow. Nothing said where the time went:

- A cluster server runs without `--trace`, so it wrote nothing.
- Even with it, the trace logs only how long each server handler ran. The trip
  through the SSH tunnel, time spent waiting on the server, reply size and the
  page's drawing were not measured.
- A reply could not be tied to the click that caused it: messages carry no
  number.

The server reads one message, handles it to the end, then reads the next
(`server.py`, `receive_loop`). One slow handler delays every message after it,
and from the outside that looks like a slow network.

## The decision

1. **The page numbers what it sends.** A page opened by `ffast-web` asks for
   the `timing` feature in HELLO. When the server grants it, every message
   carries an `id` beside `event` in the envelope. Older servers ignore the
   field.
2. **The server links replies to their message.** While a numbered message is
   handled, a context variable names it. The connection's outbound queue
   (`TimedQueue`) records the name and the time each message is queued. Tasks
   the handler starts (a metric computed later) carry the name too. A copy
   broadcast to another page is not linked: that page sent nothing.
3. **The server reports in TIMING messages**, built by `ffast/session/timing.py`:
   - after each message it sends: when it was queued and sent, its size, and
     its cause;
   - when a handler ends: when its message was read and when handling ended;
   - in answer to TIMING_PING: the server's clock.

   Each "sent" note goes straight after its message, not through the queue,
   so the page pairs it with the message it received just before.
4. **The page lines the clocks up.** It sends TIMING_PING five times on
   connect, then every ten seconds. It keeps the quickest answer, which gives
   the round trip and the clock offset to within half of it.
5. **The page groups by click.** A click (`pointerup`, so a plot drag counts
   too), a change or a shortcut key starts an action. Messages sent and
   received after it belong to it, and a reply always goes to the action of
   the message that caused it. When an action has had no traffic for a second
   and the server has finished every message it sent, the page writes one
   block. Each block gives the total time, and for each message: time to reach
   the server (network ≈ half the round trip, the rest waiting), time handled,
   queue time before sending, time to reach the page, page handler time, and
   when it was on screen (two frames after the handler). Long tasks in the
   page are added up as "page busy".
6. **The launcher writes the file.** The page posts lines to `POST /timing`
   with the launch id header (the same guard as `/cluster/`). The launcher
   appends them to `~/.ffast/logs/timing-<date-time>.log`, one file per run,
   and keeps the newest ten. The server keeps nothing, so a cluster run needs
   no file copied back.

## Alternatives

- **Turn on `--trace` on the cluster and copy the file back.** This still
  gives only handler times, and it adds an SSH copy step.
- **OpenTelemetry** (a standard tracing library) with a trace viewer. It would
  mean a collector process and a viewer for one user's two processes. The
  numbered envelope gives the same answer.
- **A timing panel in the page.** It is easier to look at, but nothing is
  kept to compare runs. It can be added later on top of the same log.

## Consequences

- Timing is on for every page `ffast-web` opens. It costs one small TIMING
  message per message sent to the page, and one per message handled.
- A page served by `ffast-server --web-port` has no launch id and keeps no
  log.
- "Reached page after" includes time the page was too busy to read the
  message. "page busy" shows how much of that there was.
- A cluster running older FFAST code sends no TIMING messages. The log then
  shows only the page's own times and says so on the "connected" line.
