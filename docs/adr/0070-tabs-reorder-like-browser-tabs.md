# Tabs reorder like browser tabs, and "Edit tab" says what it does

Status: Accepted 2026-10-29. Amends ADR 0056 ("✎, + and ⋯ sit at the right
end of the tab bar").

## The problem

Two complaints about the browser's tab bar:

- **✎ is hard to find.** It is a small pencil with no word, at the far right
  of the bar, away from the tab it edits. Tabs react to a left click and the
  arrow keys only; right-click and double-click do nothing.
- **+ is far from the tabs.** With three or four tabs it sits across an empty
  gap at the right end. In a web browser, + sits right after the last tab.
  And tabs cannot be put in another order: the bar is always built-in tabs,
  then project tabs, then your own in the order you made them.

An "Edit" menu in the top bar, next to File and Metrics, was considered
first and dropped: "Edit" already means Edit mode (CONTEXT.md, Analysis tab),
and the app has no Undo, Copy or Paste to put in it (ADR 0056 turned undo
down).

## The decision

1. **+ sits right after the last tab.** When there are more tabs than fit,
   the tabs scroll and + stays at the visible right edge of the strip, so it
   never scrolls out of sight. ✎ and ⋯ stay at the right end of the bar.
2. **Tabs can be dragged to a new place.** The order is kept on the server,
   in `~/.ffast/tabs/state.json` beside the hidden list, as a list of tab
   names (`bar_order`). Every window sees the same order. Any tab can move,
   built-in ones too.
3. **Moving follows the rules of hiding.** It works outside Edit mode and is
   written at once, with no Save: the order is not part of any tab, so a tab
   file stays the same and can still be copied as it is. A window that does
   not have control of the shared session cannot move tabs, and no tab moves
   while one is being edited.
4. **Moving without dragging.** The tab menu has **Move left** and **Move
   right**; on a tab that has the keyboard focus, Ctrl+Shift+← and
   Ctrl+Shift+→ do the same. The ⋯ menu has **Reset tab order**, which
   empties `bar_order` and brings back the default order.
5. **✎ gets a word:** the button reads **✎ Edit tab**.
6. **Right-click on a tab** opens it and shows the tab menu, with **Edit
   tab** first; then the items of ⋯ (Export, Reset to original or Delete,
   Hide, Move left, Move right, Reset tab order, the hidden tabs to show).

## Where a tab goes

- A tab listed in `bar_order` goes in that place.
- A tab not listed (a new user tab, a project tab added later, a tab file
  copied in by hand) goes after the listed ones, in the default order.
- A renamed tab keeps its place: its name is changed in `bar_order` too.
- An edited copy of a built-in or project tab takes the original's place, as
  before. **Reset to original** gives the place back to the original.
- A hidden tab keeps its place, and comes back there when shown.
- Deleting a user tab drops its name from `bar_order`.

## On the wire

One new message, `SET_TAB_ORDER {names}`: the whole order, hidden tabs
included. An empty list resets the order. A name the server does not know is
dropped (another window may have deleted that tab meanwhile); a name listed
twice is refused. It is a mutating message (a read-only window cannot send
it), answered with `TAB_SAVED` (`action = "order"`), after which the new
layout goes to every window as `TAB_LAYOUT`, like hide and show.

Sending the whole list rather than "move X before Y" keeps the server's work
to one check and one write; the browser already holds every tab's name.

## What this gives up

- **One order for everyone on a server.** Two people sharing one server see
  the same order, as they already see the same hidden tabs. An order kept in
  each browser was the other choice; it was turned down so the bar works the
  same way in every window.
- The desktop ignores user tabs (ADR 0056 rule 17), so it ignores the order
  too.
