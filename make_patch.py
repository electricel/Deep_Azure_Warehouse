import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
PATCH_DIR = ROOT / "patches"

EXCLUDE_PATTERNS = {
    ".git/",
    ".vscode/",
    ".codex_autopilot/",
    "__pycache__/",
    "node_modules/",
    "remotion/",
    "data/",
    "tmp/",
    "out/",
    "output/",
    "patches/",
    "_patch_backups/",
    "logs/",
    "screenshots/",
    "dist/",
    "build/",
    "*.zip",
    "*.db",
    "*.sqlite",
    "*.sqlite3",
    "*.pyc",
    "server.pid",
    "server_config.json",
    "test_cookies*.txt",
    ".codex_smoke_*.log",
    "analytics_final.html",
    "bom_*.html",
    "lcsc_*.html",
    "szlcsc_home.html",
    "warehouse_inventory_app.zip",
}


def run_git(args):
    return subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def normalize(path):
    return Path(path).as_posix()


def match_pattern(rel, pattern):
    rel = normalize(rel)
    pattern = pattern.strip().lstrip("/")
    if not pattern or pattern.startswith("!"):
        return False
    is_dir = pattern.endswith("/")
    clean = pattern.rstrip("/")
    if is_dir:
        return rel == clean or rel.startswith(clean + "/")
    if "/" not in clean:
        return fnmatch.fnmatch(Path(rel).name, clean) or fnmatch.fnmatch(rel, clean)
    return fnmatch.fnmatch(rel, clean)


def is_excluded(rel):
    return any(match_pattern(rel, pattern) for pattern in EXCLUDE_PATTERNS)


def git_changed_files(include_untracked):
    result = run_git(["status", "--porcelain"])
    if result.returncode != 0:
      raise SystemExit(result.stderr.strip() or "git status failed")
    files = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        status = line[:2]
        raw = line[3:].strip()
        if " -> " in raw:
            raw = raw.split(" -> ", 1)[1]
        raw = raw.strip('"')
        if status == "??" and not include_untracked:
            continue
        if status.strip() == "D":
            continue
        path = ROOT / raw
        if path.is_file() and not is_excluded(raw):
            files.append(normalize(Path(raw)))
    return sorted(set(files))


def explicit_files(paths):
    files = []
    for item in paths:
        path = ROOT / item
        if path.is_dir():
            for child in path.rglob("*"):
                if child.is_file():
                    rel = normalize(child.relative_to(ROOT))
                    if not is_excluded(rel):
                        files.append(rel)
        elif path.is_file():
            rel = normalize(path.relative_to(ROOT))
            if not is_excluded(rel):
                files.append(rel)
        else:
            raise SystemExit(f"Patch path not found: {item}")
    return sorted(set(files))


def file_sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description="Create a Warehouse hot patch zip from changed files.")
    parser.add_argument("--name", default="", help="Patch name suffix.")
    parser.add_argument("--all", action="store_true", help="Include untracked files from git status.")
    parser.add_argument("--files", nargs="*", default=[], help="Explicit files or directories to include.")
    args = parser.parse_args()

    files = explicit_files(args.files) if args.files else git_changed_files(include_untracked=args.all)
    if not files:
        raise SystemExit("No patchable files found. Use --all for untracked files or --files path1 path2.")

    PATCH_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = ("_" + "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in args.name.strip())) if args.name else ""
    out = PATCH_DIR / f"warehouse_patch_{stamp}{suffix}.zip"

    manifest_files = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            source = ROOT / rel
            manifest_files.append({"path": rel, "sha256": file_sha256(source), "bytes": source.stat().st_size})
            zf.write(source, rel)
        manifest = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "files": manifest_files,
            "restart": True,
            "health_path": "/login",
        }
        zf.writestr("PATCH_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))

    print(f"Created: {out}")
    print(f"Files:   {len(files)}")
    for rel in files:
        print(f"  - {rel}")


if __name__ == "__main__":
    main()
