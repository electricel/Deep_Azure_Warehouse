import argparse
import fnmatch
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / "data"
BACKUP_DIR = ROOT / "backups"
DB_NAME = "inventory.db"
DB_ENCRYPTED_NAME = "inventory.db.enc"

APP_INCLUDE_FILES = {
    ".dockerignore",
    ".env.example",
    "CHANGELOG.md",
    "DOCKER_DEPLOY.md",
    "DATA_SECURITY.md",
    "Dockerfile",
    "HOW_TO_RUN.md",
    "README.md",
    "SERVER_DEPLOY.md",
    "WAREHOUSE_USER_MANUAL.md",
    "app.py",
    "apply_patch.ps1",
    "backup_for_docker.bat",
    "backup_for_docker.py",
    "docker-compose.yml",
    "docker_run.ps1",
    "docker_run.sh",
    "make_patch.py",
    "requirements.txt",
    "run_server.bat",
    "run_windows.bat",
    "sample_bom.csv",
    "start.bat",
    "start_server.ps1",
    "start_server.sh",
    "stop.bat",
    "update_server.sh",
    "update_server.ps1",
}

APP_INCLUDE_DIRS = {
    "static",
    "public",
}

DATA_EXCLUDES = {
    "__pycache__/",
    "*.pyc",
    "*.tmp",
    "*.bak",
    "*.zip",
}


def rel_text(path, base):
    return path.relative_to(base).as_posix()


def match_pattern(rel, pattern):
    rel = rel.replace("\\", "/")
    pattern = pattern.strip().lstrip("/")
    if not pattern:
        return False
    is_dir = pattern.endswith("/")
    clean = pattern.rstrip("/")
    if is_dir:
        return rel == clean or rel.startswith(clean + "/")
    if "/" not in clean:
        return fnmatch.fnmatch(Path(rel).name, clean) or fnmatch.fnmatch(rel, clean)
    return fnmatch.fnmatch(rel, clean)


def excluded(rel, patterns):
    return any(match_pattern(rel, pattern) for pattern in patterns)


def app_files():
    files = []
    for name in sorted(APP_INCLUDE_FILES):
        path = ROOT / name
        if path.is_file():
            files.append(path)
    for dirname in sorted(APP_INCLUDE_DIRS):
        base = ROOT / dirname
        if not base.exists():
            continue
        for path in base.rglob("*"):
            if path.is_file():
                files.append(path)
    return sorted(set(files), key=lambda p: rel_text(p, ROOT))


def sqlite_online_backup(src_db, dst_db):
    dst_db.parent.mkdir(parents=True, exist_ok=True)
    if not src_db.exists():
        return False
    source = sqlite3.connect(f"file:{src_db.as_posix()}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dst_db)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return True


def copy_data_dir(data_dir, staging_data):
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")
    staging_data.mkdir(parents=True, exist_ok=True)
    source_db = data_dir / DB_NAME
    source_encrypted_db = data_dir / DB_ENCRYPTED_NAME
    if source_encrypted_db.exists():
        shutil.copy2(source_encrypted_db, staging_data / DB_ENCRYPTED_NAME)
        db_backed_up = True
    else:
        db_backed_up = sqlite_online_backup(source_db, staging_data / DB_NAME)
    for path in data_dir.rglob("*"):
        if not path.is_file():
            continue
        rel = rel_text(path, data_dir)
        if rel in (DB_NAME, DB_ENCRYPTED_NAME):
            continue
        if excluded(rel, DATA_EXCLUDES):
            continue
        target = staging_data / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return db_backed_up


def write_manifest(staging, data_dir, db_backed_up):
    manifest = {
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_root": str(ROOT),
        "source_data_dir": str(data_dir),
        "database_online_backup": db_backed_up,
        "encrypted_database_supported": True,
        "data_key_required": "If data/inventory.db.enc exists, restore with the same WAREHOUSE_DATA_KEY used by the old server.",
        "layout": {
            "app": "app/",
            "data": "app/data/",
        },
        "restore": [
            "unzip the backup on the new server",
            "cd app",
            "copy .env.example to .env and set WAREHOUSE_ADMIN_PASSWORD and WAREHOUSE_DATA_KEY",
            "docker compose up -d --build",
        ],
    }
    (staging / "BACKUP_MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (staging / "RESTORE_DOCKER.md").write_text(
        "# Docker 迁移包恢复说明\n\n"
        "1. 将本备份 zip 上传到新服务器并解压。\n"
        "2. 进入解压后的 `app/` 目录。\n"
        "3. 复制 `.env.example` 为 `.env`，设置 `WAREHOUSE_ADMIN_PASSWORD` 和原服务器的 `WAREHOUSE_DATA_KEY`。\n"
        "4. 执行 `docker compose up -d --build`。\n"
        "5. 浏览器访问 `http://新服务器IP:8088`。\n\n"
        "真实数据位于 `app/data/`，Docker 会挂载到容器 `/app/data`。\n"
        "如果存在 `app/data/inventory.db.enc`，必须使用旧服务器同一个 `WAREHOUSE_DATA_KEY`，否则无法解密。\n",
        encoding="utf-8",
    )


def make_backup(data_dir, output):
    data_dir = data_dir.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="warehouse_docker_backup_") as temp:
        staging = Path(temp) / "warehouse_docker_backup"
        app_root = staging / "app"
        app_root.mkdir(parents=True, exist_ok=True)
        for path in app_files():
            rel = path.relative_to(ROOT)
            target = app_root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
        db_backed_up = copy_data_dir(data_dir, app_root / "data")
        write_manifest(staging, data_dir, db_backed_up)
        if output.exists():
            output.unlink()
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in staging.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(staging).as_posix())
    return output


def main():
    parser = argparse.ArgumentParser(description="Create a full Docker migration backup with app files and live data.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Current warehouse data directory.")
    parser.add_argument("--output", default="", help="Output backup zip path.")
    args = parser.parse_args()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = Path(args.output) if args.output else BACKUP_DIR / f"warehouse_docker_full_backup_{timestamp}.zip"
    result = make_backup(Path(args.data_dir), output.resolve())
    size_mb = result.stat().st_size / (1024 * 1024)
    print(f"Created: {result}")
    print(f"Size:    {size_mb:.2f} MB")
    print("")
    print("Restore on new server:")
    print("  unzip this backup")
    print("  cd app")
    print("  cp .env.example .env")
    print("  edit .env and set WAREHOUSE_ADMIN_PASSWORD and WAREHOUSE_DATA_KEY")
    print("  docker compose up -d --build")


if __name__ == "__main__":
    main()
