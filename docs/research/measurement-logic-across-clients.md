# Measurement logic across clients: where should distance/angle/dihedral live?

Research note, 2026-10-04. Question: what is the best practice for sharing small, stable
maths (FFAST's atom measurements) between the Python server and the plain-JavaScript
browser client, and what do comparable projects do?

Labels: **[Fact]** = checked in the cited source or measured; **[Inference]** = my reasoning.

## 1. Short answer

Keep one reference version in Python (used by the server and by Qt until Qt retires), keep the
small JavaScript copy so the browser can show a measurement instantly, and add a test that
feeds the same inputs to both and checks both against the same expected numbers. That is the
standard "check in the client for speed, keep the server as the authority" pattern used for
form validation. It also matches the molecular viewers checked here: viewers that run in the
browser compute measurements in the browser. Pyodide (Python running inside the browser) and
code generation each solve the "one source" problem, but they cost about 16 MB of downloads or
a build step to save about 20 lines of maths. A per-click server request adds delay and buys
nothing here, because the browser already holds the coordinates.

## 2. What comparable projects do

| Project | Where measurements are computed | Source |
|---|---|---|
| Mol* | In the browser (TypeScript). `Vec3.dihedralAngle` gives a **signed** dihedral; labels call it directly. [Fact] | [vec3.ts](https://github.com/molstar/molstar/blob/master/src/mol-math/linear-algebra/3d/vec3.ts), [label.ts](https://github.com/molstar/molstar/blob/master/src/mol-theme/label.ts) |
| MolViewSpec (Python → Mol*) | Python only **declares** a `distance`/`angle` primitive. Mol* computes the value in the browser. [Fact] | [builder.py](https://github.com/molstar/mol-view-spec/blob/master/molviewspec/molviewspec/builder.py), [primitives.ts](https://github.com/molstar/molstar/blob/master/src/extensions/mvs/components/primitives.ts) |
| NGL Viewer | In the browser. Its dihedral is **unsigned**, 0–180°, like FFAST's. [Fact] | [dihedral-representation.ts](https://github.com/nglviewer/ngl/blob/master/src/representation/dihedral-representation.ts) |
| nglview (Python ↔ NGL) | Python sends `addRepresentation('distance', …)`; NGL in the browser computes it. Picked atoms flow back to Python as a synced `picked` dict, so users can do their own maths in Python. [Fact] | [widget.py](https://github.com/nglviewer/nglview/blob/master/nglview/widget.py) |
| 3Dmol.js | I found no built-in measurement tool (code search for "dihedral"/"measure" in `src/` found nothing relevant). It seems to be left to the host app. [Not fully verified] | [GLViewer.ts](https://github.com/3dmol/3Dmol.js/blob/master/src/GLViewer.ts) |
| Chemiscope | Python computes all numbers up front (`extract_properties`, `explore`) and writes them into the input file; the JS viewer only displays them. No measurement tool found in the viewer. [Fact; "no tool" is from code search] | [structures/\_\_init\_\_.py](https://github.com/lab-cosmo/chemiscope/blob/main/python/chemiscope/structures/__init__.py), [explore](https://github.com/lab-cosmo/chemiscope/blob/main/python/chemiscope/explore/__init__.py) |
| ChimeraX | Desktop, in Python: `chimerax.geometry.dihedral` (signed); `angle` command. No browser version. `remotecontrol rest` lets a browser send commands, which still run inside ChimeraX. [Fact] | [vector.py](https://github.com/RBVI/ChimeraX/blob/develop/src/bundles/geometry/src/vector.py), [angle.py](https://github.com/RBVI/ChimeraX/blob/develop/src/bundles/std_commands/src/angle.py), [remotecontrol](https://www.cgl.ucsf.edu/chimerax/docs/user/commands/remotecontrol.html) |
| VMD | Desktop program; `measure bond/angle/dihed` run inside the same program. [Fact] | [measure](https://www.ks.uiuc.edu/Research/vmd/current/ug/node138.html) |
| trame / ParaView | Logic and state live in the Python server; the browser shows the UI. Rendering can happen on the server (images sent) or in the browser (geometry sent). [Fact] | [trame guide](https://kitware.github.io/trame/guide/intro/getting_started.html), [trame-vtk](https://github.com/Kitware/trame-vtk) |

**Pattern [Inference]:** the code runs where the coordinates already are. Browser viewers
measure in the browser. Python-first tools compute in Python and ship the results. The
bridges (nglview, MolViewSpec) let Python *ask for* a measurement and let JavaScript do it.
None of the molecular viewers checked sends one interactive measurement to a server.
Only trame keeps everything on the server, by design.

## 3. Options for FFAST

| | **1 + 2a** Python reference + JS copy + parity test | **2b** ask server per pick | **Pyodide** | **Code generation** |
|---|---|---|---|---|
| Delay per pick | ~0 ms; browser has positions | Today 125 ms median locally (100 ms server poll); ~7 ms if handled like a metric request; plus SSH-tunnel round trip remotely [Fact, FFAST measurement] | ~0 after start; start takes seconds | ~0 |
| Offline / cluster | Works | Works (the browser needs the server anyway) | Works only if ~16 MB is vendored (copied into the repo) | Works if the generated file is committed |
| One source of truth | Python is the reference; JS is a tested copy | Yes | Yes | Yes (the spec) |
| Upkeep | Two copies of ~20 lines; test catches drift | New message, wire `selection.indices`, "waiting" state in UI | Large runtime to upgrade | Generator + build step; clashes with zero-build (ADR 0045) |
| Effort | Small | Medium | Large | Medium–large |

Supporting facts:
- Pyodide's full distribution is "200+ megabytes"; it can be self-hosted from any static file server [Fact, [Pyodide docs](https://pyodide.org/en/stable/usage/downloading-and-deploying.html)]. The minimum for numpy, measured from the official CDN for v314.0.7: `pyodide.asm.wasm` 9.6 MB + `pyodide.asm.mjs` 1.25 MB + `python_stdlib.zip` 2.5 MB + numpy wheel 3.0 MB ≈ **16.4 MB** before compression [Fact, my download]. The Pyodide roadmap says initialisation "takes 4 to 5 seconds" [Fact, [roadmap](https://pyodide.org/en/stable/project/roadmap.html); figure may be dated].
- SymPy can print formulas as JavaScript (`jscode`) [Fact, [SymPy printing](https://docs.sympy.org/latest/modules/printing.html)]. Transcrypt compiles Python to JavaScript but ports only "a small part of Numpy" [Fact, [transcrypt.org](https://www.transcrypt.org/)]. Both need a build step.
- MDN: client checks are "an important feature of good user experience", and a server round trip causes "a noticeable delay"; the server must still check [Fact, [MDN](https://developer.mozilla.org/en-US/docs/Learn_web_development/Extensions/Forms/Form_validation)]. OWASP says the same: "use it for immediate feedback" [Fact, [OWASP](https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html)].

## 4. Best practice for FFAST

**Recommendation: option 1 + 2a.** [Inference, based on the facts above]

1. **One Python home.** Treat `ffast/metrics/builtin/structure_metrics.py` as the reference. Point the
   Qt Info tool at it, so `client/mathUtils.py` no longer has its own dihedral/angle copy.
2. **Keep `measure.js`** for the browser read-out. It needs no network and no build.
3. **Add a parity test** (section 6). It is cheap: Playwright already imports ES modules in tests.
4. **Use the server for bulk work, not clicks.** Wire `selection.indices` only when you want
   "this dihedral across every frame" as a plot. The server holds all frames; the browser holds one.

Why: the maths is ~20 stable lines; the browser already has the coordinates; a per-click request
adds 7–125 ms locally and more through a tunnel; Pyodide adds ~16 MB to save 20 lines; code
generation breaks the zero-build rule.

**A drift already exists [Fact, I ran both on 2026-10-04].** For three collinear atoms, `measure.js`
reports a dihedral of **90°**. `client/mathUtils.getDihedral` and the server metric `ffast.dihedral`
return **NaN** ("not a number"). A shared test would have caught this. Decide on one rule
(e.g. show "undefined") and encode it in the test vectors.

Side note [Fact]: Mol* and ChimeraX report signed dihedrals (−180° to 180°); NGL and FFAST report
unsigned ones (0–180°). If FFAST ever switches, change Python first; the parity test will then
force the JS change.

## 5. Decision guide for other projects [Inference]

- **Small, stable maths; the client already has the data; the user wants instant feedback** → copy it
  into the client and add a parity test. (Form validation; Mol*/NGL measurements.)
- **Large or changing logic, data only the server has, or the result is saved/trusted** → compute on the
  server and send results. (trame; ipywidgets, where a Python model and a browser model are kept in
  sync [Fact, [ipywidgets](https://ipywidgets.readthedocs.io/en/stable/examples/Widget%20Low%20Level.html)]
  and Python code reacting to changes runs in the kernel [Inference].)
- **Heavy Python libraries must run with no server at all** (static site, teaching page) → Pyodide.
  Accept tens of MB and seconds of start-up.
- **Many formulas, several languages, frequent changes** → generate code from one spec. Only worth it
  when copies × change rate is high and a build step already exists.
- **Shared message shapes, not maths** → one schema (JSON Schema) checked on both sides.

## 6. Parity-test pattern

A *parity test* runs two implementations on the same inputs. A *golden file* is a stored list of
inputs with their agreed answers. Well-known projects use this: JSON-Schema-Test-Suite ships
"language agnostic" JSON cases for every validator [[README](https://github.com/json-schema-org/JSON-Schema-Test-Suite)];
Unicode requires implementations to reproduce `NormalizationTest.txt` [[UAX #15](https://www.unicode.org/reports/tr15/)];
the WebAssembly and ECMAScript specs ship shared conformance suites
[[wasm](https://github.com/WebAssembly/spec/tree/main/test/core), [test262](https://github.com/tc39/test262)].

For FFAST:

1. Store cases once, e.g. `tests/data/measure_vectors.json`: kind, points, expected value, tolerance.
2. Example case: `{"kind": "dihedral", "points": [[0,1,0],[0,0,0],[1,0,0],[1,-1,0]], "expected": 180.0}`.
3. Python side: run each case through `ffast.distance/angle/dihedral`.
4. JS side: a Playwright page imports `measure.js`; `page.evaluate` runs the same cases.
5. Compare **both** to `expected`, not only to each other, so a bug in both is still caught.
6. Include edge cases: 90°, trans (180°), cis (0°), collinear atoms, two atoms at the same spot.
7. Optional: reuse the `tests=[…]` vectors already declared on each `@metric` as the source.

## 7. Sources

- Mol*: https://github.com/molstar/molstar/blob/master/src/mol-math/linear-algebra/3d/vec3.ts ; https://github.com/molstar/molstar/blob/master/src/mol-theme/label.ts ; https://github.com/molstar/molstar/blob/master/src/mol-repr/shape/loci/dihedral.ts ; https://github.com/molstar/molstar/blob/master/src/mol-plugin-state/manager/structure/measurement.ts
- MolViewSpec: https://github.com/molstar/mol-view-spec (README, `molviewspec/molviewspec/builder.py`) ; https://github.com/molstar/molstar/blob/master/src/extensions/mvs/components/primitives.ts
- NGL: https://github.com/nglviewer/ngl/blob/master/src/representation/dihedral-representation.ts
- nglview: https://github.com/nglviewer/nglview/blob/master/nglview/widget.py ; https://github.com/nglviewer/nglview/blob/master/nglview/parameters.py
- 3Dmol.js: https://github.com/3dmol/3Dmol.js/blob/master/src/GLViewer.ts (GitHub code search)
- Chemiscope: https://github.com/lab-cosmo/chemiscope (README, `python/chemiscope/structures/__init__.py`, `python/chemiscope/explore/__init__.py`, `src/structure/viewer.ts`)
- ChimeraX: https://github.com/RBVI/ChimeraX/blob/develop/src/bundles/geometry/src/vector.py ; https://github.com/RBVI/ChimeraX/blob/develop/src/bundles/std_commands/src/angle.py ; https://www.cgl.ucsf.edu/chimerax/docs/user/commands/remotecontrol.html ; https://www.cgl.ucsf.edu/chimerax/docs/user/commands/distance.html
- VMD: https://www.ks.uiuc.edu/Research/vmd/current/ug/node138.html
- trame: https://kitware.github.io/trame/ ; https://kitware.github.io/trame/guide/intro/getting_started.html ; https://github.com/Kitware/trame-vtk
- ipywidgets: https://ipywidgets.readthedocs.io/en/stable/examples/Widget%20Low%20Level.html
- Pyodide: https://pyodide.org/en/stable/usage/downloading-and-deploying.html ; https://pyodide.org/en/stable/project/roadmap.html ; https://cdn.jsdelivr.net/pyodide/v314.0.7/full/ (file sizes)
- MDN: https://developer.mozilla.org/en-US/docs/Learn_web_development/Extensions/Forms/Form_validation
- OWASP: https://cheatsheetseries.owasp.org/cheatsheets/Input_Validation_Cheat_Sheet.html
- SymPy: https://docs.sympy.org/latest/modules/printing.html
- Transcrypt: https://www.transcrypt.org/
- JSON-Schema-Test-Suite: https://github.com/json-schema-org/JSON-Schema-Test-Suite
- Unicode UAX #15: https://www.unicode.org/reports/tr15/
- WebAssembly spec tests: https://github.com/WebAssembly/spec/tree/main/test/core
- test262: https://github.com/tc39/test262
- FFAST: `docs/adr/0045-web-client-replaces-qt.md`, `client/mathUtils.py`, `ffast/metrics/builtin/structure_metrics.py`, `ffast/metrics/input_resolver.py`, `ffast/renderers/web/static/measure.js`, `ffast/core/environment.py`, `tests/ffast/renderers/web/test_web_pure_helpers.py`
