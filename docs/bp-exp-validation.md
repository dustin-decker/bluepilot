# bp-exp integration and validation

## Current branch: Original angle controller restored (2026-09-07)

Restored the original angle strategy, gain schedule, estimator, and their tests. The experimental retune and dependent adaptations are absent from the branch. Model/parser updates, PR #194 takeover guards, AutoCal/smoothing, and gauge layout fixes are retained. Version-2 retune evidence and locks are not restored by this version-1 estimator. Factor values require independent retuning; historical results below describe the removed configuration and are not validation of the current controller.

## Independent calibration resets — September 7, 2026

Camera reset actions on TICI, MICI, and the model-selection prompt preserve steering learning. Separate offroad, confirmed delay/torque resets warn about relearning time. The Device button previously discarded steering delay; resetting the camera there after a model change now preserves that time-consuming estimate. The model-selection prompt already preserved delay and now also preserves learned torque.

Local checks: 10 reset/progress tests, 48 translation checks, and 16 native widget screenshot comparisons pass. Tests use isolated parameters; no actual device calibration is reset. The web portal's panel directory is absent on this branch and the device returns an empty panel list, so it exposes no corresponding reset action.

## Collapsed settings row layout — September 7, 2026

Reproduced overlapping lateral controls after opening descriptions and reopening the menu: hidden rows cached a height of zero. ListItem now retains its intrinsic height; Scroller still excludes hidden rows. Stock and SunnyPilot row variants have lifecycle regressions, and native component snapshots cover opening and reopening nested lateral sections. These checks reproduce the overlap; they do not establish that every possible rendering issue is resolved.

## Historical validation before removal

Experimental branch on Dustin's fork; checked 2026-09-06. This is not a driving-safety certification.

## Attribution and integration

- Starts from `bp-dev-models` (`6367931ece`): staging model catalog, tinygrad alignment and resilient downloads.
- Merges `ford-angle-autocal` (`8907ca96a7`), preserving ghbarker's original autocal/smoothing commits and Dustin's enlarged gauges.
- The latest combined [PR #172](https://github.com/BluePilotDev/bluepilot/pull/172) tree (`1cdbd4b299`) equals `6baea2adab`; the split #161/#171/#172 series does not add a newer implementation to import.
- Merges John Christman's [PR #175](https://github.com/BluePilotDev/bluepilot/pull/175), including the live-delay indicator and branding.
- Merges [PR #194](https://github.com/BluePilotDev/bluepilot/pull/194), preserving its John Christman/Claude attribution: ALP deviation budget, takeover debounce and stall guards.

Original commits are retained through merges. Integration fixes are separate commits. Optional angle smoothing remains layered on the new baseline; the inherited separate prediction/entry horizons and reset paths are retained.

## Autocal adaptation

The production command carries its actual total gain, fixed low-curve contribution, adjustable blend and issue-time speed through delay and apex matching. The estimator fits only the adjustable branch:

`ideal_adjustable_gain = (total_gain / measured_response_ratio - fixed_gain) / blend`

Normal equations retain `blend²` noise weighting. Admission-duration thresholds use seconds, and covariance uses admitted duration rather than treating inverse-noise weights as observation counts. Verification and outlier checks operate in the adjustable domain. Manual stepper and gauge labels share the new speed anchors.

Saved v1 evidence and locks are rejected. Existing factor settings are **not automatically converted**: the historical retune changes both the bases and the speed/curvature schedules, so no two-factor conversion preserves the old response everywhere. Previously calibrated settings must not be assumed calibrated under this retune. The deployment device had no saved autocal state and autocal was not enabled; its manual factors (1.0/1.1) were left untouched. A policy for migrating other installations' v1 factors remains a user decision.

## Checks

- Ruff: all Python files changed relative to `bp-dev` pass.
- Local CPU suite: 197 tests and seven subtests pass, with an external params/messaging shim.
- Real-device suite: 202 tests and seven subtests pass, without that shim. Includes autocal, smoothing, lateral strategy, lane trim, retune and gauge/layout tests.
- Independent gain formula and weighted least-squares covariance references; noisy partial-gain fits; delayed issue-time metadata; amplified outlier rejection; blend-independent verification; decay/serialization; mixed-gain simulated convergence and lock; armed F-150/Mach-E feed with neutral/max smoothing.
- Real-device SCons full build succeeds. Native C++ replay is excluded by this tree's TICI build configuration; no build-system workaround was added.
- Read-only route smoke replay: `0000001b--ca636b7372` segments 0–2 (3,419 frames, 1,695 laterally active); `0000001e--8c1cee9fce` segments 5–7 (2,508 active frames, smoothing strength 2.5). No autocal runtime errors. These are September 4 UTC routes, including the prior local evening. Both runs admitted zero calibration samples; the first route reports stock/openpilot control mode, not the new angle controller. This verifies runtime/rejection paths, **not real-route factor recovery**.
- Isolated Python UI-only replay of recorded onroad messages ran on the device. No manager/card/pandad or CAN publishers ran during replay. Camera video was not replayed. Low-FPS warnings occurred, so this is **not a performance pass**.
- Additional `0000001b--ca636b7372` segments 10–12: 2,518 active/armed frames, no runtime errors and no admitted samples; all recorded in stock/openpilot mode. Total distinct route smoke coverage: 8,445 frames.
- Actual gauge and live-delay widgets rendered into a device GPU texture and were visually inspected. Developer-panel clearance is regression-tested with sidebar open/closed. Full camera/theme/MICI visual coverage is not claimed.

Run the route smoke harness with local files only:

```sh
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python \
  opendbc_repo/opendbc/sunnypilot/car/ford/tests/replay_angle_autocal.py \
  --strength 2.5 /data/media/0/realdata/ROUTE--SEGMENT/rlog.zst
```

The harness reuses the test controller and in-memory Params. Recorded vehicle response belongs to the old controller, so counterfactual admissions are **not calibration estimates**. Synthetic closed-loop tests are simplified plants, not proof of stability on the real vehicle. Historical route replay cannot establish the retuned controller's physical closed-loop behavior.

## Selecting local routes and inspecting admission

`tools/route_inventory.py` reads local qlogs (rlog fallback) and emits JSONL. It uses
recorded wall clocks and an explicit timezone, not filenames or modification times:

```sh
python tools/route_inventory.py /data/media/0/realdata \
  --date 2026-09-03 --timezone America/Los_Angeles --ford-autocal
```

Use repeatable `--route ROUTE` arguments to restrict a scan. Each row includes the
segment/log path, local start, observed duration, valid speed-sample counts in m/s
bands, recorded lateral mode and optional `ford_candidate_low/high` scores. Missing
clocks are reported as null and excluded by a date filter. Repeated route-start
metadata does not inflate segment duration. Read failures go to stderr and produce
a nonzero exit status; the tool does not download, preserve, delete or alter routes.

Ford scores reuse the production speed/curvature/acceleration and driver-grip gates,
but are only a **shortlisting heuristic**: qlogs can miss brief inputs, and these
counts do not include full delay matching, limiters or estimator quality checks.
They are sample counts, not calibration seconds. Recheck shortlisted contiguous
segments in chronological order with the full-rlog harness above.

Thursday September 3 local-time recordings retained on the device:

| Route | Available segments | Approximate retained minutes |
| --- | --- | ---: |
| `0000001a--d8a627a07c` | 34–91 | 58 |
| `0000001b--ca636b7372` | 0–48 | 49 |
| `0000001c--953295a9e3` | 0–87 | 88 |

That is about 195 retained minutes, with low-speed and highway coverage. Route 1a's
first 34 segments are absent locally; including them would give roughly 229 minutes,
consistent with the expected four hours. The evening logs cross into September 4
**UTC**, but belong to Thursday in Los Angeles.

Follow-up full-rlog replay (neutral manual factors in isolated in-memory Params):

| Route/segments | Smoothing strength | Frames | Accepted low-side / high-side | Negative / positive |
| --- | ---: | ---: | ---: | ---: |
| 1a / 48–51 | 1.0 | 4,324 | 40 / 46 | 81 / 5 |
| 1b / 21–22 | 1.0 | 1,574 | 0 / 60 | 0 / 60 |
| 1c / 39–42 | 1.0 | 4,504 | 7 / 167 | 0 / 174 |
| 1a / 48–51 | 2.5 | 4,324 | 47 / 46 | 84 / 9 |

Low/high-side labels split at the midpoint of the production speed anchors; an
individual sample contributes interpolated evidence to both anchors. The first run
accumulated 2.252/2.036 seconds of low/high evidence; the second 0.395/2.586 seconds.
The third accumulated 3.098/5.512 seconds; maximum smoothing on the first section
gave 2.528/2.104 seconds. None nudged factors. Sparse, one-sided evidence remains insufficient; admission
gates were not relaxed. Straight-road and driver-grip-heavy sections instead exercise
rejection paths. All runs above reported zero autocal errors and stock/openpilot
recorded mode, so accepted samples **do not establish valid factor recovery**.

The harness now reports overlapping rejection diagnostics and accepted speed/sign
counts, refreshes simulated parameters on the controller cadence, and resets strategy
state after discontinuities. An observer regression verifies that diagnostics leave
admission and persistence unchanged. It does not reconstruct the raw PSCM limit bit
(`lat_ctl_lim_stat` is zero); this is another reason not to treat it as exact historical
controller reproduction. No device calibration parameters were changed.

Follow-up checks: all changed Python files pass Ruff; the local Ford suite plus route
inventory tests pass (200 tests and seven subtests, external params/messaging shim).
The three-route inventory completed on-device without read errors. The four full-log
runs above cover 10,402 distinct frames, plus 4,324 repeated at maximum smoothing.

## Adversarial review

Claude Fable 5.1 reviewed the integration and specifically the autocal adaptation, followed by a second mathematical review. It read the working tree; its Bash permission was denied, so it did not independently inspect Git diffs or run tests.

Addressed findings: diagnostic-panel overlap; nonexistent Small Signal Factor documentation; diluted verification; partial-authority outliers; duration/weight semantics; covariance normalization; stale speed anchors; missing production feed and mixed-lock tests. Additional covariance regression was added after the follow-up finding.

Remaining limitations: fixed-branch modeling errors can bias fitted anchors; low-authority observations remain noisier; limiter exclusion censors evidence; existing telemetry/menu parity gaps were not expanded into a schema redesign. Do not interpret passing replay/tests as approval for unattended calibration or vehicle operation.

## Four staging fixes — September 6, 2026

Imported individually with original author attribution from the source history of
sunnypilot staging `40d6afd30042e9a1bb452f42d3b6e67ecd00c98f`
(release snapshot identifies master `6135084c941d4d947dd90c78326a557c3c857f89`):

- Jason Wen: [cache-size race fix, sunnypilot #1958](https://github.com/sunnypilot/sunnypilot/pull/1958), source `760c19d3f91f79e404f36b020c5df027a5291e48`.
- Trey Moen: [BSD cleanup option order, openpilot #38728](https://github.com/commaai/openpilot/pull/38728), source `7cf55c3b7a2d9bcee87821e413fa322866f64c5b`.
- Jason Wen: [FPS-independent scrolling, sunnypilot #1967](https://github.com/sunnypilot/sunnypilot/pull/1967), source `78a766eb6145a416d8d95e323a13b6a914b6f9ff`. Adapted in the existing shared label, preserving 48 px/s rather than importing the newer wrapper/button hierarchy.
- Trey Moen: [replay stderr progress, openpilot #38734](https://github.com/commaai/openpilot/pull/38734), source `0e320594844b1c1d80aada3f8c51b6e0a1c14115`.

Claude Fable 5.1 reviewed the supplied production diff (no tools); an independent
Codex reviewer inspected the files and callers. Removed scanf pointer casts, added
the explicit standard header, and rejected progress beyond the total. No remaining
blocking introduced findings. Cross-fork descriptor inheritance/cancellation waits,
unquoted cleanup paths, and removal of the cache directory itself remain pre-existing
limitations; they were not expanded into separate lifecycle/cleanup changes.

Validation of code tip `52dd687b14`:

- Ruff on both changed Python modules and both new test files: passed locally and on device.
- `pytest -q -o addopts="" selfdrive/ui/bp/tests/test_staging_ui_fixes.py tools/replay/tests/test_py_downloader.py`: **7 passed** locally with the external Params/messaging shim, and **7 passed on device without the shim**. The C++ test compiles the actual bridge with `-std=c++17 -pthread -Wall -Wextra -Werror`, stubbing only logging, and exercises progress, malformed/out-of-range values, pipe saturation, diagnostics, success, failure, cancellation, and no handler.
- Device `PYTHONPATH=/data/openpilot PATH=/usr/local/venv/bin:$PATH scons -j4`: **passed**. Initial invocation omitted PYTHONPATH and failed its tinygrad subprocess import; rerunning with the launcher environment succeeded. This is the normal device build, not a clean rebuild; desktop replay is excluded by SConstruct on TICI. The bridge was compiled and executed separately by the test above.
- Device `pytest -q -o addopts="" selfdrive/ui/bp/tests`: **44 passed, 1 failed**. The unchanged `test_soundd_bp_falls_back_to_stock_on_asset_error` hardcodes generic engage/disengage sounds, whereas production selects TIZI-specific defaults. Reproduced alone; test and relevant sound modules match pre-import device tip `4535c0034`. No sound behavior or test was changed to hide this failure.
- Broader Mac checks were limited by missing native VisionIPC in the shim and Git LFS sound pointers (36 passed, 5 asset failures when excluding the uncollectable onroad module). Host SCons configuration was blocked by the uninitialized rednose tool module. These are not reported as passes.
- `git diff --check`: passed. Device remained offroad; spinner image modification preserved. No reboot, UI restart, live calibration change, CAN publishing, or on-road validation was performed. Running UI processes need their normal restart to load the new Python code.

## Unified parser and source-layout verification — September 6, 2026

Parser-only port of James Vecellio-Grant's [sunnypilot #1993](https://github.com/sunnypilot/sunnypilot/pull/1993),
source `6135084c941d4d947dd90c78326a557c3c857f89`. GPU/compiler changes are excluded.
The old split-module import remains an alias for legacy modular runners. Added the
required `ACTION_WIDTH = 2` constant, rejected uninferable widths, and rejected
inferred plan/lead shapes that downstream consumers cannot handle.

**Important upstream deviation:** unmodified #1993 interleaves means and log-stds
for 144-value leads. The catalog's original source parsers for
[RDF `a95e2c25`](https://github.com/commaai/openpilot/blob/a95e2c25cae5fbf1afba7628bfb7acc4af59e0cc/openpilot/selfdrive/modeld/parse_model_outputs.py)
and [GWM v8 `96e7b310`](https://github.com/commaai/openpilot/blob/96e7b310b164b55fb73abdd505b66fb36630718e/selfdrive/modeld/parse_model_outputs.py)
instead split all 72 means from all 72 log-stds. Actual artifact outputs confirmed
that layout. The port preserves it explicitly. The emergency RDF fix had the right
lead ordering; its successful message publication alone did not establish that.

Validation of code tip `b22e24118c` (equivalent model code on `bp-dev-models`):

- Both branches: **105 modeld tests passed**, including the live-manifest test, on
  CPU with the external Params/IPC shim. Ruff and whitespace checks passed.
- Device: **22 parser tests passed without stubs**, including distinct means/stds,
  batch sizes one/two, legacy weighted hypotheses, split/combined callers and
  consumer-shape rejection. Full normal `scons -j4` build passed while offroad.
- Isolated QCOM inference on the cached RDF artifact: three constant-image inputs,
  **21 fields exactly matching its original source parser** per input. The source
  leaves `action` raw; the new parser's action decoding is tested separately.
- Cached GWM v8 split artifact: three constant-image inputs, **22 fields exactly
  matching #1993 except leads, which match the source-model means-first layout**.
  Initial combined-only test assumptions and unmodified #1993 lead comparisons
  failed; those failures led to the split-path test and source-layout correction,
  not to relaxed numerical tolerances.
- Claude Fable reviewed the production diff and the subsequent lead-layout
  correction without tools. Addressed consumer-shape validation; checked callers
  and the 13 shared parser dimensions. Source inspection and actual inference,
  not reviewer approval or synthetic expected values alone, resolved lead ordering.

All isolated GPU checks ran offroad with no IPC/CAN publishers and no selected-model
parameter changes. Constant images validate execution and parsing, **not real-scene
perception or closed-loop driving**. Chestnut remains disabled. Autocal was enabled
at the user's request while stationary/disengaged; live controller telemetry showed
`collect`, zero samples, no error, and unchanged manual factors 1.0/1.1. Camera
calibration at 0% is a separate issue and is not fixed by enabling Ford autocal.

### Removal validation

The restored AutoCal/lateral/replay CPU suite passes: 105 tests and seven subtests (2026-09-07). The local run uses the existing external messaging shim because this checkout lacks native msgq; device validation is recorded separately. The angle strategy, gain constants, and estimator match the pre-PR integration exactly; the lifecycle additionally rejects retune-version locks.

### Learned Stops: Observe rollout (2026-09-08)

Observe records human stopping approaches and displays matched locations without applying a stopping constraint. Control is hard-disabled. Sign confirmation, three independent eligible drives, positioning quality and explicit approach approval are separate gates; imports retain exported verification and approval decisions while recalculating readiness from the evidence.

Validation: 32 observation/storage/API/native-MPC checks passed; Off and Observe produce identical native MPC outputs with successful solves in a steady-cruise scenario. A closer radar lead retains its stronger constraint. Native TICI/MICI visual checks cover 24 component screenshots, and Portal desktop/mobile checks cover evidence review, disabled Control and overflow. TypeScript checking and the production web build pass. Cursor Fable 5.1 adversarial review identified the shared Portal driving-key typo; the corrected guard uses `IsOnroad` and fails closed.

Retrospective replay covered 300 full rlog segments across 10 routes (1,739,125 samples): 36 recorded stops, 31 locations, zero qualified approaches. Exclusions overlap: 26 queue stops, 12 nonmanual stops and three maneuvers. Three full-resolution evidence frames were recovered with less than five milliseconds of timestamp alignment error.

Diagnostic 10–70 mph simulations stop and hold without overshooting, but the 0.3-second plant stops approximately 2.6–32.1 metres early. This is not acceptable reference accuracy. An initial accelerating-cruise fixture also produced identical solver resets in both baseline and Observe; it was replaced by a coherent steady-cruise equivalence case, not treated as a successful solve. The diagnostic plant is not an identified Ford brake-response model. No physical stopping accuracy, 70 mph automatic stopping, or public-road Control validation is claimed.

The conservative localization-age displacement bound can exceed the two-metre Control uncertainty budget at highway speed. High-rate/time-aligned localization and measured uncertainty must be validated before Control can be enabled. Follow-up review also corrected transient SQLite capture recovery and released evidence locks before HTTP backup streaming.

The full 15-case delay sweep (0.1/0.3/0.6 seconds) records no solver failures but reaches 59.1 metres early at 70 mph / 0.6 seconds.

Device validation: the same 32 tests pass on the native comma runtime. The Portal guard additionally treats a missing or empty driving-state parameter as unavailable (blocked), with regression coverage for native typed and fallback values.

Review-queue follow-up: 33 checks pass. Radar lead-distance queue flags now skip manual review, existing manual exclusions remain dismissed after reload, and fresh observations reopen an approach for review. Browser checks cover one-click exclusion reasons, advancement within an approach and to the next approach, failed saves, an empty queue and retained excluded history on mobile. The queue heuristic uses a lead within 25 metres during the final three seconds; it is conservative evidence filtering, not proof of a stationary lead or absence of a stop sign.

Import round-trip follow-up: 35 checks pass, covering retained confirmations/approvals, reference-ID remapping, exclusions/disabled state, repeat imports without duplicate visits, recomputed positioning readiness and unchanged mode. Imports roll back if driving begins or an exported reference conflicts with saved evidence. JSON file references are not followed; the ZIP backup remains the way to retain image files.

On-device engagement regression: the planner adapter used a removed Params method and the recorder passed structured fields to plain logger methods. Use asynchronous `Params.put(..., block=False)` and supported structured logging. All 37 Learned Stops checks pass on the host and native device, including Off/Observe planner updates with real isolated Params; the structured logging call sites were also exercised. Physical engagement still requires a subsequent drive.
