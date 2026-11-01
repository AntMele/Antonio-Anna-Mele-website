"""Failure cases for restoring private deployment inputs without public exposure."""

import base64
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile

from private_documents import find_private_documents, redact_private_text
from restore_private_document import (CHECKSUM_VARIABLE, COUNT_VARIABLE, MAX_PART_CHARS,
                                      PrivateDocumentError, restore_document)


class PrivateDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.root = self.directory / "content"
        self.root.mkdir()
        (self.root / "index.html").write_text("<p>Public page</p>")
        self.manifest = self.directory / "private.json"
        self.name = "research-statement-" + "123abc" * 4 + ".pdf"
        self.path = "documents/" + self.name
        self.data = b"%PDF-1.7\nprivate test document\n%%EOF\n"

    def tearDown(self):
        self.temporary.cleanup()

    def environment(self, *, path=None, checksum=None, extra=None, data=None):
        data = self.data if data is None else data
        metadata = {"path": self.path if path is None else path,
                    "sha256": checksum or hashlib.sha256(data).hexdigest()}
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("metadata.json", json.dumps(metadata))
            archive.writestr("document.pdf", data)
            if extra:
                archive.writestr(extra, "unexpected")
        raw = buffer.getvalue()
        encoded = base64.b64encode(raw).decode()
        parts = [encoded[i:i + MAX_PART_CHARS] for i in range(0, len(encoded), MAX_PART_CHARS)]
        return {COUNT_VARIABLE: str(len(parts)), CHECKSUM_VARIABLE: hashlib.sha256(raw).hexdigest(),
                **{f"PRIVATE_DOCUMENT_PART_{i:02d}": part for i, part in enumerate(parts, 1)}}

    def test_exact_bytes_and_private_manifest(self):
        result = restore_document(self.root, self.manifest, self.environment())
        self.assertTrue(result["integrity_verified"])
        self.assertEqual((self.root / self.path).read_bytes(), self.data)
        self.assertEqual(json.loads(self.manifest.read_text())["path"], self.path)
        self.assertEqual(find_private_documents(self.root), [self.root / self.path])
        self.assertNotIn(self.name, json.dumps(result))

    def test_missing_parts_fail_before_any_file_write(self):
        env = self.environment()
        env.pop("PRIVATE_DOCUMENT_PART_01")
        with self.assertRaises(PrivateDocumentError):
            restore_document(self.root, self.manifest, env)
        self.assertFalse(self.manifest.exists())
        self.assertFalse((self.root / self.path).exists())

    def test_multiple_secret_parts_restore_exactly(self):
        data = b"%PDF-1.7\n" + os.urandom(62000) + b"\n%%EOF\n"
        env = self.environment(data=data)
        self.assertGreater(int(env[COUNT_VARIABLE]), 1)
        restore_document(self.root, self.manifest, env)
        self.assertEqual((self.root / self.path).read_bytes(), data)

    def test_missing_middle_part_fails(self):
        env = self.environment(data=b"%PDF-1.7\n" + os.urandom(62000))
        env.pop("PRIVATE_DOCUMENT_PART_02")
        with self.assertRaisesRegex(PrivateDocumentError, "parts are missing"):
            restore_document(self.root, self.manifest, env)

    def test_invalid_count_and_oversized_part_fail(self):
        env = self.environment()
        for value in ("", "0", "13", "1\n"):
            with self.subTest(value=value):
                invalid = dict(env, **{COUNT_VARIABLE: value})
                with self.assertRaises(PrivateDocumentError):
                    restore_document(self.root, self.manifest, invalid)
        env["PRIVATE_DOCUMENT_PART_01"] = "A" * (MAX_PART_CHARS + 1)
        with self.assertRaises(PrivateDocumentError):
            restore_document(self.root, self.manifest, env)

    def test_corrupted_bundle_fails(self):
        env = self.environment()
        env[CHECKSUM_VARIABLE] = "0" * 64
        with self.assertRaisesRegex(PrivateDocumentError, "bundle failed"):
            restore_document(self.root, self.manifest, env)

    def test_wrong_pdf_hash_fails(self):
        with self.assertRaisesRegex(PrivateDocumentError, "failed its integrity"):
            restore_document(self.root, self.manifest, self.environment(checksum="0" * 64))

    def test_non_pdf_data_fails(self):
        with self.assertRaisesRegex(PrivateDocumentError, "failed its integrity"):
            restore_document(self.root, self.manifest, self.environment(data=b"not a PDF"))

    def test_paths_cannot_escape_document_directory(self):
        for path in ("../" + self.name, "documents/../" + self.name,
                     "/documents/" + self.name, "documents/nested/" + self.name,
                     "documents/Research_Statement_Mele_Public.pdf", self.path + "\n::error::oops"):
            with self.subTest(path=path):
                with self.assertRaisesRegex(PrivateDocumentError, "destination is invalid"):
                    restore_document(self.root, self.manifest, self.environment(path=path))

    def test_extra_zip_members_rejected(self):
        with self.assertRaisesRegex(PrivateDocumentError, "unexpected entries"):
            restore_document(self.root, self.manifest, self.environment(extra="secret.txt"))

    def test_manifest_cannot_be_published(self):
        with self.assertRaisesRegex(PrivateDocumentError, "outside published"):
            restore_document(self.root, self.root / "private.json", self.environment())

    def test_document_symlink_cannot_escape(self):
        outside = self.directory / "outside"
        outside.mkdir()
        (self.root / "documents").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(PrivateDocumentError, "destination is unsafe"):
            restore_document(self.root, self.manifest, self.environment())
        self.assertEqual(list(outside.iterdir()), [])

    def test_manifest_symlink_is_rejected(self):
        original = self.directory / "existing.txt"
        original.write_text("leave unchanged")
        self.manifest.symlink_to(original)
        with self.assertRaisesRegex(PrivateDocumentError, "manifest destination is unsafe"):
            restore_document(self.root, self.manifest, self.environment())
        self.assertEqual(original.read_text(), "leave unchanged")

    def test_github_registers_derived_masks_before_success(self):
        env = self.environment()
        env["GITHUB_ACTIONS"] = "true"
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            restore_document(self.root, self.manifest, env)
        lines = stdout.getvalue().splitlines()
        self.assertTrue(all(line.startswith("::add-mask::") for line in lines))
        self.assertIn("::add-mask::" + self.name, lines)
        self.assertIn("::add-mask::https://antonioannamele.com/" + self.path, lines)
        self.assertIn("::add-mask::" + "123abc" * 4, lines)

    def test_cli_error_never_prints_private_path(self):
        env = dict(os.environ)
        env.update(self.environment(checksum="0" * 64))
        result = subprocess.run([sys.executable, str(Path(__file__).with_name("restore_private_document.py")),
                                 "--root", str(self.root), "--manifest", str(self.manifest)],
                                env=env, capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(self.name, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stdout + result.stderr)

    def test_filename_and_bare_token_are_redacted(self):
        token = "123abc" * 4
        text = self.path + " token=" + token
        cleaned = redact_private_text(text)
        self.assertNotIn(self.name, cleaned)
        self.assertNotIn(token, cleaned)

    def test_public_statement_not_discovered(self):
        (self.root / "documents").mkdir()
        (self.root / "documents/Research_Statement_Mele_Public.pdf").write_bytes(self.data)
        self.assertEqual(find_private_documents(self.root), [])


if __name__ == "__main__":
    unittest.main()
