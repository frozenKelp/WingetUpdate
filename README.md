# WingetUpdate

Automation for publishing new [Moku](https://github.com/moku-project/Moku) releases to
[winget](https://github.com/microsoft/winget-pkgs) as `Moku.Moku`.

## Pipeline

| # | Workflow | Runs | What it does |
|---|----------|------|--------------|
| 1 | `UpdateMeta.yml` – *Update Moku Metadata* | daily, or manually | Reads the latest (or a given) GitHub release, writes `moku/<version>.json` and updates `moku/meta.json`. If anything changed, it starts workflow 2. |
| 2 | `GenManifest.yml` – *Generate & Validate Manifests* | from 1, or manually | Runs `wingetcreate update`, checks the installer SHA-256 against GitHub's asset digest, adds the release date and release notes, runs `winget validate`, and commits to `manifests/m/Moku/Moku/<version>/`. |
| 3 | `OpenPR.yml` – *Submit PR to winget-pkgs* | **manually only** | Syncs the `frozenKelp/winget-pkgs` fork, pushes `Update<version>` and opens a PR against `microsoft/winget-pkgs`. Stops if an open PR for that version already exists. |

Each workflow takes an optional `version` input (e.g. `0.14.1`). If you leave it empty, it uses the
latest version.

## Releasing a new version

1. Wait for the daily run, or run workflow 1 yourself. Workflow 2 starts on its own after it.
2. Check the committed manifests and install them locally:
   `winget install --manifest manifests/m/Moku/Moku/<version>`
3. Run workflow 3.

## Setup

- Secret `PAT_TOKEN`: a token for `frozenKelp` with the `public_repo` scope. Workflow 3 needs it.
- `wingetcreate` is pinned in `GenManifest.yml`. Update `$wcVersion` there when you want a newer version.
