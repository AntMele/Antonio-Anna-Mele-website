"""Failure checks for public Pages availability, privacy, and exact PDF bytes."""

import copy
from email.message import Message
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from hosting_downloads import (BASE_URL, REPOSITORY, check_pdf_response, check_remote,
    destination, fetch_manifest, load_downloads, loopback_base_url, public_manifest,
    refresh_downloads, stage_documents, validate_config, validate_public_manifest)
from verify_site_build import local_path


class Response(io.BytesIO):
    def __init__(self, data, content_type="application/pdf", attachment=False):
        super().__init__(data)
        self.status = 200
        self.headers = Message()
        self.headers["Content-Type"] = content_type
        self.headers["Content-Length"] = str(len(data))
        if attachment:
            self.headers["Content-Disposition"] = "attachment; filename=Lecture.pdf"


class PublicDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "repo"
        self.pdf = self.root / "assets/documents/Lecture.pdf"
        self.pdf.parent.mkdir(parents=True)
        self.data = b"%PDF-1.4\nfixture\n"
        self.pdf.write_bytes(self.data)
        self.item = {"asset": "Lecture.pdf", "source": "assets/documents/Lecture.pdf",
                     "size": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest(),
                     "paths": ["/documents/Lecture.pdf", "/documents/lecture.pdf"]}
        self.manifest = {"repository": REPOSITORY, "base_url": BASE_URL, "documents": [self.item]}
        self.url = destination(self.manifest, self.item)
        self.config = {"hosting": {
            "ignore": [p.lstrip("/") for p in self.item["paths"]],
            "redirects": [{"source": p, "destination": self.url, "type": 302}
                          for p in self.item["paths"]]}}
        self.folder = Path(self.temp.name) / "pages"

    def tearDown(self):
        self.temp.cleanup()

    def check(self):
        (self.root / "hosting-downloads.json").write_text(json.dumps(self.manifest))
        (self.root / "firebase.json").write_text(json.dumps(self.config))
        return validate_config(self.root)

    def test_valid_configuration_and_pdf(self):
        self.check()
        check_pdf_response(self.item, Response(self.data), True)

    def test_changed_pdf_blocks_deployment_and_refresh_updates_reviewed_hash(self):
        self.pdf.write_bytes(b"%PDF-1.4\nnew version\n")
        with self.assertRaisesRegex(ValueError, "PDF changed"):
            self.check()
        updated = load_downloads(self.root, refresh=True)
        self.assertEqual(updated["documents"][0]["sha256"], hashlib.sha256(self.pdf.read_bytes()).hexdigest())

    def test_missing_redirect_and_missing_upload_exclusion_fail(self):
        for field in ("redirects", "ignore"):
            with self.subTest(field=field):
                previous = self.config["hosting"][field]
                self.config["hosting"][field] = []
                with self.assertRaises(ValueError):
                    self.check()
                self.config["hosting"][field] = previous

    def test_refresh_adds_new_allowlisted_routes_and_preserves_existing_rules(self):
        self.check()
        self.config["hosting"]["redirects"].append({"source": "/legacy", "destination": "/new", "type": 301})
        extra = copy.deepcopy(self.item)
        extra.update(asset="NewLecture.pdf", source="assets/documents/NewLecture.pdf", size=0, sha256="")
        extra["paths"] = ["/documents/NewLecture.pdf", "/documents/newlecture.pdf"]
        (self.root / extra["source"]).write_bytes(self.data)
        self.manifest["documents"].append(extra)
        (self.root / "hosting-downloads.json").write_text(json.dumps(self.manifest))
        (self.root / "firebase.json").write_text(json.dumps(self.config))
        refresh_downloads(self.root)
        updated = validate_config(self.root)
        self.assertEqual(len(updated["documents"]), 2)
        config = json.loads((self.root / "firebase.json").read_text())["hosting"]
        self.assertIn({"source": "/legacy", "destination": "/new", "type": 301}, config["redirects"])
        self.assertIn("documents/NewLecture.pdf", config["ignore"])

    def test_refresh_rejects_stale_routes_instead_of_deleting_old_urls(self):
        self.check()
        self.config["hosting"]["redirects"].append({"source": "/documents/Old.pdf",
            "destination": BASE_URL + "/documents/Old.pdf", "type": 302})
        (self.root / "firebase.json").write_text(json.dumps(self.config))
        before = (self.root / "firebase.json").read_bytes()
        with self.assertRaises(ValueError):
            refresh_downloads(self.root)
        self.assertEqual((self.root / "firebase.json").read_bytes(), before)

    def test_existing_case_redirect_chain_is_preserved(self):
        self.config["hosting"]["redirects"][1].update(destination="/documents/Lecture.pdf", type=301)
        self.check()
        self.assertIsNone(local_path("/documents/lecture.pdf", "index.html", {}, {
            "/documents/lecture.pdf": "/documents/Lecture.pdf", "/documents/Lecture.pdf": self.url}))

    def test_redirect_cycle_fails(self):
        self.config["hosting"]["redirects"][0]["destination"] = "/documents/lecture.pdf"
        self.config["hosting"]["redirects"][1]["destination"] = "/documents/Lecture.pdf"
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.check()

    def test_protected_documents_and_traversal_fail(self):
        for name in ("Research_Statement_Mele_Public.pdf", "research-statement-fixture.pdf",
                     "CV_AntonioAnnaMele.pdf", "../Lecture.pdf"):
            with self.subTest(name=name):
                self.item["asset"] = name
                with self.assertRaises(ValueError):
                    self.check()

    def test_wrong_repository_and_origin_fail(self):
        for field in ("repository", "base_url"):
            before = self.manifest[field]
            self.manifest[field] = "unexpected"
            with self.assertRaises(ValueError):
                self.check()
            self.manifest[field] = before

    def test_symlink_source_fails(self):
        real = self.pdf.with_name("real.pdf")
        self.pdf.rename(real)
        self.pdf.symlink_to(real)
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            self.check()

    def test_stage_contains_only_allowlist_and_public_metadata(self):
        self.check()
        (self.pdf.parent / "CV_fixture.pdf").write_bytes(self.data)
        (self.pdf.parent / "research-statement-fixture.pdf").write_bytes(self.data)
        stage_documents(self.manifest, self.folder, self.root)
        files = {p.relative_to(self.folder).as_posix() for p in self.folder.rglob("*") if p.is_file()}
        self.assertEqual(files, {"documents/Lecture.pdf", ".nojekyll", "index.html", "manifest.json"})
        payload = json.loads((self.folder / "manifest.json").read_text())
        self.assertEqual(payload, public_manifest(self.manifest))
        self.assertNotIn("source", json.dumps(payload))
        self.assertEqual((self.folder / "documents/Lecture.pdf").read_bytes(), self.data)

    def test_nonempty_or_unsafe_stage_rejected(self):
        self.folder.mkdir()
        (self.folder / "keep.txt").write_text("keep")
        for folder in (self.folder, self.root, self.root / "public", self.root.parent):
            with self.subTest(folder=str(folder)), self.assertRaises(ValueError):
                stage_documents(self.manifest, folder, self.root)
        self.assertEqual((self.folder / "keep.txt").read_text(), "keep")

    def test_changed_body_mime_or_forced_download_fails(self):
        responses = [Response(b"%PDF-1.4\nwrong!!\n"), Response(self.data, "text/html"),
                     Response(self.data, attachment=True)]
        for response in responses:
            with self.subTest(headers=str(response.headers)), self.assertRaises(ValueError):
                check_pdf_response(self.item, response, True)

    def test_missing_and_false_remote_manifest_blocks_deployment(self):
        for payload in (None, {**public_manifest(self.manifest), "documents": []}):
            with patch("hosting_downloads.fetch_manifest", return_value=payload):
                with self.assertRaisesRegex(ValueError, "deployment is blocked"):
                    check_remote(self.manifest, retry_seconds=0)

    def test_only_matching_previous_documents_skip_body_download(self):
        expected = public_manifest(self.manifest)
        for previous, method, downloaded in ((None, "GET", len(self.data)), (expected, "HEAD", 0)):
            with patch("hosting_downloads.fetch_manifest", return_value=expected), patch(
                    "hosting_downloads.urllib.request.urlopen", return_value=Response(self.data)) as request:
                result = check_remote(self.manifest, previous, retry_seconds=0)
                self.assertEqual(request.call_args.args[0].method, method)
                self.assertEqual(result["verification_download_bytes"], downloaded)

    def test_invalid_public_metadata_rejected(self):
        for field, value in (("asset", "CV_fixture.pdf"), ("sha256", "wrong"), ("size", -1)):
            payload = copy.deepcopy(public_manifest(self.manifest))
            payload["documents"][0][field] = value
            with self.assertRaises(ValueError):
                validate_public_manifest(payload)
        payload = public_manifest(self.manifest)
        payload["documents"][0]["source"] = "sensitive"
        with self.assertRaises(ValueError):
            validate_public_manifest(payload)

    def test_local_test_override_cannot_redirect_to_another_host(self):
        self.assertEqual(loopback_base_url("http://127.0.0.1:1342"), "http://127.0.0.1:1342")
        for value in ("https://example.com", "http://user@localhost", "http://localhost/private", "http://127.0.0.1/?key=1"):
            with self.assertRaises(ValueError):
                loopback_base_url(value)


if __name__ == "__main__":
    unittest.main()
