"""Failure-injection tests for checks that protect a Hosting deployment."""

import base64
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from prepare_hosting_config import file_hashes, prepare_config
from verify_site_build import PORTRAIT, PUBLIC_STATEMENT, verify_site

# A deliberately fake fixture: production filenames and URLs must not enter Git.
UNLISTED = "documents/research-statement-0123456789abcdef01234567.pdf"


class DeploymentValidationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "repo"
        self.stage = Path(self.temporary.name) / "built"
        self.public = self.root / "public"
        for name in (PORTRAIT, PUBLIC_STATEMENT, UNLISTED):
            self.write(self.root / "static" / name, b"original source " + name.encode())
            self.write(self.public / name, (self.root / "static" / name).read_bytes())
        self.write(self.root / "assets/documents/TQC_talk_Noise.pdf", b"optimized PDF")
        self.write(self.public / "documents/TQC_talk_Noise.pdf", b"previous PDF")
        self.write(self.public / "legacy/index.html", b"<p>Historical page</p>")
        data = b"console.log('reviewed');"
        self.asset = "js/app." + hashlib.sha256(data).hexdigest() + ".js"
        self.write(self.public / self.asset, data)
        integrity = base64.b64encode(hashlib.sha256(data).digest()).decode()
        self.html = ('<link href="/missing-existing-favicon.ico">'
                     f'<script src="/{self.asset}" integrity="sha256-{integrity}"></script>'
                     '<a href="/documents/TQC_talk_noise.pdf">Slides</a>')
        self.write(self.public / "index.html", self.html.encode())
        self.config = {"hosting": {
            "redirects": [{"source": "/documents/TQC_talk_noise.pdf",
                           "destination": "/documents/TQC_talk_Noise.pdf", "type": 301}],
            "headers": [{"source": "/documents/research-statement-*.pdf",
                         "headers": [{"key": "X-Robots-Tag", "value": "noindex, nofollow, nosnippet"}]}],
        }}
        self.write(self.root / "firebase.json", json.dumps(self.config).encode())
        shutil.copytree(self.public, self.stage)
        self.write(self.stage / "documents/TQC_talk_Noise.pdf", b"optimized PDF")
        self.manifest = self.root / "private-document.json"
        self.write(self.manifest, json.dumps({
            "path": UNLISTED,
            "sha256": hashlib.sha256((self.stage / UNLISTED).read_bytes()).hexdigest(),
        }).encode())

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def write(file, data):
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(data)

    def check(self):
        return verify_site(self.stage, self.root, private_manifest=self.manifest)

    def test_valid_build_retains_known_baseline_issue(self):
        result = self.check()
        self.assertEqual(result["sha256_assets_checked"], 1)
        self.assertEqual(len(result["known_preexisting_missing_references"]), 1)

    def test_dropped_historical_url_fails(self):
        (self.stage / "legacy/index.html").unlink()
        with self.assertRaisesRegex(ValueError, "Dropped published URLs"):
            self.check()

    def test_stale_compatibility_pdf_fails(self):
        self.write(self.stage / "documents/TQC_talk_Noise.pdf", b"previous PDF")
        with self.assertRaisesRegex(ValueError, "stale relative to source"):
            self.check()

    def test_case_redirect_removal_fails(self):
        self.config["hosting"]["redirects"] = []
        self.write(self.root / "firebase.json", json.dumps(self.config).encode())
        with self.assertRaisesRegex(ValueError, "case-compatibility redirect"):
            self.check()

    def test_modified_hashed_asset_fails(self):
        self.write(self.stage / self.asset, b"changed without new fingerprint")
        with self.assertRaisesRegex(ValueError, "fingerprint mismatch"):
            self.check()

    def test_invalid_sri_fails(self):
        self.write(self.stage / "index.html", self.html.replace("integrity=\"sha256-", "integrity=\"sha256-WRONG").encode())
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            self.check()

    def test_new_broken_pdf_link_fails(self):
        self.write(self.stage / "index.html", (self.html + '<a href="/documents/missing.pdf">Paper</a>').encode())
        with self.assertRaisesRegex(ValueError, "New broken internal links"):
            self.check()

    def test_unlisted_search_exposure_fails(self):
        self.write(self.stage / "index.json", json.dumps({"url": "/" + UNLISTED}).encode())
        with self.assertRaisesRegex(ValueError, "Unlisted PDF exposed"):
            self.check()

    def test_noindex_removal_fails(self):
        self.config["hosting"]["headers"] = []
        self.write(self.root / "firebase.json", json.dumps(self.config).encode())
        with self.assertRaisesRegex(ValueError, "noindex header"):
            self.check()

    def test_public_only_build_omits_private_document(self):
        (self.stage / UNLISTED).unlink()
        result = verify_site(self.stage, self.root, public_only=True)
        self.assertEqual(result["sha256_assets_checked"], 1)

    def test_public_only_build_rejects_private_document(self):
        with self.assertRaises(ValueError):
            verify_site(self.stage, self.root, public_only=True)

    def test_manifest_checksum_mismatch_fails(self):
        self.write(self.manifest, json.dumps({"path": UNLISTED, "sha256": "0" * 64}).encode())
        with self.assertRaises(ValueError):
            self.check()

    def test_portrait_recompression_fails(self):
        self.write(self.stage / PORTRAIT, b"compressed portrait")
        with self.assertRaisesRegex(ValueError, "original/static document"):
            self.check()

    def test_external_build_is_copied_inside_firebase_project(self):
        before = file_hashes(self.stage)
        output = self.root / ".firebase-ci/firebase.json"
        prepare_config(self.stage, output, source_root=self.root)
        config = json.loads(output.read_text())
        public = Path(config["hosting"]["public"])
        self.assertFalse(public.is_absolute())
        self.assertNotIn("..", public.parts)
        self.assertEqual(file_hashes(output.parent / public), before)
        self.assertEqual(file_hashes(self.stage), before)
        self.assertEqual(config["hosting"]["headers"], self.config["hosting"]["headers"])
        self.assertEqual(config["hosting"]["redirects"], self.config["hosting"]["redirects"])
        # Re-preparing a release must not replace any existing content directory.
        self.write(output.parent / public / "owned-marker", b"keep existing directory")
        prepare_config(self.stage, output, source_root=self.root)
        next_public = json.loads(output.read_text())["hosting"]["public"]
        self.assertNotEqual(next_public, public.as_posix())
        self.assertTrue((output.parent / public / "owned-marker").is_file())

    def test_build_already_inside_project_is_used_directly(self):
        output = self.stage.parent / "firebase.json"
        prepare_config(self.stage, output, emulator=True, source_root=self.root)
        config = json.loads(output.read_text())
        self.assertEqual(config["hosting"]["public"], self.stage.name)
        self.assertEqual(config["emulators"]["hosting"]["port"], 1341)

    def test_copy_mismatch_prevents_config_creation(self):
        output = self.root / ".firebase-ci/firebase.json"
        with patch("prepare_hosting_config.shutil.copytree"):
            with self.assertRaisesRegex(ValueError, "differs from the reviewed build"):
                prepare_config(self.stage, output, source_root=self.root)
        self.assertFalse(output.exists())

    def test_config_inside_published_content_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the published content"):
            prepare_config(self.stage, self.stage / "config/firebase.json", source_root=self.root)

    def test_production_config_cannot_be_overwritten(self):
        with self.assertRaisesRegex(ValueError, "never overwritten"):
            prepare_config(self.stage, self.root / "firebase.json", source_root=self.root)


if __name__ == "__main__":
    unittest.main()
