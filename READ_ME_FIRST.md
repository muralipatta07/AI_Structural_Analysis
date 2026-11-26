# AI Structural Analysis — audited source update

This is a complete source folder. Keep the previous app folder and EXE as backups.

## Start

1. Extract the entire ZIP into a new folder.
2. Double-click `start_app.cmd`. It uses your Python 3.12 installation.
3. Connect to the intended ETABS instance.
4. Open **Module Diagnostics**, then **Check prerequisites**.
5. Test one module at a time. Send the exact module/action/log if it reports an error.

No change to your current ETABS model is made merely by opening this app or running
the diagnostics. Defining cases, loads, combinations or a new model requires the
corresponding action button. Save your model before editing; the model generator
replaces the connected model only after its existing confirmation.

Your existing EXE is not updated by extracting Python files. Use this folder's
`start_app.cmd` or `app.py` for testing; rebuild an EXE separately after validation.

## What report status means

- **Not run**: no action/output recorded in this session.
- **Preview / calculation only**: a preview or empirical calculation exists.
- **Action completed (no new report)**: an action completed but did not create a report.
- **Output available**: data/setup output exists; this does not mean structural checks passed.
- **Needs setup**: required cases, assignments, results or inputs are missing.
- **Error**: an operation failed; the log contains the error.
- **STALE: model changed**: the app changed the model after this output. Re-read it.

App-generated model changes invalidate cached modal/reinforcement previews.
Changes made externally in ETABS must still be reviewed and data re-read. Session
tracking is not an independent audit of every external model edit.

## Shared corrections

- Empty dropdowns remain dropdowns; mixed grid/pack action bars are supported.
- Null table headers and missing numeric values are not turned into passing zeros.
- Returned table sizes, ETABS codes and typed COM array arguments are checked.
- Story levels use the direct Story API, including the actual base elevation.
- GUI ETABS calculations run in kN/m and restore the user's display units afterwards.
- Missing results are reported as prerequisites, rather than zero results or OK.
- Existing load patterns are updated without duplicate-name Add errors.
- Combination mappings validate before any target combination is replaced.
- Eigen and Ritz now use their respective API interfaces.
- Reports distinguish setup/preview/output from engineering pass/fail.
- A Module Diagnostics page lists prerequisites for all 18 numbered modules.

## Module review

| Module | Change / current scope | Required for live use |
|---|---|---|
| 1 Automatic checks | Geometry/modifiers/settings; lock state is not proof of completed design | Review applicability; run dedicated results modules |
| 2 P-Delta | Creates a nonlinear gravity P-Delta case instead of an incomplete E2K fragment | Review factors/convergence; no preset-stiffness linking is automatic |
| 3 Mass | Replaces ignored-checkbox/SourceMass path; table import and readback checks | Compatible returned mass-table fields; assigned loads; unlocked model |
| 4 Modal setup | Eigen/Ritz dispatch, validated modes/targets, checked writes | Valid mass/model; analysis to verify participation |
| 5 Modal results | Direct mass-ratio API; no invalid rotational/translational ratio comparison | Finished selected modal case |
| 6 Time period | Direct story reader; units, base/plinth and empty-pier handling | Correct system/geometry; tall-building applicability remains separate |
| 7 Principal axes | Explicit exploratory participation proxy; **automatic application disabled** | A force-based directional study is needed to establish critical direction |
| 8 Response spectrum | Checked writes, clipped period range, missing/zero shear is NOT CHECKED | Valid functions/modal case; finished static and RS cases for scaling |
| 9 Pushover | Uses entered gravity initial case, acceleration scale and displacement | Hinges/materials, gravity state and convergence must be defined/reviewed |
| 10 Torsion | Empty/nonmatching diaphragm results cannot pass | Diaphragms and selected finished lateral cases |
| 11 Drift/displacement | Separate unavailable displacement status; no empty OK | Finished lateral cases; valid action basis and diaphragm outputs |
| 12 Base shear share | Global transformation for linear-static case extraction only | **No RS/nonlinear/combination-envelope reconstruction; no independent 25% capacity claim** |
| 13 Wall density | Direct pier dimensions/orientations; no assumed thickness/zero angle | Verified pier labels/optional blade frames and footprint area |
| 14 Load patterns | Duplicate-safe writes, type/self-weight readback | Auto lateral parameters and floor/wall loads remain separate |
| 15 Combinations | Validates all used case mappings before writes; checked API codes | Project-specific factors/schedule applicability |
| 16 Detailing | Direct concrete-design summary; invalid fits blocked; unused controls inactive | Finished concrete design; draft fit is not complete detailing/code compliance |
| 17 AI generator | Existing validated floor-wise/explicit-plan workflow retained | Confirmed geometry, material/section data; review before replacement |
| 18 Python/MCP | Valid paths, launch errors, UTF-8 logs and one process at a time | Correct interpreter/packages; MCP server launch is not geometry creation |
| Reports | Clear output states; stale/failed selected outputs blocked | Refresh selections and review scope/NOT CHECKED items |

## Verification and limitations

The included offline regression suite covers shared backend cases, COM-array
marshalling against the installed ETABS type library, Eigen/Ritz writes with
simulated responses, missing mappings/results, report states, all 18 page
constructors and Reports, dropdowns and mixed-geometry dialogs.

Run it with:

```bat
python -X utf8 test_audited_app.py
```

An ETABS G+25 window was detected during preparation, but its API returned no
connection to this process. **Live ETABS reads/writes and numerical integration
tests were not completed.** Do not interpret the offline tests as validation of
your model or all API behaviour on your machine.

This update repairs execution/data-handling paths. It is not a complete
independently verified IS 456/IS 1893/IS 13920/IS 16700 design engine. Existing
empirical/reference calculations need project/code-edition review. In particular,
M20 material acceptance, tall-building systems, wall requirements, torsion,
P-Delta, load combinations, reinforcement and foundations are not certified.

## Dependencies

Use your existing Python 3.12 environment. `requirements.txt` lists the optional
features' packages. The MCP entry point uses the FastMCP SDK interface; if your
installed MCP package is incompatible, the server prints a specific installation
message. Standalone Run Python file does not require a running MCP server.
