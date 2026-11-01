#!/usr/bin/env python3
"""Restore an unlisted document from protected environment secrets, without logging it."""

import argparse
import base64
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import tempfile
import zipfile

from private_documents import PRIVATE_NAME


PART_PREFIX = "PRIVATE_DOCUMENT_PART_"
COUNT_VARIABLE = "PRIVATE_DOCUMENT_PART_COUNT"
CHECKSUM_VARIABLE = "PRIVATE_DOCUMENT_BUNDLE_SHA256"
MAX_PARTS = 12
MAX_PART_CHARS = 40000
MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
ORIGIN = "https://antonioannamele.com"


class PrivateDocumentError(ValueError):
    """A deliberately non-sensitive failure description."""


def load_bundle(environ):
    count_text = environ.get(COUNT_VARIABLE, "")
    if not re.fullmatch(r"(?:[1-9]|1[0-2])", count_text):
        raise PrivateDocumentError("Private document parts are missing or invalid.")
    count = int(count_text)
    parts = []
    for number in range(1, count + 1):
        part = environ.get(f"{PART_PREFIX}{number:02d}", "")
        if not part or len(part) > MAX_PART_CHARS:
            raise PrivateDocumentError("Private document parts are missing or invalid.")
        parts.append(part)
    checksum = environ.get(CHECKSUM_VARIABLE, "")
    if not re.fullmatch(r"[0-9a-f]{64}", checksum):
        raise PrivateDocumentError("Private document bundle checksum is missing or invalid.")
    try:
        bundle = base64.b64decode("".join(parts), validate=True)
    except (ValueError, UnicodeError):
        raise PrivateDocumentError("Private document encoding is invalid.") from None
    if not hmac.compare_digest(hashlib.sha256(bundle).hexdigest(), checksum):
        raise PrivateDocumentError("Private document bundle failed its integrity check.")
    try:
        with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
            names = archive.namelist()
            if len(names) != 2 or set(names) != {"metadata.json", "document.pdf"}:
                raise PrivateDocumentError("Private document bundle has unexpected entries.")
            if archive.getinfo("metadata.json").file_size > 4096:
                raise PrivateDocumentError("Private document metadata is too large.")
            size = archive.getinfo("document.pdf").file_size
            if not 0 < size <= MAX_DOCUMENT_BYTES:
                raise PrivateDocumentError("Private document exceeds the supported size.")
            metadata = json.loads(archive.read("metadata.json"))
            data = archive.read("document.pdf")
    except PrivateDocumentError:
        raise
    except Exception:
        raise PrivateDocumentError("Private document bundle could not be read.") from None
    if not isinstance(metadata, dict) or set(metadata) != {"path", "sha256"}:
        raise PrivateDocumentError("Private document metadata is invalid.")
    path, expected = metadata["path"], metadata["sha256"]
    if (not isinstance(path, str) or not path.startswith("documents/")
            or not PRIVATE_NAME.fullmatch(path.removeprefix("documents/"))):
        raise PrivateDocumentError("Private document destination is invalid.")
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise PrivateDocumentError("Private document checksum is invalid.")
    if not data.startswith(b"%PDF-") or not hmac.compare_digest(hashlib.sha256(data).hexdigest(), expected):
        raise PrivateDocumentError("Private document failed its integrity check.")
    return metadata, data


def register_masks(metadata, environ):
    if environ.get("GITHUB_ACTIONS") != "true":
        return
    path = metadata["path"]
    name = Path(path).name
    token = PRIVATE_NAME.fullmatch(name).group(1)
    # Register derived values before writing files or invoking any other program.
    # Base64 secret masking alone does not mask the decoded filename or URL.
    for value in (name, path, "/" + path, ORIGIN + "/" + path, token, metadata["sha256"]):
        print("::add-mask::" + value, flush=True)


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".private-document-", delete=False) as file:
            temporary = Path(file.name)
            file.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def restore_document(root, manifest, environ=None):
    environ = os.environ if environ is None else environ
    if Path(manifest).is_symlink():
        raise PrivateDocumentError("Private document manifest destination is unsafe.")
    root, manifest = Path(root).resolve(), Path(manifest).resolve()
    if not (root / "index.html").is_file():
        raise PrivateDocumentError("Build public content before restoring the private document.")
    if manifest == root or root in manifest.parents:
        raise PrivateDocumentError("Private document manifest must stay outside published content.")
    metadata, data = load_bundle(environ)
    target = root / metadata["path"]
    if target.is_symlink() or root not in target.resolve().parents:
        raise PrivateDocumentError("Private document destination is unsafe.")
    if manifest == target:
        raise PrivateDocumentError("Private document manifest destination is unsafe.")
    register_masks(metadata, environ)
    try:
        atomic_write(target, data)
        atomic_write(manifest, (json.dumps(metadata) + "\n").encode())
    except Exception:
        raise PrivateDocumentError("Private document could not be restored safely.") from None
    return {"private_document_restored": True, "integrity_verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = restore_document(args.root, args.manifest)
    except PrivateDocumentError as exc:
        parser.exit(1, "Private document restoration failed: " + str(exc) + "\n")
    except Exception:
        parser.exit(1, "Private document restoration failed safely.\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
