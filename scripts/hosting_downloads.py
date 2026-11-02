#!/usr/bin/env python3
"""Prepare allowlisted public PDFs for Pages; never publishes or changes accounts."""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
REPOSITORY = "AntMele/Antonio-Anna-Mele-website"
BASE_URL = "https://antmele.github.io/Antonio-Anna-Mele-website"
MANIFEST_NAME = "manifest.json"


def sha256(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def load_downloads(root=ROOT, refresh=False):
    root = Path(root)
    path = root / "hosting-downloads.json"
    if not path.exists():
        return None
    manifest = json.loads(path.read_text())
    if manifest.get("repository") != REPOSITORY or manifest.get("base_url") != BASE_URL:
        raise ValueError("Unexpected public document repository or Pages origin.")
    if not manifest.get("documents"):
        raise ValueError("The public document allowlist must not be empty.")
    names, paths = set(), set()
    for item in manifest["documents"]:
        name = item["asset"]
        if (not re.fullmatch(r"[A-Za-z0-9_-]+\.pdf", name)
                or name.lower().startswith(("research", "cv")) or name.casefold() in names):
            raise ValueError("Only unique public teaching and presentation PDFs may be offloaded.")
        names.add(name.casefold())
        if item["source"] not in tuple(folder + name for folder in
                ("assets/documents/", "static/documents/", "public/documents/")):
            raise ValueError("Invalid public document source.")
        file = root / item["source"]
        if file.is_symlink() or not file.resolve().is_relative_to(root.resolve()):
            raise ValueError("Document source must remain inside the repository.")
        data = file.read_bytes()
        if not data.startswith(b"%PDF-"):
            raise ValueError("Public document source must be a PDF.")
        expected_paths = {"/documents/" + name, "/documents/" + name.lower()}
        if set(item["paths"]) != expected_paths or paths.intersection(expected_paths):
            raise ValueError("Invalid or duplicate public download paths.")
        paths.update(expected_paths)
        digest = hashlib.sha256(data).hexdigest()
        if refresh:
            item.update(size=len(data), sha256=digest)
        elif item["size"] != len(data) or item["sha256"] != digest:
            raise ValueError("Public PDF changed: review it and run hosting_downloads.py --refresh.")
    if sum(item["size"] for item in manifest["documents"]) >= 950_000_000:
        raise ValueError("Public documents approach the 1 GB Pages site limit.")
    return manifest


def destination(manifest, item):
    return f'{manifest["base_url"]}/documents/{item["asset"]}'


def validate_config(root=ROOT, manifest=None, config=None):
    manifest = manifest if manifest is not None else load_downloads(root)
    if manifest is None:
        return None
    config = config if config is not None else json.loads((Path(root) / "firebase.json").read_text())["hosting"]
    rules = config.get("redirects", [])
    redirects = {r.get("source"): r for r in rules}
    if len(redirects) != len(rules):
        raise ValueError("Duplicate download redirect rules.")
    expected_external = set()
    allowed_paths = set()
    for item in manifest["documents"]:
        target = destination(manifest, item)
        expected_external.add(target)
        allowed_paths.update(item["paths"])
        for path in item["paths"]:
            if path.lstrip("/") not in config.get("ignore", []):
                raise ValueError("Offloaded PDF must be excluded from Firebase uploads.")
            current, visited = path, set()
            while current in redirects:
                if current in visited:
                    raise ValueError("Download redirect cycle.")
                visited.add(current)
                rule = redirects[current]
                if rule["type"] not in (301, 302):
                    raise ValueError("Invalid download redirect status.")
                current = rule["destination"]
            if current != target:
                raise ValueError("Missing or incorrect public download redirect.")
    for rule in rules:
        if rule["destination"].startswith((BASE_URL, "https://github.com/")):
            if (rule["destination"] not in expected_external or rule["type"] != 302
                    or rule.get("source") not in allowed_paths):
                raise ValueError("External document redirects must match the allowlist and use 302.")
    return manifest


def refresh_downloads(root=ROOT):
    """Refresh reviewed entries and their routes; never discover or remove files."""
    root = Path(root)
    manifest = load_downloads(root, refresh=True)
    if manifest is None:
        raise ValueError("No public document allowlist configured.")
    config = json.loads((root / "firebase.json").read_text())
    hosting = config["hosting"]
    rules = hosting.setdefault("redirects", [])
    ignores = hosting.setdefault("ignore", [])
    by_source = {r.get("source"): r for r in rules}
    for item in manifest["documents"]:
        target = destination(manifest, item)
        for path in item["paths"]:
            if path not in by_source:
                rule = {"source": path, "destination": target, "type": 302}
                rules.append(rule)
                by_source[path] = rule
            elif by_source[path]["destination"].startswith(BASE_URL):
                by_source[path].update(destination=target, type=302)
            # Existing local redirects, including historical TQC aliases, remain.
            if path.lstrip("/") not in ignores:
                ignores.append(path.lstrip("/"))
    # Stale external routes must be reviewed explicitly, never silently removed.
    validate_config(root, manifest, hosting)
    (root / "hosting-downloads.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (root / "firebase.json").write_text(json.dumps(config, indent=2) + "\n")
    return manifest


def public_manifest(manifest):
    return {"base_url": manifest["base_url"], "documents": [
        {key: item[key] for key in ("asset", "size", "sha256")}
        for item in manifest["documents"]]}


def stage_documents(manifest, folder, root=ROOT):
    root, folder = Path(root).resolve(), Path(folder)
    if folder.is_symlink():
        raise ValueError("Refusing a symlink staging directory.")
    folder = folder.resolve()
    if (folder == root or folder in root.parents
            or (folder.is_relative_to(root) and not folder.is_relative_to(root / "output"))):
        raise ValueError("Stage outside the repository or under ignored output/ only.")
    if folder.exists() and any(folder.iterdir()):
        raise ValueError("Use a new or empty staging directory; stale/private files must not be uploaded.")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "documents").mkdir()
    for item in manifest["documents"]:
        target = folder / "documents" / item["asset"]
        shutil.copyfile(root / item["source"], target)
        if target.stat().st_size != item["size"] or sha256(target) != item["sha256"]:
            raise ValueError("Staged PDF checksum mismatch.")
    (folder / ".nojekyll").write_text("")
    (folder / "index.html").write_text(
        '<!doctype html><html lang="en"><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<title>Antonio Anna Mele — public documents</title>'
        '<p>Public teaching materials and presentations from '
        '<a href="https://antonioannamele.com/">Antonio Anna Mele</a>.</p></html>\n')
    (folder / MANIFEST_NAME).write_text(json.dumps(public_manifest(manifest), indent=2) + "\n")
    expected = {".nojekyll", "index.html", MANIFEST_NAME} | {
        "documents/" + item["asset"] for item in manifest["documents"]}
    actual = {p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()}
    if actual != expected:
        raise ValueError("Unexpected file in public Pages artifact.")
    return folder


def validate_public_manifest(payload):
    if not isinstance(payload, dict) or set(payload) != {"base_url", "documents"} or payload["base_url"] != BASE_URL:
        raise ValueError("Invalid remote public document manifest.")
    if not isinstance(payload["documents"], list):
        raise ValueError("Invalid remote public document list.")
    names = set()
    for item in payload["documents"]:
        if (not isinstance(item, dict) or set(item) != {"asset", "size", "sha256"}
                or not isinstance(item["asset"], str)
                or not re.fullmatch(r"[A-Za-z0-9_-]+\.pdf", item["asset"])
                or item["asset"].lower().startswith(("research", "cv"))
                or item["asset"].casefold() in names
                or type(item["size"]) is not int or item["size"] <= 0
                or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
            raise ValueError("Invalid remote public document metadata.")
        names.add(item["asset"].casefold())
    return payload


def loopback_base_url(value):
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost", "::1")
            or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("Remote test overrides must use an HTTP loopback origin.")
    return value.rstrip("/")


def fetch_manifest(manifest, base_url=None):
    request = urllib.request.Request((base_url or manifest["base_url"]) + "/" + MANIFEST_NAME,
                                     headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read(1_000_001)
            if len(payload) > 1_000_000:
                raise ValueError("Remote document manifest is unexpectedly large.")
            return validate_public_manifest(json.loads(payload))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def check_pdf_response(item, response, read_bytes):
    if response.status != 200 or response.headers.get_content_type() != "application/pdf":
        raise ValueError("Public PDF is unavailable or has an incorrect content type: " + item["asset"])
    if "attachment" in response.headers.get("Content-Disposition", "").lower():
        raise ValueError("Public PDF would force a download: " + item["asset"])
    length = response.headers.get("Content-Length")
    if length is not None and int(length) != item["size"]:
        raise ValueError("Remote PDF size differs: " + item["asset"])
    if read_bytes:
        digest, size = hashlib.sha256(), 0
        while chunk := response.read(1024 * 1024):
            size += len(chunk)
            if size > item["size"]:
                raise ValueError("Remote PDF is larger than the reviewed file.")
            digest.update(chunk)
        if size != item["size"] or digest.hexdigest() != item["sha256"]:
            raise ValueError("Remote PDF checksum mismatch: " + item["asset"])


def check_remote(manifest, previous=None, retry_seconds=180, base_url=None):
    # A prior manifest only skips unchanged PDF body downloads, never current headers.
    before = {}
    if base_url:
        base_url = loopback_base_url(base_url)
    if previous:
        validate_public_manifest(previous)
        before = {item["asset"]: item for item in previous.get("documents", [])}
    deadline = time.monotonic() + retry_seconds
    checked = set()
    downloaded = 0
    while True:
        try:
            if fetch_manifest(manifest, base_url) != public_manifest(manifest):
                raise ValueError("Pages manifest has not reached the reviewed document version.")
            for item in manifest["documents"]:
                if item["asset"] in checked:
                    continue
                read_bytes = before.get(item["asset"]) != {
                    key: item[key] for key in ("asset", "size", "sha256")}
                url = (base_url or manifest["base_url"]) + "/documents/" + item["asset"]
                request = urllib.request.Request(url,
                    method="GET" if read_bytes else "HEAD",
                    headers={"Accept-Encoding": "identity", "Cache-Control": "no-cache"})
                with urllib.request.urlopen(request, timeout=30) as response:
                    check_pdf_response(item, response, read_bytes)
                checked.add(item["asset"])
                if read_bytes:
                    downloaded += item["size"]
            return {"verified_documents": len(checked), "verification_download_bytes": downloaded}
        except (ValueError, OSError) as exc:
            if time.monotonic() >= deadline:
                raise ValueError("Pages verification failed; Firebase deployment is blocked: " + str(exc)) from None
            time.sleep(min(10, max(0, deadline - time.monotonic())))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remote", action="store_true")
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--snapshot", type=Path, help="Save pre-deployment remote public metadata; 404 saves null.")
    parser.add_argument("--previous", type=Path, help="Metadata snapshot used to verify only changed PDF bodies.")
    parser.add_argument("--refresh", action="store_true", help="Refresh allowlisted PDF hashes and matching routes; never discover files.")
    parser.add_argument("--retry-seconds", type=int, default=180)
    parser.add_argument("--base-url", help="HTTP loopback origin for local remote-verification tests only.")
    args = parser.parse_args()
    if args.base_url and not args.remote:
        parser.error("--base-url is only valid with --remote")
    if not 0 <= args.retry_seconds <= 300:
        parser.error("--retry-seconds must be between 0 and 300")
    if args.refresh:
        refresh_downloads()
    manifest = validate_config()
    if not manifest:
        raise SystemExit("No public document allowlist configured.")
    result = {"documents": len(manifest["documents"]),
              "bytes": sum(item["size"] for item in manifest["documents"]), "remote_verified": False}
    if args.stage:
        stage_documents(manifest, args.stage)
    if args.snapshot:
        args.snapshot.parent.mkdir(parents=True, exist_ok=True)
        args.snapshot.write_text(json.dumps(fetch_manifest(manifest), indent=2) + "\n")
    if args.remote:
        previous = json.loads(args.previous.read_text()) if args.previous else None
        result.update(check_remote(manifest, previous, args.retry_seconds, args.base_url))
        result["remote_verified"] = True
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
