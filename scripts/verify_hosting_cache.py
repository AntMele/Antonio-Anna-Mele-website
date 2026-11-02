#!/usr/bin/env python3
"""Check Firebase cache headers against a local Hosting emulator.

Usage: python3 scripts/verify_hosting_cache.py --root public --url http://127.0.0.1:1339
This reads content and headers only; it never deploys or changes files.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re

from private_documents import find_private_documents, redact_private_text
import urllib.error
import urllib.request


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--url", default="http://127.0.0.1:1339")
parser.add_argument("--public-only", action="store_true")
parser.add_argument("--config", type=Path,
                    default=Path(__file__).resolve().parent.parent / "firebase.json")
args = parser.parse_args()
try:
    config = json.loads(args.config.read_text())
    immutable_rule = next(rule for rule in config["hosting"]["headers"] if "regex" in rule)
    pattern = re.compile(immutable_rule["regex"])
    immutable = "public, max-age=31536000, immutable"
    fresh = "public, max-age=0, must-revalidate"
    private_paths = ["/" + p.relative_to(args.root).as_posix() for p in find_private_documents(args.root)]
    if args.public_only and private_paths:
        raise SystemExit("Private documents must not appear in a public-only build.")
    if not args.public_only and len(private_paths) != 1:
        raise SystemExit("The private document is missing; deployment is blocked.")
    private_probe = "/documents/research-statement-" + "0" * 24 + ".pdf"

    # A version string or the unlisted PDF's random token must not grant immutable caching.
    for path in [
        private_probe, "/documents/Research_Statement_Mele_Public.pdf", "/images/PhotoSlack3.jpg",
        "/css/style.min.css", "/js/theme.12345678.js", "/images/avatar." + "a" * 63 + ".webp",
        "/documents/statement." + "a" * 64 + ".pdf",
    ]:
        assert not pattern.fullmatch(path), path


    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def request(path, headers=None):
        req = urllib.request.Request(args.url.rstrip("/") + path, method="HEAD", headers=headers or {})
        try:
            return opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            return exc


    hashed_paths = []
    for file in sorted(args.root.rglob("*")):
        if file.is_file():
            path = "/" + file.relative_to(args.root).as_posix()
            if pattern.fullmatch(path):
                expected_digest = file.name.split(".")[-2]
                assert hashlib.sha256(file.read_bytes()).hexdigest() == expected_digest, path
                hashed_paths.append(path)
    assert hashed_paths, "No SHA256 assets found; build the production site first."

    fresh_paths = [
        "/", "/index.html", "/about/", "/about/index.html", "/research/", "/news/",
        "/index.json", "/sitemap.xml", "/css/style.min.css", "/js/tsparticles.min.js",
        "/images/PhotoSlack3.jpg", "/documents/Research_Statement_Mele_Public.pdf",
        "/documents/Slides_ChannelLearning.pdf",
    ]
    results = []
    redirects = {r["source"]: r for r in config["hosting"].get("redirects", []) if "source" in r}
    redirect_results = []
    for path, rule in redirects.items():
        with request(path) as response:
            assert response.status == rule["type"], (path, response.status)
            assert response.headers.get("Location") == rule["destination"], (path, "Wrong redirect destination")
            redirect_results.append({"path": path, "status": response.status, "destination": rule["destination"]})
    fresh_paths = [path for path in fresh_paths if path not in redirects]
    for path in fresh_paths + private_paths + hashed_paths:
        expected = immutable if path in hashed_paths else fresh
        with request(path) as response:
            assert response.status == 200, (path, response.status)
            actual = response.headers.get("Cache-Control")
            assert actual == expected, (path, actual, expected)
            robots = response.headers.get("X-Robots-Tag")
            assert robots == ("noindex, nofollow, nosnippet" if path in private_paths else None), (path, robots)
            results.append({"path": path, "status": response.status, "cache_control": actual, "x_robots_tag": robots})

    conditional_results = []
    for path in ["/about/", "/documents/Research_Statement_Mele_Public.pdf"] + private_paths:
        with request(path) as initial:
            etag = initial.headers.get("ETag")
            assert etag, (path, "Missing ETag")
        with request(path, {"If-None-Match": etag}) as revalidation:
            assert revalidation.status in (200, 304), (path, revalidation.status)
            conditional_results.append({"path": path, "status": revalidation.status})

    print(redact_private_text(json.dumps({
        "sha256_assets_verified": len(hashed_paths),
        "headers": results,
        "redirects": redirect_results,
        "conditional_requests": conditional_results,
        "conditional_note": "The local emulator may return 200 for matching ETags; inspect these statuses separately. Header checks do not prove production 304 behavior.",
    }, indent=2)))

except Exception as exc:
    raise SystemExit(redact_private_text(str(exc))) from None
