#!/usr/bin/env python3
"""Build a release archive from the committed tree.

Usage: python3 scripts/release.py <out_dir>
Writes <out_dir>/wavy-os-<version>.zip (from `git archive HEAD`, so only committed files ship),
a SHA-256 checksum, and a copy of README.md. Refuses to run with uncommitted changes, and fails
if the archive contains anything that shouldn't leave the building: internal review material,
local data, keys, or a client name.
"""
import hashlib
import re
import shutil
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE = ("review/", "var/", ".claude/", "scripts/release.py")
BLOCKLIST = re.compile(rb"atrakt|max.?muscle|exhale|budpop|andrewlauchner|wos_[A-Za-z0-9_-]{20,}|pk_[A-Za-z0-9]{20,}", re.I)


def main(out_dir: str) -> int:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    dirty = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    if dirty:
        print("commit or stash your changes first:\n" + dirty)
        return 1
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = out / f"wavy-os-{version}.raw.zip"
    subprocess.run(["git", "archive", "--format=zip", f"--prefix=wavy-os-{version}/", "-o", str(raw), "HEAD"], cwd=ROOT, check=True)
    final = out / f"wavy-os-{version}.zip"
    problems = []
    with zipfile.ZipFile(raw) as src, zipfile.ZipFile(final, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            rel = info.filename.split("/", 1)[1] if "/" in info.filename else ""
            if any(rel.startswith(e) for e in EXCLUDE):
                continue
            data = src.read(info)
            if BLOCKLIST.search(data):
                problems.append(rel)
            dst.writestr(info, data)
    raw.unlink()
    if problems:
        final.unlink()
        print("refusing to release; these files contain blocked content:\n  " + "\n  ".join(problems))
        return 2
    digest = hashlib.sha256(final.read_bytes()).hexdigest()
    (out / f"wavy-os-{version}.zip.sha256").write_text(f"{digest}  {final.name}\n")
    shutil.copy(ROOT / "README.md", out / "README.md")
    with zipfile.ZipFile(final) as z:
        n = len(z.infolist())
    print(f"{final} ({final.stat().st_size // 1024} KB, {n} files)\nsha256 {digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "dist"))
