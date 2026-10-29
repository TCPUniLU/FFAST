Status: Accepted

# Retire three hollowed shells

Each of these was a real module once. A later ADR removed the thing it existed
for and left the shell behind, so its interface became as complex as the nothing
behind it. Deleting each *removes* complexity rather than moving it — the
opposite motion to a deepening, which is why they are grouped.

The architecture review that identified them listed four. The second — the dead
metric ordering surface — was already resolved by ADR 0046, so three remain.

## ConnectionRegistry → `decide_role`

`ffast/session/registry.py` held a `websocket -> role` dict. ADR 0044 Phase 2
removed the single-CONTROLLING gate: every admitted connection controls its own
views, so there is no global slot to arbitrate. That left the dict with nothing
to do. `role_of`, `has_controlling` and `count` had **no callers outside their own
tests**, and connection liveness is owned by `ConnectionHub`, which the server
already consults for `count`, `is_empty` and broadcast fan-out. What remained was
a three-line role decision wrapped in redundant bookkeeping.

Now a pure `decide_role(token_ok, read_only_requested) -> ClientRole`. `server.py`
drops the registry entirely: `_do_hello_handshake` and `_handler` no longer take
it, and the disconnect log reads the role from the connection's own local instead
of asking a registry to hand back what it had just been told.

The class was better tested than used — twelve tests, most asserting the dict's
bookkeeping. Six remain, covering the decisions that were ever behaviour: a valid
token grants CONTROLLING, N valid tokens grant N controllers (the Phase 2 change),
an explicit read-only opt-in beats a valid token (PRD story 73), and the decision
is stateless.

## BroadcastChannel satellite → deleted

ADR 0043 gave the web client a popped-out 3D view that held **no socket**: the
main tab relayed scenes over a `BroadcastChannel` and the satellite posted frame
intents back. ADR 0044 Phase 4 then made the pop-out a real second client with its
own connection, state replay and view — and the satellite stayed wired alongside
it as the fallback "for an older, single-client server".

That fallback was unreachable. `negotiate()` appends `MULTI_CLIENT_FEATURE`
**unconditionally**, so every ffast-server advertises it, and `_openPopout` took
the satellite branch only when the feature was absent. The decisive point is that
a version mismatch cannot happen at all: the web client is *served by the server
process*, shipped in the same wheel as `ffast.renderers.web` package data
(ADR 0045 Phase 6), so client and server are always the same build. There is no
older server for the fallback to serve.

Deleted: `satellite.js` (`LoupeSatelliteApp`), the `?mode=loupe` bootstrap branch,
and the main tab's mirroring — `_setupBroadcast`, `_broadcastScene`,
`_broadcastMeta`, `_onBroadcast`, `_bc`, `_chId`, the `bye` teardown, and the
`?ch=` channel plumbing. `FFastConnection.multiClient` went with it: its only
reader was the branch that chose between the two mechanisms.

`app.js`: 1,026 → 964 lines (1,333 before ADR 0050).

One field survived a first pass at deletion and is worth recording, because its
name and comment both misled. `_lastScene` was commented "cached snapshot for a
late-joining satellite" and was indeed posted to the channel — but
`_currentDynamicBondPairs()` also reads it, for an unrelated live feature: the
wire ships bond segments as *coordinates*, never index pairs, so bonds
"fill from dynamic" recovers pairs by matching segment endpoints back to the last
rendered atom positions. Removing the field would have silently broken that.
It is kept, with a comment that says what it is actually for.

The pop-out button's enablement was also coupled to the dead path — it was gated
on `this._bc` existing, i.e. on `BroadcastChannel` support, even though the live
pop-out has no such dependency. It now enables when a view opens.

## LoupeMenuHandler → deleted, and its base folded

ADR 0040 moved every Loupe menu item into the sidebar panes: bond width/colour
into BONDS, atom size and background colour into DISPLAY. `LoupeMenuHandler` was
left with `connectActions()` returning `pass`, and `UI/loupe/window.py` assigned
the constructed object to `self.menuHandler` where nothing ever read it. The
Loupe's `menuBar()` accessor existed for it and was never called either — the
`window.menuBar()` call in `MainMenuHandler` resolves against the main window.

Deleted, along with `UI/menuShared.py`: with `LoupeMenuHandler` gone,
`MenuHandlerBase` had exactly one subclass and nothing left to share, so its
`__init__`, `_addLoupeMenu`, `newLoupe` and `setNewLoupeEnabled` folded into
`MainMenuHandler`, which now derives from `EventClass` directly.

The empty `QMenuBar` **stays**. It is unreferenced now, but it is not invisible —
it reserves a strip in the Loupe window's layout, so removing it changes what the
window looks like. That is a UI decision, not dead-code removal, and it is left
with a comment explaining why an empty bar is deliberate.

## Verification

1190 pass. `import UI.mainMenu`, `import UI.loupe.window`, `import server` all
clean; `MainMenuHandler` keeps `_addLoupeMenu` and `setNewLoupeEnabled` (the
latter is called by `UIHandler._onRemoteConnected` on `REMOTE_CONNECTED`).

The pop-out is covered by the existing
`test_web_popout_opens_independent_live_controller`, which drives the live path —
the only path left. Nothing ever tested satellite mode, which is part of why it
survived this long.

Not GUI-verified: the Loupe's menu bar area and the pop-out button both want a
look.
