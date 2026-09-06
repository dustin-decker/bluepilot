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
- Actual gauge and live-delay widgets rendered into a device GPU texture and were visually inspected. Developer-panel clearance is regression-tested with sidebar open/closed. Full camera/theme/MICI visual coverage is not claimed.

Run the route smoke harness with local files only:

```sh
PYTHONPATH=/data/openpilot /usr/local/venv/bin/python \
  opendbc_repo/opendbc/sunnypilot/car/ford/tests/replay_angle_autocal.py \
  --strength 2.5 /data/media/0/realdata/ROUTE--SEGMENT/rlog.zst
```

The harness reuses the test controller and in-memory Params. Recorded vehicle response belongs to the old controller, so counterfactual admissions are **not calibration estimates**. Synthetic closed-loop tests are simplified plants, not proof of stability on the real vehicle. Historical route replay cannot establish the retuned controller's physical closed-loop behavior.

## Adversarial review

Claude Fable 5.1 reviewed the integration and specifically the autocal adaptation, followed by a second mathematical review. It read the working tree; its Bash permission was denied, so it did not independently inspect Git diffs or run tests.

Addressed findings: diagnostic-panel overlap; nonexistent Small Signal Factor documentation; diluted verification; partial-authority outliers; duration/weight semantics; covariance normalization; stale speed anchors; missing production feed and mixed-lock tests. Additional covariance regression was added after the follow-up finding.

Remaining limitations: fixed-branch modeling errors can bias fitted anchors; low-authority observations remain noisier; limiter exclusion censors evidence; existing telemetry/menu parity gaps were not expanded into a schema redesign. Do not interpret passing replay/tests as approval for unattended calibration or vehicle operation.
