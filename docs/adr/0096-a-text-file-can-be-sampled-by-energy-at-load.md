Status: Proposed 2026-11-12. Builds on ADR 0095 (readers); needs its Level 2.

# A text file can be sampled by energy at load

## The problem

A large trajectory holds many near-identical low-energy frames and few
high-energy ones. Today a user can thin it at load only with "every n-th
frame" (`slice_num`). That keeps the same mix: the rare high-energy frames,
where a force field most often fails, stay rare or are dropped.

After a full load a user can already zoom the energy plot and tick Sub to make
a subset. That needs the whole file in memory first, which is exactly what a
file too big to load cannot give.

An energy is one number per frame. A Level 2 reader (ADR 0095) already walks
the whole file once in `find_frames`, and in a text format the energy sits on
the header line that pass reads anyway. So the energies can be known before
any positions or forces are read.

## Decision

1. **Level 2 readers only.** `find_frames` may also return each frame's
   energies, one array per energy name. FFAST saves them with the frame list
   in `~/.ffast/frame-index`, so a second load reads no energies at all.
   Energy sampling is offered only when the reader returned energies.
   Level 1 readers do not get it.

2. **Two ways to share frames among bins**, chosen in the load dialog:
   - **Even across energies** (the default): about the same number from each
     bin. Rare high-energy frames are not drowned out by the many low-energy
     ones.
   - **Keep the energy distribution**: each bin's share is proportional to
     how many frames it holds, rounded up, so every non-empty bin keeps at
     least one frame.

3. **The user sets N, the number of frames to keep.** Bins are equal-width,
   from the lowest to the highest energy. Their number is automatic by the
   Freedman–Diaconis rule (width = 2 × interquartile range / ∛N), capped at
   N/2. The user can override it.

4. **Exactly N frames.** A bin with fewer frames than its share gives all it
   has. What is left is shared among the bins that still have frames, until
   N are chosen or the file runs out.

5. **Inside a bin, frames are picked at random**, with a seed (42 by default,
   editable in the dialog). Neighbouring MD frames are near-copies. Picking
   every k-th frame inside a bin could line up with a periodic motion, such as
   a bond vibration; random picks do not.

6. **Which energy.** The one chosen as the dataset's reference energy. It is
   binned **per atom** when frames have different atom counts, since total
   energies of different-sized structures are not comparable, and **total**
   when every frame has the same atoms. The dialog says which one it used.

7. **Frames without an energy** (missing or not a number) are left out of the
   sample, with a warning naming how many. A file with no energies at all
   does not offer the option.

8. **A sampled dataset stores the list of frames it kept,** and the settings
   that made it: method, N, bins, seed and energy name. A prediction loaded
   later reads exactly those frames from its own file. A saved session stores
   the list, about 8 MB per million frames. Datasets loaded with every n-th
   frame keep the stride rule of ADR 0095.

9. **Either energy sampling or every n-th frame,** not both in one load.

10. **A preview before loading.** Once the energies are known, the load dialog
    shows a small energy histogram: all frames in grey, the frames that would
    be kept in colour. It updates as the method, N, bins or seed change.
    Positions and forces are read only after the user presses Load.

## Alternatives considered

- **Only proportional sampling.** It is the usual choice for a training set
  that should represent the data. It is offered, but it is not the default:
  the user's aim here is to see the rare high-energy frames.
- **A fixed 50 bins.** Simpler, but it ignores how wide the energies spread
  and how many frames are asked for.
- **Evenly spaced frames inside a bin, with no seed.** Repeatable without a
  seed, but it can line up with periodic motion. Repeatability comes from the
  stored frame list anyway (decision 8).
- **Energy sampling for Level 1 readers through a `read_energies()` method.**
  Possible later. Not needed now, and it would widen the contract every author
  reads.
- **Sample after a full load (zoom + Sub).** Already possible, but useless for
  a file that does not fit in memory.

## Consequences

- A user can load 5,000 frames spread over the whole energy range from a file
  of millions, reading positions and forces for those 5,000 only.
- `find_frames` must parse header energies. For xyz this adds a little work
  to a pass that already reads every header line.
- The frame-index cache grows by one float per frame per energy name.
- A dataset's kept frames are no longer always "every n-th", so every place
  that maps a dataset frame to a file frame must use the stored list.
- NumPy does not promise the same random numbers across versions. Re-sampling
  a file after a NumPy upgrade may pick different frames. A loaded dataset and
  its saved session do not change, since they store the list.
