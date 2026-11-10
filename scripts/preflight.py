#!/usr/bin/env python3
"""Pre-PR safety checks for one app version's winget manifests.

Collects EVERY problem it can find (it does not stop at the first one), prints
each as a GitHub ::error:: annotation, writes them to the job summary, and
exits 1 if there are any.

Two kinds of problem:
  hard    – wrong hash/URL/ID/version, duplicate, release changed, ... Always fail.
  change  – something differs from the previous version (a field, the installer
            type, the architectures, ...). Can be legitimate, so after reviewing
            it you can re-run with --accept-changes to let these through. Workflows run this before committing manifests and
again before pushing anything to winget-pkgs, so a failed run means nothing
was submitted.

Usage:
  preflight.py --app moku --version 0.14.1 --manifest-dir manifests/m/Moku/Moku/0.14.1
               [--compare-root DIR] [--upstream] [--check-release] [--download]

  --compare-root  manifest tree to find the previous version in (repo root or a
                  winget-pkgs clone). The new version is compared field by field
                  with the newest older version found there.
  --upstream      compare-root is winget-pkgs: the version must not already be
                  there, and a previous version must exist.
  --check-release re-read the GitHub release (needs GH_TOKEN and `gh`).
  --download      download every installer and re-hash it.
  --accept-changes  report "change" problems as warnings instead of failing.
"""
import argparse
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request

import yaml

NOTES_MAX = 10000  # winget schema limit for ReleaseNotes

# Fields that should not change between versions. A change here usually means
# wingetcreate guessed wrong or the upstream release changed shape.
STABLE = {
    "version": ["DefaultLocale", "ManifestVersion"],
    "installer": ["InstallerLocale", "InstallerType", "NestedInstallerType", "Scope",
                  "InstallModes", "UpgradeBehavior", "ElevationRequirement", "Platform",
                  "MinimumOSVersion", "ManifestVersion"],
    "locale": ["Publisher", "PublisherUrl", "PublisherSupportUrl", "Author", "PackageName",
               "PackageUrl", "License", "LicenseUrl", "Moniker", "ManifestVersion"],
}
STABLE_PER_INSTALLER = ["InstallerType", "NestedInstallerType", "Scope", "InstallerLocale",
                        "UpgradeBehavior", "ElevationRequirement"]
# Top-level keys allowed to appear/disappear between versions.
VOLATILE_KEYS = {"ReleaseNotes", "ReleaseDate"}


def vkey(v):
    """Sortable key for versions like 1.2.10 or 1.0.0-beta."""
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in re.split(r"[.\-+]", v))


class Checker:
    def __init__(self):
        self.problems = []   # hard: always block
        self.changes = []    # differences from the previous version

    def fail(self, msg):
        self.problems.append(msg)

    def expect(self, cond, msg):
        if not cond:
            self.fail(msg)
        return cond

    def change(self, cond, msg):
        if not cond:
            self.changes.append(msg)
        return cond


def load_yaml(path, c):
    try:
        with open(path, encoding="utf-8-sig") as f:
            data = yaml.load(f, Loader=yaml.BaseLoader)  # all scalars as strings: 1.10 stays "1.10"
    except Exception as e:  # noqa: BLE001 – any parse error is a problem to report
        c.fail(f"{path}: cannot parse YAML ({e})")
        return None
    if not isinstance(data, dict):
        c.fail(f"{path}: not a YAML mapping")
        return None
    return data


def load_set(d, ident, c, label):
    """Load {kind: dict} for the version/installer/default-locale manifests in d."""
    out = {}
    out["version"] = load_yaml(os.path.join(d, f"{ident}.yaml"), c)
    out["installer"] = load_yaml(os.path.join(d, f"{ident}.installer.yaml"), c)
    loc = (out["version"] or {}).get("DefaultLocale")
    if not loc:
        c.fail(f"{label}: {ident}.yaml has no DefaultLocale")
        out["locale"] = None
    else:
        out["locale"] = load_yaml(os.path.join(d, f"{ident}.locale.{loc}.yaml"), c)
    return out


def installers_of(inst):
    """Installer entries with top-level defaults applied (winget inheritance)."""
    if not inst:
        return []
    top = {k: v for k, v in inst.items() if k != "Installers"}
    return [{**top, **i} for i in (inst.get("Installers") or [])]


def check_files(c, ident, version, mdir):
    if not c.expect(os.path.isdir(mdir), f"Manifest folder {mdir} does not exist"):
        return False
    files = sorted(os.listdir(mdir))
    for f in files:
        ok = f in (f"{ident}.yaml", f"{ident}.installer.yaml") or (
            f.startswith(f"{ident}.locale.") and f.endswith(".yaml"))
        c.expect(ok, f"Unexpected file in manifest folder: {f}")
    c.expect(os.path.basename(os.path.normpath(mdir)) == version,
             f"Manifest folder name is not the version {version}")
    return True


def check_manifests(c, ident, version, m, vdata, project_url):
    kinds = {"version": "version", "installer": "installer", "locale": "defaultLocale"}
    mversions = set()
    for kind, doc in m.items():
        if doc is None:
            continue
        c.expect(str(doc.get("PackageIdentifier")) == ident,
                 f"{kind} manifest: PackageIdentifier is {doc.get('PackageIdentifier')!r}, expected {ident!r}")
        c.expect(str(doc.get("PackageVersion")) == version,
                 f"{kind} manifest: PackageVersion is {doc.get('PackageVersion')!r}, expected {version!r}")
        c.expect(doc.get("ManifestType") == kinds[kind],
                 f"{kind} manifest: ManifestType is {doc.get('ManifestType')!r}, expected {kinds[kind]!r}")
        mversions.add(str(doc.get("ManifestVersion")))
        for k, v in doc.items():
            c.expect(v not in (None, "", []), f"{kind} manifest: {k} is empty")
    c.expect(len(mversions) == 1, f"Manifests disagree on ManifestVersion: {sorted(mversions)}")

    # ── installers vs. what workflow 1 recorded from the GitHub release ──
    expected = vdata.get("installers") or [
        {"url": vdata.get("installerUrl"), "sha256": vdata.get("installerSha256")}]
    want = {e["url"]: str(e["sha256"]).upper() for e in expected}
    got = installers_of(m["installer"])
    c.expect(len(got) == len(want),
             f"Manifest has {len(got)} installer(s), release metadata has {len(want)}")
    prefix = f"{project_url.rstrip('/')}/releases/download/{vdata.get('tagName')}/"
    arches = []
    for i in got:
        url, sha = i.get("InstallerUrl"), str(i.get("InstallerSha256", "")).upper()
        arches.append((i.get("Architecture"), i.get("Scope"), i.get("InstallerLocale")))
        c.expect(str(url).startswith(prefix), f"Installer URL {url} is not under {prefix}")
        if c.expect(url in want, f"Installer URL {url} is not one recorded from the release"):
            c.expect(sha == want[url],
                     f"SHA-256 for {url} is {sha}, release digest is {want[url]}")
        c.expect(re.fullmatch(r"[0-9A-F]{64}", sha) is not None, f"Malformed SHA-256 {sha!r}")
        c.expect(i.get("Architecture") in ("x86", "x64", "arm", "arm64", "neutral"),
                 f"Installer {url} has unexpected Architecture {i.get('Architecture')!r}")
    c.expect(len(arches) == len(set(arches)),
             "Two installers share the same Architecture/Scope/Locale")

    # ── dates and notes ──
    inst = m["installer"] or {}
    rd = str(inst.get("ReleaseDate", ""))
    if c.expect(bool(rd), "installer manifest: ReleaseDate missing"):
        c.expect(rd == vdata.get("publishedAt"),
                 f"ReleaseDate {rd} differs from release publish date {vdata.get('publishedAt')}")
        try:
            c.expect(datetime.date.fromisoformat(rd) <= datetime.date.today(),
                     f"ReleaseDate {rd} is in the future")
        except ValueError:
            c.fail(f"ReleaseDate {rd!r} is not YYYY-MM-DD")
    loc = m["locale"] or {}
    c.expect(loc.get("ReleaseNotesUrl") == vdata.get("releaseNotesUrl"),
             f"ReleaseNotesUrl {loc.get('ReleaseNotesUrl')!r} differs from {vdata.get('releaseNotesUrl')!r}")
    notes = loc.get("ReleaseNotes")
    if notes is not None:
        c.expect(len(str(notes)) <= NOTES_MAX,
                 f"ReleaseNotes is {len(str(notes))} chars (limit {NOTES_MAX})")
    elif (vdata.get("releaseNotes") or "").strip():
        c.fail("Release has notes but the locale manifest has no ReleaseNotes")


def find_previous(root, ident, version):
    pdir = os.path.join(root, "manifests", ident[0].lower(), *ident.split("."))
    if not os.path.isdir(pdir):
        return None, []
    vs = [v for v in os.listdir(pdir)
          if v != version and os.path.isfile(os.path.join(pdir, v, f"{ident}.yaml"))]
    older = [v for v in vs if vkey(v) < vkey(version)]
    if not older:
        return None, vs
    return os.path.join(pdir, max(older, key=vkey)), vs


def check_against_previous(c, ident, version, m, root, upstream):
    pdir_root = os.path.join(root, "manifests", ident[0].lower(), *ident.split("."))
    if upstream:
        c.expect(not os.path.isdir(os.path.join(pdir_root, version)),
                 f"{ident} {version} is already in winget-pkgs")
    prev_dir, others = find_previous(root, ident, version)
    newer = [v for v in others if vkey(v) > vkey(version)]
    c.change(not newer, f"{version} is older than existing version(s) {sorted(newer, key=vkey)}")
    if prev_dir is None:
        if upstream:
            c.fail(f"No earlier {ident} version found in winget-pkgs – is the identifier right?")
        else:
            print(f"  (no earlier local version of {ident} to compare with)")
        return
    prev_v = os.path.basename(prev_dir)
    print(f"  comparing with previous version {prev_v} in {root}")

    p = load_set(prev_dir, ident, Checker(), f"previous {prev_v}")
    for kind, fields in STABLE.items():
        new, old = m.get(kind) or {}, p.get(kind) or {}
        if not old:
            continue
        for f in fields:
            c.change(new.get(f) == old.get(f),
                     f"{kind} manifest: {f} changed from {old.get(f)!r} ({prev_v}) to {new.get(f)!r}")
        missing = set(old) - set(new) - VOLATILE_KEYS
        added = set(new) - set(old) - VOLATILE_KEYS
        c.change(not missing, f"{kind} manifest: fields dropped since {prev_v}: {sorted(missing)}")
        c.change(not added, f"{kind} manifest: new fields since {prev_v}: {sorted(added)}")

    old_i = {i.get("Architecture"): i for i in installers_of(p.get("installer"))}
    new_i = {i.get("Architecture"): i for i in installers_of(m.get("installer"))}
    c.change(set(old_i) == set(new_i),
             f"Installer architectures changed from {sorted(map(str, old_i))} ({prev_v}) to {sorted(map(str, new_i))}")
    for arch in set(old_i) & set(new_i):
        for f in STABLE_PER_INSTALLER:
            c.change(new_i[arch].get(f) == old_i[arch].get(f),
                     f"{arch} installer: {f} changed from {old_i[arch].get(f)!r} to {new_i[arch].get(f)!r}")
        c.change(new_i[arch].get("InstallerSha256") != old_i[arch].get("InstallerSha256"),
                 f"{arch} installer has the same SHA-256 as {prev_v} – same file re-released?")


def check_release(c, project_url, vdata):
    owner_repo = project_url.rstrip("/").removeprefix("https://github.com/")
    try:
        rel = json.loads(subprocess.run(
            ["gh", "api", f"repos/{owner_repo}/releases/tags/{vdata.get('tagName')}"],
            check=True, capture_output=True, text=True).stdout)
    except Exception as e:  # noqa: BLE001
        c.fail(f"Could not re-read GitHub release {vdata.get('tagName')}: {e}")
        return
    c.expect(not rel.get("draft"), "GitHub release is a draft")
    c.expect(not rel.get("prerelease"), "GitHub release is marked as a pre-release")
    assets = {a["browser_download_url"]: a for a in rel.get("assets", [])}
    expected = vdata.get("installers") or [{"url": vdata.get("installerUrl"), "sha256": vdata.get("installerSha256")}]
    for e in expected:
        a = assets.get(e["url"])
        if c.expect(a is not None, f"Asset {e['url']} is no longer on the release"):
            digest = (a.get("digest") or "").removeprefix("sha256:").upper()
            c.expect(digest == str(e["sha256"]).upper(),
                     f"Asset {e['url']} changed since metadata was recorded (digest now {digest})")


def check_download(c, vdata):
    expected = vdata.get("installers") or [{"url": vdata.get("installerUrl"), "sha256": vdata.get("installerSha256")}]
    for e in expected:
        h = hashlib.sha256()
        try:
            with urllib.request.urlopen(e["url"], timeout=300) as r:
                for chunk in iter(lambda: r.read(1 << 20), b""):
                    h.update(chunk)
        except Exception as ex:  # noqa: BLE001
            c.fail(f"Could not download {e['url']}: {ex}")
            continue
        got = h.hexdigest().upper()
        c.expect(got == str(e["sha256"]).upper(),
                 f"Downloaded {e['url']} has SHA-256 {got}, expected {e['sha256']}")
        print(f"  downloaded and hashed {e['url']}")


def report(c, title, accept_changes=False):
    blocking = c.problems + ([] if accept_changes else c.changes)
    for p in c.problems:
        print(f"::error title=Preflight::{p}")
    for p in c.changes:
        print(f"::{'warning' if accept_changes else 'error'} title=Preflight (change vs previous version)::{p}")

    lines = []
    if blocking:
        lines.append(f"## ❌ {title}: {len(blocking)} problem(s) – stopped, nothing was submitted\n")
    else:
        print("✓ Preflight passed")
        lines.append(f"## ✅ {title}: all checks passed\n")
    if c.problems:
        lines += ["### Problems (cannot be overridden)"] + [f"- {p}" for p in c.problems] + [""]
    if c.changes:
        if accept_changes:
            lines += ["### Changes vs previous version (accepted with `accept_changes`)"]
        else:
            lines += ["### Changes vs previous version",
                      "Review these. If they are intended, re-run with **accept_changes** ticked."]
        lines += [f"- {p}" for p in c.changes] + [""]

    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    return 1 if blocking else 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--app", required=True)
    ap.add_argument("--version", required=True)
    ap.add_argument("--manifest-dir", required=True)
    ap.add_argument("--apps-root", default="apps")
    ap.add_argument("--compare-root")
    ap.add_argument("--upstream", action="store_true")
    ap.add_argument("--check-release", action="store_true")
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--accept-changes", action="store_true")
    a = ap.parse_args(argv)

    c = Checker()
    title = f"Preflight {a.app} {a.version}"
    try:
        with open(os.path.join(a.apps_root, a.app, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        with open(os.path.join(a.apps_root, a.app, f"{a.version}.json"), encoding="utf-8") as f:
            vdata = json.load(f)
    except Exception as e:  # noqa: BLE001
        c.fail(f"Cannot read app metadata: {e}")
        return report(c, title, a.accept_changes)

    ident, project_url = meta.get("packageIdentifier", ""), meta.get("projectUrl", "")
    c.expect(vdata.get("version") == a.version,
             f"{a.version}.json says version {vdata.get('version')!r}")
    c.expect(any(e.get("version") == a.version for e in meta.get("versions", [])),
             f"{a.version} is not listed in meta.json versions")

    if check_files(c, ident, a.version, a.manifest_dir):
        m = load_set(a.manifest_dir, ident, c, "new manifests")
        if all(m.values()):
            check_manifests(c, ident, a.version, m, vdata, project_url)
            if a.compare_root:
                check_against_previous(c, ident, a.version, m, a.compare_root, a.upstream)
    if a.check_release:
        check_release(c, project_url, vdata)
    if a.download:
        check_download(c, vdata)
    return report(c, title, a.accept_changes)


if __name__ == "__main__":
    sys.exit(main())
