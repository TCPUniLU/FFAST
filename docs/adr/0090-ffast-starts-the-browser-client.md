# `ffast` starts the browser client

Status: Accepted 2026-11-07. Built 2026-11-07. Supersedes the "Qt when PySide6
is present" rule of [ADR 0045](0045-web-client-replaces-qt.md), and the wait
for a real-cluster test in [ADR 0071](0071-the-web-launcher-opens-cluster-connections.md).

## The problem

`ffast` started the desktop client whenever PySide6 could be imported, and the
browser client otherwise. So the same command gave two different programs on
two machines. A developer with the `gui` extra never saw what a new user sees,
and the README, the tours and the tab templates are all written for the browser
client. The desktop client is being retired and gets no new features.

## What was chosen

`ffast` always starts the browser client. `ffast-qt` still starts the desktop
client. `ffast-web` is unchanged. `launcher.py` no longer looks for PySide6.

Alternatives that were live:

- Keep the dispatch and only change the order. Rejected: the command would
  still depend on what is installed.
- Remove `ffast-qt` now. Rejected: the desktop client still works and some
  people use it until the web client covers everything.

## What it gives up

A user who installed the `gui` extra and typed `ffast` to get the desktop
window now gets a browser tab. They must type `ffast-qt`. The old
"PySide6 is not installed" message is gone because nothing checks any more.
