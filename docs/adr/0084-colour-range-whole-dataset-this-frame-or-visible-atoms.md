# Colour range: whole dataset, this frame or visible atoms

Status: Accepted 2026-11-03.

## The problem

Colouring atoms by a metric (Force Difference, a formula, any per-atom or
per-element metric) takes its automatic colour range over every frame of the
dataset (ADR 0064, amendment "Colouring reads the whole dataset"). That keeps a
colour meaning the same error in every frame, but a frame whose errors are all
small is drawn in one colour, and there is no way to see which atoms of that
frame are worst. Typed limits can do it, but only by reading the frame's
numbers and typing them again on every frame.

ADR 0064 left "a choice between every frame and this frame" for when someone
asked. A user asked on 2026-11-03.

## The decision

1. **A Range setting with three choices**, for every colouring by a metric:
   - *Whole dataset* (the default, as before): lowest and highest value over
     every frame.
   - *This frame*: lowest and highest value over every atom of the frame on
     screen.
   - *Visible atoms*: lowest and highest value over the atoms the atom filter
     leaves visible in the frame on screen. With no atoms hidden it gives the
     same as *This frame*, and stays chosen, so a user can set it once and then
     try filters.

   It is the key `range` (`dataset`, `frame`, `visible`) of the view's
   `ffast.atom_color` parameters, next to the colour map, so it is per view and
   saved like the colour map.
2. **Range changes only the ends of the colour bar.** The values are always
   the metric over the whole dataset cut to the frame, as now. An atom's value
   matches its 2D plot under every range, and a formula with `by=all` keeps
   working. *This frame* therefore still waits for the whole-dataset result the
   first time.
3. **A typed limit still wins, and switching the range clears both typed
   limits**, as changing the colour source does, so the switch always shows.
4. **When the whole dataset can't be computed** (a prediction for the frame on
   screen only), *Whole dataset* is greyed out with the hint "Prediction covers
   this frame only", and the row shows *This frame*.
5. **The control** is a Range row of radio buttons in the Colour By pane,
   styled like the Combine row, shown only when the colour source is a metric.

## Alternatives

- **Only for Force Difference.** Force Difference is an ordinary metric; a
  special case would gain nothing.
- **One setting for every view.** Simpler, but two views could not compare a
  frame's range with the dataset's side by side.
- **A tag on the colour bar to switch.** As easy to miss as the clickable
  limits.
- **Frames and atoms as two separate choices** (four combinations). "Whole
  dataset, visible atoms only" would apply the filter to every frame; for an
  atom number on a dataset of different structures, "atom 3" is a different
  atom in each frame. Can be added later without changing the three choices.
- **This frame computes the frame alone** (faster first view on a huge
  dataset). Switching the range would then change the numbers, not only the
  colours, and `by=all` formulas would be refused.

## Consequences

- A screenshot does not say which range it used; the Colour By pane does.
- Easy to reverse: one parameter key with a default, read in one place
  (`scene_builder.build_scene`).

## Not now

- **Predict every frame on demand** from a greyed-out *Whole dataset*.
- **Percentile range** for outliers (ADR 0064 "Not now").
