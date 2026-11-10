"""Tamper with copies of real Moku manifests and check preflight catches it.

Run from the repo root:  python3 -m unittest scripts/test_preflight.py
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
import preflight  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REL = os.path.join("manifests", "m", "Moku", "Moku")
NEW, PREV = "0.14.1", "0.13.1"


class PreflightTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        for v in (NEW, PREV):
            shutil.copytree(os.path.join(REPO, REL, v), os.path.join(self.root, REL, v))
        self.dir = os.path.join(self.root, REL, NEW)

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_pf(self, *extra):
        args = ["--app", "moku", "--version", NEW, "--manifest-dir", self.dir,
                "--apps-root", os.path.join(REPO, "apps"), "--compare-root", self.root, *extra]
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = preflight.main(args)
        return code, out.getvalue()

    def edit(self, name, old, new):
        p = Path(self.dir, name)
        s = p.read_text(encoding="utf-8")
        self.assertIn(old, s)
        p.write_text(s.replace(old, new), encoding="utf-8")

    def assertBlocked(self, needle, *extra):
        code, out = self.run_pf(*extra)
        self.assertEqual(code, 1, out)
        self.assertIn(needle, out)

    def test_clean_passes(self):
        code, out = self.run_pf()
        self.assertEqual(code, 0, out)

    def test_hash_mismatch(self):
        self.edit("Moku.Moku.installer.yaml", "96457F1A", "00000000")
        self.assertBlocked("SHA-256 for")

    def test_hash_mismatch_not_overridable(self):
        self.edit("Moku.Moku.installer.yaml", "96457F1A", "00000000")
        self.assertBlocked("SHA-256 for", "--accept-changes")

    def test_wrong_url(self):
        self.edit("Moku.Moku.installer.yaml", "moku-project/Moku/releases", "evil/Moku/releases")
        self.assertBlocked("is not under")

    def test_wrong_version(self):
        self.edit("Moku.Moku.yaml", "PackageVersion: 0.14.1", "PackageVersion: 0.14.2")
        self.assertBlocked("PackageVersion is '0.14.2'")

    def test_release_date(self):
        self.edit("Moku.Moku.installer.yaml", "ReleaseDate: 2026-10-24", "ReleaseDate: 2026-10-25")
        self.assertBlocked("ReleaseDate 2026-10-25 differs")

    def test_unexpected_file(self):
        Path(self.dir, "stray.txt").touch()
        self.assertBlocked("Unexpected file")

    def test_installer_type_change_needs_accept(self):
        self.edit("Moku.Moku.installer.yaml", "InstallerType: nullsoft", "InstallerType: inno")
        self.assertBlocked("InstallerType changed")
        code, out = self.run_pf("--accept-changes")
        self.assertEqual(code, 0, out)
        self.assertIn("::warning", out)

    def test_dropped_field(self):
        self.edit("Moku.Moku.locale.en-US.yaml", "Moniker: moku\n", "")
        self.assertBlocked("Moniker changed")

    def test_same_hash_as_previous(self):
        prev = Path(self.root, REL, PREV, "Moku.Moku.installer.yaml")
        s = prev.read_text(encoding="utf-8")
        s = s.replace("D0F80344CB031BC25B5EC4A2070C2A9B1916F32AD0A91EE689EC01DF833F9EDF",
                      "96457F1AEFD2A954D647308B74E665A901CF57943937DF25636157B4E2A5EF5F")
        prev.write_text(s, encoding="utf-8")
        self.assertBlocked("same SHA-256")

    def test_already_upstream(self):
        self.assertBlocked("already in winget-pkgs", "--upstream")

    def test_broken_yaml(self):
        self.edit("Moku.Moku.yaml", "DefaultLocale: en-US", "DefaultLocale: [en-US")
        self.assertBlocked("cannot parse YAML")


if __name__ == "__main__":
    unittest.main()
