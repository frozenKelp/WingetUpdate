# WingetUpdate

Publishes new versions of GitHub-released apps to
[winget](https://github.com/microsoft/winget-pkgs). Each app has its own folder under `apps/`.
Currently configured: [Moku](https://github.com/moku-project/Moku) (`Moku.Moku`).

## Pipeline

Workflow 0 runs weekly by itself. Workflows 1–3 are started by hand from the **Actions** tab, or
by the workflow before them. Workflows 1–3 each ask for:

- `app`: the folder name under `apps/`, e.g. `moku`.
- `version` (optional), e.g. `0.14.1`. Leave it empty to use the latest version.

| # | Workflow | What it does |
|---|----------|--------------|
| 0 | **Check All Apps for Updates** (`CheckUpdates.yml`) | Runs every Monday at 05:23 UTC, or by hand. For each app, it compares `latestVersion` in `meta.json` with the latest GitHub release. For each app that differs, it starts workflow 1, then opens **one issue** assigned to you listing the updates. GitHub emails you about the issue. It never opens a winget-pkgs PR. |
| 1 | **Update App Metadata** (`UpdateMeta.yml`) | Reads the GitHub release, picks the installers using `assetPatterns`, and gets each SHA-256 from GitHub. It writes `apps/<app>/<version>.json` and updates `apps/<app>/meta.json`. If anything changed, it starts workflow 2 for that version. |
| 2 | **Generate & Validate Manifests** (`GenManifest.yml`) | Runs `wingetcreate update` and checks the installer hashes against GitHub's. It adds the release date and release notes, runs `winget validate`, and commits the result to `manifests/<x>/<Publisher>/<Package>/<version>/`. |
| 3 | **Submit PR to winget-pkgs** (`OpenPR.yml`) | Syncs the `frozenKelp/winget-pkgs` fork and pushes branch `<PackageIdentifier>-<version>`. Then it opens a PR against `microsoft/winget-pkgs`. It stops if an open PR for that branch already exists. |

## Safety checks

`scripts/preflight.py` runs in workflow 2 before the manifests are committed. It runs again in
workflow 3 after the fork is synced and before anything is pushed. It lists **every** problem it
finds. If there is any problem, the run fails without submitting anything. GitHub emails you about
the failed run, and the run's summary page lists each problem.

**Problems that always block** (no override):
- The ID, version or manifest type is wrong in any file, a field is empty, a file is unexpected, or
  a YAML file doesn't parse.
- An installer URL isn't under `<projectUrl>/releases/download/<tag>/`, or the installer count
  differs from the release.
- An installer SHA-256 differs from GitHub's digest. Workflow 3 also downloads each installer again
  and re-hashes it.
- The release was changed after the metadata was recorded: it's now a draft or pre-release, or an
  asset was removed or replaced.
- `ReleaseDate` differs from the release's publish date or is in the future, or the release notes
  are missing or too long.
- The version is already in winget-pkgs, or someone already has an open PR for it.
- The checker's own tests (`scripts/test_preflight.py`) fail.

**Changes from the previous version** (they block workflow 3 until you review them):
- Publisher, name, license, moniker, installer type, scope, upgrade behavior or architectures changed.
- A field was added or dropped.
- The installer has the same SHA-256 as the previous version.
- The version is older than one already published.

If you've checked the listed changes and they're intended, re-run workflow 3 with
**accept_changes** ticked. In workflow 2 these changes are only warnings, so the files are committed
for you to review.

Make sure GitHub notifications for failed Actions runs are on. They're on by default; check
GitHub → Settings → Notifications → Actions.

Run the checker's tests locally: `python3 -m unittest scripts/test_preflight.py`

## Running for a specific app

Each app is a folder under `apps/`. The workflows pick the app from the **`app`** text box, so
adding an app never requires editing a workflow.

- **GitHub website:** **Actions** tab → pick the workflow → **Run workflow** → type the folder name
  (e.g. `moku`) in `app`, and optionally a version.
- **Command line** ([gh](https://cli.github.com)):
  `gh workflow run UpdateMeta.yml -f app=moku`
  `gh workflow run GenManifest.yml -f app=moku -f version=0.14.1`
  `gh workflow run OpenPR.yml -f app=moku -f version=0.14.1`

Run each app separately: one run handles one app and one version.

## Re-running and `force`

Existing data is never overwritten silently:

| Workflow | Data for this version already exists | With **force** ticked |
|----------|---------------------------------------|-----------------------|
| 1 | Same as the release now: nothing happens, and workflow 2 isn't started. Differs from the release (e.g. the installer was re-uploaded): the run **stops** and lists what changed. | Overwrites the version file and starts workflow 2 with `force`. |
| 2 | Manifests already committed: the run **stops**. | Deletes the old folder and regenerates it. The commit shows exactly what changed. |
| 3 | Version already in winget-pkgs, or an open PR exists: the run **stops** (no override). | – (its override is `accept_changes`) |

To fix a bad version before submitting it, re-run workflow 2 with `force`. Re-run workflow 1 with
`force` only if the release itself changed.

## Notifications

You hear about everything through GitHub issues assigned to you, or failed-run emails:

- **Weekly check found updates or couldn't read a release:** one issue listing the apps.
- **Workflow 1 or 2 failed:** an issue named `❌ <workflow> failed: <app> <version>` with a link to
  the run. This matters when workflow 0 started the run: GitHub only emails the person who started
  a run, and here that's a workflow.
- **Workflow 3 failed:** you started it, so GitHub emails you about the failed run.

Close each issue when you're done with it. GitHub disables scheduled workflows in a repo with no
activity for 60 days. If workflow 0 stops running, re-enable it in the **Actions** tab.

## Releasing a new version

1. Wait for the weekly check's issue, or run workflow 1 with the app name yourself. Workflow 2
   starts automatically afterwards.
2. Check the committed manifests, then test them locally:
   `winget install --manifest manifests/<x>/<Publisher>/<Package>/<version>`
3. Run workflow 3 with the same app name and version.

## Adding an app

The app must **already be on winget**, because this uses `wingetcreate update`. Its installers must
be **GitHub release assets**. For a package that isn't on winget yet, submit the first version by
hand once, then use this repo for later versions.

Create `apps/<app>/meta.json`:

```json
{
  "name": "My App",
  "packageIdentifier": "Publisher.MyApp",
  "projectUrl": "https://github.com/owner/repo",
  "tagPrefix": "v",
  "assetPatterns": ["x64-setup\\.exe$"],
  "latestVersion": "",
  "versions": []
}
```

| Key | Meaning |
|-----|---------|
| `packageIdentifier` | The exact winget ID, which also sets the manifest path. For example, `Publisher.MyApp` → `manifests/p/Publisher/MyApp/`. |
| `projectUrl` | The GitHub repo that publishes the releases. |
| `tagPrefix` | The text before the version in release tags: `"v"` for `v1.2.3`, `""` for `1.2.3`. |
| `assetPatterns` | One regex per installer, matched against the asset file names. Each one must match **exactly one** asset, e.g. `["x64-setup\\.exe$", "arm64-setup\\.exe$"]` for two architectures. The release assets must have GitHub's SHA-256 `digest`. |

Leave `latestVersion` and `versions` empty; workflow 1 fills them in. Then run workflow 1 with
`app` set to the new folder name.

## Setup

- Secret `PAT_TOKEN`: a token for `frozenKelp` with the `public_repo` scope. Workflow 3 needs it.
- `wingetcreate` is pinned in `GenManifest.yml`. Change `$wcVersion` there to upgrade it.
