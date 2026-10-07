Status: Accepted — not yet implemented. Prototype results added 2026-10-07.

# The browser client gets its own layout; layout state stays in the browser

Reported from a review of the running web client: "I don't like it. It's
overloaded."

Screenshots of `ffast-web` with the example dataset and prediction showed where
that impression comes from:

- **Top bar:** server URL, token, read-only, Connect, Disconnect, Save Session
  and Load Session are always on screen. A local launch needs none of them, and
  it still makes you click Connect, although the launcher already passed the
  port in the URL.
- **3D tab:** five pick tools shown as icons with no names; all eight sidebar
  sections expanded at once, so the sidebar scrolls; and a nine-item playback
  strip. Before anything is loaded, every one of these controls is shown and
  none of them can do anything.
- **Analysis tabs:** each tab shows a dataset/prediction picker with nothing to
  pick when one of each is loaded. Basic Errors without a prediction draws about
  eight boxes, each saying "Select a prediction to compute this panel", even when
  no prediction is loaded at all.
- **A real bug among the clutter:** the Subsystem Errors tables printed `0.00`
  for force errors around 0.002, because tables round to a fixed number of
  decimal places. The desktop has the same bug.

The density was inherited, not designed. `index.html` was written to *mirror*
the Qt shell: the same palette, the same object rail beside tabbed content, and
the same stacked Loupe panes. ADR 0045 never asked for that. Its definition of
parity is "every *task* a Qt user can perform is performable in the browser —
**not** pixel- or interaction-identical chrome". Copying the desktop brought the
desktop's density into a page that has less room and fewer conventions to lean
on.

## Decision

**1. The browser lays itself out for the browser.** Only the web client changes;
the Qt desktop is left alone. The existing section names stay (Colour By,
Camera, Display, Bonds, Force Vectors, Extract Subset, Alignment, Export), so a
desktop user can still find things by name.

**2. A control appears when it can act.** Nothing is removed. Controls move to
where they are needed, or wait until there is something for them to act on:

| Area | Change |
|---|---|
| Connection | The page connects on its own when its URL carries the server port (the launcher's link, or `ffast-server --web-port`'s). The top bar shows only a status label; clicking it opens a dialog with the address, token, read-only, Connect/Disconnect and the last five servers. The dialog opens by itself when no server is known or a token is needed. |
| Session actions | A **File** menu: Load Dataset…, Load Prediction…, Save Session…, Load Session…, Export Selected Dataset…, Connect to Server…. The rail's `+` buttons stay as shortcuts; the unlabelled `⬇` goes. |
| 3D sidebar | The eight sections form a list where only one is open at a time. Arming a pick tool opens that tool's section. The whole sidebar can be hidden. On a first visit, Colour By is open. |
| 3D empty state | Until a dataset is open, the sidebar, pick toolbar and playback strip are hidden, and the viewport shows a Load Dataset… button. Loading a dataset while no view is open opens it. Controls with no data behind them are hidden, for example the Prediction selectors before a prediction is loaded. |
| Inside sections | Camera's azimuth, elevation and distance fields move behind an "Exact angles" link. The Pick radius control is removed: a click anywhere on an atom's drawn ball picks it, and very small atoms keep a minimum target of a few pixels. |
| Sidebar search | Typing opens every section with a match and highlights the matching rows. It also finds rows behind "Exact angles". Rows that need missing data are shown greyed out with the reason ("Load a prediction first"). Clearing the search restores the section that was open. |
| Quick styles | Three buttons that only set existing controls: Force error (disabled until a prediction is loaded), Publication and Reset. They live in the browser only. |
| Help | A hint bar with numbered next steps, and a "?" on each section and on the tab picker. Input boxes become dark instead of white. |
| 3D drawing | Below an atom-count threshold (1000 atoms, set by measuring frame time), structures get the chemistry.alive look the prototype settled: ball-and-stick atoms (0.42 × covalent radius, kept within 0.24–0.55 Å, times the Atom size setting), thin bonds drawn in two halves coloured like their atoms (a chosen Bond colour replaces this), smoother spheres and bonds, slightly glossier materials, lights that turn with the camera (a sky light plus key, fill and rim lights) so an atom keeps its shade while you orbit, and a 35° lens. Above the threshold, today's cheaper look stays, with atoms at the server's sizes. The pixel ratio is capped at 2. No tone mapping: it shifts data colours away from the colour bar. The background stays black. |
| Pick and playback strips | Pick tools show their names next to their icons. While a tool is armed, the atom under the pointer is highlighted, larger and lighter, so you can see what a click would pick. FPS and Skip move into a ⚙ pop-up; its ⚙ button is drawn larger than the other strip icons. |
| Analysis tabs | The tab picker appears only when two or more datasets or predictions are loaded. A tab where no panel can draw shows one guide with a button. A tab where some panels can draw shows them, plus one line for the rest. The message says "Load" when nothing is loaded and "Select" when something is loaded but not chosen. Plot cards and table sizes are unchanged. |
| Keyboard | One action list feeds the File menu, the shortcuts and a Ctrl/Cmd+K command palette. The shortcuts: Space (play/pause), ← → (frame), Esc, Ctrl/Cmd+S (save session), Ctrl/Cmd+O (load session) and `/` (sidebar search). Shortcuts are ignored while typing in a text box. Load Session takes Ctrl/Cmd+O rather than the desktop's Ctrl+L, because browsers use Ctrl+L for the address bar. |

A throwaway prototype of the 3D tab settled five points in the table:

- **Atom look.** The chemistry.alive look was preferred over today's: its
  lighting and, after the first implementation kept server sizes and looked
  wrong, its ball-and-stick atoms and two-tone bonds too. The threshold keeps
  large structures fast.

- **Pick radius.** It only limited how far from an atom's *centre* a click
  could land (12 px). Atoms are usually drawn larger than that, so changing it
  made no visible difference. Picking by the drawn ball replaces it.
- **Hover highlight.** Asked for while testing the pick tools. The desktop
  already has one.
- **FPS and Skip.** Both layouts were tried; the ⚙ pop-up was kept, with a
  larger button.
- **Tone mapping.** Dropped: data colours must match the colour bar.

**3. Layout state lives in browser storage, never in the session file.** This
covers which section is open, whether the sidebar is hidden, the recent-servers
list and dismissed hints. Saved sessions are never migrated (ADR 0008), so
anything written into one is permanent. These layout choices were made on the
understanding that they stay cheap to revisit. Tokens are never stored: a saved
token gives control of the session to anyone at that browser.

**4. One table-formatting rule for both clients.** `precision` keeps its
documented meaning, decimal places, but a non-zero value that would round to
zero shows three significant digits instead (`0.00213`, not `0.00`). This is the
one change to the desktop, and it is a bug fix rather than a layout change.

## Alternatives rejected

- **One comparison picker shared by every tab.** ADR 0053's reason still holds:
  the rail drives the 3D view, and comparing four models in one tab must not
  change what the 3D view shows. Hiding the picker when there is no choice
  removes the clutter without losing per-tab scope.
- **Four group tabs (Look / Colour / Selection / Export).** These would add group
  names on top of the section names, so you would have to guess that Bonds lives
  under "Look". The one-open list keeps all eight names visible and shows only
  one section's controls.
- **A "More" toggle in every section.** With one section open, the largest
  (Camera) shows eight rows. A second hiding layer everywhere would save too
  little. The two odd rows were moved instead.
- **Hiding Extract and Alignment until atoms are picked.** Both also accept
  typed indices (`0 1 2`, `C -H`), so hiding them would remove that route.
- **Hiding Force Vectors until a prediction is loaded.** Its source defaults to
  the reference forces, so it works with no prediction.
- **Moving each plot's settings behind a ⚙.** The only per-plot setting is
  Smoothing, and it is in use. Hiding it saves one row.
- **Server-side presentation presets now.** The glossary already reserves
  "Presentation preset" for named, shared, inheritable settings. That design
  touches the server, the protocol and session files. Quick styles are
  deliberately smaller and are not the same thing.
- **Redefining `precision` as significant digits.** That would silently change
  every existing config that sets it.
- **A full-window viewport with floating panels, rounded "glass" styling and a
  gradient background.** The panels would cover the molecule, the layout does
  not suit the analysis tabs, and the style does not suit a research tool.

## What this gives up

- **The two clients no longer look alike.** Screenshots and instructions written
  for one will not match the other. Keeping the section names makes switching
  between them easier, but you still have to learn where things moved.
- **Hidden controls can be missed.** The sidebar search, the "?" help, greyed
  rows that state their reason, and the one-time hints exist to make up for
  this. If they turn out not to be enough, that is a reason to show more by
  default. It is not a reason to go back to mirroring the desktop.
- **A server that needs a token fails its first automatic connection.** This is
  intended: the dialog opens with a message saying a token is needed. Putting
  the token in the URL avoids it, at the cost of the token appearing in browser
  history.

## The rich look is browser-only presentation

ADR 0052 makes the server the owner of presentation, so both clients draw the
same sizes and colours. The rich look departs from that in the browser only:
the server still sends covalent radius × Atom size, and the browser redraws
those as ball-and-stick; it also keeps its own 35° lens while reporting the
server's field of view back unchanged, so no server state moves. The desktop
keeps drawing the server's sizes. Moving this look onto the server, as a
style both clients share, remains possible and would restore ADR 0052's
single source; it was not done now because ADR 0055 changes no server code.

## Reversibility

Apart from the one-line desktop table fix, all of this is in
`ffast/renderers/web/static/`. No server code, protocol message or session-file
field changes. Each row of the table above lands as its own commit, so any one
of them can be reverted without touching the others. That is also why rule 3
matters: it is what keeps the rest reversible.
