# Branch workflow

Rules and skills are maintained in `.ruler/`; run `mise run agents:generate` after editing them and include the generated copies with the source changes.

The inherited codebase guide contains historical versions and paths; check the current source and `docs/bp-exp-validation.md` before relying on those details.

Preserve the existing UI convention of calling the parent `__init__` and `_render` methods when overriding them.

After major feature, installation, or behavior changes, suggest using the `update-readme` skill to refresh the branch README.
