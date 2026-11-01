#!/usr/bin/env python3
"""Verify built Hosting content before it can be deployed (standard library only)."""

import argparse
import base64
import hashlib
import fnmatch
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import unquote, urljoin, urlsplit

from build_bandwidth_preview import ROOT, document_sources, inventory
from private_documents import find_private_documents, redact_private_text


PUBLIC_STATEMENT = "documents/Research_Statement_Mele_Public.pdf"
PORTRAIT = "images/PhotoSlack3.jpg"
ORIGIN = "https://antonioannamele.com/"
HASHED = re.compile(r"\.([0-9a-f]{64})\.[^.]+$")


class PageReferences(HTMLParser):
    def __init__(self):
        super().__init__()
        self.references = []
        self.integrity = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        for key in ("href", "src", "data-src"):
            if key in attributes:
                self.references.append(attributes[key])
        for key in ("srcset", "data-srcset"):
            for candidate in attributes.get(key, "").split(","):
                if candidate.strip():
                    self.references.append(candidate.strip().split()[0])
        if "integrity" in attributes:
            self.integrity.append((attributes.get("src") or attributes.get("href"),
                                   attributes["integrity"]))


def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def local_path(reference, page, files, redirects):
    url = urlsplit(urljoin(ORIGIN + page, reference))
    if url.scheme not in ("http", "https") or url.netloc != urlsplit(ORIGIN).netloc:
        return None
    path = unquote(url.path)
    for _ in range(5):
        if path not in redirects:
            break
        path = redirects[path]
    else:
        raise ValueError(f"Redirect cycle for {reference}")
    name = path.lstrip("/")
    if not name or path.endswith("/"):
        name += "index.html"
    elif name not in files and name + "/index.html" in files:
        name += "/index.html"
    return name


def parsed_pages(files):
    pages = {}
    for name, file in files.items():
        if name.endswith(".html"):
            parser = PageReferences()
            # Some retained historical pages use a legacy encoding. Their URL
            # attributes are ASCII; preserve the files and tolerate their prose.
            parser.feed(file.read_text(errors="replace"))
            pages[name] = parser
    return pages


def missing_references(pages, files, redirects):
    return {(page, target) for page, parser in pages.items()
            for ref in parser.references
            if (target := local_path(ref, page, files, redirects)) is not None
            and target not in files}


def verify_site(root, source_root=ROOT, public_only=False, private_manifest=None):
    root, source_root = Path(root), Path(source_root)
    files = inventory(root)
    baseline = inventory(source_root / "public")
    private_files = find_private_documents(root)
    expected_private = {}
    if public_only:
        if private_files:
            raise ValueError("Private documents must not appear in a public-only build.")
        private_baseline = {p.relative_to(source_root / "public").as_posix()
                            for p in find_private_documents(source_root / "public")}
        baseline = {name: file for name, file in baseline.items() if name not in private_baseline}
    elif private_manifest is not None:
        metadata = json.loads(Path(private_manifest).read_text())
        expected_private = {metadata["path"]: metadata["sha256"]}
    else:
        private_sources = find_private_documents(source_root / "static")
        expected_private = {p.relative_to(source_root / "static").as_posix(): digest(p)
                            for p in private_sources}
    if not public_only:
        actual_private = {p.relative_to(root).as_posix(): digest(p) for p in private_files}
        if len(expected_private) != 1 or actual_private != expected_private:
            raise ValueError("Missing or altered private document; deployment is blocked.")
    missing_files = sorted(baseline.keys() - files.keys())
    if missing_files:
        raise ValueError(redact_private_text(f"Dropped published URLs (exact case): {missing_files}"))
    config = json.loads((source_root / "firebase.json").read_text())
    redirects = {r["source"]: r["destination"] for r in config["hosting"].get("redirects", [])
                 if "source" in r and not any(c in r["source"] for c in "*?{}")}
    tqc_redirect = {"source": "/documents/TQC_talk_noise.pdf",
                    "destination": "/documents/TQC_talk_Noise.pdf", "type": 301}
    if tqc_redirect not in config["hosting"].get("redirects", []):
        raise ValueError("The historical TQC PDF case-compatibility redirect is missing or changed.")

    for name in (PUBLIC_STATEMENT, PORTRAIT):
        source = source_root / "static" / name
        if name not in files or not source.is_file() or digest(files[name]) != digest(source):
            raise ValueError(f"Missing or altered original/static document: /{name}")

    # A stale upper-case compatibility file is easy to miss on macOS but breaks
    # the intended optimized PDF on Linux. Check all actual published spellings.
    sources = document_sources(source_root)
    pdf_count = 0
    for name, file in files.items():
        if name.startswith("documents/") and name.lower().endswith(".pdf"):
            source = sources.get(file.name.casefold())
            if source and digest(source) != digest(file):
                raise ValueError(f"Published PDF is stale relative to source: /{name}")
            pdf_count += 1

    hashed = 0
    for name, file in files.items():
        match = HASHED.search(name)
        if match:
            if digest(file) != match.group(1):
                raise ValueError(f"Content fingerprint mismatch: /{name}")
            hashed += 1
    if not hashed:
        raise ValueError("No content-hashed assets were built.")

    pages = parsed_pages(files)
    baseline_missing = missing_references(parsed_pages(baseline), baseline, redirects)
    current_missing = missing_references(pages, files, redirects)
    regressions = sorted(current_missing - baseline_missing)
    if regressions:
        raise ValueError(f"New broken internal links or assets: {regressions}")
    sri_count = 0
    for page, parser in pages.items():
        for reference, integrity in parser.integrity:
            target = local_path(reference, page, files, redirects)
            if target is None:
                continue
            if target not in files:
                raise ValueError(f"Missing SRI asset: {target}")
            valid = False
            for token in integrity.split():
                algorithm, expected = token.split("-", 1)
                if algorithm in ("sha256", "sha384", "sha512"):
                    actual = base64.b64encode(hashlib.new(algorithm, files[target].read_bytes()).digest()).decode()
                    valid = valid or actual == expected
            if not valid:
                raise ValueError(f"Subresource integrity mismatch: {page}: {target}")
            sri_count += 1

    tokens = [part for name in expected_private
              for part in (Path(name).name, Path(name).stem.removeprefix("research-statement-"))]
    for name, file in files.items():
        if file.suffix in (".html", ".md", ".json", ".xml", ".txt", ".js", ".css", ".map", ".svg"):
            text = file.read_text(errors="replace")
            if any(token in text for token in tokens):
                raise ValueError(f"Unlisted PDF exposed in public page/index: /{name}")
    private_paths = list(expected_private) or ["documents/research-statement-" + "0" * 24 + ".pdf"]
    noindex = all(any(fnmatch.fnmatchcase("/" + name, r.get("source", "")) and any(
        h["key"].lower() == "x-robots-tag" and h["value"] == "noindex, nofollow, nosnippet"
        for h in r.get("headers", [])) for r in config["hosting"].get("headers", []))
        for name in private_paths)
    if not noindex:
        raise ValueError("Unlisted statement noindex header is missing or changed.")
    if any("/lib/katex/" in ref for ref in pages["index.html"].references):
        raise ValueError("Unused homepage maths assets were reintroduced.")
    return {
        "built_files": len(files), "published_paths_preserved": len(baseline),
        "pdf_files_checked": pdf_count, "sha256_assets_checked": hashed,
        "sri_attributes_checked": sri_count,
        "unlisted_privacy_checks": "public-only build" if public_only else "passed",
        "known_preexisting_missing_references": sorted(current_missing),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--public-only", action="store_true")
    parser.add_argument("--private-manifest", type=Path)
    args = parser.parse_args()
    if args.public_only and args.private_manifest:
        parser.error("Choose public-only verification or a private manifest, not both.")
    try:
        result = verify_site(args.root, public_only=args.public_only, private_manifest=args.private_manifest)
    except Exception as exc:
        raise SystemExit(redact_private_text(str(exc))) from None
    print(redact_private_text(json.dumps(result, indent=2)))


if __name__ == "__main__":
    main()
