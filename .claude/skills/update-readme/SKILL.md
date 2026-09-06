---
name: update-readme
description: Update this branch's root README with its user-facing differences from BluePilot bp-dev, preserving contributor attribution and the agreed installation layout.
---

# Update the branch README

Read `README.md`, `docs/bp-exp-validation.md`, and the relevant branch changes before editing; verify the current branch and upstream base rather than assuming their names or contents.

Preserve this layout:

- Begin with appreciation for the BluePilot team and significant contributors, plus sunnypilot and comma/openpilot.
- Follow with one simple paragraph explaining what this experimental branch on Dustin's fork adds over upstream `bp-dev`; retain best-effort maintenance and the distinction from an official BluePilot release.
- Keep the installation URL and existing-installation SSH commands inside a collapsed `<details>` section, with build success required before reboot.
- Keep the additions section visible, credit original authors and integration authors separately, and link to originating PRs or commits rather than claiming inherited work as new.

Check attribution against Git history and the original PRs. Verify installer repository aliases and branch targets before changing URLs; do not invent a shortcut. Keep SSH instructions non-destructive and scoped to an existing BluePilot checkout. Updating instructions does not authorize running them on a device.

Describe only implemented changes. Keep disabled hardware support and experimental calibration limitations explicit; passing unit tests or counterfactual route replays do not establish real-world steering safety or calibration accuracy. Link to the validation document for detailed evidence instead of expanding the README into a test report.

Check Markdown structure, local links, and shell-block syntax after editing. Do not commit, push, or deploy solely because this skill was invoked.
