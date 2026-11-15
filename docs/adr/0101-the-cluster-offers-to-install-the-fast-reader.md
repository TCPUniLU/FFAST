# The cluster offers to install the fast reader before a file load

Status: Accepted 2026-11-15. Extends ADR 0100 (optional fast parser) and
ADR 0028 (cluster provisioning). Built in this change.

## The problem

A provisioned cluster server has FFAST but not the optional `ffast-xyz`
package. The first `.xyz` load uses Python and shows a warning telling the
user to run a command. A browser user cannot run that command on the compute
node, and the server wheel carries no Rust source for a fallback build.

## Decision

1. **Ask before loading a dataset or prediction file.** On a connection made
   through a cluster profile, the first `.xyz` or `.extxyz` load asks when the
   fast reader is missing or incompatible. Offer **Install and load** and
   **Load with Python**, plus **Cancel load**. **Don't ask again for this
   cluster** is stored per profile in this browser. Local connections retain
   the existing CLI installation path.

2. **The server owns the offer and installation.** One `FastReaderSetup` per
   Environment grants one offer across windows and runs at most one install.
   `FAST_READER` carries an action (`offer`, `skip`, `install`, `wait`);
   `FAST_READER_STATE` carries correlated progress and one final reply. It is
   a controlling-client operation. `LOADER_TYPES.fast_reader_setup` advertises
   support, so a new page never waits on an old server's unknown request.

3. **Only change a FFAST-provisioned venv.** The server requires a venv,
   its `.ffast-install.sha256` marker, and a writable prefix. Manually managed
   environments get the command and a Python fallback, with no Install button;
   the server also enforces this restriction. The installer targets the
   server's Python, with `--no-deps` so installing the reader does not change
   the scientific dependencies.

4. **Reuse the existing installer.** Prefer a matching release wheel checked
   against `SHA256SUMS`, then try a source build with Rust. Provisioning stages
   only the crate's source files in the venv's `ffast-xyz/`; laptop binaries
   never cross to the node. Changes to those files change the install marker.
   The node still needs access to the release for a download, and a source
   build needs Rust and its build dependencies. Failure keeps Python usable.

5. **Keep the load waiting, with progress.** The dialog shows the latest step
   and expandable installation details. Installation runs beside normal
   messages. Success activates a previously absent package in the running
   server and continues the load. An incompatible native extension already
   loaded in memory may require a new server job; say so and offer Python for
   the current load. A failed install waits for **Continue with Python** or
   **Cancel load** instead of silently choosing.

6. **Disconnecting cancels the wait, not the install.** The server holds the
   install task independently of the requesting window. Other windows can
   wait for that task and see its progress; they never launch a duplicate.
   Cancelling or changing a connection never sends the queued load to the
   next connection. A declined offer also suppresses the old missing-reader
   warning for this server.

## Alternatives considered

- **Install automatically during every cluster setup.** Avoids a prompt but
  adds a download/build to connections before a user needs this reader.
- **Show the existing warning.** Leaves users to find and activate the right
  environment on a compute node themselves.
- **Install into any writable server environment.** Could alter a manually
  maintained or shared environment; leave that choice with its maintainer.
- **Restart the server after every install.** Loses the current session just
  to discover a newly installed package. Import it immediately when possible;
  require restart only when an old native extension prevents activation.

## Consequences

The prompt covers file loads through the browser's dataset/prediction loader,
including Recent files. Session restoration, CLI scripts and local connections
keep their existing behavior. Remembered declines belong to a browser profile;
the one-off offer and install coordination belong to the server process.
