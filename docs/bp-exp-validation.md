# bp-exp integration and validation

Experimental branch on Dustin's fork; checked 2026-09-06. This is not a driving-safety certification.

## Attribution and integration

- Starts from `bp-dev-models` (`6367931ece`): staging model catalog, tinygrad alignment and resilient downloads.
- Merges `ford-angle-autocal` (`8907ca96a7`), preserving ghbarker's original autocal/smoothing commits and Dustin's enlarged gauges.
- The latest combined [PR #172](https://github.com/BluePilotDev/bluepilot/pull/172) tree (`1cdbd4b299`) equals `6baea2adab`; the split #161/#171/#172 series does not add a newer implementation to import.
- Merges John Christman's [PR #175](https://github.com/BluePilotDev/bluepilot/pull/175), including the live-delay indicator and branding.
- Merges Praeuner's full [PR #191](https://github.com/BluePilotDev/bluepilot/pull/191) gain retune and prediction blend, not just its smoothing changes.
- Merges [PR #194](https://github.com/BluePilotDev/bluepilot/pull/194), preserving its John Christman/Claude attribution: ALP deviation budget, takeover debounce and stall guards.

Original commits are retained through merges. Integration fixes are separate commits. Optional angle smoothing remains layered on the new baseline; the inherited separate prediction/entry horizons and reset paths are retained.

## Autocal adaptation

The production command carries its actual total gain, fixed low-curve contribution, adjustable blend and issue-time speed through delay and apex matching. The estimator fits only the adjustable branch:

`ideal_adjustable_gain = (total_gain / measured_response_ratio - fixed_gain) / blend`

Normal equations retain `blend²` noise weighting. Admission-duration thresholds use seconds, and covariance uses admitted duration rather than treating inverse-noise weights as observation counts. Verification and outlier checks operate in the adjustable domain. Manual stepper and gauge labels share the new speed anchors.

Saved v1 evidence and locks are rejected. Existing factor settings are **not automatically converted**: PR #191 changes both the bases and the speed/curvature schedules, so no two-factor conversion preserves the old response everywhere. Previously calibrated settings must not be assumed calibrated under this retune. The deployment device had no saved autocal state and autocal was not enabled; its manual factors (1.0/1.1) were left untouched. A policy for migrating other installations' v1 factors remains a user decision.

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
