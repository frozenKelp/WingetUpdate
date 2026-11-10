# WingetUpdate

Publishes new versions of GitHub-released apps to
[winget](https://github.com/microsoft/winget-pkgs). Each app has its own folder under `apps/`.
Currently configured: [Moku](https://github.com/moku-project/Moku) (`Moku.Moku`).

## Pipeline

Every workflow is started by hand from the **Actions** tab. Each one asks for:

- `app`: the folder name under `apps/`, e.g. `moku`.
- `version` (optional), e.g. `0.14.1`. Leave it empty to use the latest version.

| # | Workflow | What it does |
|---|----------|--------------|
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

## Releasing a new version

1. Run workflow 1 with the app name. Workflow 2 starts automatically afterwards.
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
