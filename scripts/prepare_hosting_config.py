#!/usr/bin/env python3
"""Write a separate Firebase config for already-built content; never deploy."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile


ROOT = Path(__file__).resolve().parent.parent


def file_hashes(root):
    hashes = {}
    for file in root.rglob("*"):
        if file.is_file():
            digest = hashlib.sha256()
            with file.open("rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            hashes[file.relative_to(root).as_posix()] = digest.hexdigest()
    return hashes


def prepare_config(root, output, emulator=False, source_root=ROOT):
    root, output = root.resolve(), output.resolve()
    if output == source_root.resolve() / "firebase.json":
        raise ValueError("Use a separate config; the production firebase.json is never overwritten.")
    if not (root / "index.html").is_file():
        raise ValueError("Build the content directory before generating its Hosting config.")
    if output.parent == root or root in output.parent.parents:
        raise ValueError("The generated config must be outside the published content directory.")

    config = json.loads((source_root / "firebase.json").read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.parent not in root.parents:
        # The emulator permits ../public paths that production deployment rejects.
        # Copy into a unique, owned child directory; never replace a user's folder.
        expected = file_hashes(root)
        published = Path(tempfile.mkdtemp(prefix="hosting-content-", dir=output.parent))
        shutil.copytree(root, published, dirs_exist_ok=True)
        if file_hashes(published) != expected:
            raise ValueError("Copied Hosting content differs from the reviewed build.")
    else:
        published = root
    config["hosting"]["public"] = published.relative_to(output.parent).as_posix()
    if emulator:
        config["emulators"] = {
            "hosting": {"host": "127.0.0.1", "port": 1341},
            "hub": {"host": "127.0.0.1", "port": 4411},
            "logging": {"host": "127.0.0.1", "port": 4511},
            "ui": {"enabled": False},
            "singleProjectMode": True,
        }
    output.write_text(json.dumps(config, indent=2) + "\n")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--emulator", action="store_true")
    args = parser.parse_args()
    print(prepare_config(args.root, args.output, args.emulator))


if __name__ == "__main__":
    main()
