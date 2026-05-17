import fnmatch
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
OUT = ROOT / "warehouse_inventory_app.zip"

ALWAYS_INCLUDE_FILES = {
    "app.py",
    "CHANGELOG.md",
    "README.md",
    "HOW_TO_RUN.md",
    "SERVER_DEPLOY.md",
    "WAREHOUSE_USER_MANUAL.md",
    "DOCKER_DEPLOY.md",
    "DATA_SECURITY.md",
    "Dockerfile",
    ".dockerignore",
    "docker-compose.yml",
    ".env.example",
    "requirements.txt",
    "docker_run.sh",
    "docker_run.ps1",
    "apply_patch.ps1",
    "backup_for_docker.bat",
    "backup_for_docker.py",
    "configure_env.ps1",
    "make_patch.py",
    "run_windows.bat",
    "start.bat",
    "run_server.bat",
    "start_server.sh",
    "start_server.ps1",
    "stop.bat",
    "update_server.sh",
    "update_server.ps1",
    "sample_bom.csv",
}

ALWAYS_INCLUDE_DIRS = {
    "static",
    "public",
}

DEFAULT_EXCLUDES = {
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
    "backups/",
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


def load_gitignore_patterns():
    patterns = []
    path = ROOT / ".gitignore"
    if not path.exists():
        return patterns
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        patterns.append(line)
    return patterns


def normalize(path):
    return path.as_posix()


def match_pattern(rel, pattern):
    rel = normalize(rel)
    pattern = pattern.strip()
    if not pattern:
        return False
    if pattern.startswith("!"):
        return False
    pattern = pattern.lstrip("/")
    is_dir_pattern = pattern.endswith("/")
    clean = pattern.rstrip("/")
    if is_dir_pattern:
        return rel == clean or rel.startswith(clean + "/")
    if "/" not in clean:
        return fnmatch.fnmatch(Path(rel).name, clean) or fnmatch.fnmatch(rel, clean)
    return fnmatch.fnmatch(rel, clean)


def is_excluded(rel, patterns):
    return any(match_pattern(rel, pattern) for pattern in patterns)


def should_include(path, patterns):
    rel = path.relative_to(ROOT)
    rel_text = normalize(rel)
    if path.is_dir():
        return False
    if rel_text in ALWAYS_INCLUDE_FILES:
        return True
    if rel.parts and rel.parts[0] in ALWAYS_INCLUDE_DIRS:
        return not is_excluded(rel, patterns)
    return False


def collect_files():
    patterns = [*DEFAULT_EXCLUDES, *load_gitignore_patterns()]
    files = []
    for path in ROOT.rglob("*"):
        if should_include(path, patterns):
            files.append(path)
    return sorted(set(files), key=lambda item: normalize(item.relative_to(ROOT)))


def main():
    if OUT.exists():
        OUT.unlink()
    files = collect_files()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in files:
            zf.write(path, normalize(path.relative_to(ROOT)))
    size_mb = OUT.stat().st_size / (1024 * 1024)
    print(f"Created: {OUT}")
    print(f"Files:   {len(files)}")
    print(f"Size:    {size_mb:.2f} MB")
    print("")
    print("Included top-level files:")
    for item in sorted(ALWAYS_INCLUDE_FILES):
        if (ROOT / item).exists():
            print(f"  - {item}")
    print("")
    print("Run on server:")
    print("  Windows: run_windows.bat")
    print("  PowerShell: powershell -ExecutionPolicy Bypass -File .\\start_server.ps1")


if __name__ == "__main__":
    main()
