# bp-exp

Thank you to the **BluePilot team** for building and maintaining the Ford-focused foundation this branch depends on—especially [Alan Polk](https://github.com/alan-polk), [tonesto7](https://github.com/tonesto7), [John Christman](https://github.com/jchristman75), Nathan Ingraham, [ghbarker](https://github.com/ghbarker), and [Praeuner](https://github.com/Praeuner), alongside everyone contributing code, testing, and feedback. Credit also belongs to the [sunnypilot](https://github.com/sunnypilot/sunnypilot) and [comma/openpilot](https://github.com/commaai/openpilot) communities whose work makes all of this possible.

`bp-exp` is an experimental branch on [my fork](https://github.com/dustin-decker/bluepilot), combining upstream [BluePilot `bp-dev`](https://github.com/BluePilotDev/bluepilot/tree/bp-dev) with newer models from sunnypilot staging, more resilient model downloads, and selected Ford steering and display improvements. It brings together angle-mode auto-calibration, optional anti-weave smoothing, larger onroad calibration gauges, a steering-delay indicator, and newer steering tuning, with integration fixes and replay tests. Maintenance is **best-effort**, not a promise of stability or support. I'm happy to upstream a PR for changes the BluePilot team wants.

<details>
<summary>Installation: device URL and SSH</summary>

Install only while parked, with reliable power and internet. This is experimental driving software; keep a known-good recovery option available.

### Device installation URL

At the comma device's **Custom Software** installation screen, enter:

```text
installer.comma.ai/dustin-decker/bp-exp
```

### Existing BluePilot installation over SSH

Connect with `ssh comma@DEVICE_IP`, replacing `DEVICE_IP` with your device's address. These commands change the update remote to this fork, switch branches, update submodules, and build **before** rebooting. They are for an existing, unmodified BluePilot checkout—not a fresh OS installation.

First inspect local changes:

```sh
cd /data/openpilot && git status --short
```

If there are changes you want to keep, back them up before continuing. Do not force-reset through a conflict or diverged branch.

```sh
cd /data/openpilot &&
git remote set-url origin https://github.com/dustin-decker/bluepilot.git &&
git fetch origin &&
git switch bp-exp &&
git merge --ff-only origin/bp-exp &&
git submodule update --init --recursive &&
scons -j4 &&
sudo reboot
```

On a checkout without a local `bp-exp`, Git normally creates it from `origin/bp-exp`. If any step fails, stop and resolve it; the command chain will not reboot after a failed build.

</details>

## What's added in bp-exp

These are additions to this branch's `bp-dev` base, not claims that the underlying features were invented here. Upstream may merge or revise them independently; original commit attribution is preserved.

| Addition | Authors and origin |
| --- | --- |
| **Newer driving models and selector:** sunnypilot staging's model catalog/selector support, chunked artifacts, and matching tinygrad/model loading. USB-GPU/Chestnut support remains gated off pending hardware validation. | [sunnypilot contributors](https://github.com/sunnypilot/sunnypilot/tree/master/openpilot/sunnypilot/models); ported by [Dustin Decker](https://github.com/dustin-decker), with Claude Fable 5.1 co-authorship: [selector port](https://github.com/dustin-decker/bluepilot/commit/514dcc4c0e), [runtime alignment](https://github.com/dustin-decker/bluepilot/commit/4e91c194cc). |
| **More resilient model downloads:** retry interrupted transfers, retain verified chunks for resuming, and preserve existing cached manifests. | Dustin Decker: [download reliability](https://github.com/dustin-decker/bluepilot/commit/ea983de785), [cache preservation](https://github.com/dustin-decker/bluepilot/commit/6367931ece), building on sunnypilot's downloader. |
| **Unified model-output parsing:** shared split/combined parser, preserving RDF's source-model lead layout and rejecting layouts unsupported by this branch's consumers. This supersedes the temporary RDF crash workaround; it does not import the PR's GPU/compiler changes. | James Vecellio-Grant (Discountchubbs): [sunnypilot #1993](https://github.com/sunnypilot/sunnypilot/pull/1993); parser-only integration and regression checks by Dustin Decker, retaining the [RDF source parser's lead layout](https://github.com/commaai/openpilot/blob/a95e2c25cae5fbf1afba7628bfb7acc4af59e0cc/openpilot/selfdrive/modeld/parse_model_outputs.py). |
| **Ford angle-mode auto-calibration and optional anti-weave smoothing**, with calibration evidence/lock gauges. | ghbarker: [auto-calibration #161](https://github.com/BluePilotDev/bluepilot/pull/161), [smoothing #171](https://github.com/BluePilotDev/bluepilot/pull/171), [gauges #172](https://github.com/BluePilotDev/bluepilot/pull/172). |
| **Larger, visible onroad autocal gauges**, including comma 3X placement and layout fixes alongside the delay indicator. | Dustin Decker, extending ghbarker's gauges: [onroad placement](https://github.com/dustin-decker/bluepilot/commit/70d8f1f22c), [larger gauges](https://github.com/dustin-decker/bluepilot/commit/8907ca96a7), [integration/layout fixes](https://github.com/dustin-decker/bluepilot/commit/5b082a3d52). |
| **Steering-delay calibration indicator** and the associated branding updates. | John Christman: [#175](https://github.com/BluePilotDev/bluepilot/pull/175). |
| **Full angle-control gain retune and predicted-curvature blending**, including speed-dependent prediction weighting. | Praeuner: [#191](https://github.com/BluePilotDev/bluepilot/pull/191). |
| **Lane-positioning limits and takeover guards:** symmetric lane-positioning budget, stall handling, and protection against takeover oscillation. | John Christman, with credited Claude co-authorship: [#194](https://github.com/BluePilotDev/bluepilot/pull/194). |
| **Autocal adapted to the retuned gains**, with partial-blend fitting, uncertainty fixes, updated speed-anchor labels, regression tests, and read-only route-selection/replay diagnostics. | Dustin Decker, integrating ghbarker's autocal with Praeuner's retune: [adaptation](https://github.com/dustin-decker/bluepilot/commit/028663239b), [uncertainty/anchor fixes](https://github.com/dustin-decker/bluepilot/commit/21b2bdeff2), [route tools](https://github.com/dustin-decker/bluepilot/commit/4535c0034a). |

**Validation limits:** builds, tests, and historical replay checks are documented in [bp-exp validation](docs/bp-exp-validation.md); they are not proof of real-world steering stability or calibration accuracy. Old calibration evidence is invalidated, and previously learned manual factors are not automatically converted for the retune. Do not assume old calibration remains valid.

For the underlying project, see [BluePilot](https://github.com/BluePilotDev/bluepilot) and the [sunnypilot README](README_SP.md). Existing [license terms](LICENSE) and component-specific licenses continue to apply.
