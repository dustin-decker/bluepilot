# Offline analysis tooling

Run `mise run tooling:setup` once, then `mise run tooling:check`. Setup initializes the pinned submodules, installs locked Python dependencies with uv, and builds native parameter and messaging bindings. Python is selected from the repository's supported version range. Set `BP_BUILD_JOBS` to control build parallelism (default 4).

For Linux process replay, open this repository in the supplied devcontainer. Its build context contains only `.devcontainer/`; private logs and reports are not baked into the image. The container keeps its Python environment in `/opt/venv`, separate from the host `.venv`. Native `.so` files are built in the workspace: rebuild when switching between Linux and macOS. Prefer a separate checkout for the container if you use both concurrently. No device access or privileged container is required.

All commands below accept `--help` and require explicit input/output paths. Keep evidence and reports under gitignored `docs/analysis/`. The inventory must already contain only the requested date/timezone window; extraction does not infer “today.”

| mise task | Purpose |
|---|---|
| `autocal:extract` | Read chronological rlogs from an inventory into per-route NumPy caches and metadata. Ford CAN-FD message 982, bus 0 only. |
| `autocal:diagnose` | Plot straight-road oscillation candidates and extract clean five-second response windows. Diagnostic plots retain driver-torque flags; calibration windows exclude driver input. |
| `autocal:confidence` | Bootstrap whole evidence runs, compare directions/speeds, and leave each route out. Requires the vehicle's platform gain. |
| `autocal:replay` | Run the real `card` process using recorded Params and Ford's extra input services. Run from the exact desired source revision. |
| `autocal:identify` | Fit a stable second-order command-to-wheel response, validate on held-out routes, and test its cascade with openpilot's VehicleModel. |
| `autocal:simulate` | Feed the actual angle strategy through the fitted actuator and VehicleModel, returning simulated yaw to the strategy. Prescribed reference, not camera/planner simulation. |

Example:

```sh
mise run autocal:extract -- --inventory docs/analysis/run/inventory.jsonl --logs /path/to/rlogs --output docs/analysis/run/cache
mise run autocal:diagnose -- --cache docs/analysis/run/cache --output docs/analysis/run
mise run autocal:identify -- docs/analysis/run/response_windows.npz --metadata docs/analysis/run/cache/metadata.json --output docs/analysis/run/response-model.json
mise run autocal:confidence -- docs/analysis/run/accepted-samples.json --platform-gain 0.95 --output docs/analysis/run/confidence.json
mise run autocal:replay -- /path/to/segment/rlog.zst --output docs/analysis/run/native-replay
mise run autocal:simulate -- --log /path/to/segment/rlog.zst --model docs/analysis/run/response-model.json --revision "$(git rev-parse HEAD)" --output docs/analysis/run/simulation
```

Confidence input rows are `[route, monotonic_seconds, speed_mps, command_curvature, measured_curvature, actual_applied_gain, positive_weight]`, exported from accepted AutoCal samples. Preserve chronology and the actual historical gain. Groups separated by more than two seconds are evidence runs, not necessarily distinct turns. The reported factors are unclamped so unstable fits remain visible.

Replay restores the route-start parameter snapshot; manual mid-route setting changes and filter history can prevent exact wire equivalence. Compare a warmed-up historical replay with the original before interpreting a changed controller. Native replay uses isolated local Params through openpilot's process-replay framework and sends no commands to the vehicle.

Identification removes window means for transient validation and is observational, not a causal actuator measurement. Its discrete input delay is not the total steering delay. The simulated plant must beat held-out baselines before its results can inform settings. Scripts never apply calibration, restore confidence, enable on-vehicle maneuver testing, or deploy code.

## Route telemetry

New routes include the controller's runtime angle factors, dampening, anti-weave toggle/strength/effective state, and requested AutoCal enable/lock settings in `controllerStateBP`. Require `angleTuningValid` before interpreting these additions: older logs default to false. Anti-weave strength 1.0 is passthrough even with its toggle on.

`bmsAngleAutoCalState` contains live confidence, apex counts, rejection counters, and verification status. `angleAutoCalEvents` is versioned JSON with the latest 16 settings/lifecycle events (arm, phase, reset, unlock, manual factor edit, nudge, lock, error). Each has a sequence number and monotonic timestamp in nanoseconds. History persists across AutoCal resets, repeats in ten-frame bursts once per second (and after changes) in the 100 Hz rlog stream, spanning the 10× qlog decimation, and restarts with the controller process. Empty event fields mean no update; forward-fill within a controller session. Deduplicate by timestamp/sequence, retaining the latest copy of an aggregate; identical recurring errors retain their first timestamp plus a count/last timestamp, and sequence gaps indicate overwritten events. This bounded history improves short-event capture but is not a durable event journal. Settings snapshots include lane positioning and centering controls; factor fields use the controller's current values rather than the five-second Params cache.

Replay and simulation outputs record actual checkout HEAD, dirty/untracked status, and tracked diff statistics separately from a supplied revision label. Archives without their own Git metadata are explicitly unverified; keep their source archive and checksum with the evidence. A dirty tree is not an exact committed-revision run.
## Learned-stop evidence and simulation

`mise run stops:build` builds the native solver; `mise run stops:check` checks observation exclusions, store/API guards, and diagnostic MPC behavior. Use `mise run stops:replay -- --logs LOG_DIRECTORY --output docs/analysis/STUDY/replay` for chronological local segment logs and `mise run stops:simulate -- --output docs/analysis/STUDY/simulations.json` for 10–70 mph diagnostics with 0.1/0.3/0.6-second simulated response delay. These outputs never enable Control. Inspect stopping error and solver failures, not just whether the vehicle stopped.

`uv run python tools/learned_stops.py capture --store STORE --logs LOG_DIRECTORY` extracts timestamp-indexed front/wide evidence using existing HEVC indexing and PyAV. It stops on ignition-on; the device worker runs only offroad. Unavailable video is reported rather than substituted. Saved frames survive ordinary route deletion, with a 512 MiB capture budget. The Portal can delete an observation’s images after confirmation without deleting its history. `osm --bounds SOUTH WEST NORTH EAST --output STORE/osm/AREA.json` fetches a bounded map cache explicitly; directional corroboration is presented for review and never substitutes a map-node coordinate for a stopping reference.

The live database and frames live in `/data/bluepilot/learned_stops`. Portal JSON imports are limited to 1,000 observations per request and retain exported sign confirmations, approvals, stopping references, exclusions and disabled states. Imports are atomic, match exact observation identities, and recalculate readiness without changing the current mode; the separate ZIP backup contains a consistent SQLite snapshot and saved JPEGs. Do not restore a database while the device is driving or the observer is writing. Reports and collected route data belong in gitignored `docs/analysis/`.

## Python API and type checks

`mise run types:check` first runs pinned mypy on the strict runtime/tool/Ford modules listed in `mypy.ini`, then checks every Python addition or modified function body against `bp-dev-models`. The branch gate follows imports silently, accepts only validated dynamic Cereal/JSON/native boundaries, and retains a pre-existing diagnostic only when a merge-base worktree proves the same path, qualified function, source line, and message. It includes untracked additions and fails on malformed paths, syntax, or configuration errors.

`mise run types:test` verifies that the checker rejects removed Params methods, unsupported logger keyword arguments, and treating typed `IsOnroad` as bytes. `common/params_pyx.pyi` supplies the real Cython API; `common/tests/test_params_stub.py` is in the strict scope and checks its signature drift on a built host/device. The strict and branch gates run in the BluePilot types CI workflow. Missing external/generated bindings and validated heterogeneous JSON still use dynamic types; runtime validation and native integration tests remain required. Mypy is a development/CI tool and is not run by the device manager.

## Learned Stops CPU comparison

`tools/learned_stops_benchmark.py` compares an archived package with the current recorder using identical rlogs and isolated SQLite/Params copies. Supply `--baseline-package`, `--database`, `--logs`, and `--output`; `--repeats 3` alternates runs. The default replay requires identical publications and observations. On a parked Linux device, `--native-transport` includes real messaging and measures only the recorder subprocess in a private IPC namespace. Never compare profiled candidate timings with an unprofiled baseline; `--profile-candidate` is diagnostic only. Results apply to the supplied route and evidence library, not total device CPU or physical driving validation.
