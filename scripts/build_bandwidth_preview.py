#!/usr/bin/env python3
"""Build a production preview without changing public/ or deploying.

Keep previously published URLs, but take PDF bytes from current source files.
The same helper is used locally and by GitHub Actions; no output/ backups are used.
"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

from private_documents import find_private_documents, redact_private_text

ROOT = Path(__file__).resolve().parent.parent
HUGO_VERSION = "0.107.0"


def inventory(root):
    return {p.relative_to(root).as_posix(): p for p in root.rglob("*")
            if p.is_file() and not any(part.startswith(".") for part in p.relative_to(root).parts)}


def document_sources(root):
    """Hugo lowercases resource URLs; static files take precedence over assets."""
    sources = {}
    for directory in (root / "assets/documents", root / "static/documents"):
        for file in sorted(directory.glob("*.pdf")):
            sources[file.name.casefold()] = file
    return sources


def build_site(destination, root=ROOT, public_only=False):
    root = root.resolve()
    destination = destination.resolve()
    if destination == root or destination in root.parents or root in destination.parents:
        raise ValueError("The preview destination must be outside the repository.")
    version = subprocess.check_output(["hugo", "version"], text=True)
    if not re.search(r"\bv" + re.escape(HUGO_VERSION) + r"\+extended\b", version):
        raise RuntimeError(f"Use Hugo extended {HUGO_VERSION}; found {version.strip()}")

    public = root / "public"
    baseline = inventory(public)
    if public_only:
        private_paths = {p.relative_to(public).as_posix() for p in find_private_documents(public)}
        baseline = {name: file for name, file in baseline.items() if name not in private_paths}
    if not baseline:
        raise RuntimeError("The public/ compatibility baseline is missing.")
    destination.mkdir(parents=True, exist_ok=True)
    for name, source in baseline.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    with tempfile.TemporaryDirectory(prefix="mele-hugo-cache-") as cache:
        subprocess.run([
            "hugo", "--environment", "production", "--destination", str(destination),
            "--cacheDir", cache,
        ], cwd=root, check=True)

    if public_only:
        for private_file in find_private_documents(destination):
            private_file.unlink()

    # Update every published spelling of a source PDF, including old unreferenced
    # URLs. On Linux both TQC_talk_Noise.pdf and TQC_talk_noise.pdf can coexist;
    # neither may retain the old PDF simply because Hugo generated the other one.
    sources = document_sources(root)
    for name, target in inventory(destination).items():
        if name.startswith("documents/") and name.lower().endswith(".pdf"):
            source = sources.get(target.name.casefold())
            if source:
                shutil.copy2(source, target)

    # On macOS, writing a lower-case URL can retain a different on-disk spelling.
    # Check exact names, not Path.exists(), and preserve the published spelling.
    actual = inventory(destination)
    for name in baseline.keys() - actual.keys():
        variants = [p for key, p in actual.items() if key.casefold() == name.casefold()]
        if len(variants) != 1:
            raise RuntimeError(redact_private_text(f"Missing or ambiguous historical URL: /{name}"))
        target = destination / name
        if target.exists():  # Case-insensitive filesystem: rename via a temporary name.
            intermediate = variants[0].with_name(variants[0].name + ".case-rename")
            variants[0].rename(intermediate)
            intermediate.rename(target)
        else:
            shutil.copy2(variants[0], target)
    missing = sorted(baseline.keys() - inventory(destination).keys())
    if missing:
        raise RuntimeError(redact_private_text(f"Missing published file URLs: {missing}"))
    return {"preview_directory": str(destination), "preserved_file_urls": len(baseline)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path,
                        default=Path(tempfile.gettempdir()) / "mele-bandwidth-preview")
    parser.add_argument("--public-only", action="store_true",
                        help="Build public content without private documents for untrusted checks.")
    args = parser.parse_args()
    print(json.dumps(build_site(args.destination, public_only=args.public_only), indent=2))


if __name__ == "__main__":
    main()
