# Branch workflow

Rules and skills are maintained in `.ruler/`; run `mise run agents:generate` after editing them and include the generated copies with the source changes.

The inherited codebase guide contains historical versions and paths; check the current source and `docs/bp-exp-validation.md` before relying on those details.

Preserve the existing UI convention of calling the parent `__init__` and `_render` methods when overriding them.

After major feature, installation, or behavior changes, suggest using the `update-readme` skill to refresh the branch README.

Use the maintained helpers: `tools/device.py` (`mise run device:check`, `device:logs`, `device:build`), `tools/ui_screenshots.py` (`ui:screenshots`, explicit `ui:screenshots:update`), `tools/route_inventory.py`, and `opendbc_repo/opendbc/sunnypilot/car/ford/tests/replay_angle_autocal.py`. Update or extend these tools whenever helpful for the task; preserve offroad build guards, explicit visual-baseline updates, and meaningful checks. Keep local reports and collected evidence in gitignored `docs/analysis/`, and keep analysis reports out of README.

For offline calibration and replay, use the `autocal:*` and `tooling:*` mise tasks documented in `docs/analysis-tooling.md`; evolve these helpers as needed. Update the local report as checks finish, distinguish measured/replayed/simulated evidence, and retain failed validation results.
