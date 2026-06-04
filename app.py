import base64
import csv
import hashlib
import hmac
import html
import ipaddress
import io
import json
import mimetypes
import os
import re
import secrets
import shutil
import signal
import socket
import sqlite3
import subprocess
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zipfile
import xml.etree.ElementTree as ET
import warehouse_bom as bom_tools
from warehouse_epro_converter import process_epro_upload
from warehouse_bom import (
    COMMON_PACKAGE_CODES,
    PACKAGE_SIZE_CODES,
    component_value_aliases,
    component_value_key,
    extract_lcsc_codes,
    infer_category,
    normalize_key,
    normalized_header,
    split_designators,
)

try:
    from warehouse_interactive_bom import build_interactive_bom_payload as _build_interactive_bom_payload
except ImportError:
    _build_interactive_bom_payload = None

from contextlib import nullcontext
from datetime import datetime, timedelta
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # pragma: no cover - deployment dependency check
    AESGCM = None


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CONFIG_PATH = BASE_DIR / "server_config.json"
CHANGELOG_PATH = BASE_DIR / "CHANGELOG.md"
ENV_PATH = BASE_DIR / ".env"


def load_env_file(path=ENV_PATH):
    path = Path(path)
    if not path.exists():
        return
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except Exception:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if not os.environ.get(key):
            os.environ[key] = value


load_env_file()

DEFAULT_CONFIG = {
    "site_name": "Warehouse Inventory Server",
    "host": "127.0.0.1",
    "port": "8088",
    "data_dir": str(BASE_DIR / "data"),
    "public_url": "",
    "tunnel_mode": "off",
    "allow_registration": "0",
    "weekly_report_day": "Sunday",
    "low_stock_threshold": "0",
    "hot_search_threshold": "3",
    "hot_demand_threshold": "50",
    "ui_accent_start": "#0a84ff",
    "ui_accent_end": "#18a47d",
    "ui_glass_opacity": "0.58",
    "ui_glass_blur": "30",
    "ui_panel_radius": "14",
    "ui_gradient_angle": "135",
    "ui_product_depth": "220",
    "ui_product_tilt": "10",
    "ui_product_speed": "22",
    "allowed_hosts": "",
    "allowed_api_hosts": "",
}


def load_boot_config():
    config = DEFAULT_CONFIG.copy()
    if CONFIG_PATH.exists():
        try:
            config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return config


BOOT_CONFIG = load_boot_config()
DATA_DIR = Path(os.environ.get("WAREHOUSE_DATA_DIR", BOOT_CONFIG["data_dir"])).resolve()
LOGS_DIR = BASE_DIR / "logs"
SERVER_ERRORS_LOG = LOGS_DIR / "server_errors.log"
DB_PATH = DATA_DIR / "inventory.db"
DB_ENCRYPTED_PATH = DATA_DIR / "inventory.db.enc"
FORMS_DIR = DATA_DIR / "forms"
REPORTS_DIR = DATA_DIR / "reports"
UPLOADS_DIR = DATA_DIR / "uploads"
PCB_UPLOADS_DIR = DATA_DIR / "pcb_uploads"
PURCHASES_DIR = DATA_DIR / "purchase_orders"
LCSC_DIR = DATA_DIR / "lcsc"
USER_KNOWLEDGE_DIR = DATA_DIR / "user_knowledge"
WORKFLOW_UPLOADS_DIR = DATA_DIR / "workflow_uploads"
COMPETITION_MATERIALS_DIR = DATA_DIR / "competition_materials"
LCSC_CACHE_JSON = LCSC_DIR / "lcsc_products.json"
LCSC_CACHE_CSV = LCSC_DIR / "lcsc_products.csv"
LCSC_CATEGORIES_JSON = LCSC_DIR / "lcsc_categories.json"
EASYEDA_COMPONENT_API_VERSION = "6.4.19.5"
EASYEDA_STEP_BASE_URL = "https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y"
EASYEDA_OBJ_BASE_URL = "https://modules.easyeda.com/3dmodel"
EASYEDA_EDITOR_URL = "https://easyeda.com/editor"
ASSISTANT_CONFIG_JSON = DATA_DIR / "assistant_config.json"
IMAGE_RECOGNITION_CONFIG_JSON = DATA_DIR / "image_recognition_config.json"
BOMS_JSON = DATA_DIR / "boms.json"
PCB_KEYFRAMES_JSON = DATA_DIR / "pcb_keyframes.json"
CSV_PATH = FORMS_DIR / "warehouse_records.csv"
HOST = os.environ.get("WAREHOUSE_HOST", BOOT_CONFIG.get("host", "127.0.0.1"))
PORT = int(os.environ.get("WAREHOUSE_PORT", BOOT_CONFIG["port"]))
DEFAULT_ADMIN = os.environ.get("WAREHOUSE_ADMIN_USER", "admin")
DEFAULT_PASSWORD = os.environ.get("WAREHOUSE_ADMIN_PASSWORD", "admin123")
ADMIN_PASSWORD_FROM_ENV = bool(os.environ.get("WAREHOUSE_ADMIN_PASSWORD"))
APP_ENV = os.environ.get("WAREHOUSE_ENV", os.environ.get("APP_ENV", "development")).strip().lower()
DEFAULT_PASSWORD_IN_USE = DEFAULT_ADMIN == "admin" and DEFAULT_PASSWORD == "admin123"
INSECURE_ADMIN_PASSWORDS = {
    "admin",
    "admin123",
    "change-this-strong-password",
    "letmein",
    "password",
    "qwerty",
    "root",
    "123456",
    "12345678",
}
DATA_KEY_PLACEHOLDERS = {"change-this-32-byte-data-encryption-key", "replace-with-generated-secret"}
DATA_ENCRYPTION_ENABLED = os.environ.get("WAREHOUSE_DATA_ENCRYPTION", "1").strip().lower() not in {
    "0",
    "false",
    "off",
    "no",
}
DATA_KEY_ENV = "WAREHOUSE_DATA_KEY"
ENCRYPTED_PAYLOAD_MAGIC = b"WHSENC1\x00"
ENCRYPTED_TEXT_PREFIX = "enc:v1:"
DATA_ENCRYPTION_AAD_PREFIX = b"warehouse-data:"
ENCRYPTED_DATA_PATHS = (
    ASSISTANT_CONFIG_JSON,
    IMAGE_RECOGNITION_CONFIG_JSON,
    BOMS_JSON,
    PCB_KEYFRAMES_JSON,
    LCSC_CACHE_JSON,
    LCSC_CATEGORIES_JSON,
)

SESSIONS = {}
SESSIONS_LOCK = threading.RLock()
SESSION_TTL_SECONDS = 8 * 60 * 60
LOGIN_FAILURES = {}
LOGIN_FAILURES_LOCK = threading.RLock()
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60
LOGIN_LOCKOUT_SECONDS = 5 * 60
LOGIN_MAX_FAILURES = 8
PASSWORD_HASH_ITERATIONS = 600000
CSRF_COOKIE_NAME = "csrf_token"
CSRF_FIELD_NAME = "csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_TOKEN_TTL_SECONDS = SESSION_TTL_SECONDS
PCB_PREVIEW_TOKEN_TTL_SECONDS = 60 * 60
MIN_USER_PASSWORD_LENGTH = 10
MIN_ADMIN_PASSWORD_LENGTH = 14
MAX_FORM_BODY_BYTES = 1 * 1024 * 1024
MAX_MULTIPART_BODY_BYTES = 100 * 1024 * 1024
BOM_UPLOAD_MAX_BYTES = 12 * 1024 * 1024
PCB_UPLOAD_MAX_BYTES = 80 * 1024 * 1024
INVENTORY_SEARCH_RESULT_LIMIT = 120
INVENTORY_SEARCH_MIN_TOKEN_CHARS = 2
TUNNEL_URL = None
TUNNEL_PROCESS = None
STOP_EVENT = threading.Event()
BOMS_LOCK = threading.RLock()
SOLDERING_ACTIVE_STATUSES = ("active",)
SOLDERING_COMPLETED_STATUS = "completed"
ANALYSIS_JOB_TASK_TYPE = "schematic_pdf_analysis"
ANALYSIS_JOB_STATUSES = ("queued", "running", "completed", "failed")
ANALYSIS_JOB_ACTIVE_STATUSES = ("queued", "running")
ANALYSIS_ERROR_SUMMARY_MAX = 240
ANALYSIS_HISTORY_DEFAULT_LIMIT = 25
ANALYSIS_HISTORY_MAX_LIMIT = 100
ANALYSIS_HISTORY_CANDIDATE_LIMIT = 1000
ANALYSIS_WORKBENCH_HISTORY_LIMIT = 5
PDF_ANALYSIS_TEXT_CHAR_LIMIT = 200000
PDF_ANALYSIS_MAX_DETECTED_ITEMS = 8
ANALYSIS_WORKER_DEFAULT_ENABLED = "0"
ANALYSIS_WORKER_DEFAULT_INTERVAL_SECONDS = 120
ANALYSIS_WORKER_MIN_INTERVAL_SECONDS = 30
ANALYSIS_WORKER_MAX_INTERVAL_SECONDS = 3600
ANALYSIS_WORKER_DEFAULT_BATCH_LIMIT = 1
ANALYSIS_WORKER_MIN_BATCH_LIMIT = 1
ANALYSIS_WORKER_MAX_BATCH_LIMIT = 5
ANALYSIS_WORKER_SETTING_DEFAULTS = {
    "analysis_worker_enabled": ANALYSIS_WORKER_DEFAULT_ENABLED,
    "analysis_worker_interval_seconds": str(ANALYSIS_WORKER_DEFAULT_INTERVAL_SECONDS),
    "analysis_worker_batch_limit": str(ANALYSIS_WORKER_DEFAULT_BATCH_LIMIT),
}
ANALYSIS_WORKER_THREAD = None
ANALYSIS_WORKER_WAKE_EVENT = threading.Event()
ANALYSIS_WORKER_LOCK = threading.Lock()
ANALYSIS_WORKER_STATE = {
    "started": False,
    "started_at": "",
    "running": False,
    "last_tick_at": "",
    "last_processed_count": 0,
    "last_error_summary": "",
    "last_status": "not_started",
}
PCB_UPLOAD_FIELD_NAMES = ("pcb_file", "file", "files")
PCB_PDF_EXTENSIONS = {".pdf"}
PCB_ALLOWED_EXTENSIONS = {
    ".zip",
    ".gbr",
    ".ger",
    ".gtl",
    ".gbl",
    ".gts",
    ".gbs",
    ".gto",
    ".gbo",
    ".gm1",
    ".drl",
    ".pcb",
    ".kicad_pcb",
    ".brd",
    ".fbrd",
    ".html",
    ".htm",
    ".pdf",
    ".json",
    ".txt",
    ".csv",
    ".epro",
}
PCB_GERBER_EXTENSIONS = {".gbr", ".ger", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gm1", ".drl"}
PCB_KIND_LABELS = {
    "gerber_archive": "Gerber 压缩包",
    "gerber_file": "Gerber 文件",
    "pcb_file": "PCB 版图",
    "html_assistant": "HTML 预览",
    "schematic_pdf": "PDF 原理图",
    "support_file": "辅助文件",
}
PCB_FILE_ROLE_DEFAULTS = {
    "gerber_archive": "pcb_fabrication",
    "gerber_file": "pcb_fabrication",
    "pcb_file": "pcb_layout",
    "html_assistant": "pcb_preview",
    "schematic_pdf": "schematic",
    "support_file": "support",
}
PCB_DOCUMENT_TYPE_DEFAULTS = {
    "gerber_archive": "gerber_archive",
    "gerber_file": "gerber_file",
    "pcb_file": "pcb_layout",
    "html_assistant": "html_assistant",
    "schematic_pdf": "schematic_pdf",
    "support_file": "support_file",
}
TEAM_GROUPS = ("机械组", "电控组", "硬件组", "视觉组", "运营组")
WORKFLOW_FILE_FIELDS = ("project_file", "result_file", "files", "file")
WORKFLOW_STAGE_COLORS = ("#0a84ff", "#18c7b6", "#ffb020", "#8b5cf6", "#ef5da8", "#22c55e")
WORKFLOW_STATUS_LABELS = {
    "planned": "计划中",
    "active": "进行中",
    "blocked": "受阻",
    "review": "待复核",
    "done": "已完成",
}

DEFAULT_ASSISTANT_CONFIG = {
    "enabled": "0",
    "endpoint": "",
    "model": "",
    "api_key": "",
    "temperature": "0.2",
    "max_tokens": "900",
    "timeout": "30",
    "knowledge_depth": "inventory_core",
    "system_prompt": (
        "你是这个仓库系统的专属库存智能助手。只回答与当前库存、BOM 缺口、"
        "器件功能、封装、替代料、数据手册研读和电子设计知识网络有关的问题。"
        "不要泛化成通用聊天机器人。回答必须优先引用后端提供的库存摘要、LCSC编号、"
        "BOM 缺口和器件关系；如果库存里没有依据，需要明确说明缺少依据。"
    ),
}

DATA_KEY_CACHE = None
DATA_KEY_LOCK = threading.RLock()
ENCRYPTED_DB_LOCK = threading.RLock()
DB_THREAD_STATE = threading.local()


def now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def short_date():
    return datetime.now().strftime("%Y-%m-%d")


def split_config_list(value):
    parts = re.split(r"[\s,;]+", str(value or ""))
    return [part.strip().lower() for part in parts if part.strip()]


def normalize_host_value(value):
    host = str(value or "").strip().lower()
    if not host:
        return ""
    if host.startswith("[") and "]" in host:
        host = host[1 : host.index("]")]
    elif host.count(":") > 1:
        return host.rstrip(".")
    else:
        host = host.split(":", 1)[0]
    return host.rstrip(".")


def allowed_origin_for_host(host, scheme):
    raw = str(host or "").strip().lower()
    if not raw:
        return ""
    if "://" in raw:
        parsed = urllib.parse.urlparse(raw)
        if parsed.scheme and parsed.netloc:
            return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    parsed = urllib.parse.urlparse(f"//{raw}")
    netloc = parsed.netloc.lower()
    if not netloc:
        normalized = normalize_host_value(raw)
        return f"{scheme}://{normalized}" if normalized else ""
    return f"{scheme}://{netloc}"


def configured_allowed_hosts():
    values = split_config_list(os.environ.get("WAREHOUSE_ALLOWED_HOSTS", ""))
    values.extend(split_config_list(BOOT_CONFIG.get("allowed_hosts", "")))
    public_url = str(BOOT_CONFIG.get("public_url") or "").strip()
    if public_url:
        parsed = urllib.parse.urlparse(public_url)
        if parsed.hostname:
            values.append(parsed.hostname.lower())
    if TUNNEL_URL:
        parsed = urllib.parse.urlparse(TUNNEL_URL)
        if parsed.hostname:
            values.append(parsed.hostname.lower())
    local_hosts = ["localhost", "127.0.0.1", "::1"]
    bind_host = normalize_host_value(HOST)
    if bind_host and bind_host not in ("0.0.0.0", "::"):
        local_hosts.append(bind_host)
    elif HOST in ("0.0.0.0", "::"):
        local_hosts.append("__private_network__")
        try:
            local_hosts.append(local_ip())
        except Exception:
            pass
    try:
        local_hosts.extend([socket.gethostname(), socket.getfqdn()])
    except Exception:
        pass
    return {normalize_host_value(item) for item in [*values, *local_hosts] if normalize_host_value(item)}


def configured_allowed_origins():
    origins = set()
    values = split_config_list(os.environ.get("WAREHOUSE_ALLOWED_HOSTS", ""))
    values.extend(split_config_list(BOOT_CONFIG.get("allowed_hosts", "")))
    host_values = [*values, "localhost", "127.0.0.1", "[::1]"]
    bind_host = normalize_host_value(HOST)
    if bind_host and bind_host not in ("0.0.0.0", "::"):
        host_values.append(HOST)
    elif HOST in ("0.0.0.0", "::"):
        try:
            host_values.append(local_ip())
        except Exception:
            pass
    try:
        host_values.extend([socket.gethostname(), socket.getfqdn()])
    except Exception:
        pass
    for host in host_values:
        if not host or str(host).startswith("*.") or str(host) == "*":
            continue
        raw = str(host).strip()
        has_port = bool(urllib.parse.urlparse(f"//{raw}").port)
        for scheme in ("http", "https"):
            origin = allowed_origin_for_host(raw, scheme)
            if origin:
                origins.add(origin)
            if not has_port and str(raw).strip("[]") in {"localhost", "127.0.0.1", "::1", HOST, bind_host}:
                origins.add(f"{scheme}://{normalize_host_value(raw)}:{PORT}")
    return origins


def host_matches_allowed(host, allowed_hosts):
    normalized = normalize_host_value(host)
    if not normalized:
        return False
    for allowed in allowed_hosts:
        allowed = normalize_host_value(allowed)
        if not allowed:
            continue
        if allowed == "*":
            return True
        if allowed == "__private_network__":
            try:
                ip = ipaddress.ip_address(normalized)
                if ip.is_private or ip.is_loopback or ip.is_link_local:
                    return True
            except ValueError:
                pass
        if allowed.startswith("*.") and normalized.endswith(allowed[1:]):
            return True
        if normalized == allowed:
            return True
    return False


def allowed_external_api_hosts():
    raw = os.environ.get("WAREHOUSE_ALLOWED_API_HOSTS") or BOOT_CONFIG.get("allowed_api_hosts", "")
    return {normalize_host_value(item) for item in split_config_list(raw)}


def endpoint_host_is_allowed(host):
    allowed = allowed_external_api_hosts()
    if not allowed:
        return True
    return host_matches_allowed(host, allowed)


def ip_is_forbidden_outbound(ip_text):
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_external_api_url(url):
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("API 地址无效，只允许 http 或 https。")
    if parsed.username or parsed.password:
        raise ValueError("API 地址不能包含用户名或密码。")
    if not endpoint_host_is_allowed(parsed.hostname):
        raise ValueError("API 地址不在服务器允许的外部域名白名单内。")
    try:
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("API 地址域名无法解析。") from exc
    addresses = {info[4][0] for info in infos if info and info[4]}
    if not addresses:
        raise ValueError("API 地址域名无法解析。")
    if any(ip_is_forbidden_outbound(address) for address in addresses):
        raise ValueError("API 地址解析到内网、回环或保留地址，已阻止。")
    return url


class SafeExternalRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_external_api_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def safe_external_urlopen(request, timeout=30):
    url = request.full_url if isinstance(request, urllib.request.Request) else str(request or "")
    validate_external_api_url(url)
    opener = urllib.request.build_opener(SafeExternalRedirectHandler)
    return opener.open(request, timeout=timeout)


def security_error_message(exc=None):
    return "请求未通过安全校验，请检查来源、权限或后台配置。"


def public_error_message(exc=None, fallback="操作失败，请检查输入后重试。"):
    if isinstance(exc, (ValueError, LookupError, OverflowError, PermissionError)):
        return str(exc)
    return fallback


def password_policy_error(password, role="user", username=""):
    password = str(password or "")
    role = str(role or "user").lower()
    min_len = MIN_ADMIN_PASSWORD_LENGTH if role in ("admin", "superadmin") else MIN_USER_PASSWORD_LENGTH
    if len(password) < min_len:
        return f"密码至少需要 {min_len} 位。"
    lowered = password.lower()
    if lowered in INSECURE_ADMIN_PASSWORDS or lowered == str(username or "").strip().lower():
        return "密码过于常见或与账号相同。"
    if re.fullmatch(r"\d+", password) or re.fullmatch(r"[A-Za-z]+", password):
        return "密码不能只包含数字或只包含字母。"
    if "123456" in lowered or "password" in lowered or "qwerty" in lowered:
        return "密码包含常见弱口令片段。"
    return ""


def ensure_dirs():
    for folder in (
        DATA_DIR,
        FORMS_DIR,
        REPORTS_DIR,
        UPLOADS_DIR,
        PCB_UPLOADS_DIR,
        PURCHASES_DIR,
        LCSC_DIR,
        USER_KNOWLEDGE_DIR,
        WORKFLOW_UPLOADS_DIR,
        COMPETITION_MATERIALS_DIR,
    ):
        folder.mkdir(parents=True, exist_ok=True)


def _decode_configured_data_key(value):
    raw = str(value or "").strip()
    if not raw:
        return None
    if raw in DATA_KEY_PLACEHOLDERS:
        return None
    candidates = [raw]
    if raw.startswith("base64:"):
        candidates = [raw.removeprefix("base64:")]
    for candidate in candidates:
        try:
            decoded = base64.urlsafe_b64decode(candidate + "=" * (-len(candidate) % 4))
            if len(decoded) == 32:
                return decoded
        except Exception:
            pass
    return hashlib.sha256(raw.encode("utf-8")).digest()


def data_encryption_key(required=False):
    global DATA_KEY_CACHE
    with DATA_KEY_LOCK:
        if DATA_KEY_CACHE is None:
            DATA_KEY_CACHE = _decode_configured_data_key(os.environ.get(DATA_KEY_ENV, ""))
        if required and DATA_ENCRYPTION_ENABLED and DATA_KEY_CACHE is None:
            raise RuntimeError(
                f"{DATA_KEY_ENV} is required because WAREHOUSE_DATA_ENCRYPTION is enabled. "
                "Generate one once and keep it outside the data directory."
            )
        return DATA_KEY_CACHE


def ensure_crypto_ready():
    if DATA_ENCRYPTION_ENABLED and AESGCM is None:
        raise RuntimeError("cryptography is required when WAREHOUSE_DATA_ENCRYPTION is enabled.")
    if DATA_ENCRYPTION_ENABLED:
        data_encryption_key(required=True)


def admin_password_is_insecure(password):
    return str(password or "").strip().lower() in INSECURE_ADMIN_PASSWORDS


def enforce_production_secrets():
    if APP_ENV not in ("production", "prod"):
        return
    if admin_password_is_insecure(DEFAULT_PASSWORD):
        raise RuntimeError(
            "WAREHOUSE_ADMIN_PASSWORD is insecure or still uses a default value. "
            "Set a private strong password before production start."
        )
    if DATA_ENCRYPTION_ENABLED:
        data_encryption_key(required=True)


def encryption_aad(label):
    return DATA_ENCRYPTION_AAD_PREFIX + str(label or "").encode("utf-8")


def encrypt_bytes(payload, label):
    key = data_encryption_key(required=True)
    nonce = secrets.token_bytes(12)
    encrypted = AESGCM(key).encrypt(nonce, bytes(payload or b""), encryption_aad(label))
    return ENCRYPTED_PAYLOAD_MAGIC + nonce + encrypted


def decrypt_bytes(payload, label):
    data = bytes(payload or b"")
    if not data.startswith(ENCRYPTED_PAYLOAD_MAGIC):
        return data
    start = len(ENCRYPTED_PAYLOAD_MAGIC)
    nonce = data[start : start + 12]
    encrypted = data[start + 12 :]
    if len(nonce) != 12 or not encrypted:
        raise ValueError("Encrypted payload is truncated.")
    key = data_encryption_key(required=True)
    return AESGCM(key).decrypt(nonce, encrypted, encryption_aad(label))


def is_encrypted_payload(payload):
    return bytes(payload or b"").startswith(ENCRYPTED_PAYLOAD_MAGIC)


def encrypt_text(value, label):
    if value in (None, ""):
        return ""
    encrypted = encrypt_bytes(str(value).encode("utf-8"), label)
    return ENCRYPTED_TEXT_PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")


def decrypt_text(value, label):
    text = str(value or "")
    if not text.startswith(ENCRYPTED_TEXT_PREFIX):
        return text
    payload = base64.urlsafe_b64decode(text.removeprefix(ENCRYPTED_TEXT_PREFIX).encode("ascii"))
    return decrypt_bytes(payload, label).decode("utf-8")


def encrypted_label_for_path(path):
    target = Path(path)
    name = target.name
    if name.endswith(".tmp"):
        target = target.with_name(name[:-4])
    try:
        return target.resolve().relative_to(DATA_DIR.resolve()).as_posix()
    except Exception:
        return target.name


def read_data_bytes(path):
    with ENCRYPTED_DB_LOCK:
        payload = Path(path).read_bytes()
        if DATA_ENCRYPTION_ENABLED and is_encrypted_payload(payload):
            return decrypt_bytes(payload, encrypted_label_for_path(path))
        return payload


def write_data_bytes(path, payload):
    with ENCRYPTED_DB_LOCK:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = bytes(payload or b"")
        if DATA_ENCRYPTION_ENABLED:
            data = encrypt_bytes(data, encrypted_label_for_path(target))
        target.write_bytes(data)
        mark_data_dirty()


def atomic_write_data_bytes(path, payload):
    with ENCRYPTED_DB_LOCK:
        target = Path(path)
        tmp = target.with_name(target.name + ".tmp")
        write_data_bytes(tmp, payload)
        tmp.replace(target)
        mark_data_dirty()


def atomic_write_raw_bytes(path, payload):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_bytes(bytes(payload or b""))
    tmp.replace(target)


def read_data_text(path, encoding="utf-8"):
    return read_data_bytes(path).decode(encoding)


def write_data_text(path, text, encoding="utf-8"):
    write_data_bytes(path, str(text).encode(encoding))


def atomic_write_data_text(path, text, encoding="utf-8"):
    atomic_write_data_bytes(path, str(text).encode(encoding))


def migrate_known_data_files_to_encryption():
    if not DATA_ENCRYPTION_ENABLED:
        return
    with ENCRYPTED_DB_LOCK:
        for path in ENCRYPTED_DATA_PATHS:
            if not path.exists() or not path.is_file():
                continue
            payload = path.read_bytes()
            if is_encrypted_payload(payload):
                continue
            write_data_bytes(path, payload)


def mark_data_dirty():
    try:
        connection = getattr(DB_THREAD_STATE, "connection", None)
        if connection is not None:
            connection.mark_dirty()
    except Exception:
        pass


class EncryptedDbConnection:
    def __init__(self):
        ensure_crypto_ready()
        self.lock_acquired = False
        self.previous_connection = None
        if getattr(DB_THREAD_STATE, "connection", None) is None:
            ENCRYPTED_DB_LOCK.acquire()
            self.lock_acquired = True
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self._dirty = False
        try:
            if DB_ENCRYPTED_PATH.exists():
                payload = decrypt_bytes(DB_ENCRYPTED_PATH.read_bytes(), DB_ENCRYPTED_PATH.name)
                if payload:
                    self.conn.deserialize(payload)
            elif DB_PATH.exists() and DB_PATH.stat().st_size > 0:
                source = sqlite3.connect(DB_PATH)
                try:
                    source.backup(self.conn)
                finally:
                    source.close()
        except Exception:
            self.conn.close()
            if self.lock_acquired:
                ENCRYPTED_DB_LOCK.release()
            raise

    def __enter__(self):
        self.previous_connection = getattr(DB_THREAD_STATE, "connection", None)
        DB_THREAD_STATE.connection = self
        return self.conn

    def mark_dirty(self):
        self._dirty = True

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.conn.commit()
                payload = self.conn.serialize()
                atomic_write_raw_bytes(DB_ENCRYPTED_PATH, encrypt_bytes(payload, DB_ENCRYPTED_PATH.name))
                if DB_PATH.exists():
                    DB_PATH.unlink()
            else:
                self.conn.rollback()
        finally:
            if getattr(DB_THREAD_STATE, "connection", None) is self:
                DB_THREAD_STATE.connection = self.previous_connection
            self.conn.close()
            if self.lock_acquired:
                ENCRYPTED_DB_LOCK.release()
        return False


def db():
    if DATA_ENCRYPTION_ENABLED:
        active = getattr(DB_THREAD_STATE, "connection", None)
        if active is not None:
            return nullcontext(active.conn)
        return EncryptedDbConnection()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def sqlite_connection_context(conn=None):
    if conn is None:
        return db()
    return conn if hasattr(conn, "__enter__") else nullcontext(conn)

def hash_password(password):
    salt = secrets.token_hex(16)
    iterations = PASSWORD_HASH_ITERATIONS
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("ascii"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${digest.hex()}"


def verify_password(password, stored):
    try:
        method, iterations, salt, digest = stored.split("$", 3)
        if method != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("ascii"), int(iterations)
        ).hex()
        return hmac.compare_digest(actual, digest)
    except Exception:
        return False


def password_hash_needs_upgrade(stored):
    try:
        method, iterations, _, _ = str(stored or "").split("$", 3)
        return method != "pbkdf2_sha256" or int(iterations) < PASSWORD_HASH_ITERATIONS
    except Exception:
        return True


def ensure_column(conn, table, column, ddl):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db():
    ensure_dirs()
    with db() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_column(conn, "users", "team_group", "team_group TEXT DEFAULT ''")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS inventory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                location TEXT NOT NULL,
                note TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS manual_inventory_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_username TEXT NOT NULL,
                actor_user_id INTEGER,
                actor_username TEXT NOT NULL,
                inventory_id INTEGER,
                action TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual_inventory',
                category_snapshot TEXT NOT NULL,
                name_snapshot TEXT NOT NULL,
                location_snapshot TEXT DEFAULT '',
                note_snapshot TEXT DEFAULT '',
                quantity_before INTEGER,
                quantity_after INTEGER,
                quantity_delta INTEGER NOT NULL,
                reason TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS report_runs (
                run_date TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS access_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                status INTEGER NOT NULL,
                ip TEXT NOT NULL,
                user_agent TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS search_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                term TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bom_uploads (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_name TEXT NOT NULL,
                stored_name TEXT NOT NULL,
                uploaded_by TEXT NOT NULL,
                total_items INTEGER NOT NULL,
                total_quantity INTEGER NOT NULL,
                purchase_file TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS bom_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upload_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                quantity INTEGER NOT NULL,
                uploaded_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                upload_id INTEGER NOT NULL,
                file_name TEXT NOT NULL,
                created_by TEXT NOT NULL,
                item_count INTEGER NOT NULL,
                total_quantity INTEGER NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER NOT NULL,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                required_quantity INTEGER NOT NULL,
                stock_quantity INTEGER NOT NULL,
                purchase_quantity INTEGER NOT NULL,
                reason TEXT NOT NULL,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS purchase_receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                receipt_id TEXT NOT NULL UNIQUE,
                purchase_item_id INTEGER NOT NULL,
                purchase_order_id INTEGER NOT NULL,
                owner_username TEXT NOT NULL,
                category_snapshot TEXT NOT NULL,
                name_snapshot TEXT NOT NULL,
                received_quantity INTEGER NOT NULL,
                matched_inventory_id INTEGER,
                matched_soldering_job_id TEXT DEFAULT '',
                matched_bom_id TEXT DEFAULT '',
                matched_bom_upload_id INTEGER,
                note TEXT DEFAULT '',
                received_by_user_id INTEGER,
                received_by_username TEXT NOT NULL,
                inventory_entry_id INTEGER,
                inventory_posted_at TEXT DEFAULT '',
                inventory_posted_by_user_id INTEGER,
                inventory_posted_by_username TEXT DEFAULT '',
                inventory_location TEXT DEFAULT '',
                inventory_note TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        ensure_column(conn, "purchase_receipts", "status", "status TEXT DEFAULT 'active'")
        ensure_column(conn, "purchase_receipts", "voided_at", "voided_at TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "voided_by_user_id", "voided_by_user_id INTEGER")
        ensure_column(conn, "purchase_receipts", "voided_by_username", "voided_by_username TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "void_reason", "void_reason TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "inventory_entry_id", "inventory_entry_id INTEGER")
        ensure_column(conn, "purchase_receipts", "inventory_posted_at", "inventory_posted_at TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "inventory_posted_by_user_id", "inventory_posted_by_user_id INTEGER")
        ensure_column(conn, "purchase_receipts", "inventory_posted_by_username", "inventory_posted_by_username TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "inventory_location", "inventory_location TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "inventory_note", "inventory_note TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "reversal_inventory_entry_id", "reversal_inventory_entry_id INTEGER")
        ensure_column(conn, "purchase_receipts", "inventory_reversed_at", "inventory_reversed_at TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "inventory_reversed_by_user_id", "inventory_reversed_by_user_id INTEGER")
        ensure_column(conn, "purchase_receipts", "inventory_reversed_by_username", "inventory_reversed_by_username TEXT DEFAULT ''")
        ensure_column(conn, "purchase_receipts", "inventory_reversal_reason", "inventory_reversal_reason TEXT DEFAULT ''")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spare_bop_lists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bop_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                season TEXT DEFAULT '',
                robot_scope TEXT NOT NULL DEFAULT 'dual_robot',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                latest_purchase_order_id INTEGER,
                latest_purchase_file TEXT DEFAULT '',
                latest_purchase_at TEXT DEFAULT ''
            )
            """
        )
        ensure_column(conn, "spare_bop_lists", "latest_purchase_order_id", "latest_purchase_order_id INTEGER")
        ensure_column(conn, "spare_bop_lists", "latest_purchase_file", "latest_purchase_file TEXT DEFAULT ''")
        ensure_column(conn, "spare_bop_lists", "latest_purchase_at", "latest_purchase_at TEXT DEFAULT ''")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS spare_bop_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bop_id TEXT NOT NULL,
                robot_name TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                spec TEXT DEFAULT '',
                unit TEXT DEFAULT '个',
                quantity_per_robot INTEGER NOT NULL DEFAULT 0,
                robot_count INTEGER NOT NULL DEFAULT 1,
                spare_quantity INTEGER NOT NULL DEFAULT 0,
                required_quantity INTEGER NOT NULL DEFAULT 0,
                location_hint TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spare_bop_items_bop ON spare_bop_items (bop_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_spare_bop_items_name ON spare_bop_items (category, name)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS competition_material_boms (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bom_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                robot_name TEXT NOT NULL DEFAULT '',
                season TEXT DEFAULT '',
                original_name TEXT DEFAULT '',
                stored_name TEXT DEFAULT '',
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS competition_material_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_type TEXT NOT NULL,
                bom_id TEXT DEFAULT '',
                robot_name TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                spec TEXT DEFAULT '',
                quantity INTEGER NOT NULL DEFAULT 1,
                unit TEXT DEFAULT '个',
                owner_name TEXT NOT NULL,
                owner_group TEXT NOT NULL,
                responsible_by TEXT DEFAULT '',
                note TEXT DEFAULT '',
                hardware_compare INTEGER NOT NULL DEFAULT 0,
                created_by TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_competition_material_boms_bom ON competition_material_boms (bom_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_competition_material_items_bom ON competition_material_items (bom_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_competition_material_items_name ON competition_material_items (category, name)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_competition_material_items_owner ON competition_material_items (owner_name, owner_group)")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS assistant_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                meta_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS soldering_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                bom_id TEXT NOT NULL,
                bom_upload_id INTEGER,
                pcb_file_id TEXT DEFAULT '',
                owner_user_id INTEGER,
                owner_username TEXT NOT NULL,
                created_by_user_id INTEGER,
                created_by_username TEXT NOT NULL,
                started_by_user_id INTEGER,
                started_by_username TEXT NOT NULL,
                board_count INTEGER NOT NULL,
                status TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                inventory_consumed INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        ensure_column(conn, "soldering_jobs", "inventory_consumption_json", "inventory_consumption_json TEXT DEFAULT '{}'")
        ensure_column(conn, "soldering_jobs", "purchase_check_json", "purchase_check_json TEXT DEFAULT '{}'")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS soldering_consumption_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                bom_id TEXT DEFAULT '',
                bom_upload_id INTEGER,
                bom_item_id INTEGER,
                source_row_ref TEXT DEFAULT '',
                inventory_id INTEGER,
                category TEXT NOT NULL,
                name TEXT NOT NULL,
                required_quantity INTEGER NOT NULL DEFAULT 0,
                consumed_quantity INTEGER NOT NULL DEFAULT 0,
                shortage_quantity INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                match_key TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS analysis_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL UNIQUE,
                owner_username TEXT NOT NULL,
                bom_id TEXT NOT NULL,
                pcb_file_id TEXT NOT NULL,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL,
                progress INTEGER NOT NULL DEFAULT 0,
                error_summary TEXT DEFAULT '',
                result_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT ''
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_soldering_jobs_bom_status
            ON soldering_jobs (bom_id, owner_username, status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_soldering_jobs_owner_status
            ON soldering_jobs (owner_username, status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_soldering_consumption_job
            ON soldering_consumption_items (job_id, bom_item_id, inventory_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_soldering_consumption_inventory
            ON soldering_consumption_items (inventory_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_jobs_bom_file_status
            ON analysis_jobs (bom_id, pcb_file_id, task_type, status, updated_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_jobs_owner_status
            ON analysis_jobs (owner_username, status, updated_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_analysis_jobs_job_id
            ON analysis_jobs (job_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_purchase_receipts_purchase_item
            ON purchase_receipts (purchase_item_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_purchase_receipts_order
            ON purchase_receipts (purchase_order_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_purchase_receipts_owner
            ON purchase_receipts (owner_username, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_purchase_receipts_purchase_item_status
            ON purchase_receipts (purchase_item_id, status, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_purchase_receipts_inventory_entry
            ON purchase_receipts (inventory_entry_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_purchase_receipts_reversal_entry
            ON purchase_receipts (reversal_inventory_entry_id)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_manual_inventory_adjustments_owner_time
            ON manual_inventory_adjustments (owner_username, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_manual_inventory_adjustments_inventory_time
            ON manual_inventory_adjustments (inventory_id, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_manual_inventory_adjustments_actor_time
            ON manual_inventory_adjustments (actor_username, created_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_manual_inventory_adjustments_action_time
            ON manual_inventory_adjustments (action, created_at)
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                detail TEXT NOT NULL,
                duration_days INTEGER NOT NULL,
                owner_username TEXT NOT NULL,
                owner_group TEXT DEFAULT '',
                collaborators_json TEXT DEFAULT '[]',
                file_json TEXT DEFAULT '{}',
                ai_plan_json TEXT DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'planned',
                progress INTEGER NOT NULL DEFAULT 0,
                started_at TEXT NOT NULL,
                due_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_stages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                title TEXT NOT NULL,
                category TEXT NOT NULL,
                detail TEXT NOT NULL,
                owner_username TEXT DEFAULT '',
                start_day INTEGER NOT NULL DEFAULT 1,
                end_day INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'planned',
                progress INTEGER NOT NULL DEFAULT 0,
                color TEXT DEFAULT '#0a84ff',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                stage_id INTEGER,
                username TEXT NOT NULL,
                update_type TEXT NOT NULL,
                status TEXT DEFAULT '',
                progress INTEGER NOT NULL DEFAULT 0,
                note TEXT DEFAULT '',
                file_json TEXT DEFAULT '{}',
                pomodoro_minutes INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_projects_owner_group
            ON workflow_projects (owner_username, owner_group, status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_stages_workflow
            ON workflow_stages (workflow_id, status)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_workflow_updates_workflow
            ON workflow_updates (workflow_id, created_at)
            """
        )
        for key, value in DEFAULT_CONFIG.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        for key, value in ANALYSIS_WORKER_SETTING_DEFAULTS.items():
            conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, str(value)))
        for key, value in BOOT_CONFIG.items():
            if key in DEFAULT_CONFIG:
                conn.execute("UPDATE settings SET value = ? WHERE key = ?", (str(value), key))
        existing = conn.execute("SELECT id FROM users WHERE username = ?", (DEFAULT_ADMIN,)).fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (username, password_hash, role, team_group, created_at) VALUES (?, ?, 'admin', ?, ?)",
                (DEFAULT_ADMIN, hash_password(DEFAULT_PASSWORD), "运营组", now_text()),
            )
        elif ADMIN_PASSWORD_FROM_ENV:
            conn.execute(
                "UPDATE users SET password_hash = ?, role = 'admin' WHERE username = ?",
                (hash_password(DEFAULT_PASSWORD), DEFAULT_ADMIN),
            )
        conn.execute(
            "UPDATE users SET team_group = ? WHERE username = ? AND COALESCE(team_group, '') = ''",
            ("运营组", DEFAULT_ADMIN),
        )
    cleanup_analytics_noise()


def get_config():
    config = DEFAULT_CONFIG.copy()
    try:
        with db() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        config.update({row["key"]: row["value"] for row in rows})
    except Exception:
        config.update(BOOT_CONFIG)
    return config


def runtime_config_for_display(config=None):
    current = (config or get_config()).copy()
    current["host"] = HOST
    current["port"] = str(PORT)
    current["data_dir"] = str(DATA_DIR)
    current["tunnel_mode"] = os.environ.get("WAREHOUSE_TUNNEL", current.get("tunnel_mode", "off"))
    return current


def save_config(config):
    clean = DEFAULT_CONFIG.copy()
    for key in clean:
        clean[key] = str(config.get(key, clean[key])).strip()
    host = clean.get("host") or DEFAULT_CONFIG["host"]
    if host == "localhost":
        host = "127.0.0.1"
    clean["host"] = host
    clean["port"] = str(max(1, min(65535, parse_int(clean["port"], 8088))))
    clean["tunnel_mode"] = "auto" if clean.get("tunnel_mode") in ("auto", "1", "on", "true", "yes") else "off"
    clean["allow_registration"] = "1" if clean["allow_registration"] in ("1", "on", "true", "yes") else "0"
    clean["ui_accent_start"] = css_hex_color(clean.get("ui_accent_start"), DEFAULT_CONFIG["ui_accent_start"])
    clean["ui_accent_end"] = css_hex_color(clean.get("ui_accent_end"), DEFAULT_CONFIG["ui_accent_end"])
    clean["ui_glass_opacity"] = str(clamp_float(clean.get("ui_glass_opacity"), 0.58, 0.22, 0.86))
    clean["ui_glass_blur"] = str(clamp_int(clean.get("ui_glass_blur"), 30, 12, 48))
    clean["ui_panel_radius"] = str(clamp_int(clean.get("ui_panel_radius"), 14, 6, 28))
    clean["ui_gradient_angle"] = str(clamp_int(clean.get("ui_gradient_angle"), 135, 0, 360))
    clean["ui_product_depth"] = str(clamp_int(clean.get("ui_product_depth"), 220, 120, 360))
    clean["ui_product_tilt"] = str(clamp_int(clean.get("ui_product_tilt"), 10, 0, 18))
    clean["ui_product_speed"] = str(clamp_int(clean.get("ui_product_speed"), 22, 8, 40))
    clean["allowed_hosts"] = ",".join(split_config_list(clean.get("allowed_hosts", "")))
    clean["allowed_api_hosts"] = ",".join(split_config_list(clean.get("allowed_api_hosts", "")))
    with db() as conn:
        for key, value in clean.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    CONFIG_PATH.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def reencrypt_secret_config_files():
    if ASSISTANT_CONFIG_JSON.exists():
        save_assistant_config(get_assistant_config())
    if IMAGE_RECOGNITION_CONFIG_JSON.exists():
        save_image_recognition_config(get_image_recognition_config())


def get_assistant_config(mask_key=False):
    config = DEFAULT_ASSISTANT_CONFIG.copy()
    if ASSISTANT_CONFIG_JSON.exists():
        try:
            data = json.loads(read_data_text(ASSISTANT_CONFIG_JSON, encoding="utf-8"))
            if isinstance(data, dict):
                config.update({key: str(value) for key, value in data.items() if key in config})
        except Exception:
            pass
    config["api_key"] = decrypt_text(config.get("api_key", ""), "assistant_config.api_key")
    if mask_key and config.get("api_key"):
        config["api_key"] = "********"
    return config


def save_assistant_config(values):
    current = get_assistant_config()
    clean = DEFAULT_ASSISTANT_CONFIG.copy()
    for key in DEFAULT_ASSISTANT_CONFIG:
        value = str(values.get(key, current.get(key, DEFAULT_ASSISTANT_CONFIG[key]))).strip()
        if key == "api_key" and value in ("", "********"):
            value = current.get("api_key", "")
        clean[key] = value
    clean["enabled"] = "1" if clean.get("enabled") == "1" else "0"
    ASSISTANT_CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)
    stored = clean.copy()
    stored["api_key"] = encrypt_text(clean.get("api_key", ""), "assistant_config.api_key") if clean.get("api_key") else ""
    write_data_text(ASSISTANT_CONFIG_JSON, json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def get_image_recognition_config(mask_key=False):
    config = DEFAULT_IMAGE_RECOGNITION_CONFIG.copy()
    if IMAGE_RECOGNITION_CONFIG_JSON.exists():
        try:
            data = json.loads(read_data_text(IMAGE_RECOGNITION_CONFIG_JSON, encoding="utf-8"))
            if isinstance(data, dict):
                config.update({key: str(value) for key, value in data.items() if key in config})
        except Exception:
            pass
    config["api_key"] = decrypt_text(config.get("api_key", ""), "image_recognition_config.api_key")
    if not config.get("endpoint") or not config.get("model"):
        assistant_cfg = get_assistant_config()
        for key in ("endpoint", "model", "api_key", "timeout"):
            if not config.get(key) and assistant_cfg.get(key):
                config[key] = assistant_cfg.get(key, "")
    if mask_key and config.get("api_key"):
        config["api_key"] = "********"
    return config


def save_image_recognition_config(values):
    current = get_image_recognition_config()
    clean = DEFAULT_IMAGE_RECOGNITION_CONFIG.copy()
    for key in DEFAULT_IMAGE_RECOGNITION_CONFIG:
        value = str(values.get(key, current.get(key, DEFAULT_IMAGE_RECOGNITION_CONFIG[key]))).strip()
        if key == "api_key" and value in ("", "********"):
            value = current.get("api_key", "")
        clean[key] = value
    clean["enabled"] = "1" if clean.get("enabled") == "1" else "0"
    clean["temperature"] = str(clamp_float(clean.get("temperature"), 0, 0, 2))
    clean["max_tokens"] = str(clamp_int(clean.get("max_tokens"), 900, 100, 4000))
    clean["timeout"] = str(clamp_int(clean.get("timeout"), 45, 5, 180))
    IMAGE_RECOGNITION_CONFIG_JSON.parent.mkdir(parents=True, exist_ok=True)
    stored = clean.copy()
    stored["api_key"] = (
        encrypt_text(clean.get("api_key", ""), "image_recognition_config.api_key") if clean.get("api_key") else ""
    )
    write_data_text(IMAGE_RECOGNITION_CONFIG_JSON, json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8")
    return clean


def _load_bom_records_unlocked():
    if not BOMS_JSON.exists():
        return []
    try:
        data = json.loads(read_data_text(BOMS_JSON, encoding="utf-8"))
    except Exception as exc:
        print(f"[bom archive] failed to read {BOMS_JSON}: {exc}")
        return []
    if isinstance(data, dict):
        records = data.get("records", [])
    else:
        records = data
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]


def load_bom_records():
    with BOMS_LOCK:
        return _load_bom_records_unlocked()


def save_bom_records(records):
    payload = {"version": 1, "records": records}
    with BOMS_LOCK:
        BOMS_JSON.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_data_text(BOMS_JSON, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_bom_record(record):
    with BOMS_LOCK:
        records = _load_bom_records_unlocked()
        records.append(record)
        payload = {"version": 1, "records": records}
        BOMS_JSON.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_data_text(BOMS_JSON, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def update_bom_record(bom_id, updater):
    with BOMS_LOCK:
        records = _load_bom_records_unlocked()
        for index, record in enumerate(records):
            if str(record.get("bom_id") or "") != str(bom_id or ""):
                continue
            updated = updater(dict(record))
            if not isinstance(updated, dict):
                raise ValueError("BOM updater must return a record.")
            records[index] = updated
            payload = {"version": 1, "records": records}
            BOMS_JSON.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_data_text(BOMS_JSON, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            return updated
    return None


def is_admin_role(user):
    return bool(user and str(user["role"]).lower() in ("admin", "superadmin"))


def user_can_view_bom(record, user):
    if not user:
        return False
    if is_admin_role(user):
        return True
    owner = record.get("owner") if isinstance(record.get("owner"), dict) else {}
    owner_username = record.get("owner_username") or owner.get("username")
    owner_id = record.get("owner_user_id") or owner.get("user_id")
    return owner_username == user["username"] or str(owner_id or "") == str(user["id"])


def find_bom_record(bom_id):
    for record in load_bom_records():
        if str(record.get("bom_id") or "") == str(bom_id or ""):
            return record
    return None


def require_text(data, key, max_len=120):
    value = (data.get(key, [""])[0] or "").strip()
    if not value:
        raise ValueError(f"{key} is required")
    return value[:max_len]


def parse_int(value, default=0):
    try:
        return int(str(value).strip())
    except Exception:
        return default


def parse_required_json_int(value, field_name):
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if re.fullmatch(r"[+-]?\d+", text):
            return int(text)
    raise ValueError(f"{field_name} must be an integer.")


def content_disposition_filename(name, fallback="download"):
    filename = Path(str(name or fallback)).name.strip()
    filename = re.sub(r'[\r\n"\\;]+', "_", filename).strip() or fallback
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename).strip("._") or fallback
    utf8_name = urllib.parse.quote(filename, safe="")
    return f'filename="{ascii_name}"; filename*=UTF-8\'\'{utf8_name}'


def truthy_value(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "confirm", "confirmed")


def clamp_int(value, default, min_value, max_value):
    return max(min_value, min(max_value, parse_int(value, default)))


def clamp_float(value, default, min_value, max_value):
    try:
        parsed = float(str(value).strip())
    except Exception:
        parsed = default
    return round(max(min_value, min(max_value, parsed)), 2)


def css_hex_color(value, fallback):
    value = str(value or "").strip()
    return value if re.fullmatch(r"#[0-9a-fA-F]{6}", value) else fallback


def ui_theme_values(config=None):
    config = config or get_config()
    return {
        "accent_start": css_hex_color(config.get("ui_accent_start"), DEFAULT_CONFIG["ui_accent_start"]),
        "accent_end": css_hex_color(config.get("ui_accent_end"), DEFAULT_CONFIG["ui_accent_end"]),
        "glass_opacity": clamp_float(config.get("ui_glass_opacity"), 0.58, 0.22, 0.86),
        "glass_blur": clamp_int(config.get("ui_glass_blur"), 30, 12, 48),
        "panel_radius": clamp_int(config.get("ui_panel_radius"), 14, 6, 28),
        "gradient_angle": clamp_int(config.get("ui_gradient_angle"), 135, 0, 360),
        "product_depth": clamp_int(config.get("ui_product_depth"), 220, 120, 360),
        "product_tilt": clamp_int(config.get("ui_product_tilt"), 10, 0, 18),
        "product_speed": clamp_int(config.get("ui_product_speed"), 22, 8, 40),
    }


def ui_theme_style(config=None):
    values = ui_theme_values(config)
    return f"""<style id="ui-theme-vars">
:root {{
  --accent-a: {values["accent_start"]};
  --accent-b: {values["accent_end"]};
  --glass-alpha: {values["glass_opacity"]};
  --glass-blur: {values["glass_blur"]}px;
  --panel-radius: {values["panel_radius"]}px;
  --gradient-angle: {values["gradient_angle"]}deg;
  --product-depth: {values["product_depth"]}px;
  --product-tilt: {values["product_tilt"]}deg;
  --product-speed: {values["product_speed"]}s;
}}
</style>"""


def append_csv(row):
    rows = []
    if CSV_PATH.exists():
        existing = read_data_text(CSV_PATH, encoding="utf-8-sig")
        rows = list(csv.reader(io.StringIO(existing)))
    if not rows:
        rows.append(["时间", "商品类别", "商品名称", "数量", "位置", "备注", "录入账号"])
    rows.append(row)
    buffer = io.StringIO(newline="")
    csv.writer(buffer).writerows(rows)
    write_data_text(CSV_PATH, buffer.getvalue(), encoding="utf-8-sig")


def xml_escape(value):
    return html.escape(str(value), quote=True)


def col_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def write_xlsx(path, sheet_name, headers, rows):
    def cell_xml(row_index, col_index, value):
        ref = f"{col_name(col_index)}{row_index}"
        if isinstance(value, int):
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{xml_escape(value)}</t></is></c>'

    all_rows = [headers] + rows
    sheet_rows = []
    for row_index, row in enumerate(all_rows, start=1):
        cells = [cell_xml(row_index, col_index, value) for col_index, value in enumerate(row, start=1)]
        sheet_rows.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    worksheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f'<sheetData>{"".join(sheet_rows)}</sheetData></worksheet>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{xml_escape(sheet_name)[:31]}" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/worksheets/sheet1.xml", worksheet)
    write_data_bytes(path, buffer.getvalue())


def inventory_rows(where="", params=(), limit=None, order_by="recent"):
    query = "SELECT id, created_at, category, name, quantity, location, note, created_by FROM inventory"
    query_params = list(params or [])
    if where:
        query += " WHERE " + where
    if order_by == "location":
        query += " ORDER BY location COLLATE NOCASE ASC, datetime(created_at) DESC, id DESC"
    else:
        query += " ORDER BY datetime(created_at) DESC, id DESC"
    if limit is not None:
        query += " LIMIT ?"
        query_params.append(max(1, parse_int(limit, INVENTORY_SEARCH_RESULT_LIMIT)))
    with db() as conn:
        return conn.execute(query, query_params).fetchall()


def export_current_inventory():
    rows = inventory_rows()
    data = [
        [r["created_at"], r["category"], r["name"], r["quantity"], r["location"], r["note"], r["created_by"]]
        for r in rows
    ]
    write_xlsx(
        REPORTS_DIR / "all_inventory_detail.xlsx",
        "库存明细",
        ["时间", "商品类别", "商品名称", "数量", "位置", "备注", "录入账号"],
        data,
    )


def generate_weekly_report(force=False):
    today = short_date()
    with db() as conn:
        if not force:
            done = conn.execute("SELECT run_date FROM report_runs WHERE run_date = ?", (today,)).fetchone()
            if done:
                return None
        start = datetime.now() - timedelta(days=7)
        rows = conn.execute(
            """
            SELECT category, name, location, SUM(quantity) AS quantity, COUNT(*) AS entries
            FROM inventory
            WHERE datetime(created_at) >= datetime(?)
            GROUP BY category, name, location
            ORDER BY category, name, location
            """,
            (start.strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchall()
        all_rows = conn.execute(
            """
            SELECT category, name, location, SUM(quantity) AS quantity, COUNT(*) AS entries
            FROM inventory
            GROUP BY category, name, location
            ORDER BY category, name, location
            """
        ).fetchall()
        conn.execute("INSERT OR REPLACE INTO report_runs (run_date, created_at) VALUES (?, ?)", (today, now_text()))
        weekly_path = REPORTS_DIR / f"weekly_summary_{today}.xlsx"
        all_path = REPORTS_DIR / f"all_inventory_compare_{today}.xlsx"
        write_xlsx(
            weekly_path,
            "本周入库汇总",
            ["商品类别", "商品名称", "位置", "本周入库数量", "入库次数"],
            [[r["category"], r["name"], r["location"], r["quantity"], r["entries"]] for r in rows],
        )
        write_xlsx(
            all_path,
            "全量库存对照",
            ["商品类别", "商品名称", "位置", "累计数量", "记录次数"],
            [[r["category"], r["name"], r["location"], r["quantity"], r["entries"]] for r in all_rows],
        )
        export_current_inventory()
    return weekly_path


def report_scheduler():
    while not STOP_EVENT.is_set():
        try:
            cleanup_analytics_noise()
            day_map = {
                "Monday": 0,
                "Tuesday": 1,
                "Wednesday": 2,
                "Thursday": 3,
                "Friday": 4,
                "Saturday": 5,
                "Sunday": 6,
            }
            target_day = day_map.get(get_config().get("weekly_report_day", "Sunday"), 6)
            if datetime.now().weekday() == target_day:
                generate_weekly_report(force=False)
        except Exception as exc:
            print(f"[report] weekly report failed: {exc}")
        STOP_EVENT.wait(3600)


def start_tunnel():
    global TUNNEL_URL, TUNNEL_PROCESS
    config = get_config()
    public_url = os.environ.get("WAREHOUSE_PUBLIC_URL") or config.get("public_url")
    if public_url:
        TUNNEL_URL = public_url
        return
    tunnel_setting = os.environ.get("WAREHOUSE_TUNNEL", config.get("tunnel_mode", "off")).lower()
    if tunnel_setting in ("off", "0", "false"):
        return
    cloudflared = shutil.which("cloudflared")
    if cloudflared:
        try:
            TUNNEL_PROCESS = subprocess.Popen(
                [cloudflared, "tunnel", "--url", f"http://127.0.0.1:{PORT}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="ignore",
            )
            threading.Thread(target=read_tunnel_output, daemon=True).start()
            return
        except Exception as exc:
            print(f"[tunnel] cloudflared start failed: {exc}")
    ngrok = shutil.which("ngrok")
    if ngrok and os.environ.get("NGROK_AUTHTOKEN"):
        try:
            subprocess.run([ngrok, "config", "add-authtoken", os.environ["NGROK_AUTHTOKEN"]], check=False)
            TUNNEL_PROCESS = subprocess.Popen([ngrok, "http", str(PORT)], stdout=subprocess.DEVNULL)
            TUNNEL_URL = "ngrok started. Open http://127.0.0.1:4040 to view public URL."
        except Exception as exc:
            print(f"[tunnel] ngrok start failed: {exc}")


def read_tunnel_output():
    global TUNNEL_URL
    if not TUNNEL_PROCESS or not TUNNEL_PROCESS.stdout:
        return
    for line in TUNNEL_PROCESS.stdout:
        print("[tunnel]", line.strip())
        if "trycloudflare.com" in line:
            for part in line.split():
                if "trycloudflare.com" in part:
                    TUNNEL_URL = part.strip()
                    break


def local_ip():
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        value = sock.getsockname()[0]
        sock.close()
        return value
    except Exception:
        return "127.0.0.1"


def safe_name(name):
    stem = re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", Path(name).name).strip("._")
    return stem or f"upload_{int(time.time())}"


def collect_uploaded_files(files, field_names):
    uploads = []
    for field_name in field_names:
        value = files.get(field_name)
        if isinstance(value, list):
            uploads.extend(value)
        elif value:
            uploads.append(value)
    return [upload for upload in uploads if upload and upload.get("filename")]


def pcb_upload_kind(extension):
    if extension == ".zip":
        return "gerber_archive"
    if extension in PCB_GERBER_EXTENSIONS:
        return "gerber_file"
    if extension in (".pcb", ".kicad_pcb", ".brd", ".fbrd"):
        return "pcb_file"
    if extension in (".html", ".htm"):
        return "html_assistant"
    if extension in PCB_PDF_EXTENSIONS:
        return "schematic_pdf"
    if extension == ".epro":
        return "easyeda_pro_project"
    return "support_file"


def pcb_file_metadata_defaults(item=None, extension="", kind=""):
    source = item if isinstance(item, dict) else {}
    extension = str(extension or source.get("extension") or "").lower()
    if not extension:
        filename = source.get("original_filename") or source.get("stored_filename") or ""
        extension = Path(str(filename)).suffix.lower()
    kind = str(kind or source.get("kind") or pcb_upload_kind(extension)).strip() or "support_file"
    document_type = str(source.get("document_type") or PCB_DOCUMENT_TYPE_DEFAULTS.get(kind) or "support_file").strip()
    if extension in PCB_PDF_EXTENSIONS or kind == "schematic_pdf":
        kind = "schematic_pdf"
        document_type = "schematic_pdf"
    file_role = str(source.get("file_role") or PCB_FILE_ROLE_DEFAULTS.get(kind) or "support").strip()
    analysis_default = "pending" if kind == "schematic_pdf" or document_type == "schematic_pdf" else "not_applicable"
    return {
        "kind": kind,
        "file_role": file_role,
        "document_type": document_type,
        "processing_status": str(source.get("processing_status") or "uploaded").strip() or "uploaded",
        "analysis_status": str(source.get("analysis_status") or analysis_default).strip() or analysis_default,
    }


def pcb_file_kind_label(kind):
    return PCB_KIND_LABELS.get(str(kind or ""), str(kind or "support_file").replace("_", " ").title())


PCB_KIND_LABELS.setdefault("easyeda_pro_project", "EasyEDA Pro project")
PCB_FILE_ROLE_DEFAULTS.setdefault("easyeda_pro_project", "pcb_project")
PCB_DOCUMENT_TYPE_DEFAULTS.setdefault("easyeda_pro_project", "easyeda_pro_project")


def format_file_size(size):
    try:
        value = int(size or 0)
    except Exception:
        value = 0
    if value >= 1024 * 1024:
        return f"{value / (1024 * 1024):.1f} MB"
    if value >= 1024:
        return f"{value / 1024:.1f} KB"
    return f"{value} B"


def pcb_file_compact_status(item):
    if not isinstance(item, dict):
        return ""
    kind = item.get("kind") or "support_file"
    processing_status = str(item.get("processing_status") or "uploaded")
    analysis_status = str(item.get("analysis_status") or "not_applicable")
    parts = [pcb_file_kind_label(kind), zh_status(processing_status)]
    if analysis_status not in ("not_applicable", "not_requested"):
        parts.append(f"分析 {zh_status(analysis_status)}")
    if item.get("size_bytes") is not None:
        parts.append(format_file_size(item.get("size_bytes")))
    return " - ".join(part for part in parts if part)


def validate_pcb_upload_content(extension, content):
    if len(content or b"") > PCB_UPLOAD_MAX_BYTES:
        raise ValueError("Uploaded PCB/support file exceeds the 80MB limit.")
    if extension in PCB_PDF_EXTENSIONS and not (content or b"").lstrip().startswith(b"%PDF-"):
        raise ValueError("Uploaded PDF schematic is not a valid PDF file.")


def pcb_upload_mime_type(filename, extension, uploaded_type=""):
    if extension in PCB_PDF_EXTENSIONS:
        return "application/pdf"
    return str(uploaded_type or "").strip() or mimetypes.guess_type(filename)[0] or "application/octet-stream"


def path_is_relative_to(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except Exception:
        return False


def data_relative_path(path):
    return path.resolve().relative_to(DATA_DIR.resolve()).as_posix()


def migrate_upload_tree_to_encryption(root):
    if not DATA_ENCRYPTION_ENABLED or not Path(root).exists():
        return
    with ENCRYPTED_DB_LOCK:
        for path in Path(root).rglob("*"):
            if not path.is_file():
                continue
            if path.suffix == ".tmp":
                continue
            if path.resolve() in (DB_PATH.resolve(), DB_ENCRYPTED_PATH.resolve()):
                continue
            payload = path.read_bytes()
            if is_encrypted_payload(payload):
                continue
            write_data_bytes(path, payload)


def migrate_runtime_files_to_encryption():
    if not DATA_ENCRYPTION_ENABLED:
        return
    migrate_known_data_files_to_encryption()
    migrate_upload_tree_to_encryption(DATA_DIR)


def store_pcb_uploads(bom_id, uploads, user):
    if not uploads:
        raise ValueError("No PCB file was uploaded.")
    prepared = []
    for upload in uploads:
        original = Path(str(upload.get("filename") or "")).name
        filename = safe_name(original)
        extension = Path(filename).suffix.lower()
        content = upload.get("content") or b""
        if extension not in PCB_ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported PCB file extension: {extension or '(none)'}.")
        if not content:
            raise ValueError("Uploaded PCB files cannot be empty.")
        validate_pcb_upload_content(extension, content)
        prepared.append((upload, original, filename, extension, content))

    bom_folder = safe_name(str(bom_id or "bom"))
    upload_dir = (PCB_UPLOADS_DIR / bom_folder).resolve()
    if not path_is_relative_to(upload_dir, PCB_UPLOADS_DIR):
        raise ValueError("Invalid BOM upload target.")
    upload_dir.mkdir(parents=True, exist_ok=True)

    stored = []
    for upload, original, filename, extension, content in prepared:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        token = secrets.token_hex(4)
        stored_name = f"{stamp}_{token}_{filename}"
        target = upload_dir / stored_name
        counter = 1
        while target.exists():
            stored_name = f"{stamp}_{token}_{counter}_{filename}"
            target = upload_dir / stored_name
            counter += 1
        write_data_bytes(target, content)
        sha256 = hashlib.sha256(content).hexdigest()
        content_type = pcb_upload_mime_type(filename, extension, upload.get("content_type"))
        kind = pcb_upload_kind(extension)
        metadata_defaults = pcb_file_metadata_defaults(extension=extension, kind=kind)
        item = {
            "pcb_id": f"pcb_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}",
            "original_filename": original,
            "stored_filename": stored_name,
            "relative_path": data_relative_path(target),
            "extension": extension,
            "mime_type": content_type,
            "size_bytes": len(content),
            "sha256": sha256,
            "uploaded_at": datetime.now().isoformat(timespec="seconds"),
            "uploaded_by": user["username"],
            **metadata_defaults,
        }
        stored.append(item)

        if extension == ".epro":
            conversion = process_epro_upload(
                content,
                original,
                os.environ.get("WAREHOUSE_EPRO_CONVERTER_CMD", ""),
            )
            item["processing_status"] = conversion.status
            item["conversion_status"] = conversion.status
            item["conversion_message"] = conversion.message
            item["conversion_diagnostics"] = conversion.diagnostics
            item["derived_file_count"] = len(conversion.generated_files)
            for generated in conversion.generated_files:
                generated_filename = safe_name(generated.filename)
                generated_extension = Path(generated_filename).suffix.lower()
                if generated_extension not in PCB_ALLOWED_EXTENSIONS or generated_extension == ".epro":
                    continue
                generated_stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
                generated_token = secrets.token_hex(4)
                generated_stored_name = f"{generated_stamp}_{generated_token}_{generated_filename}"
                generated_target = upload_dir / generated_stored_name
                generated_counter = 1
                while generated_target.exists():
                    generated_stored_name = f"{generated_stamp}_{generated_token}_{generated_counter}_{generated_filename}"
                    generated_target = upload_dir / generated_stored_name
                    generated_counter += 1
                write_data_bytes(generated_target, generated.content)
                generated_kind = pcb_upload_kind(generated_extension)
                generated_defaults = pcb_file_metadata_defaults(extension=generated_extension, kind=generated_kind)
                stored.append(
                    {
                        "pcb_id": f"pcb_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}",
                        "original_filename": generated_filename,
                        "stored_filename": generated_stored_name,
                        "relative_path": data_relative_path(generated_target),
                        "extension": generated_extension,
                        "mime_type": pcb_upload_mime_type(generated_filename, generated_extension),
                        "size_bytes": len(generated.content),
                        "sha256": hashlib.sha256(generated.content).hexdigest(),
                        "uploaded_at": datetime.now().isoformat(timespec="seconds"),
                        "uploaded_by": user["username"],
                        "derived_from_pcb_id": item["pcb_id"],
                        "derived_from_filename": original,
                        "conversion_source": generated.source,
                        "conversion_note": generated.note,
                        **generated_defaults,
                        "processing_status": "converted",
                    }
                )
    return stored


def append_pcb_metadata(record, pcb_files):
    existing = record.get("pcb_files")
    if isinstance(existing, list):
        merged = list(existing)
    elif existing:
        merged = [existing]
    else:
        merged = []
    merged.extend(pcb_files)
    record["pcb_files"] = merged
    record["latest_pcb_file"] = merged[-1] if merged else None
    record["pcb_file_count"] = len(merged)
    record["pcb_updated_at"] = datetime.now().isoformat(timespec="seconds")
    if merged and record.get("status") not in ("soldering_active",):
        record["status"] = "pcb_attached"
    return record


def team_group_select(name="team_group", selected="", include_empty=True):
    options = []
    if include_empty:
        options.append(f'<option value="" {"selected" if not selected else ""}>暂不选择</option>')
    for group in TEAM_GROUPS:
        options.append(
            f'<option value="{html.escape(group)}" {"selected" if selected == group else ""}>{html.escape(group)}</option>'
        )
    return f'<select name="{html.escape(name)}">' + "".join(options) + "</select>"


def normalize_team_group(value):
    value = str(value or "").strip()
    return value if value in TEAM_GROUPS else ""


def workflow_status_label(status):
    return WORKFLOW_STATUS_LABELS.get(str(status or ""), "计划中")


def workflow_json_load(value, fallback):
    if value in (None, ""):
        return fallback
    try:
        parsed = json.loads(value)
        return parsed if parsed is not None else fallback
    except Exception:
        return fallback


def store_workflow_upload(upload, workflow_id, user, kind="project"):
    if not upload or not upload.get("filename"):
        return {}
    original = Path(str(upload.get("filename") or "")).name
    filename = safe_name(original)
    content = upload.get("content") or b""
    if not content:
        return {}
    if len(content) > 80 * 1024 * 1024:
        raise ValueError("工作流文件不能超过 80MB。")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    token = secrets.token_hex(4)
    stored_name = f"{safe_name(workflow_id)}_{kind}_{stamp}_{token}_{filename}"
    target = (WORKFLOW_UPLOADS_DIR / stored_name).resolve()
    if not str(target).startswith(str(WORKFLOW_UPLOADS_DIR.resolve())):
        raise ValueError("工作流上传目标无效。")
    write_data_bytes(target, content)
    return {
        "original_filename": original,
        "stored_filename": stored_name,
        "download_url": f"/download?type=workflow&name={urllib.parse.quote(stored_name)}",
        "mime_type": upload.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream",
        "size_bytes": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "kind": kind,
        "uploaded_by": user["username"],
        "uploaded_at": now_text(),
    }


def workflow_user_options():
    with db() as conn:
        rows = conn.execute(
            "SELECT id, username, role, team_group FROM users ORDER BY COALESCE(team_group, ''), username"
        ).fetchall()
    return [dict(row) for row in rows]


def workflow_collaborators_from_fields(fields):
    users = workflow_user_options()
    by_id = {str(user["id"]): user["username"] for user in users}
    selected = []
    for key, value in fields.items():
        if not key.startswith("collaborator_") or str(value or "") not in ("1", "on", "true", "yes"):
            continue
        username = by_id.get(key.removeprefix("collaborator_"))
        if username and username not in selected:
            selected.append(username)
    return selected[:24]


def workflow_stage_plan(name, detail, duration_days, collaborators, owner_username):
    duration_days = max(1, min(parse_int(duration_days, 14), 365))
    templates = [
        ("需求拆解", "项目范围、交付物、风险点和协作边界确认。", "planning"),
        ("方案建模", "把详细内容拆成模块任务，确定接口、文件和验收标准。", "design"),
        ("执行推进", "按模块推进工作，上传过程截图、文档和阶段成果。", "execution"),
        ("集成复核", "跨组检查依赖、缺口和返工点，完成联调记录。", "review"),
        ("归档交付", "沉淀最终文件、结论、剩余问题和下次迭代建议。", "delivery"),
    ]
    if duration_days <= 3:
        templates = templates[:3]
    owners = [owner_username] + [item for item in collaborators if item != owner_username]
    owners = owners or [owner_username]
    stages = []
    count = len(templates)
    for index, (title, fallback, category) in enumerate(templates):
        start_day = max(1, int(index * duration_days / count) + 1)
        end_day = max(start_day, int((index + 1) * duration_days / count))
        stage_detail = fallback
        if index == 0 and detail:
            stage_detail = f"{fallback} 项目摘要：{detail[:220]}"
        stages.append(
            {
                "title": title,
                "category": category,
                "detail": stage_detail,
                "owner_username": owners[index % len(owners)],
                "start_day": start_day,
                "end_day": end_day,
                "status": "planned" if index else "active",
                "progress": 0,
                "color": WORKFLOW_STAGE_COLORS[index % len(WORKFLOW_STAGE_COLORS)],
            }
        )
    return stages


def extract_json_object(text):
    text = str(text or "").strip()
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            text = text[start : end + 1]
    try:
        return json.loads(text)
    except Exception:
        return None


def normalize_inventory_image_item(item):
    if not isinstance(item, dict):
        return None

    def text_value(*keys, max_len=240):
        for key in keys:
            value = item.get(key)
            if value is not None:
                return str(value).strip()[:max_len]
        return ""

    quantity = parse_int(item.get("quantity") or item.get("qty") or item.get("count"), 1)
    quantity = max(1, min(quantity, 999999))
    confidence_raw = item.get("confidence", "")
    try:
        confidence = round(float(confidence_raw), 2)
        confidence = max(0, min(confidence, 1))
    except Exception:
        confidence = ""
    normalized = {
        "lcsc_code": text_value("lcsc_code", "lcsc", "supplier_part", "supplier_code", max_len=40).upper(),
        "category": text_value("category", "type", max_len=160),
        "name": text_value("name", "product_name", "part_name", "model", max_len=180),
        "value_spec": text_value("value_spec", "value", "spec", "resistance", "capacitance", max_len=120),
        "package": text_value("package", "footprint", "case", max_len=80),
        "voltage": text_value("voltage", "rating", "power", max_len=80),
        "brand": text_value("brand", "manufacturer", max_len=120),
        "quantity": quantity,
        "location": text_value("location", "bin", "shelf", max_len=120),
        "product_url": text_value("product_url", "url", "link", max_len=500),
        "note": text_value("note", "notes", "description", max_len=500),
        "confidence": confidence,
    }
    if not normalized["name"]:
        normalized["name"] = compose_inventory_name(
            "",
            normalized["value_spec"],
            normalized["package"],
            normalized["voltage"],
            None,
            normalized["lcsc_code"],
        )
    if not normalized["category"]:
        normalized["category"] = infer_category(normalized["value_spec"] or normalized["name"], "", normalized["package"])
    if not any(normalized.get(key) for key in ("lcsc_code", "name", "value_spec", "package", "brand")):
        return None
    return normalized


def normalize_inventory_image_result(parsed):
    if not isinstance(parsed, dict):
        return []
    raw_items = parsed.get("items")
    if not isinstance(raw_items, list):
        raw_items = [parsed]
    items = []
    for item in raw_items[:12]:
        normalized = normalize_inventory_image_item(item)
        if normalized:
            items.append(normalized)
    return items


def normalize_ai_stages(parsed, fallback_stages, collaborators, owner_username, duration_days):
    raw_stages = parsed.get("stages") if isinstance(parsed, dict) else None
    if not isinstance(raw_stages, list):
        return fallback_stages
    normalized = []
    owners = [owner_username] + [item for item in collaborators if item != owner_username]
    owners = owners or [owner_username]
    for index, item in enumerate(raw_stages[:8]):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or item.get("name") or "").strip()[:80]
        detail = str(item.get("detail") or item.get("description") or item.get("content") or "").strip()[:800]
        if not title:
            continue
        start_day = max(1, parse_int(item.get("start_day") or item.get("start"), 1))
        end_day = max(start_day, parse_int(item.get("end_day") or item.get("end"), start_day))
        normalized.append(
            {
                "title": title,
                "category": str(item.get("category") or "ai_plan")[:80],
                "detail": detail or title,
                "owner_username": str(item.get("owner_username") or item.get("owner") or owners[index % len(owners)])[:60],
                "start_day": min(start_day, duration_days),
                "end_day": min(end_day, duration_days),
                "status": "planned" if index else "active",
                "progress": 0,
                "color": str(item.get("color") or WORKFLOW_STAGE_COLORS[index % len(WORKFLOW_STAGE_COLORS)])[:20],
            }
        )
    return normalized or fallback_stages


def build_workflow_ai_plan(name, detail, duration_days, collaborators, user, fields):
    fallback_stages = workflow_stage_plan(name, detail, duration_days, collaborators, user["username"])
    plan = {
        "source": "local_template",
        "model": "",
        "summary": "本地模板已根据项目周期生成阶段计划。",
        "thinking": [
            "读取项目名称、周期、协作成员和上传文件摘要",
            "按工作流阶段拆分计划、执行、复核和交付",
            "写入每个阶段的颜色、负责人和周期窗口",
        ],
        "raw": "",
    }
    endpoint = str(fields.get("ai_endpoint") or "").strip()
    api_key = str(fields.get("ai_api_key") or "").strip()
    model = str(fields.get("ai_model") or "").strip()
    source = "user_api" if endpoint and model else ""
    if not source:
        cfg = get_assistant_config()
        if cfg.get("enabled") == "1" and cfg.get("endpoint") and cfg.get("model"):
            endpoint = cfg.get("endpoint", "")
            api_key = cfg.get("api_key", "")
            model = cfg.get("model", "")
            source = "server_assistant"
    if not source:
        plan["stages"] = fallback_stages
        return plan
    plan["thinking"].append("检测到可用 AI API，准备发送受限的项目规划提示")
    prompt = (
        "请为仓管/硬件团队项目生成工作流计划，只返回 JSON。JSON 格式："
        "{\"summary\":\"...\",\"stages\":[{\"title\":\"...\",\"category\":\"...\",\"detail\":\"...\","
        "\"owner_username\":\"...\",\"start_day\":1,\"end_day\":3,\"color\":\"#0a84ff\"}]}。"
        f"\n项目名称：{name}\n持续天数：{duration_days}\n协作成员：{', '.join(collaborators) or '无'}"
        f"\n负责人：{user['username']}\n详细内容：{detail[:1800]}"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是工作流规划助手。不要输出 Markdown，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1000,
    }
    try:
        data = assistant_http_json(assistant_chat_url(endpoint), api_key, body, 30)
        answer = ""
        if isinstance(data, dict):
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                answer = message.get("content") or choices[0].get("text") or ""
            answer = answer or data.get("answer") or data.get("content") or json.dumps(data, ensure_ascii=False)
        else:
            answer = json.dumps(data, ensure_ascii=False)
        parsed = extract_json_object(answer)
        plan.update(
            {
                "source": source,
                "model": model,
                "summary": str((parsed or {}).get("summary") or "AI 已生成工作流阶段。")[:600],
                "raw": answer[:4000],
                "thinking": plan["thinking"]
                + [
                    "AI 已返回规划内容",
                    "解析阶段 JSON 并写入工作流数据库",
                ],
                "stages": normalize_ai_stages(parsed or {}, fallback_stages, collaborators, user["username"], duration_days),
            }
        )
    except Exception as exc:
        plan["thinking"].append(f"AI 调用失败，使用本地模板继续创建：{exc}")
        plan["stages"] = fallback_stages
    return plan


def workflow_project_from_row(row):
    project = dict(row)
    project["collaborators"] = workflow_json_load(project.pop("collaborators_json", "[]"), [])
    project["file"] = workflow_json_load(project.pop("file_json", "{}"), {})
    project["ai_plan"] = workflow_json_load(project.pop("ai_plan_json", "{}"), {})
    project["status_label"] = workflow_status_label(project.get("status"))
    project["progress"] = max(0, min(100, parse_int(project.get("progress"), 0)))
    return project


def workflow_user_can_view(project, user):
    if not user:
        return False
    if is_admin_role(user):
        return True
    if project.get("owner_username") == user["username"]:
        return True
    collaborators = project.get("collaborators") or []
    if user["username"] in collaborators:
        return True
    owner_group = str(project.get("owner_group") or "")
    return bool(owner_group and owner_group == str(user["team_group"] or ""))


def workflow_user_can_edit(project, user):
    return workflow_user_can_view(project, user)


def workflow_visible_projects(user):
    with db() as conn:
        rows = conn.execute(
            "SELECT * FROM workflow_projects ORDER BY datetime(updated_at) DESC, id DESC LIMIT 120"
        ).fetchall()
        stage_rows = conn.execute(
            "SELECT * FROM workflow_stages ORDER BY workflow_id, start_day, id"
        ).fetchall()
        update_rows = conn.execute(
            "SELECT * FROM workflow_updates ORDER BY datetime(created_at) DESC, id DESC LIMIT 200"
        ).fetchall()
    stages_by_workflow = {}
    for row in stage_rows:
        stage = dict(row)
        stage["status_label"] = workflow_status_label(stage.get("status"))
        stages_by_workflow.setdefault(stage["workflow_id"], []).append(stage)
    updates_by_workflow = {}
    for row in update_rows:
        update = dict(row)
        update["file"] = workflow_json_load(update.pop("file_json", "{}"), {})
        updates_by_workflow.setdefault(update["workflow_id"], []).append(update)
    projects = []
    for row in rows:
        project = workflow_project_from_row(row)
        if not workflow_user_can_view(project, user):
            continue
        project["stages"] = stages_by_workflow.get(project["workflow_id"], [])
        project["updates"] = updates_by_workflow.get(project["workflow_id"], [])[:10]
        projects.append(project)
    return projects


def workflow_payload(user):
    projects = workflow_visible_projects(user)
    users = workflow_user_options()
    return {
        "projects": projects,
        "users": users,
        "groups": list(TEAM_GROUPS),
        "count": len(projects),
        "viewer": {
            "username": user["username"],
            "role": user["role"],
            "team_group": user["team_group"] or "",
        },
        "updated_at": now_text(),
    }


def update_workflow_project_rollup(conn, workflow_id):
    stages = conn.execute("SELECT status, progress FROM workflow_stages WHERE workflow_id = ?", (workflow_id,)).fetchall()
    if not stages:
        return
    progress = round(sum(max(0, min(100, parse_int(row["progress"], 0))) for row in stages) / len(stages))
    statuses = {row["status"] for row in stages}
    if all(row["status"] == "done" or parse_int(row["progress"], 0) >= 100 for row in stages):
        status = "done"
        progress = 100
    elif "blocked" in statuses:
        status = "blocked"
    elif any(row["status"] in ("active", "review") or parse_int(row["progress"], 0) > 0 for row in stages):
        status = "active"
    else:
        status = "planned"
    conn.execute(
        "UPDATE workflow_projects SET progress = ?, status = ?, updated_at = ? WHERE workflow_id = ?",
        (progress, status, now_text(), workflow_id),
    )


def bom_owner_identity(record):
    owner = record.get("owner") if isinstance(record.get("owner"), dict) else {}
    owner_user_id = record.get("owner_user_id") or owner.get("user_id")
    owner_username = record.get("owner_username") or owner.get("username") or ""
    upload_id = parse_int(record.get("upload_id"), 0)
    if upload_id and (not owner_username or not owner_user_id):
        try:
            with db() as conn:
                row = conn.execute("SELECT uploaded_by FROM bom_uploads WHERE id = ?", (upload_id,)).fetchone()
                if row and not owner_username:
                    owner_username = row["uploaded_by"]
                if owner_username and not owner_user_id:
                    user_row = conn.execute("SELECT id FROM users WHERE username = ?", (owner_username,)).fetchone()
                    if user_row:
                        owner_user_id = user_row["id"]
        except Exception:
            pass
    return owner_user_id, str(owner_username or "")


def pcb_file_metadata_matches(item, pcb_file_id):
    target = str(pcb_file_id or "").strip()
    if not target or not isinstance(item, dict):
        return False
    candidates = (item.get("pcb_id"), item.get("pcb_file_id"), item.get("id"))
    return any(str(candidate or "") == target for candidate in candidates)


def pcb_file_metadata_id(item):
    if not isinstance(item, dict):
        return ""
    for key in ("pcb_id", "pcb_file_id", "id"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return ""


def pcb_file_is_internal_ibom_html(item, extension=""):
    if not isinstance(item, dict):
        return False
    extension = str(extension or item.get("extension") or "").lower()
    if not extension:
        extension = Path(str(item.get("stored_filename") or item.get("original_filename") or "")).suffix.lower()
    if extension not in (".html", ".htm"):
        return False
    filename = str(item.get("original_filename") or item.get("stored_filename") or "").lower()
    if str(item.get("conversion_source") or "") == "internal_epcb_parser":
        return True
    return (
        filename.endswith(".ibom.html")
        and bool(item.get("derived_from_pcb_id"))
        and str(item.get("processing_status") or "").lower() == "converted"
    )


def find_pcb_file_metadata(record, pcb_file_id):
    target = str(pcb_file_id or "").strip()
    if not target:
        return None
    pcb_files = record.get("pcb_files") if isinstance(record.get("pcb_files"), list) else []
    for item in pcb_files:
        if pcb_file_metadata_matches(item, target):
            return item
    return None


def pcb_file_view_url(bom_id, pcb_file_id):
    return f"/pcb/{urllib.parse.quote(str(bom_id or ''))}/{urllib.parse.quote(str(pcb_file_id or ''))}"


def pcb_preview_token_secret():
    key = data_encryption_key(required=False)
    if key:
        return key
    seed = "|".join(
        [
            str(DEFAULT_PASSWORD or ""),
            str(CONFIG_PATH.resolve()),
            str(DATA_DIR.resolve()),
            str(APP_ENV or ""),
        ]
    )
    return hashlib.sha256(seed.encode("utf-8", errors="ignore")).digest()


def user_username_value(user):
    if not user:
        return ""
    if isinstance(user, dict):
        return str(user.get("username") or "")
    try:
        return str(user["username"] or "")
    except Exception:
        return str(getattr(user, "username", "") or "")


def make_pcb_preview_token(bom_id, pcb_file_id, user=None, now=None):
    expires = int((now or time.time()) + PCB_PREVIEW_TOKEN_TTL_SECONDS)
    username = user_username_value(user)
    payload = "|".join([str(bom_id or ""), str(pcb_file_id or ""), str(expires), username])
    signature = hmac.new(pcb_preview_token_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{expires}.{urllib.parse.quote(username, safe='')}.{signature}"


def verify_pcb_preview_token(token, bom_id, pcb_file_id, user=None, now=None):
    parts = str(token or "").split(".", 2)
    if len(parts) != 3:
        return False
    expires_text, username_quoted, signature = parts
    if not re.fullmatch(r"\d{9,12}", expires_text or ""):
        return False
    expires = int(expires_text)
    if expires < int(now or time.time()):
        return False
    username = urllib.parse.unquote(username_quoted or "")
    if user is not None and username and username != user_username_value(user):
        return False
    payload = "|".join([str(bom_id or ""), str(pcb_file_id or ""), str(expires), username])
    expected = hmac.new(pcb_preview_token_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def pcb_file_preview_url(bom_id, pcb_file_id, user=None):
    base = pcb_file_view_url(bom_id, pcb_file_id)
    token = make_pcb_preview_token(bom_id, pcb_file_id, user=user)
    return base + "?preview_token=" + urllib.parse.quote(token, safe="")


def soldering_workbench_url(bom_id, pcb_file_id=""):
    query = {"bom_id": str(bom_id or "")}
    if pcb_file_id:
        query["pcb_file_id"] = str(pcb_file_id)
    return "/soldering/workbench?" + urllib.parse.urlencode(query)


def decorate_pcb_file(record, item):
    if not isinstance(item, dict):
        return item
    decorated = dict(item)
    defaults = pcb_file_metadata_defaults(decorated)
    for key, value in defaults.items():
        if not decorated.get(key):
            decorated[key] = value
    pcb_id = decorated.get("pcb_id") or decorated.get("pcb_file_id") or decorated.get("id") or ""
    bom_id = record.get("bom_id") or ""
    if pcb_id and bom_id:
        decorated["view_url"] = pcb_file_view_url(bom_id, pcb_id)
        decorated["workbench_url"] = soldering_workbench_url(bom_id, pcb_id)
    decorated["kind_label"] = pcb_file_kind_label(decorated.get("kind"))
    decorated["status_text"] = pcb_file_compact_status(decorated)
    return decorated


def decorate_bom_record(record):
    if not isinstance(record, dict):
        return record
    decorated = dict(record)
    pcb_files = decorated.get("pcb_files") if isinstance(decorated.get("pcb_files"), list) else []
    decorated["pcb_files"] = [decorate_pcb_file(decorated, item) for item in pcb_files]
    if decorated.get("latest_pcb_file"):
        decorated["latest_pcb_file"] = decorate_pcb_file(decorated, decorated["latest_pcb_file"])
    elif decorated["pcb_files"]:
        decorated["latest_pcb_file"] = decorated["pcb_files"][-1]
    active = find_active_soldering_job_for_bom(decorated)
    if active:
        decorated["active_soldering_job"] = active
    return decorated


def parse_bom_pcb_analysis_path(path):
    parts = str(path or "").strip("/").split("/")
    if len(parts) != 6 or parts[0] != "api" or parts[1] != "boms" or parts[3] != "pcb" or parts[5] != "analysis":
        return None
    return urllib.parse.unquote(parts[2]).strip(), urllib.parse.unquote(parts[4]).strip()


def parse_analysis_job_status_path(path):
    prefix = "/api/analysis-jobs/"
    suffix = "/status"
    if not str(path or "").startswith(prefix) or not str(path or "").endswith(suffix):
        return ""
    return urllib.parse.unquote(path[len(prefix) : -len(suffix)]).strip()


def parse_analysis_job_retry_path(path):
    prefix = "/api/analysis-jobs/"
    suffix = "/retry"
    if not str(path or "").startswith(prefix) or not str(path or "").endswith(suffix):
        return ""
    return urllib.parse.unquote(path[len(prefix) : -len(suffix)]).strip()


def parse_admin_analysis_job_retry_path(path):
    prefix = "/api/admin/analysis-jobs/"
    suffix = "/retry"
    if not str(path or "").startswith(prefix) or not str(path or "").endswith(suffix):
        return ""
    return urllib.parse.unquote(path[len(prefix) : -len(suffix)]).strip()


def analysis_job_detail_url(job_id):
    job_id = str(job_id or "").strip()
    return f"/analysis-jobs/{urllib.parse.quote(job_id)}" if job_id else ""


def pcb_file_is_pdf_schematic(item):
    if not isinstance(item, dict):
        return False
    defaults = pcb_file_metadata_defaults(item)
    extension = str(item.get("extension") or "").lower()
    if not extension:
        filename = item.get("original_filename") or item.get("stored_filename") or ""
        extension = Path(str(filename)).suffix.lower()
    kind = str(item.get("kind") or defaults.get("kind") or "").strip()
    document_type = str(item.get("document_type") or defaults.get("document_type") or "").strip()
    return extension in PCB_PDF_EXTENSIONS or kind == "schematic_pdf" or document_type == "schematic_pdf"


def resolve_bom_pcb_file_for_user(user, bom_id, pcb_file_id):
    record = find_bom_record(bom_id)
    if not record:
        raise LookupError("BOM record not found.")
    if not user_can_view_bom(record, user):
        raise PermissionError("Forbidden.")
    metadata = find_pcb_file_metadata(record, pcb_file_id)
    if not metadata:
        raise FileNotFoundError("PCB file does not belong to this BOM.")
    return record, decorate_pcb_file(record, metadata)


def require_pdf_schematic_file(item):
    if not pcb_file_is_pdf_schematic(item):
        raise ValueError("Only attached PDF schematic files can be queued for analysis.")


def clamp_analysis_progress(value):
    return max(0, min(100, parse_int(value, 0)))


def normalize_analysis_status(status):
    value = str(status or "queued").strip().lower()
    if value not in ANALYSIS_JOB_STATUSES:
        raise ValueError("analysis status must be queued, running, completed, or failed.")
    return value


def sanitize_analysis_error_summary(value, max_len=ANALYSIS_ERROR_SUMMARY_MAX):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"(?i)(api[_-]?key|authorization|bearer)\s*[:=]\s*\S+", r"\1=[redacted]", text)
    if len(text) > max_len:
        text = text[: max(0, max_len - 3)].rstrip() + "..."
    return text


def analysis_job_from_row(row):
    if not row:
        return None
    job = dict(row)
    job["job_id"] = str(job.get("job_id") or "")
    job["detail_url"] = analysis_job_detail_url(job.get("job_id"))
    job["progress"] = clamp_analysis_progress(job.get("progress"))
    raw_result = job.get("result_json") or "{}"
    try:
        parsed_result = json.loads(raw_result) if isinstance(raw_result, str) else raw_result
    except Exception:
        parsed_result = {}
    job["result_json"] = parsed_result if isinstance(parsed_result, (dict, list)) else {}
    job["error_summary"] = sanitize_analysis_error_summary(job.get("error_summary"))
    return job


def safe_analysis_display_text(value, max_len=240):
    return sanitize_analysis_error_summary(value, max_len=max_len)


def safe_display_filename(value, max_len=180):
    name = Path(str(value or "")).name.strip()
    if len(name) > max_len:
        name = name[: max(0, max_len - 3)].rstrip() + "..."
    return safe_analysis_display_text(name, max_len=max_len)


def safe_pcb_analysis_status_payload(pcb_file):
    source = pcb_file if isinstance(pcb_file, dict) else {}
    pcb_id = pcb_file_metadata_id(source)
    payload = {
        "pcb_id": pcb_id,
        "pcb_file_id": pcb_id,
        "original_filename": safe_display_filename(source.get("original_filename")),
        "kind": str(source.get("kind") or ""),
        "file_role": str(source.get("file_role") or ""),
        "document_type": str(source.get("document_type") or ""),
        "processing_status": safe_analysis_display_text(source.get("processing_status"), max_len=80),
        "analysis_status": safe_analysis_display_text(source.get("analysis_status"), max_len=80),
        "analysis_progress": clamp_analysis_progress(source.get("analysis_progress")),
        "analysis_job_id": str(source.get("analysis_job_id") or source.get("latest_analysis_job_id") or ""),
        "latest_analysis_job_id": str(source.get("latest_analysis_job_id") or source.get("analysis_job_id") or ""),
        "analysis_updated_at": str(source.get("analysis_updated_at") or ""),
        "analysis_started_at": str(source.get("analysis_started_at") or ""),
        "analysis_finished_at": str(source.get("analysis_finished_at") or ""),
        "analysis_error_summary": sanitize_analysis_error_summary(source.get("analysis_error_summary")),
        "analysis_result_summary": safe_analysis_display_text(source.get("analysis_result_summary"), max_len=600),
        "size_bytes": parse_int(source.get("size_bytes"), 0) if source.get("size_bytes") is not None else None,
        "mime_type": safe_analysis_display_text(source.get("mime_type"), max_len=120),
        "view_url": str(source.get("view_url") or ""),
        "workbench_url": str(source.get("workbench_url") or ""),
        "status_text": safe_analysis_display_text(source.get("status_text") or pcb_file_compact_status(source), max_len=220),
    }
    return payload


def safe_bom_pcb_analysis_status_record(record):
    source = record if isinstance(record, dict) else {}
    pcb_files = source.get("pcb_files") if isinstance(source.get("pcb_files"), list) else []
    return {
        "bom_id": str(source.get("bom_id") or ""),
        "upload_id": parse_int(source.get("upload_id"), 0) or None,
        "original_filename": safe_display_filename(source.get("original_filename")),
        "latest_analysis_job_id": str(source.get("latest_analysis_job_id") or ""),
        "latest_analysis_status": safe_analysis_display_text(source.get("latest_analysis_status"), max_len=80),
        "latest_analysis_updated_at": str(source.get("latest_analysis_updated_at") or ""),
        "pcb_files": [safe_pcb_analysis_status_payload(item) for item in pcb_files if isinstance(item, dict)],
    }


def safe_analysis_job_status_payload(job):
    if not job:
        return None
    status = str(job.get("status") or "")
    return {
        "job_id": str(job.get("job_id") or ""),
        "owner_username": str(job.get("owner_username") or ""),
        "bom_id": str(job.get("bom_id") or ""),
        "pcb_file_id": str(job.get("pcb_file_id") or ""),
        "task_type": str(job.get("task_type") or ""),
        "status": status,
        "progress": clamp_analysis_progress(job.get("progress")),
        "error_summary": sanitize_analysis_error_summary(job.get("error_summary")) if status == "failed" else "",
        "created_at": str(job.get("created_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
        "started_at": str(job.get("started_at") or ""),
        "finished_at": str(job.get("finished_at") or ""),
        "detail_url": analysis_job_detail_url(job.get("job_id")),
    }


def safe_analysis_display_list(values, limit=5, max_len=220):
    if not isinstance(values, list):
        return []
    cleaned = []
    for item in values:
        text = ""
        if isinstance(item, dict):
            for key in ("summary", "title", "name", "message", "note", "text"):
                if str(item.get(key) or "").strip():
                    text = item.get(key)
                    break
        else:
            text = item
        text = safe_analysis_display_text(text, max_len=max_len)
        if text:
            cleaned.append(text)
        if len(cleaned) >= limit:
            break
    return cleaned


def safe_analysis_result_preview(result):
    if not isinstance(result, dict) or not result:
        return {}
    preview = {
        "analysis_type": safe_analysis_display_text(result.get("analysis_type"), max_len=120),
        "summary": safe_analysis_display_text(result.get("summary"), max_len=600),
        "risks": safe_analysis_display_list(result.get("risks"), limit=6),
        "recommendations": safe_analysis_display_list(result.get("recommendations"), limit=6),
        "manual_review": safe_analysis_display_list(result.get("manual_review"), limit=6),
        "notes": safe_analysis_display_list(result.get("notes"), limit=4),
    }
    module_items = result.get("detected_modules") if isinstance(result.get("detected_modules"), list) else []
    modules = []
    for item in module_items:
        if not isinstance(item, dict):
            continue
        modules.append(
            {
                "name": safe_analysis_display_text(item.get("name"), max_len=120),
                "matches": parse_int(item.get("matches"), 0),
                "keywords": safe_analysis_display_list(item.get("keywords"), limit=8, max_len=60),
            }
        )
        if len(modules) >= PDF_ANALYSIS_MAX_DETECTED_ITEMS:
            break
    preview["detected_modules"] = [item for item in modules if item.get("name")]
    section_items = result.get("detected_sections") if isinstance(result.get("detected_sections"), list) else []
    sections = []
    for item in section_items:
        if not isinstance(item, dict):
            continue
        title = safe_analysis_display_text(item.get("title"), max_len=180)
        if title:
            sections.append({"title": title, "line": parse_int(item.get("line"), 0)})
        if len(sections) >= PDF_ANALYSIS_MAX_DETECTED_ITEMS:
            break
    preview["detected_sections"] = sections
    source = result.get("source") if isinstance(result.get("source"), dict) else {}
    preview["source"] = {
        "filename": safe_display_filename(source.get("filename")),
        "mode": safe_analysis_display_text(source.get("mode"), max_len=80),
        "extractor": safe_analysis_display_text(source.get("extractor"), max_len=80),
        "page_count": parse_int(source.get("page_count"), 0),
        "character_count": parse_int(source.get("character_count"), 0),
        "analyzed_character_count": parse_int(source.get("analyzed_character_count"), 0),
        "text_truncated": bool(source.get("text_truncated")),
    }
    return preview


def analysis_admin_filter_values(query=None):
    query = query or {}
    status = str(payload_value(query, "status", default="all") or "all").strip().lower()
    if not status:
        status = "all"
    if status != "all" and status not in ANALYSIS_JOB_STATUSES:
        raise ValueError("status must be all, queued, running, completed, or failed.")
    task_type = str(payload_value(query, "type", "task_type", default="") or "").strip()
    if task_type.lower() == "all":
        task_type = ""
    owner = str(payload_value(query, "owner", "username", default="") or "").strip()[:120]
    search = str(payload_value(query, "q", "query", default="") or "").strip()[:120]
    limit = clamp_int(payload_value(query, "limit", default=25), 25, 1, 100)
    return {"status": status, "type": task_type, "owner": owner, "q": search, "limit": limit}


def analysis_admin_where_parts(filters, include_status=True):
    clauses = []
    params = []
    task_type = str((filters or {}).get("type") or "").strip()
    if task_type:
        clauses.append("task_type = ?")
        params.append(task_type)
    status = str((filters or {}).get("status") or "all").strip().lower()
    if include_status and status and status != "all":
        clauses.append("status = ?")
        params.append(status)
    owner = str((filters or {}).get("owner") or "").strip()
    if owner:
        clauses.append("owner_username = ?")
        params.append(owner)
    search = str((filters or {}).get("q") or "").strip()
    if search:
        like = f"%{search}%"
        clauses.append(
            "(job_id LIKE ? OR owner_username LIKE ? OR bom_id LIKE ? OR pcb_file_id LIKE ? OR error_summary LIKE ?)"
        )
        params.extend([like, like, like, like, like])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def analysis_owner_lookup(usernames):
    names = sorted({str(name or "").strip() for name in usernames if str(name or "").strip()})
    if not names:
        return {}
    placeholders = ",".join("?" for _ in names)
    with db() as conn:
        rows = conn.execute(
            f"SELECT id, username, role FROM users WHERE username IN ({placeholders})",
            names,
        ).fetchall()
    return {str(row["username"]): {"id": row["id"], "username": row["username"], "role": row["role"]} for row in rows}


def analysis_retry_url_for_user(job_id, user=None):
    job_id = str(job_id or "").strip()
    if not job_id:
        return ""
    quoted = urllib.parse.quote(job_id)
    if user is not None and not is_admin_role(user):
        return f"/api/analysis-jobs/{quoted}/retry"
    return f"/api/admin/analysis-jobs/{quoted}/retry"


def analysis_job_admin_payload(job, owner_lookup=None, bom_lookup=None, user=None, include_stored_filenames=True):
    job = job or {}
    owner_lookup = owner_lookup or {}
    bom_lookup = bom_lookup or {}
    bom_id = str(job.get("bom_id") or "")
    record = bom_lookup.get(bom_id)
    if record is None and bom_id:
        candidate = find_bom_record(bom_id)
        if candidate and (user is None or user_can_view_bom(candidate, user)):
            record = candidate
    pcb_file = find_pcb_file_metadata(record or {}, job.get("pcb_file_id")) if record else None
    decorated_file = decorate_pcb_file(record, pcb_file) if record and pcb_file else {}
    owner = owner_lookup.get(str(job.get("owner_username") or ""), {})
    result_preview = safe_analysis_result_preview(job.get("result_json"))
    result_summary = result_preview.get("summary") or ""
    if not result_summary and isinstance(job.get("result_json"), dict):
        result_summary = safe_analysis_display_text(job.get("result_json", {}).get("summary"), max_len=600)
    retryable_file = bool(pcb_file and pcb_file_is_pdf_schematic(decorated_file or pcb_file))
    retry_allowed_for_user = (
        user is None
        or is_admin_role(user)
        or (record is not None and user_can_access_analysis_job(job, user) and user_can_view_bom(record, user))
    )
    can_retry = (
        str(job.get("status") or "") == "failed"
        and str(job.get("task_type") or "") == ANALYSIS_JOB_TASK_TYPE
        and retryable_file
        and retry_allowed_for_user
    )
    job_id = str(job.get("job_id") or "")
    bom_filename_source = (record or {}).get("original_filename")
    if not bom_filename_source and include_stored_filenames:
        bom_filename_source = (record or {}).get("stored_filename")
    pcb_filename_source = (decorated_file or {}).get("original_filename")
    if not pcb_filename_source and include_stored_filenames:
        pcb_filename_source = (decorated_file or {}).get("stored_filename")
    return {
        "job_id": job_id,
        "owner_username": str(job.get("owner_username") or ""),
        "owner_id": owner.get("id"),
        "owner": owner or {"username": str(job.get("owner_username") or "")},
        "job_type": str(job.get("task_type") or ""),
        "task_type": str(job.get("task_type") or ""),
        "status": str(job.get("status") or ""),
        "progress": clamp_analysis_progress(job.get("progress")),
        "error_summary": sanitize_analysis_error_summary(job.get("error_summary")),
        "result_summary": result_summary,
        "result_preview": result_preview,
        "bom_id": bom_id,
        "bom_upload_id": parse_int((record or {}).get("upload_id"), 0) or None,
        "bom_filename": safe_display_filename(bom_filename_source),
        "pcb_file_id": str(job.get("pcb_file_id") or ""),
        "original_filename": safe_display_filename(pcb_filename_source),
        "file_kind": str((decorated_file or {}).get("kind") or ""),
        "document_type": str((decorated_file or {}).get("document_type") or ""),
        "status_text": safe_analysis_display_text((decorated_file or {}).get("status_text"), max_len=180),
        "created_at": str(job.get("created_at") or ""),
        "updated_at": str(job.get("updated_at") or ""),
        "started_at": str(job.get("started_at") or ""),
        "completed_at": str(job.get("finished_at") or ""),
        "finished_at": str(job.get("finished_at") or ""),
        "detail_url": analysis_job_detail_url(job_id),
        "status_url": f"/api/analysis-jobs/{urllib.parse.quote(job_id)}" if job_id else "",
        "retry_url": analysis_retry_url_for_user(job_id, user=user) if can_retry and job_id else "",
        "can_retry": can_retry,
    }


def admin_analysis_jobs_payload(query=None):
    filters = analysis_admin_filter_values(query or {})
    where, params = analysis_admin_where_parts(filters, include_status=True)
    count_where, count_params = analysis_admin_where_parts(filters, include_status=False)
    with db() as conn:
        total_row = conn.execute(f"SELECT COUNT(*) AS count FROM analysis_jobs{where}", params).fetchone()
        rows = conn.execute(
            f"""
            SELECT * FROM analysis_jobs{where}
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT ?
            """,
            (*params, filters["limit"]),
        ).fetchall()
        count_rows = conn.execute(
            f"SELECT status, COUNT(*) AS count FROM analysis_jobs{count_where} GROUP BY status",
            count_params,
        ).fetchall()
    jobs = [analysis_job_from_row(row) for row in rows]
    owner_lookup = analysis_owner_lookup(job.get("owner_username") for job in jobs if job)
    bom_lookup = {str(record.get("bom_id") or ""): record for record in load_bom_records()}
    display_jobs = [analysis_job_admin_payload(job, owner_lookup=owner_lookup, bom_lookup=bom_lookup) for job in jobs if job]
    status_counts = {status: 0 for status in ANALYSIS_JOB_STATUSES}
    for row in count_rows:
        status = str(row["status"] or "")
        if status in status_counts:
            status_counts[status] = parse_int(row["count"], 0)
    summary = {
        "total_count": sum(status_counts.values()),
        "filtered_count": parse_int(total_row["count"] if total_row else 0, 0),
        "queued_count": status_counts.get("queued", 0),
        "running_count": status_counts.get("running", 0),
        "completed_count": status_counts.get("completed", 0),
        "failed_count": status_counts.get("failed", 0),
        "status_counts": status_counts,
    }
    return {
        "jobs": display_jobs,
        "count": len(display_jobs),
        "summary": summary,
        "filters": filters,
        "actions": {
            "can_process_queued": True,
            "process_queued_url": "/api/admin/analysis-jobs/process-queued",
            "retry_failed_url_template": "/api/admin/analysis-jobs/{job_id}/retry",
        },
    }


def analysis_history_filter_values(query=None, user=None):
    query = query or {}
    status = str(payload_value(query, "status", default="all") or "all").strip().lower()
    if not status:
        status = "all"
    if status != "all" and status not in ANALYSIS_JOB_STATUSES:
        raise ValueError("status must be all, queued, running, completed, or failed.")
    search = re.sub(r"[\x00-\x1f\x7f]+", " ", str(payload_value(query, "q", "query", default="") or ""))
    search = re.sub(r"\s+", " ", search).strip()[:120]
    filters = {
        "status": status,
        "q": search,
        "limit": clamp_int(payload_value(query, "limit", default=ANALYSIS_HISTORY_DEFAULT_LIMIT), ANALYSIS_HISTORY_DEFAULT_LIMIT, 1, ANALYSIS_HISTORY_MAX_LIMIT),
        "owner": "",
    }
    if is_admin_role(user):
        owner = re.sub(r"[\x00-\x1f\x7f]+", " ", str(payload_value(query, "owner", "username", default="") or ""))
        filters["owner"] = re.sub(r"\s+", " ", owner).strip()[:120]
    return filters


def analysis_history_query(filters, extra=None):
    merged = dict(filters or {})
    merged.update(extra or {})
    params = {}
    for key in ("status", "owner", "q"):
        value = str(merged.get(key) or "").strip()
        if value and not (key == "status" and value == "all"):
            params[key] = value
    limit = parse_int(merged.get("limit"), ANALYSIS_HISTORY_DEFAULT_LIMIT)
    if limit != ANALYSIS_HISTORY_DEFAULT_LIMIT:
        params["limit"] = str(limit)
    return urllib.parse.urlencode(params)


def analysis_history_rows_for_user(user, filters):
    clauses = ["task_type = ?"]
    params = [ANALYSIS_JOB_TASK_TYPE]
    if is_admin_role(user):
        owner = str((filters or {}).get("owner") or "").strip()
        if owner:
            clauses.append("owner_username = ?")
            params.append(owner)
    else:
        clauses.append("owner_username = ?")
        params.append(str(user["username"] if user else ""))
    where = " WHERE " + " AND ".join(clauses)
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM analysis_jobs{where}
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT ?
            """,
            (*params, ANALYSIS_HISTORY_CANDIDATE_LIMIT),
        ).fetchall()
    return rows


def analysis_history_job_matches_search(job, search, include_owner=False):
    needle = str(search or "").strip().lower()
    if not needle:
        return True
    preview = job.get("result_preview") if isinstance(job.get("result_preview"), dict) else {}
    source = preview.get("source") if isinstance(preview.get("source"), dict) else {}
    fields = [
        job.get("job_id"),
        job.get("bom_id"),
        job.get("bom_upload_id"),
        job.get("bom_filename"),
        job.get("pcb_file_id"),
        job.get("original_filename"),
        job.get("error_summary"),
        source.get("filename"),
        source.get("mode"),
        source.get("extractor"),
    ]
    if include_owner:
        fields.append(job.get("owner_username"))
    haystack = " ".join(str(value or "") for value in fields).lower()
    return needle in haystack


def analysis_history_jobs_payload(user, query=None):
    filters = analysis_history_filter_values(query or {}, user=user)
    rows = analysis_history_rows_for_user(user, filters)
    raw_jobs = []
    for row in rows:
        job = analysis_job_from_row(row)
        if not job or str(job.get("task_type") or "") != ANALYSIS_JOB_TASK_TYPE:
            continue
        if not user_can_access_analysis_job(job, user):
            continue
        raw_jobs.append(job)
    owner_lookup = analysis_owner_lookup(job.get("owner_username") for job in raw_jobs)
    bom_lookup = {
        str(record.get("bom_id") or ""): record
        for record in load_bom_records()
        if user_can_view_bom(record, user)
    }
    display_jobs = [
        analysis_job_admin_payload(
            job,
            owner_lookup=owner_lookup,
            bom_lookup=bom_lookup,
            user=user,
            include_stored_filenames=False,
        )
        for job in raw_jobs
    ]
    include_owner = is_admin_role(user)
    searched_jobs = [
        job for job in display_jobs if analysis_history_job_matches_search(job, filters["q"], include_owner=include_owner)
    ]
    status_counts = {status: 0 for status in ANALYSIS_JOB_STATUSES}
    for job in searched_jobs:
        status = str(job.get("status") or "")
        if status in status_counts:
            status_counts[status] += 1
    if filters["status"] == "all":
        filtered_jobs = searched_jobs
    else:
        filtered_jobs = [job for job in searched_jobs if str(job.get("status") or "") == filters["status"]]
    limit = filters["limit"]
    shown_jobs = filtered_jobs[:limit]
    summary = {
        "total_count": len(searched_jobs),
        "filtered_count": len(filtered_jobs),
        "shown_count": len(shown_jobs),
        "queued_count": status_counts.get("queued", 0),
        "running_count": status_counts.get("running", 0),
        "completed_count": status_counts.get("completed", 0),
        "failed_count": status_counts.get("failed", 0),
        "status_counts": status_counts,
        "candidate_limited": len(rows) >= ANALYSIS_HISTORY_CANDIDATE_LIMIT,
        "candidate_limit": ANALYSIS_HISTORY_CANDIDATE_LIMIT,
    }
    return {"jobs": shown_jobs, "summary": summary, "filters": filters, "show_owner": include_owner}


def analysis_workbench_history_summary(jobs):
    jobs = jobs if isinstance(jobs, list) else []
    latest = jobs[0] if jobs else {}
    latest_status = safe_analysis_display_text((latest or {}).get("status"), max_len=80)
    latest_updated_at = str((latest or {}).get("updated_at") or (latest or {}).get("created_at") or "")
    latest_job_id = str((latest or {}).get("job_id") or "")
    latest_text = "暂无最新任务"
    if latest_status:
        latest_text = f"最新 {zh_status(latest_status)}"
        if latest_updated_at:
            latest_text = f"{latest_text} {latest_updated_at}"
    return {
        "count": len(jobs),
        "count_text": f"最近 {len(jobs)} 个",
        "latest_status": latest_status,
        "latest_updated_at": latest_updated_at,
        "latest_job_id": latest_job_id,
        "latest_text": latest_text,
    }


def analysis_workbench_history_payload(user, record, selected_pcb_id="", limit=ANALYSIS_WORKBENCH_HISTORY_LIMIT):
    record = record or {}
    bom_id = str(record.get("bom_id") or "")
    limit = clamp_int(limit, ANALYSIS_WORKBENCH_HISTORY_LIMIT, 1, ANALYSIS_HISTORY_MAX_LIMIT)
    base_payload = {
        "jobs": [],
        "count": 0,
        "limit": limit,
        "bom_id": bom_id,
        "cross_user_restricted": False,
        "history_url": "/analysis-jobs",
        "filtered_history_url": "/analysis-jobs",
        "admin_queue_url": "",
        "summary": analysis_workbench_history_summary([]),
    }
    if not bom_id or not user_can_view_bom(record, user):
        return base_payload
    _, owner_username = bom_owner_identity(record)
    owner_username = str(owner_username or "").strip()
    username = str(user["username"] if user else "").strip()
    query_params = {"q": bom_id, "limit": str(limit)}
    if is_admin_role(user) and owner_username:
        query_params["owner"] = owner_username
    base_payload["filtered_history_url"] = "/analysis-jobs?" + urllib.parse.urlencode(query_params)
    if is_admin_role(user) and owner_username and owner_username != username:
        base_payload["cross_user_restricted"] = True
        return base_payload

    clauses = ["bom_id = ?", "task_type = ?"]
    params = [bom_id, ANALYSIS_JOB_TASK_TYPE]
    if username:
        clauses.append("owner_username = ?")
        params.append(username)
    where = " WHERE " + " AND ".join(clauses)
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM analysis_jobs{where}
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    raw_jobs = []
    for row in rows:
        job = analysis_job_from_row(row)
        if job and user_can_access_analysis_job(job, user):
            raw_jobs.append(job)
    owner_lookup = analysis_owner_lookup(job.get("owner_username") for job in raw_jobs)
    bom_lookup = {bom_id: record}
    display_jobs = [
        analysis_job_admin_payload(
            job,
            owner_lookup=owner_lookup,
            bom_lookup=bom_lookup,
            user=user,
            include_stored_filenames=False,
        )
        for job in raw_jobs
    ]
    selected = str(selected_pcb_id or "").strip()
    for job in display_jobs:
        job["is_selected_pcb"] = bool(selected and str(job.get("pcb_file_id") or "") == selected)
    base_payload["jobs"] = display_jobs
    base_payload["count"] = len(display_jobs)
    base_payload["summary"] = analysis_workbench_history_summary(display_jobs)
    return base_payload


def analysis_job_retry_context(job_id, user=None, require_user_checks=False):
    previous = get_analysis_job(job_id)
    if not previous:
        raise LookupError("Analysis job not found.")
    if str(previous.get("task_type") or "") != ANALYSIS_JOB_TASK_TYPE or str(previous.get("status") or "") != "failed":
        raise ValueError("Only failed schematic PDF analysis jobs can be retried.")
    if require_user_checks and not user_can_access_analysis_job(previous, user):
        raise PermissionError("Forbidden.")
    record = find_bom_record(previous.get("bom_id"))
    if not record:
        raise LookupError("BOM record not found for analysis job.")
    if require_user_checks and not user_can_view_bom(record, user):
        raise PermissionError("Forbidden.")
    pcb_file = find_pcb_file_metadata(record, previous.get("pcb_file_id"))
    if not pcb_file:
        raise FileNotFoundError("PCB file metadata not found for analysis job.")
    decorated_file = decorate_pcb_file(record, pcb_file)
    require_pdf_schematic_file(decorated_file)
    return previous, record, decorated_file


def retry_failed_analysis_job(job_id, user=None, require_user_checks=False):
    previous, record, decorated_file = analysis_job_retry_context(
        job_id,
        user=user,
        require_user_checks=require_user_checks,
    )
    job, created = create_or_get_analysis_job(record, decorated_file)
    if require_user_checks:
        if not user_can_access_analysis_job(job, user):
            raise PermissionError("Forbidden.")
        updated_record = find_bom_record(job.get("bom_id")) or record
        if not user_can_view_bom(updated_record, user):
            raise PermissionError("Forbidden.")
    return previous, job, created


def active_analysis_status_placeholders():
    return ",".join("?" for _ in ANALYSIS_JOB_ACTIVE_STATUSES)


def get_analysis_job(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM analysis_jobs WHERE job_id = ?", (str(job_id or ""),)).fetchone()
    return analysis_job_from_row(row)


def user_can_access_analysis_job(job, user):
    if not job or not user:
        return False
    if is_admin_role(user):
        return True
    return str(job.get("owner_username") or "") == str(user["username"] or "")


def find_active_analysis_job_for_file(bom_id, pcb_file_id):
    with db() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM analysis_jobs
            WHERE bom_id = ?
              AND pcb_file_id = ?
              AND task_type = ?
              AND status IN ({active_analysis_status_placeholders()})
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT 1
            """,
            (str(bom_id or ""), str(pcb_file_id or ""), ANALYSIS_JOB_TASK_TYPE, *ANALYSIS_JOB_ACTIVE_STATUSES),
        ).fetchone()
    return analysis_job_from_row(row)


def find_latest_analysis_job_for_file(bom_id, pcb_file_id):
    with db() as conn:
        row = conn.execute(
            """
            SELECT * FROM analysis_jobs
            WHERE bom_id = ? AND pcb_file_id = ? AND task_type = ?
            ORDER BY datetime(updated_at) DESC, id DESC
            LIMIT 1
            """,
            (str(bom_id or ""), str(pcb_file_id or ""), ANALYSIS_JOB_TASK_TYPE),
        ).fetchone()
    return analysis_job_from_row(row)


def apply_analysis_job_to_bom_record(record, job):
    if not isinstance(record, dict) or not job:
        return record
    target = str(job.get("pcb_file_id") or "").strip()
    if not target:
        return record
    status = normalize_analysis_status(job.get("status"))
    progress = clamp_analysis_progress(job.get("progress"))
    updated_at = str(job.get("updated_at") or now_text())
    error_summary = sanitize_analysis_error_summary(job.get("error_summary")) if status == "failed" else ""
    result_payload = job.get("result_json") if isinstance(job.get("result_json"), dict) else {}
    result_summary = str(result_payload.get("summary") or "").strip()[:600] if status == "completed" else ""

    def updated_item(source):
        item = dict(source)
        item["analysis_status"] = status
        item["analysis_progress"] = progress
        item["analysis_job_id"] = job.get("job_id") or ""
        item["latest_analysis_job_id"] = job.get("job_id") or ""
        item["analysis_updated_at"] = updated_at
        item["analysis_error_summary"] = error_summary
        item["analysis_result"] = result_payload if status == "completed" else {}
        item["analysis_result_summary"] = result_summary
        if job.get("started_at"):
            item["analysis_started_at"] = job.get("started_at")
        if job.get("finished_at"):
            item["analysis_finished_at"] = job.get("finished_at")
        return item

    pcb_files = record.get("pcb_files") if isinstance(record.get("pcb_files"), list) else []
    merged = []
    matched = False
    for item in pcb_files:
        if isinstance(item, dict) and pcb_file_metadata_matches(item, target):
            merged.append(updated_item(item))
            matched = True
        else:
            merged.append(item)
    if matched:
        record["pcb_files"] = merged
        latest = record.get("latest_pcb_file")
        if isinstance(latest, dict) and pcb_file_metadata_matches(latest, target):
            record["latest_pcb_file"] = updated_item(latest)
        record["latest_analysis_job_id"] = job.get("job_id") or ""
        record["latest_analysis_status"] = status
        record["latest_analysis_updated_at"] = updated_at
    return record


def mirror_analysis_job_to_bom(job):
    if not job:
        return None
    bom_id = str(job.get("bom_id") or "")
    if not bom_id:
        return None
    return update_bom_record(bom_id, lambda current: apply_analysis_job_to_bom_record(current, job))


def create_or_get_analysis_job(record, pcb_file):
    bom_id = str(record.get("bom_id") or "")
    pcb_file_id = pcb_file_metadata_id(pcb_file)
    active = find_active_analysis_job_for_file(bom_id, pcb_file_id)
    if active:
        mirror_analysis_job_to_bom(active)
        return active, False

    _, owner_username = bom_owner_identity(record)
    if not owner_username:
        owner_username = str(pcb_file.get("uploaded_by") or "")
    now = now_text()
    job_id = f"analysis_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"
    with db() as conn:
        conn.execute(
            """
            INSERT INTO analysis_jobs (
                job_id, owner_username, bom_id, pcb_file_id, task_type, status, progress,
                error_summary, result_json, created_at, updated_at, started_at, finished_at
            )
            VALUES (?, ?, ?, ?, ?, 'queued', 0, '', '{}', ?, ?, '', '')
            """,
            (job_id, owner_username, bom_id, pcb_file_id, ANALYSIS_JOB_TASK_TYPE, now, now),
        )
    job = get_analysis_job(job_id)
    mirror_analysis_job_to_bom(job)
    return job, True


def update_analysis_job_status(job_id, status, progress=None, error_summary="", result_json=None):
    existing = get_analysis_job(job_id)
    if not existing:
        return None
    next_status = normalize_analysis_status(status)
    if progress is None:
        next_progress = 100 if next_status == "completed" else clamp_analysis_progress(existing.get("progress"))
    else:
        next_progress = clamp_analysis_progress(progress)
    next_error = sanitize_analysis_error_summary(error_summary) if next_status == "failed" else ""
    now = now_text()
    started_at = existing.get("started_at") or ""
    finished_at = existing.get("finished_at") or ""
    if next_status in ("running", "completed", "failed") and not started_at:
        started_at = now
    if next_status in ("completed", "failed"):
        finished_at = now
    elif next_status in ("queued", "running"):
        finished_at = ""
    raw_result = existing.get("result_json") if isinstance(existing.get("result_json"), (dict, list)) else {}
    if result_json is not None:
        raw_result = result_json if isinstance(result_json, (dict, list)) else {"value": str(result_json)}
    result_text = json.dumps(raw_result or {}, ensure_ascii=False)
    with db() as conn:
        conn.execute(
            """
            UPDATE analysis_jobs
            SET status = ?, progress = ?, error_summary = ?, result_json = ?,
                updated_at = ?, started_at = ?, finished_at = ?
            WHERE job_id = ?
            """,
            (next_status, next_progress, next_error, result_text, now, started_at, finished_at, str(job_id or "")),
        )
    updated = get_analysis_job(job_id)
    mirror_analysis_job_to_bom(updated)
    return updated


def count_queued_analysis_jobs():
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM analysis_jobs
            WHERE task_type = ? AND status = 'queued'
            """,
            (ANALYSIS_JOB_TASK_TYPE,),
        ).fetchone()
    return parse_int(row["count"] if row else 0, 0)


def claim_next_queued_analysis_job():
    now = now_text()
    row = None
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        candidate = conn.execute(
            """
            SELECT * FROM analysis_jobs
            WHERE task_type = ? AND status = 'queued'
            ORDER BY datetime(created_at) ASC, id ASC
            LIMIT 1
            """,
            (ANALYSIS_JOB_TASK_TYPE,),
        ).fetchone()
        if not candidate:
            return None
        updated = conn.execute(
            """
            UPDATE analysis_jobs
            SET status = 'running',
                progress = ?,
                error_summary = '',
                updated_at = ?,
                started_at = CASE WHEN COALESCE(started_at, '') = '' THEN ? ELSE started_at END,
                finished_at = ''
            WHERE id = ? AND status = 'queued'
            """,
            (10, now, now, candidate["id"]),
        )
        if updated.rowcount != 1:
            return None
        row = conn.execute("SELECT * FROM analysis_jobs WHERE id = ?", (candidate["id"],)).fetchone()
    job = analysis_job_from_row(row)
    mirror_analysis_job_to_bom(job)
    return job


def attached_pdf_path_from_metadata(item):
    relative = str((item or {}).get("relative_path") or "").strip()
    if not relative:
        raise FileNotFoundError("Attached PDF file path is missing.")
    target = (DATA_DIR / relative).resolve()
    if not path_is_relative_to(target, PCB_UPLOADS_DIR):
        raise FileNotFoundError("Attached PDF file path is outside the PCB upload area.")
    if not target.exists() or not target.is_file():
        raise FileNotFoundError("Attached PDF file is missing.")
    return target


def extract_pdf_text_local(pdf_path):
    pdf_reader = None
    extractor_name = ""
    try:
        from pypdf import PdfReader as pdf_reader  # type: ignore

        extractor_name = "pypdf"
    except Exception:
        try:
            from PyPDF2 import PdfReader as pdf_reader  # type: ignore

            extractor_name = "PyPDF2"
        except Exception as exc:
            raise RuntimeError("PDF text extraction library unavailable; install pypdf or PyPDF2.") from exc

    page_texts = []
    page_errors = []
    pdf_bytes = read_data_bytes(pdf_path)
    with io.BytesIO(pdf_bytes) as handle:
        reader = pdf_reader(handle)
        pages = list(getattr(reader, "pages", []) or [])
        for index, page in enumerate(pages, 1):
            try:
                page_texts.append(str(page.extract_text() or ""))
            except Exception as exc:
                page_errors.append(f"page {index}: {sanitize_analysis_error_summary(str(exc), max_len=120)}")
                page_texts.append("")
    text = "\n\n".join(page_texts)
    return {
        "text": text,
        "page_count": len(page_texts),
        "character_count": len(text),
        "extractor": extractor_name,
        "page_errors": page_errors[:5],
    }


def normalize_pdf_extraction_result(result):
    payload = result if isinstance(result, dict) else {"text": str(result or "")}
    text = str(payload.get("text") or "")
    page_count = parse_int(payload.get("page_count"), 0)
    if page_count <= 0 and isinstance(payload.get("pages"), list):
        page_count = len(payload.get("pages") or [])
    return {
        "text": text,
        "page_count": max(0, page_count),
        "character_count": parse_int(payload.get("character_count"), len(text)),
        "extractor": str(payload.get("extractor") or "local_stub").strip()[:80],
        "page_errors": [
            sanitize_analysis_error_summary(item, max_len=120)
            for item in (payload.get("page_errors") if isinstance(payload.get("page_errors"), list) else [])
            if str(item or "").strip()
        ][:5],
    }


def clean_pdf_analysis_line(value, max_len=180):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        text = text[: max(0, max_len - 3)].rstrip() + "..."
    return text


def pdf_analysis_lines(text):
    lines = []
    for raw in str(text or "").splitlines():
        clean = clean_pdf_analysis_line(raw)
        if clean:
            lines.append(clean)
        if len(lines) >= 500:
            break
    return lines


PDF_ANALYSIS_MODULE_RULES = (
    ("power", "Power supply", ("vcc", "vdd", "3v3", "3.3v", "5v", "vin", "gnd", "ldo", "regulator", "buck", "boost")),
    ("mcu", "MCU/digital control", ("mcu", "microcontroller", "gpio", "reset", "boot", "swd", "jtag", "xtal", "crystal")),
    ("interfaces", "Interfaces/connectors", ("usb", "uart", "i2c", "spi", "can", "connector", "header", "jst", "type-c")),
    ("protection", "Protection", ("esd", "tvs", "fuse", "polyfuse", "reverse", "diode", "clamp", "protection")),
    ("analog", "Analog/sensor", ("adc", "dac", "opamp", "op-amp", "sensor", "amplifier", "filter", "reference")),
    ("rf", "RF/wireless", ("antenna", "rf", "wifi", "ble", "bluetooth", "lora", "radio")),
)


def detect_pdf_sections(lines):
    sections = []
    seen = set()
    section_terms = {
        "power",
        "supply",
        "usb",
        "interface",
        "connector",
        "mcu",
        "microcontroller",
        "sensor",
        "analog",
        "protection",
        "rf",
        "wireless",
        "sheet",
    }
    for index, line in enumerate(lines, 1):
        lower = line.lower()
        if len(line) > 90 or not any(term in lower for term in section_terms):
            continue
        key = lower
        if key in seen:
            continue
        seen.add(key)
        sections.append({"title": line, "line": index})
        if len(sections) >= PDF_ANALYSIS_MAX_DETECTED_ITEMS:
            break
    return sections


def detect_pdf_modules(lines):
    modules = []
    lowered_lines = [line.lower() for line in lines]
    for module_id, label, keywords in PDF_ANALYSIS_MODULE_RULES:
        evidence = []
        matches = 0
        for original, lowered in zip(lines, lowered_lines):
            line_hits = [keyword for keyword in keywords if keyword in lowered]
            if not line_hits:
                continue
            matches += len(line_hits)
            if len(evidence) < 3:
                evidence.append(original)
        if matches:
            modules.append(
                {
                    "id": module_id,
                    "name": label,
                    "matches": matches,
                    "keywords": [keyword for keyword in keywords if any(keyword in line for line in lowered_lines)][:8],
                    "evidence": evidence,
                }
            )
    return modules


def build_local_pdf_analysis_result(extraction, job, record, pcb_file):
    normalized = normalize_pdf_extraction_result(extraction)
    full_text = normalized["text"]
    analysis_text = full_text[:PDF_ANALYSIS_TEXT_CHAR_LIMIT]
    lines = pdf_analysis_lines(analysis_text)
    modules = detect_pdf_modules(lines)
    sections = detect_pdf_sections(lines)
    module_names = [item["name"] for item in modules]
    notes = ["Local deterministic placeholder analysis; no external AI, OCR, or DeepSeek call was made."]
    risks = []
    recommendations = []
    manual_review = []
    if normalized["page_errors"]:
        notes.append("Some pages could not be text-extracted: " + "; ".join(normalized["page_errors"]))
    if not full_text.strip():
        summary = "No extractable schematic text was found in the PDF."
        risks.append("The PDF may be image-only or flattened, so text-only analysis cannot inspect schematic details.")
        recommendations.append("Review the schematic visually and consider adding OCR before model-backed analysis.")
        manual_review.append("Open the PDF preview and verify power rails, connectors, protection, and reference designators manually.")
    else:
        detected = ", ".join(module_names) if module_names else "no known module keyword groups"
        summary = (
            f"Local PDF text extraction found {normalized['character_count']} characters across "
            f"{normalized['page_count']} pages; detected {detected}."
        )
        if "Power supply" not in module_names:
            risks.append("No explicit power-supply keywords were detected in extracted text.")
            manual_review.append("Confirm voltage rails, regulators, decoupling, and ground symbols in the schematic.")
        else:
            recommendations.append("Check regulator input/output limits and decoupling capacitor placement against the PCB layout.")
        if "Protection" not in module_names:
            risks.append("No explicit ESD/fuse/protection keywords were detected in extracted text.")
            recommendations.append("Review external connectors for ESD, reverse-polarity, and over-current protection needs.")
        if "Interfaces/connectors" in module_names:
            manual_review.append("Verify connector pinout, orientation, cable power limits, and mating part assumptions.")
        if not modules:
            manual_review.append("Keyword detection was sparse; inspect schematic hierarchy and sheet names manually.")
    return {
        "schema_version": 1,
        "analysis_type": "local_schematic_pdf_text",
        "summary": summary,
        "detected_sections": sections,
        "detected_modules": modules,
        "risks": risks,
        "notes": notes,
        "recommendations": recommendations,
        "manual_review": manual_review,
        "source": {
            "bom_id": str((job or {}).get("bom_id") or (record or {}).get("bom_id") or ""),
            "pcb_file_id": str((job or {}).get("pcb_file_id") or pcb_file_metadata_id(pcb_file)),
            "filename": str((pcb_file or {}).get("original_filename") or (pcb_file or {}).get("stored_filename") or ""),
            "mode": "local_pdf_text",
            "extractor": normalized["extractor"],
            "page_count": normalized["page_count"],
            "character_count": normalized["character_count"],
            "analyzed_character_count": len(analysis_text),
            "line_count": len(lines),
            "text_truncated": len(full_text) > len(analysis_text),
        },
    }


def run_local_pdf_analysis_for_job(job, extractor=None):
    if not job:
        raise LookupError("Analysis job not found.")
    record = find_bom_record(job.get("bom_id"))
    if not record:
        raise LookupError("BOM record not found for analysis job.")
    pcb_file = find_pcb_file_metadata(record, job.get("pcb_file_id"))
    if not pcb_file:
        raise FileNotFoundError("PCB file metadata not found for analysis job.")
    decorated_file = decorate_pcb_file(record, pcb_file)
    require_pdf_schematic_file(decorated_file)
    pdf_path = attached_pdf_path_from_metadata(decorated_file)
    extraction = extractor(pdf_path) if extractor else extract_pdf_text_local(pdf_path)
    return build_local_pdf_analysis_result(extraction, job, record, decorated_file)


def process_queued_pdf_analysis_jobs(limit=1, extractor=None):
    limit = clamp_int(limit, 1, 1, 10)
    results = []
    for _ in range(limit):
        job = claim_next_queued_analysis_job()
        if not job:
            break
        try:
            result = run_local_pdf_analysis_for_job(job, extractor=extractor)
            updated = update_analysis_job_status(
                job.get("job_id"),
                "completed",
                progress=100,
                result_json=result,
            )
            results.append({"ok": True, "job": updated, "error_summary": ""})
        except Exception as exc:
            updated = update_analysis_job_status(
                job.get("job_id"),
                "failed",
                progress=100,
                error_summary=str(exc),
                result_json={},
            )
            results.append(
                {
                    "ok": False,
                    "job": updated,
                    "error_summary": (updated or {}).get("error_summary") or sanitize_analysis_error_summary(str(exc)),
                }
            )
    return {
        "processed": len(results),
        "results": results,
        "remaining_queued": count_queued_analysis_jobs(),
    }


def analysis_worker_record_state(**updates):
    with ANALYSIS_WORKER_LOCK:
        ANALYSIS_WORKER_STATE.update(updates)
        return dict(ANALYSIS_WORKER_STATE)


def get_analysis_worker_config():
    values = ANALYSIS_WORKER_SETTING_DEFAULTS.copy()
    try:
        with db() as conn:
            rows = conn.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?, ?)",
                tuple(ANALYSIS_WORKER_SETTING_DEFAULTS.keys()),
            ).fetchall()
        values.update({row["key"]: row["value"] for row in rows})
    except Exception:
        pass
    return {
        "enabled": truthy_value(values.get("analysis_worker_enabled")),
        "interval_seconds": clamp_int(
            values.get("analysis_worker_interval_seconds"),
            ANALYSIS_WORKER_DEFAULT_INTERVAL_SECONDS,
            ANALYSIS_WORKER_MIN_INTERVAL_SECONDS,
            ANALYSIS_WORKER_MAX_INTERVAL_SECONDS,
        ),
        "batch_limit": clamp_int(
            values.get("analysis_worker_batch_limit"),
            ANALYSIS_WORKER_DEFAULT_BATCH_LIMIT,
            ANALYSIS_WORKER_MIN_BATCH_LIMIT,
            ANALYSIS_WORKER_MAX_BATCH_LIMIT,
        ),
        "bounds": {
            "interval_seconds": {
                "min": ANALYSIS_WORKER_MIN_INTERVAL_SECONDS,
                "max": ANALYSIS_WORKER_MAX_INTERVAL_SECONDS,
            },
            "batch_limit": {
                "min": ANALYSIS_WORKER_MIN_BATCH_LIMIT,
                "max": ANALYSIS_WORKER_MAX_BATCH_LIMIT,
            },
        },
    }


def save_analysis_worker_config(values):
    values = values if isinstance(values, dict) else {}
    current = get_analysis_worker_config()
    enabled_value = payload_value(values, "enabled", default=current["enabled"])
    interval_value = payload_value(
        values,
        "interval_seconds",
        "poll_interval_seconds",
        "interval",
        default=current["interval_seconds"],
    )
    batch_value = payload_value(values, "batch_limit", "limit", "batch", default=current["batch_limit"])
    clean = {
        "analysis_worker_enabled": "1" if truthy_value(enabled_value) else "0",
        "analysis_worker_interval_seconds": str(
            clamp_int(
                interval_value,
                current["interval_seconds"],
                ANALYSIS_WORKER_MIN_INTERVAL_SECONDS,
                ANALYSIS_WORKER_MAX_INTERVAL_SECONDS,
            )
        ),
        "analysis_worker_batch_limit": str(
            clamp_int(
                batch_value,
                current["batch_limit"],
                ANALYSIS_WORKER_MIN_BATCH_LIMIT,
                ANALYSIS_WORKER_MAX_BATCH_LIMIT,
            )
        ),
    }
    with db() as conn:
        for key, value in clean.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    ANALYSIS_WORKER_WAKE_EVENT.set()
    return get_analysis_worker_config()


def analysis_job_status_counts(task_type=ANALYSIS_JOB_TASK_TYPE):
    counts = {status: 0 for status in ANALYSIS_JOB_STATUSES}
    try:
        with db() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM analysis_jobs
                WHERE task_type = ?
                GROUP BY status
                """,
                (task_type,),
            ).fetchall()
        for row in rows:
            status = str(row["status"] or "")
            if status in counts:
                counts[status] = parse_int(row["count"], 0)
    except Exception:
        pass
    return counts


def analysis_worker_status_payload():
    config = get_analysis_worker_config()
    with ANALYSIS_WORKER_LOCK:
        state = dict(ANALYSIS_WORKER_STATE)
    thread = ANALYSIS_WORKER_THREAD
    counts = analysis_job_status_counts()
    return {
        "enabled": bool(config["enabled"]),
        "interval_seconds": config["interval_seconds"],
        "poll_interval_seconds": config["interval_seconds"],
        "batch_limit": config["batch_limit"],
        "bounds": config["bounds"],
        "started": bool(state.get("started")),
        "started_at": str(state.get("started_at") or ""),
        "running": bool(state.get("running")),
        "thread_alive": bool(thread and thread.is_alive()),
        "last_tick_at": str(state.get("last_tick_at") or ""),
        "last_tick_time": str(state.get("last_tick_at") or ""),
        "last_processed_count": parse_int(state.get("last_processed_count"), 0),
        "last_error_summary": sanitize_analysis_error_summary(state.get("last_error_summary")),
        "last_status": sanitize_analysis_error_summary(state.get("last_status"), max_len=80),
        "queue": {
            "queued_count": counts.get("queued", 0),
            "running_count": counts.get("running", 0),
            "completed_count": counts.get("completed", 0),
            "failed_count": counts.get("failed", 0),
            "status_counts": counts,
        },
        "queued_count": counts.get("queued", 0),
    }


def run_analysis_worker_tick(config=None, extractor=None, source="scheduler", force=False):
    config = config or get_analysis_worker_config()
    if not force and not config.get("enabled"):
        analysis_worker_record_state(running=False, last_status="disabled")
        return {
            "processed": 0,
            "results": [],
            "remaining_queued": count_queued_analysis_jobs(),
            "skipped": True,
            "reason": "disabled",
        }
    tick_time = now_text()
    analysis_worker_record_state(
        running=True,
        last_tick_at=tick_time,
        last_status=f"{source}: running",
        last_error_summary="",
    )
    try:
        result = process_queued_pdf_analysis_jobs(limit=config.get("batch_limit", 1), extractor=extractor)
        failed_errors = [
            sanitize_analysis_error_summary(item.get("error_summary"), max_len=120)
            for item in result.get("results", [])
            if isinstance(item, dict) and not item.get("ok") and str(item.get("error_summary") or "").strip()
        ]
        last_error = "; ".join(error for error in failed_errors if error)
        analysis_worker_record_state(
            running=False,
            last_processed_count=parse_int(result.get("processed"), 0),
            last_error_summary=sanitize_analysis_error_summary(last_error),
            last_status=f"{source}: {'job_failed' if last_error else 'ok'}",
        )
        result["last_error_summary"] = sanitize_analysis_error_summary(last_error)
        result["skipped"] = False
        return result
    except Exception as exc:
        error = sanitize_analysis_error_summary(str(exc))
        analysis_worker_record_state(
            running=False,
            last_processed_count=0,
            last_error_summary=error,
            last_status=f"{source}: error",
        )
        return {
            "processed": 0,
            "results": [],
            "remaining_queued": count_queued_analysis_jobs(),
            "error_summary": error,
            "last_error_summary": error,
            "skipped": False,
        }


def analysis_worker_scheduler_loop():
    while not STOP_EVENT.is_set():
        config = get_analysis_worker_config()
        if config.get("enabled"):
            run_analysis_worker_tick(config=config, source="scheduler")
        wait_seconds = config.get("interval_seconds") or ANALYSIS_WORKER_DEFAULT_INTERVAL_SECONDS
        ANALYSIS_WORKER_WAKE_EVENT.wait(wait_seconds)
        ANALYSIS_WORKER_WAKE_EVENT.clear()


def start_analysis_worker_scheduler():
    global ANALYSIS_WORKER_THREAD
    with ANALYSIS_WORKER_LOCK:
        if ANALYSIS_WORKER_THREAD and ANALYSIS_WORKER_THREAD.is_alive():
            return ANALYSIS_WORKER_THREAD
        ANALYSIS_WORKER_STATE.update(
            {
                "started": True,
                "started_at": ANALYSIS_WORKER_STATE.get("started_at") or now_text(),
                "running": False,
                "last_status": "started",
            }
        )
        ANALYSIS_WORKER_THREAD = threading.Thread(
            target=analysis_worker_scheduler_loop,
            name="analysis-pdf-worker",
            daemon=True,
        )
        ANALYSIS_WORKER_THREAD.start()
        return ANALYSIS_WORKER_THREAD


def soldering_job_from_row(row):
    if not row:
        return None
    job = dict(row)
    job["inventory_consumed"] = int(job.get("inventory_consumed") or 0)
    for key in ("inventory_consumption_json", "purchase_check_json"):
        raw = job.get(key) or "{}"
        try:
            job[key] = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            job[key] = {}
    return job


def user_can_access_soldering_job(job, user):
    if not job or not user:
        return False
    if is_admin_role(user):
        return True
    return (
        str(job.get("owner_username") or "") == str(user["username"])
        or str(job.get("owner_user_id") or "") == str(user["id"])
    )


def active_soldering_status_placeholders():
    return ",".join("?" for _ in SOLDERING_ACTIVE_STATUSES)


def find_active_soldering_job_for_bom(record):
    owner_user_id, owner_username = bom_owner_identity(record)
    bom_id = str(record.get("bom_id") or "")
    if not bom_id:
        return None
    clauses = ["bom_id = ?", f"status IN ({active_soldering_status_placeholders()})"]
    params = [bom_id, *SOLDERING_ACTIVE_STATUSES]
    owner_filters = []
    if owner_username:
        owner_filters.append("owner_username = ?")
        params.append(owner_username)
    if owner_user_id:
        owner_filters.append("owner_user_id = ?")
        params.append(owner_user_id)
    if owner_filters:
        clauses.append("(" + " OR ".join(owner_filters) + ")")
    with db() as conn:
        row = conn.execute(
            f"""
            SELECT * FROM soldering_jobs
            WHERE {" AND ".join(clauses)}
            ORDER BY datetime(started_at) DESC, id DESC
            LIMIT 1
            """,
            params,
        ).fetchone()
    return soldering_job_from_row(row)


def get_soldering_job(job_id):
    with db() as conn:
        row = conn.execute("SELECT * FROM soldering_jobs WHERE job_id = ?", (str(job_id or ""),)).fetchone()
    return soldering_job_from_row(row)


def list_soldering_jobs(user, bom_id=None, active_only=False):
    clauses = []
    params = []
    if bom_id:
        clauses.append("bom_id = ?")
        params.append(str(bom_id))
    if active_only:
        clauses.append(f"status IN ({active_soldering_status_placeholders()})")
        params.extend(SOLDERING_ACTIVE_STATUSES)
    if not is_admin_role(user):
        clauses.append("(owner_username = ? OR owner_user_id = ?)")
        params.extend([user["username"], user["id"]])
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM soldering_jobs
            {where}
            ORDER BY datetime(started_at) DESC, id DESC
            """,
            params,
        ).fetchall()
    return [soldering_job_from_row(row) for row in rows]


def list_soldering_jobs_for_bom(record, user):
    owner_user_id, owner_username = bom_owner_identity(record)
    clauses = ["bom_id = ?"]
    params = [str(record.get("bom_id") or "")]
    owner_filters = []
    if owner_username:
        owner_filters.append("owner_username = ?")
        params.append(owner_username)
    if owner_user_id:
        owner_filters.append("owner_user_id = ?")
        params.append(owner_user_id)
    if owner_filters:
        clauses.append("(" + " OR ".join(owner_filters) + ")")
    if not is_admin_role(user):
        clauses.append("(owner_username = ? OR owner_user_id = ?)")
        params.extend([user["username"], user["id"]])
    with db() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM soldering_jobs
            WHERE {" AND ".join(clauses)}
            ORDER BY datetime(started_at) DESC, id DESC
            """,
            params,
        ).fetchall()
    return [soldering_job_from_row(row) for row in rows]


def create_soldering_job(record, user, board_count, pcb_file_id="", notes=""):
    owner_user_id, owner_username = bom_owner_identity(record)
    if not owner_username:
        owner_username = user["username"]
    if not owner_user_id:
        owner_user_id = user["id"]
    now = now_text()
    job_id = f"solder_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"
    bom_upload_id = parse_int(record.get("upload_id"), 0) or None
    with db() as conn:
        conn.execute(
            """
            INSERT INTO soldering_jobs (
                job_id, bom_id, bom_upload_id, pcb_file_id, owner_user_id, owner_username,
                created_by_user_id, created_by_username, started_by_user_id, started_by_username,
                board_count, status, started_at, finished_at, notes, inventory_consumed,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, 0, ?, ?)
            """,
            (
                job_id,
                str(record.get("bom_id") or ""),
                bom_upload_id,
                str(pcb_file_id or ""),
                owner_user_id,
                owner_username,
                user["id"],
                user["username"],
                user["id"],
                user["username"],
                int(board_count),
                SOLDERING_ACTIVE_STATUSES[0],
                now,
                str(notes or "")[:1000],
                now,
                now,
            ),
        )
    return get_soldering_job(job_id)


def finish_soldering_job(job, notes=None):
    now = now_text()
    new_notes = job.get("notes") or ""
    if notes is not None:
        new_notes = str(notes or "")[:1000]
    with db() as conn:
        conn.execute(
            """
            UPDATE soldering_jobs
            SET status = ?, finished_at = ?, notes = ?, updated_at = ?
            WHERE job_id = ?
            """,
            (SOLDERING_COMPLETED_STATUS, now, new_notes, now, job["job_id"]),
        )
    return get_soldering_job(job["job_id"])


def aggregate_soldering_consumption(record, board_count):
    multiplier = max(1, parse_int(board_count, 1))
    entries = record.get("component_rows")
    if not isinstance(entries, list) or not entries:
        entries = record.get("aggregated_items")
    consumption = {}
    for item in entries if isinstance(entries, list) else []:
        if not isinstance(item, dict):
            continue
        quantity = max(0, parse_int(item.get("quantity"), 0)) * multiplier
        if quantity <= 0:
            continue
        category = str(item.get("category") or "BOM").strip() or "BOM"
        name = str(
            item.get("normalized_name")
            or item.get("name")
            or item.get("part_name")
            or item.get("value")
            or item.get("lcsc_code")
            or ""
        ).strip()
        if not name:
            continue
        key = (normalize_key(category), normalize_key(name))
        current = consumption.setdefault(
            key,
            {
                "category": category,
                "name": name,
                "quantity": 0,
                "source_rows": [],
                "lcsc_codes": [],
                "designators": [],
            },
        )
        current["quantity"] += quantity
        if item.get("row_index"):
            current["source_rows"].append(item.get("row_index"))
        if item.get("lcsc_code"):
            current["lcsc_codes"].append(str(item.get("lcsc_code")))
        designators = item.get("designators") if isinstance(item.get("designators"), list) else split_designators(item.get("designator"))
        current["designators"].extend(designators)
    for item in consumption.values():
        item["source_rows"] = sorted({parse_int(value, 0) for value in item["source_rows"] if parse_int(value, 0)})
        item["lcsc_codes"] = sorted({value for value in item["lcsc_codes"] if value})
        item["designators"] = sorted({value for value in item["designators"] if value})
    return list(consumption.values())


def unique_nonempty_values(values):
    result = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = normalize_key(text)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def bom_record_items_for_consumption(record):
    items = []
    if not isinstance(record, dict):
        return items
    for key in ("component_rows", "aggregated_items"):
        rows = record.get(key)
        if not isinstance(rows, list):
            continue
        items.extend(item for item in rows if isinstance(item, dict))
    return items


def consumption_item_values(item):
    values = [
        item.get("normalized_name"),
        item.get("name"),
        item.get("part_name"),
        item.get("value"),
        item.get("lcsc_code"),
        item.get("supplier_code"),
        item.get("manufacturer_part_number"),
    ]
    values.extend(item.get("aliases") if isinstance(item.get("aliases"), list) else [])
    return unique_nonempty_values(values)


def consumption_match_alias_allowed(value):
    text = str(value or "").strip()
    key = component_match_key(text)
    if not key:
        return False
    if key in {"0201", "0402", "0603", "0805", "1206", "1210", "1812", "2512"}:
        return False
    if re.fullmatch(r"[a-z]{1,3}\d+([,; ]+[a-z]{1,3}\d+)*", key, re.I):
        return False
    return True


def consumption_match_aliases(values):
    return unique_nonempty_values(value for value in values if consumption_match_alias_allowed(value))


def enrich_consumption_source_from_record(source, record):
    aliases = [source.get("name")]
    aliases.extend(str(source.get("name") or "").split("|"))
    structured_values = []
    source_name_key = normalize_key(source.get("name"))
    source_category_key = normalize_key(source.get("category"))
    source_lcsc = set(extract_lcsc_codes([source.get("name")]))
    source_rows = []
    designators = []
    for item in bom_record_items_for_consumption(record):
        item_values = consumption_item_values(item)
        item_keys = {normalize_key(value) for value in item_values if normalize_key(value)}
        item_lcsc = set(extract_lcsc_codes(item_values))
        related = bool(source_lcsc and item_lcsc and source_lcsc.intersection(item_lcsc))
        if source_name_key and source_name_key in item_keys:
            related = True
        if not related and source_category_key and source_category_key == normalize_key(item.get("category")):
            related = any(
                len(key) >= 4 and source_name_key and (key in source_name_key or source_name_key in key)
                for key in item_keys
            )
        if not related:
            continue
        aliases.extend(item_values)
        structured_values.extend(
            [
                item.get("lcsc_code"),
                item.get("supplier_code"),
                item.get("manufacturer_part_number"),
            ]
        )
        if item.get("row_index"):
            source_rows.append(parse_int(item.get("row_index"), 0))
        designators.extend(item.get("designators") if isinstance(item.get("designators"), list) else split_designators(item.get("designator")))
    source["aliases"] = consumption_match_aliases(aliases)
    source["structured_values"] = unique_nonempty_values(structured_values)
    source["lcsc_codes"] = sorted(set(extract_lcsc_codes(source["aliases"] + source["structured_values"])))
    source["source_rows"] = sorted({value for value in source_rows if value})
    source["designators"] = sorted({value for value in designators if value})
    if source["source_rows"] and not source.get("source_row_ref"):
        source["source_row_ref"] = "rows:" + ",".join(str(value) for value in source["source_rows"])
    return source


def load_soldering_consumption_sources(conn, record, job):
    board_count = max(1, parse_int(job.get("board_count"), 1))
    upload_id = parse_int(job.get("bom_upload_id"), 0)
    if not upload_id and isinstance(record, dict):
        upload_id = parse_int(record.get("upload_id"), 0)
    sources = []
    if upload_id:
        rows = conn.execute(
            """
            SELECT id, upload_id, category, name, quantity
            FROM bom_items
            WHERE upload_id = ?
            ORDER BY id
            """,
            (upload_id,),
        ).fetchall()
        for row in rows:
            per_board_quantity = max(0, parse_int(row["quantity"], 0))
            required_quantity = per_board_quantity * board_count
            if required_quantity <= 0:
                continue
            source = {
                "bom_upload_id": row["upload_id"],
                "bom_item_id": row["id"],
                "source_row_ref": f"bom_items:{row['id']}",
                "category": row["category"],
                "name": row["name"],
                "per_board_quantity": per_board_quantity,
                "required_quantity": required_quantity,
                "quantity": required_quantity,
            }
            sources.append(enrich_consumption_source_from_record(source, record))
    if sources:
        return sources
    for index, item in enumerate(aggregate_soldering_consumption(record or {}, board_count), start=1):
        required_quantity = max(0, parse_int(item.get("quantity"), 0))
        if required_quantity <= 0:
            continue
        source = {
            "bom_upload_id": upload_id or None,
            "bom_item_id": None,
            "source_row_ref": "rows:" + ",".join(str(value) for value in item.get("source_rows", []))
            if item.get("source_rows")
            else f"fallback:{index}",
            "category": item["category"],
            "name": item["name"],
            "per_board_quantity": 0,
            "required_quantity": required_quantity,
            "quantity": required_quantity,
            "aliases": unique_nonempty_values([item["name"]] + item.get("lcsc_codes", [])),
            "lcsc_codes": item.get("lcsc_codes", []),
            "source_rows": item.get("source_rows", []),
            "designators": item.get("designators", []),
            "structured_values": item.get("lcsc_codes", []),
        }
        sources.append(enrich_consumption_source_from_record(source, record))
    return sources


def inventory_row_matches_existing_bom_logic(row, source):
    candidates = consumption_match_aliases([source.get("name")] + list(source.get("aliases") or []) + str(source.get("name") or "").split("|"))
    normalized = {normalize_key(value) for value in candidates if str(value or "").strip()}
    component_keys = {component_match_key(value) for value in candidates if str(value or "").strip()}
    component_keys = {value for value in component_keys if len(value) >= 4}
    value_keys = {
        component_value_key(value, source.get("category"))
        for value in candidates
        if str(value or "").strip() and component_value_key(value, source.get("category"))
    }
    inv_name = row["name"]
    inv_norm = normalize_key(inv_name)
    inv_key = component_match_key(inv_name)
    inv_value_key = component_value_key(inv_name, row["category"])
    direct = inv_norm in normalized or any(
        len(candidate) >= 4 and (candidate in inv_norm or inv_norm in candidate) for candidate in normalized
    )
    component = len(inv_key) >= 4 and (
        inv_key in component_keys
        or any(len(candidate) >= 4 and (candidate in inv_key or inv_key in candidate) for candidate in component_keys)
    )
    value_match = inv_value_key and inv_value_key in value_keys
    return bool(direct or component or value_match)


def inventory_row_matches_structured_terms(row, source):
    text = f"{row['name']} {row['note'] or ''}"
    text_upper = text.upper()
    text_key = normalize_key(text)
    for code in source.get("lcsc_codes") or []:
        if code and str(code).upper() in text_upper:
            return True
    # Prefer explicit supplier/manufacturer terms when the JSON BOM record has them.
    for value in source.get("structured_values") or []:
        key = normalize_key(value)
        if len(key) >= 4 and key in text_key:
            return True
    return False


def matching_inventory_rows_for_consumption(conn, source):
    rows = conn.execute(
        """
        SELECT id, category, name, quantity, location, note, created_by, created_at
        FROM inventory
        WHERE quantity > 0
        ORDER BY datetime(created_at) ASC, id ASC
        """
    ).fetchall()
    structured = [row for row in rows if inventory_row_matches_structured_terms(row, source)]
    structured_ids = {row["id"] for row in structured}
    fallback = [
        row
        for row in rows
        if row["id"] not in structured_ids and inventory_row_matches_existing_bom_logic(row, source)
    ]
    if structured and fallback:
        return structured + fallback, "structured+normalized"
    if structured:
        return structured, "structured"
    return fallback, "normalized" if fallback else "unmatched"


def insert_soldering_consumption_ledger(
    conn,
    job,
    source,
    inventory_id,
    required_quantity,
    consumed_quantity,
    shortage_quantity,
    status,
    match_key,
    created_at,
):
    conn.execute(
        """
        INSERT INTO soldering_consumption_items (
            job_id, bom_id, bom_upload_id, bom_item_id, source_row_ref, inventory_id,
            category, name, required_quantity, consumed_quantity, shortage_quantity,
            status, match_key, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            job.get("job_id"),
            job.get("bom_id") or "",
            source.get("bom_upload_id") or job.get("bom_upload_id"),
            source.get("bom_item_id"),
            source.get("source_row_ref") or "",
            inventory_id,
            source.get("category") or "BOM",
            source.get("name") or "",
            int(required_quantity or 0),
            int(consumed_quantity or 0),
            int(shortage_quantity or 0),
            status,
            match_key,
            created_at,
        ),
    )


def apply_soldering_inventory_consumption(conn, job, sources, created_at):
    for source in sources:
        required_quantity = max(0, parse_int(source.get("required_quantity"), 0))
        if required_quantity <= 0:
            continue
        remaining = required_quantity
        matches, match_key = matching_inventory_rows_for_consumption(conn, source)
        for row in matches:
            if remaining <= 0:
                break
            available = max(0, parse_int(row["quantity"], 0))
            if available <= 0:
                continue
            consumed = min(available, remaining)
            updated = conn.execute(
                "UPDATE inventory SET quantity = quantity - ? WHERE id = ? AND quantity >= ?",
                (consumed, row["id"], consumed),
            )
            if updated.rowcount != 1:
                continue
            remaining -= consumed
            insert_soldering_consumption_ledger(
                conn,
                job,
                source,
                row["id"],
                required_quantity,
                consumed,
                0,
                "consumed",
                match_key,
                created_at,
            )
        if remaining > 0:
            insert_soldering_consumption_ledger(
                conn,
                job,
                source,
                None,
                required_quantity,
                0,
                remaining,
                "unmatched" if not matches else "shortage",
                match_key,
                created_at,
            )


def default_consumption_summary(job, purchase_status=None):
    return {
        "consumed": bool(int(job.get("inventory_consumed") or 0)) if job else False,
        "status": "not_consumed",
        "job_id": (job or {}).get("job_id"),
        "bom_id": (job or {}).get("bom_id"),
        "board_count": parse_int((job or {}).get("board_count"), 1),
        "items": [],
        "ledger_items": [],
        "summary": {
            "required_quantity": 0,
            "consumed_quantity": 0,
            "shortage_quantity": 0,
            "item_count": 0,
            "shortage_count": 0,
            "unmatched_count": 0,
        },
        "purchase_status": purchase_status or {"checked": 0, "received_count": 0, "pending_count": 0, "items": []},
        "message": "No soldering consumption ledger has been recorded for this job.",
    }


def build_consumption_summary_from_ledger(job, ledger_rows, purchase_status=None):
    summary = default_consumption_summary(job, purchase_status)
    groups = {}
    order = []
    for row in ledger_rows:
        data = dict(row)
        ledger_item = {
            "id": data.get("id"),
            "job_id": data.get("job_id"),
            "bom_upload_id": data.get("bom_upload_id"),
            "bom_item_id": data.get("bom_item_id"),
            "source_row_ref": data.get("source_row_ref") or "",
            "inventory_id": data.get("inventory_id"),
            "inventory_name": data.get("inventory_name") or "",
            "category": data.get("category") or "BOM",
            "name": data.get("name") or "",
            "required_quantity": parse_int(data.get("required_quantity"), 0),
            "consumed_quantity": parse_int(data.get("consumed_quantity"), 0),
            "shortage_quantity": parse_int(data.get("shortage_quantity"), 0),
            "status": data.get("status") or "",
            "match_key": data.get("match_key") or "",
            "created_at": data.get("created_at") or "",
        }
        summary["ledger_items"].append(ledger_item)
        key = (
            str(ledger_item["bom_item_id"])
            if ledger_item["bom_item_id"] is not None
            else f"{ledger_item['source_row_ref']}|{normalize_key(ledger_item['category'])}|{normalize_key(ledger_item['name'])}"
        )
        if key not in groups:
            groups[key] = {
                "bom_upload_id": ledger_item["bom_upload_id"],
                "bom_item_id": ledger_item["bom_item_id"],
                "source_row_ref": ledger_item["source_row_ref"],
                "category": ledger_item["category"],
                "name": ledger_item["name"],
                "required_quantity": 0,
                "consumed_quantity": 0,
                "shortage_quantity": 0,
                "inventory_ids": [],
                "inventory_names": [],
                "inventory": [],
                "match_key": ledger_item["match_key"],
                "_statuses": [],
            }
            order.append(key)
        group = groups[key]
        group["required_quantity"] = max(group["required_quantity"], ledger_item["required_quantity"])
        group["consumed_quantity"] += ledger_item["consumed_quantity"]
        group["shortage_quantity"] += ledger_item["shortage_quantity"]
        if ledger_item["inventory_id"] is not None:
            group["inventory_ids"].append(ledger_item["inventory_id"])
            group["inventory"].append(
                {"id": ledger_item["inventory_id"], "name": ledger_item.get("inventory_name") or ""}
            )
        if ledger_item.get("inventory_name"):
            group["inventory_names"].append(ledger_item["inventory_name"])
        if ledger_item["match_key"] and ledger_item["match_key"] not in str(group["match_key"]):
            group["match_key"] = f"{group['match_key']}+{ledger_item['match_key']}" if group["match_key"] else ledger_item["match_key"]
        group["_statuses"].append(ledger_item["status"])
    for key in order:
        group = groups[key]
        statuses = set(group.pop("_statuses", []))
        group["inventory_ids"] = sorted(set(group["inventory_ids"]))
        seen_inventory = set()
        compact_inventory = []
        for item in group["inventory"]:
            inv_key = (item.get("id"), item.get("name") or "")
            if inv_key in seen_inventory:
                continue
            seen_inventory.add(inv_key)
            compact_inventory.append(item)
        group["inventory"] = compact_inventory
        group["inventory_names"] = sorted({value for value in group["inventory_names"] if value})
        group["inventory_id"] = group["inventory_ids"][0] if len(group["inventory_ids"]) == 1 else None
        group["inventory_name"] = group["inventory_names"][0] if len(group["inventory_names"]) == 1 else ""
        group["quantity"] = group["required_quantity"]
        if group["shortage_quantity"] > 0 and group["consumed_quantity"] > 0:
            group["status"] = "partial"
        elif group["shortage_quantity"] > 0:
            group["status"] = "unmatched" if "unmatched" in statuses else "shortage"
        elif group["consumed_quantity"] > 0:
            group["status"] = "consumed"
        else:
            group["status"] = "no_consumption"
        summary["items"].append(group)
    summary["summary"] = {
        "required_quantity": sum(item["required_quantity"] for item in summary["items"]),
        "consumed_quantity": sum(item["consumed_quantity"] for item in summary["items"]),
        "shortage_quantity": sum(item["shortage_quantity"] for item in summary["items"]),
        "item_count": len(summary["items"]),
        "shortage_count": sum(1 for item in summary["items"] if item["shortage_quantity"] > 0),
        "unmatched_count": sum(1 for item in summary["items"] if item["status"] == "unmatched"),
    }
    summary["consumed"] = bool(int(job.get("inventory_consumed") or 0)) or bool(summary["ledger_items"])
    if summary["summary"]["shortage_quantity"] > 0:
        summary["status"] = "consumed_with_shortage"
    elif summary["summary"]["consumed_quantity"] > 0:
        summary["status"] = "consumed"
    elif summary["ledger_items"]:
        summary["status"] = "ledger_recorded"
    summary["message"] = ""
    return summary


def soldering_consumption_summary(conn, job, purchase_status=None):
    if not job:
        return default_consumption_summary({}, purchase_status)
    rows = conn.execute(
        """
        SELECT sci.*, inventory.name AS inventory_name
        FROM soldering_consumption_items sci
        LEFT JOIN inventory ON inventory.id = sci.inventory_id
        WHERE sci.job_id = ?
        ORDER BY sci.id
        """,
        (job.get("job_id"),),
    ).fetchall()
    if rows:
        return build_consumption_summary_from_ledger(job, rows, purchase_status or job.get("purchase_check_json") or {})
    raw = job.get("inventory_consumption_json") or {}
    if isinstance(raw, dict) and raw:
        result = dict(raw)
        result.setdefault("consumed", bool(int(job.get("inventory_consumed") or 0)))
        result.setdefault("job_id", job.get("job_id"))
        result.setdefault("bom_id", job.get("bom_id"))
        result.setdefault("board_count", parse_int(job.get("board_count"), 1))
        result.setdefault("items", [])
        result.setdefault("ledger_items", [])
        if "summary" not in result:
            result["summary"] = {
                "required_quantity": sum(parse_int(item.get("quantity") or item.get("required_quantity"), 0) for item in result.get("items", [])),
                "consumed_quantity": sum(parse_int(item.get("consumed_quantity") or item.get("quantity"), 0) for item in result.get("items", [])),
                "shortage_quantity": sum(parse_int(item.get("shortage_quantity"), 0) for item in result.get("items", [])),
                "item_count": len(result.get("items", [])),
                "shortage_count": sum(1 for item in result.get("items", []) if parse_int(item.get("shortage_quantity"), 0) > 0),
                "unmatched_count": sum(1 for item in result.get("items", []) if item.get("status") == "unmatched"),
            }
        result["purchase_status"] = purchase_status or job.get("purchase_check_json") or result.get("purchase_status") or {}
        return result
    return default_consumption_summary(job, purchase_status or job.get("purchase_check_json") or {})


def normalized_consumption_totals(summary):
    summary = summary if isinstance(summary, dict) else {}
    return {
        "required_quantity": parse_int(summary.get("required_quantity"), 0),
        "consumed_quantity": parse_int(summary.get("consumed_quantity"), 0),
        "shortage_quantity": parse_int(summary.get("shortage_quantity"), 0),
        "item_count": parse_int(summary.get("item_count"), 0),
        "shortage_count": parse_int(summary.get("shortage_count"), 0),
        "unmatched_count": parse_int(summary.get("unmatched_count"), 0),
    }


def soldering_consumption_response(job, consumption=None):
    if not job:
        consumption = consumption or default_consumption_summary({})
        totals = normalized_consumption_totals(consumption.get("summary"))
        return {
            "job_id": None,
            "status": "",
            "owner": {"user_id": None, "username": ""},
            "board_count": 0,
            "finished_at": "",
            "inventory_consumed": False,
            "summary": totals,
            "totals": totals,
            "purchase_status": consumption.get("purchase_status", {}),
            "items": consumption.get("items", []),
            "ledger_items": consumption.get("ledger_items", []),
            "consumption": consumption,
        }
    if consumption is None:
        with db() as conn:
            consumption = soldering_consumption_summary(conn, job)
    totals = normalized_consumption_totals(consumption.get("summary"))
    return {
        "job_id": job.get("job_id"),
        "status": job.get("status") or "",
        "owner": {
            "user_id": job.get("owner_user_id"),
            "username": job.get("owner_username") or "",
        },
        "board_count": parse_int(job.get("board_count"), 0),
        "finished_at": job.get("finished_at") or "",
        "inventory_consumed": bool(int(job.get("inventory_consumed") or 0)),
        "summary": totals,
        "totals": totals,
        "purchase_status": consumption.get("purchase_status", {}),
        "items": consumption.get("items", []),
        "ledger_items": consumption.get("ledger_items", []),
        "consumption": consumption,
        "job": job,
    }


def admin_soldering_dashboard_payload():
    records_by_bom_id = {str(record.get("bom_id") or ""): record for record in load_bom_records()}
    with db() as conn:
        jobs = [
            soldering_job_from_row(row)
            for row in conn.execute(
                """
                SELECT * FROM soldering_jobs
                ORDER BY datetime(started_at) DESC, id DESC
                """
            ).fetchall()
        ]
        upload_names = {
            row["id"]: row["original_name"]
            for row in conn.execute("SELECT id, original_name FROM bom_uploads").fetchall()
        }
        rows = []
        totals = {
            "job_count": 0,
            "active_count": 0,
            "completed_count": 0,
            "other_count": 0,
            "board_count_total": 0,
            "required_quantity_total": 0,
            "consumed_quantity_total": 0,
            "shortage_quantity_total": 0,
            "shortage_item_count": 0,
            "pending_purchase_count": 0,
        }
        for job in jobs:
            if not job:
                continue
            record = records_by_bom_id.get(str(job.get("bom_id") or "")) or {}
            consumption = soldering_consumption_summary(conn, job)
            quantity_totals = normalized_consumption_totals(consumption.get("summary"))
            purchase_status = consumption.get("purchase_status") if isinstance(consumption.get("purchase_status"), dict) else {}
            bom_upload_id = parse_int(job.get("bom_upload_id"), 0) or parse_int(record.get("upload_id"), 0)
            bom_name = (
                upload_names.get(bom_upload_id)
                or record.get("original_filename")
                or record.get("stored_filename")
                or job.get("bom_id")
                or ""
            )
            pending_purchase_count = parse_int(purchase_status.get("pending_count"), 0)
            status = str(job.get("status") or "")
            board_count = parse_int(job.get("board_count"), 0)
            row = {
                "job_id": job.get("job_id") or "",
                "owner": {
                    "user_id": job.get("owner_user_id"),
                    "username": job.get("owner_username") or "",
                },
                "owner_username": job.get("owner_username") or "",
                "bom_id": job.get("bom_id") or "",
                "bom_upload_id": bom_upload_id,
                "bom_name": bom_name,
                "board_count": board_count,
                "status": status,
                "started_at": job.get("started_at") or "",
                "finished_at": job.get("finished_at") or "",
                "inventory_consumed": bool(int(job.get("inventory_consumed") or 0)),
                "required_quantity_total": quantity_totals["required_quantity"],
                "consumed_quantity_total": quantity_totals["consumed_quantity"],
                "shortage_quantity_total": quantity_totals["shortage_quantity"],
                "shortage_total": quantity_totals["shortage_quantity"],
                "shortage_item_count": quantity_totals["shortage_count"],
                "unmatched_count": quantity_totals["unmatched_count"],
                "pending_purchase_count": pending_purchase_count,
                "consumption_status": consumption.get("status") or "",
            }
            rows.append(row)
            totals["job_count"] += 1
            totals["board_count_total"] += board_count
            totals["required_quantity_total"] += row["required_quantity_total"]
            totals["consumed_quantity_total"] += row["consumed_quantity_total"]
            totals["shortage_quantity_total"] += row["shortage_quantity_total"]
            totals["shortage_item_count"] += row["shortage_item_count"]
            totals["pending_purchase_count"] += pending_purchase_count
            if status in SOLDERING_ACTIVE_STATUSES:
                totals["active_count"] += 1
            elif status == SOLDERING_COMPLETED_STATUS:
                totals["completed_count"] += 1
            else:
                totals["other_count"] += 1
    totals["shortage_total"] = totals["shortage_quantity_total"]
    return {"summary": totals, "jobs": rows, "count": len(rows)}


ADMIN_SOLDERING_SHORTAGE_CSV_FIELDS = [
    "job_id",
    "owner_username",
    "bom_id",
    "bom_upload_id",
    "bom_name",
    "board_count",
    "job_status",
    "started_at",
    "finished_at",
    "item_category",
    "item_name",
    "required_quantity",
    "consumed_quantity",
    "shortage_quantity",
    "item_status",
    "bom_item_id",
    "pending_purchase_count",
    "pending_purchase_status",
    "job_pending_purchase_count",
]

ADMIN_INVENTORY_ADJUSTMENT_CSV_FIELDS = [
    "movement_type",
    "status",
    "owner_username",
    "operator_username",
    "category",
    "part_name",
    "source_type",
    "source_id",
    "quantity_delta",
    "timestamp",
    "note_reason",
    "current_inventory_snapshot",
    "inventory_entry_id",
    "purchase_receipt_id",
    "purchase_item_id",
    "purchase_order_id",
    "soldering_job_id",
    "bom_id",
    "bom_upload_id",
    "quantity_before",
    "quantity_after",
    "manual_adjustment_id",
    "manual_action",
    "manual_source",
]


def admin_soldering_shortage_filters(query=None):
    query = query or {}
    return {
        "owner": str(payload_value(query, "owner") or "").strip(),
        "status": str(payload_value(query, "status") or "").strip(),
        "bom_id": str(payload_value(query, "bom_id") or "").strip(),
        "bom_upload_id": str(payload_value(query, "bom_upload_id") or "").strip(),
        "q": str(payload_value(query, "q") or "").strip(),
    }


def admin_soldering_shortage_query(filters, extra=None):
    params = {key: str(value) for key, value in (filters or {}).items() if str(value or "").strip()}
    for key, value in (extra or {}).items():
        if str(value or "").strip():
            params[key] = str(value)
    return urllib.parse.urlencode(params)


def purchase_context_for_shortage_item(purchase_status, shortage_item):
    purchase_status = purchase_status if isinstance(purchase_status, dict) else {}
    purchase_items = purchase_status.get("items") if isinstance(purchase_status.get("items"), list) else []
    item_category = normalize_key(shortage_item.get("category") or shortage_item.get("item_category"))
    item_name = normalize_key(shortage_item.get("name") or shortage_item.get("item_name"))
    matches = []
    for purchase_item in purchase_items:
        if not isinstance(purchase_item, dict):
            continue
        if normalize_key(purchase_item.get("category")) == item_category and normalize_key(purchase_item.get("name")) == item_name:
            matches.append(purchase_item)
    job_pending = parse_int(purchase_status.get("pending_count"), 0)
    if matches:
        pending = sum(1 for item in matches if not item.get("received"))
        status = "pending" if pending else "received"
        return pending, status, job_pending
    if parse_int(purchase_status.get("checked"), 0):
        return 0, "not_listed", job_pending
    return 0, "not_checked", job_pending


def admin_soldering_shortage_row_matches(row, filters):
    filters = filters or {}
    owner = filters.get("owner")
    if owner and normalize_key(row.get("owner_username")) != normalize_key(owner):
        return False
    status = filters.get("status")
    if status:
        status_key = normalize_key(status)
        row_statuses = {
            normalize_key(row.get("status")),
            normalize_key(row.get("job_status")),
            normalize_key(row.get("item_status")),
        }
        if status_key not in row_statuses:
            return False
    bom_id = filters.get("bom_id")
    if bom_id and normalize_key(row.get("bom_id")) != normalize_key(bom_id):
        return False
    bom_upload_id = filters.get("bom_upload_id")
    if bom_upload_id:
        row_upload_id = parse_int(row.get("bom_upload_id"), 0)
        if str(row_upload_id) != str(parse_int(bom_upload_id, -1)):
            return False
    q = filters.get("q")
    if q:
        haystack = " ".join(
            str(row.get(key) or "")
            for key in ("owner_username", "bom_name", "bom_id", "bom_upload_id", "job_id", "item_name", "item_category")
        ).lower()
        if str(q).strip().lower() not in haystack:
            return False
    return True


def admin_soldering_shortage_payload(query=None):
    filters = admin_soldering_shortage_filters(query)
    records_by_bom_id = {str(record.get("bom_id") or ""): record for record in load_bom_records()}
    rows = []
    with db() as conn:
        jobs = [
            soldering_job_from_row(row)
            for row in conn.execute(
                """
                SELECT * FROM soldering_jobs
                ORDER BY datetime(started_at) DESC, id DESC
                """
            ).fetchall()
        ]
        upload_names = {
            row["id"]: row["original_name"]
            for row in conn.execute("SELECT id, original_name FROM bom_uploads").fetchall()
        }
        for job in jobs:
            if not job:
                continue
            consumption = soldering_consumption_summary(conn, job)
            ledger_items = consumption.get("ledger_items") if isinstance(consumption.get("ledger_items"), list) else []
            if job.get("status") != SOLDERING_COMPLETED_STATUS and not ledger_items:
                continue
            items = consumption.get("items") if isinstance(consumption.get("items"), list) else []
            shortage_items = [item for item in items if parse_int(item.get("shortage_quantity"), 0) > 0]
            if not shortage_items:
                continue
            record = records_by_bom_id.get(str(job.get("bom_id") or "")) or {}
            bom_upload_id = parse_int(job.get("bom_upload_id"), 0) or parse_int(record.get("upload_id"), 0)
            bom_name = (
                upload_names.get(bom_upload_id)
                or record.get("original_filename")
                or record.get("stored_filename")
                or job.get("bom_id")
                or ""
            )
            purchase_status = consumption.get("purchase_status") if isinstance(consumption.get("purchase_status"), dict) else {}
            job_pending = parse_int(purchase_status.get("pending_count"), 0)
            for item in shortage_items:
                pending_count, pending_status, job_pending_count = purchase_context_for_shortage_item(purchase_status, item)
                row = {
                    "job_id": job.get("job_id") or "",
                    "owner_username": job.get("owner_username") or "",
                    "bom_id": job.get("bom_id") or "",
                    "bom_upload_id": bom_upload_id,
                    "bom_name": bom_name,
                    "board_count": parse_int(job.get("board_count"), 0),
                    "status": job.get("status") or "",
                    "job_status": job.get("status") or "",
                    "started_at": job.get("started_at") or "",
                    "finished_at": job.get("finished_at") or "",
                    "item_category": item.get("category") or "BOM",
                    "item_name": item.get("name") or "",
                    "category": item.get("category") or "BOM",
                    "name": item.get("name") or "",
                    "required_quantity": parse_int(item.get("required_quantity"), 0),
                    "consumed_quantity": parse_int(item.get("consumed_quantity"), 0),
                    "shortage_quantity": parse_int(item.get("shortage_quantity"), 0),
                    "item_status": item.get("status") or "",
                    "bom_item_id": item.get("bom_item_id"),
                    "pending_purchase_count": pending_count,
                    "pending_purchase_status": pending_status,
                    "purchase_status": pending_status,
                    "job_pending_purchase_count": job_pending_count,
                    "job_purchase_pending_count": job_pending,
                }
                if admin_soldering_shortage_row_matches(row, filters):
                    rows.append(row)
    summary = {
        "row_count": len(rows),
        "shortage_item_count": len(rows),
        "shortage_quantity_total": sum(parse_int(row.get("shortage_quantity"), 0) for row in rows),
        "pending_purchase_count": sum(parse_int(row.get("pending_purchase_count"), 0) for row in rows),
        "job_pending_purchase_count": sum(parse_int(row.get("job_pending_purchase_count"), 0) for row in rows),
    }
    summary["shortage_total"] = summary["shortage_quantity_total"]
    return {"filters": filters, "summary": summary, "rows": rows, "count": len(rows)}


RECEIVING_PRIORITY_ACTION_RANKS = {
    "stock_available": 0,
    "partially_available": 1,
    "receipt_confirmed": 2,
    "awaiting_purchase_receipt": 3,
    "needs_purchase_order": 4,
    "no_shortage": 9,
}

DEFAULT_IMAGE_RECOGNITION_CONFIG = {
    "enabled": "0",
    "endpoint": "",
    "model": "",
    "api_key": "",
    "temperature": "0",
    "max_tokens": "900",
    "timeout": "45",
    "system_prompt": (
        "你是仓库入库图片识别助手。请从图片中识别电子元器件标签、包装袋、料盘、"
        "手写标记或快递到货信息，并只返回 JSON。字段包括 items 数组；每个 item 包含 "
        "lcsc_code、category、name、value_spec、package、voltage、brand、quantity、location、"
        "product_url、note、confidence。无法确认的字段返回空字符串，数量无法判断时返回 1。"
    ),
}

INVENTORY_IMAGE_FIELD_NAMES = ("inventory_image", "image", "file", "photo")
INVENTORY_IMAGE_ALLOWED_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
INVENTORY_IMAGE_MAX_BYTES = 8 * 1024 * 1024


def receiving_priority_source_from_shortage(row):
    category = row.get("item_category") or row.get("category") or "BOM"
    name = row.get("item_name") or row.get("name") or ""
    aliases = consumption_match_aliases([name, row.get("name"), row.get("item_name")])
    structured_values = unique_nonempty_values(extract_lcsc_codes(aliases))
    return {
        "category": category,
        "name": name,
        "aliases": aliases,
        "structured_values": structured_values,
        "lcsc_codes": extract_lcsc_codes(aliases + structured_values),
    }


def receiving_priority_inventory_context(conn, source, limit=5):
    matches, match_key = matching_inventory_rows_for_consumption(conn, source)
    current_stock = sum(max(0, parse_int(row["quantity"], 0)) for row in matches)
    recent_rows = sorted(
        matches,
        key=lambda row: (str(row["created_at"] or ""), parse_int(row["id"], 0)),
        reverse=True,
    )[:limit]
    recent_inventory = [
        {
            "id": row["id"],
            "category": row["category"],
            "name": row["name"],
            "quantity": parse_int(row["quantity"], 0),
            "location": row["location"],
            "created_at": row["created_at"],
            "created_by": row["created_by"],
        }
        for row in recent_rows
    ]
    return {
        "current_matching_stock": current_stock,
        "matching_inventory_count": len(matches),
        "inventory_match_strategy": match_key,
        "recent_inventory": recent_inventory,
    }


def purchase_item_matches_receiving_priority(row, source):
    source_category_key = normalize_key(source.get("category"))
    row_category_key = normalize_key(row["category"])
    category_compatible = not source_category_key or source_category_key == "bom" or source_category_key == row_category_key
    if not category_compatible:
        return False
    fake_row = {
        "category": row["category"],
        "name": row["name"],
        "note": row["reason"] or "",
    }
    return inventory_row_matches_structured_terms(fake_row, source) or inventory_row_matches_existing_bom_logic(
        fake_row, source
    )


PURCHASE_RECEIPT_ACTIVE_STATUS = "active"
PURCHASE_RECEIPT_VOIDED_STATUS = "voided"
PURCHASE_RECEIPT_STATUS_FILTERS = {PURCHASE_RECEIPT_ACTIVE_STATUS, PURCHASE_RECEIPT_VOIDED_STATUS, "all"}
PURCHASE_RECEIPT_ACTIVE_SQL = "LOWER(COALESCE(NULLIF(TRIM(status), ''), 'active')) = 'active'"
PURCHASE_RECEIPT_VOIDED_SQL = "LOWER(COALESCE(NULLIF(TRIM(status), ''), 'active')) = 'voided'"
PURCHASE_RECEIPT_FINALIZED_SQL = "(COALESCE(inventory_entry_id, 0) > 0 OR TRIM(COALESCE(inventory_posted_at, '')) <> '')"
PURCHASE_RECEIPT_REVERSED_SQL = "(COALESCE(reversal_inventory_entry_id, 0) > 0 OR TRIM(COALESCE(inventory_reversed_at, '')) <> '')"
PURCHASE_RECEIPT_STOCKED_SQL = PURCHASE_RECEIPT_FINALIZED_SQL + " AND NOT " + PURCHASE_RECEIPT_REVERSED_SQL


def row_value(row, key, default=None):
    if not row:
        return default
    try:
        if key in row.keys():
            value = row[key]
            return default if value is None else value
    except Exception:
        pass
    return default


def normalize_purchase_receipt_status(value):
    status = str(value or "").strip().lower()
    if status == PURCHASE_RECEIPT_VOIDED_STATUS:
        return PURCHASE_RECEIPT_VOIDED_STATUS
    return PURCHASE_RECEIPT_ACTIVE_STATUS


def purchase_receipt_is_active_value(value):
    return normalize_purchase_receipt_status(value) == PURCHASE_RECEIPT_ACTIVE_STATUS


def purchase_receipt_from_row(row):
    if not row:
        return None
    status = normalize_purchase_receipt_status(row_value(row, "status", PURCHASE_RECEIPT_ACTIVE_STATUS))
    voided_at = str(row_value(row, "voided_at", "") or "")
    voided_by_username = str(row_value(row, "voided_by_username", "") or "")
    void_reason = str(row_value(row, "void_reason", "") or "")
    inventory_entry_id = parse_int(row_value(row, "inventory_entry_id", 0), 0) or None
    inventory_posted_at = str(row_value(row, "inventory_posted_at", "") or "")
    inventory_posted_by_username = str(row_value(row, "inventory_posted_by_username", "") or "")
    inventory_location = str(row_value(row, "inventory_location", "") or "")
    inventory_note = str(row_value(row, "inventory_note", "") or "")
    reversal_inventory_entry_id = parse_int(row_value(row, "reversal_inventory_entry_id", 0), 0) or None
    inventory_reversed_at = str(row_value(row, "inventory_reversed_at", "") or "")
    inventory_reversed_by_username = str(row_value(row, "inventory_reversed_by_username", "") or "")
    inventory_reversal_reason = str(row_value(row, "inventory_reversal_reason", "") or "")
    stock_finalized = bool(inventory_entry_id or inventory_posted_at)
    stock_reversed = bool(reversal_inventory_entry_id or inventory_reversed_at)
    if status == PURCHASE_RECEIPT_VOIDED_STATUS:
        stock_status = PURCHASE_RECEIPT_VOIDED_STATUS
    elif stock_reversed:
        stock_status = "reversed"
    elif stock_finalized:
        stock_status = "stocked"
    else:
        stock_status = "pending_stock_in"
    return {
        "id": row["id"],
        "receipt_id": row["receipt_id"],
        "purchase_item_id": row["purchase_item_id"],
        "purchase_order_id": row["purchase_order_id"],
        "owner_username": row["owner_username"],
        "category": row["category_snapshot"],
        "category_snapshot": row["category_snapshot"],
        "name": row["name_snapshot"],
        "name_snapshot": row["name_snapshot"],
        "received_quantity": parse_int(row["received_quantity"], 0),
        "matched_inventory_id": row["matched_inventory_id"],
        "matched_soldering_job_id": row["matched_soldering_job_id"] or "",
        "matched_bom_id": row["matched_bom_id"] or "",
        "matched_bom_upload_id": row["matched_bom_upload_id"],
        "note": row["note"] or "",
        "status": status,
        "active": status == PURCHASE_RECEIPT_ACTIVE_STATUS,
        "is_voided": status == PURCHASE_RECEIPT_VOIDED_STATUS,
        "voided_at": voided_at,
        "voided_by": {
            "user_id": row_value(row, "voided_by_user_id"),
            "username": voided_by_username,
        },
        "voided_by_user_id": row_value(row, "voided_by_user_id"),
        "voided_by_username": voided_by_username,
        "void_reason": void_reason,
        "inventory_entry_id": inventory_entry_id,
        "inventory_posted": stock_finalized,
        "inventory_finalized": stock_finalized,
        "stock_finalized": stock_finalized,
        "inventory_reversed": stock_reversed,
        "stock_reversed": stock_reversed,
        "reversal_inventory_entry_id": reversal_inventory_entry_id,
        "stock_status": stock_status,
        "finalize_url": f"/api/admin/purchase-receipts/{urllib.parse.quote(str(row['receipt_id']))}/finalize",
        "reverse_url": f"/api/admin/purchase-receipts/{urllib.parse.quote(str(row['receipt_id']))}/reverse",
        "inventory_posted_at": inventory_posted_at,
        "inventory_posted_by": {
            "user_id": row_value(row, "inventory_posted_by_user_id"),
            "username": inventory_posted_by_username,
        },
        "inventory_posted_by_user_id": row_value(row, "inventory_posted_by_user_id"),
        "inventory_posted_by_username": inventory_posted_by_username,
        "inventory_location": inventory_location,
        "inventory_note": inventory_note,
        "inventory_reversed_at": inventory_reversed_at,
        "inventory_reversed_by": {
            "user_id": row_value(row, "inventory_reversed_by_user_id"),
            "username": inventory_reversed_by_username,
        },
        "inventory_reversed_by_user_id": row_value(row, "inventory_reversed_by_user_id"),
        "inventory_reversed_by_username": inventory_reversed_by_username,
        "inventory_reversal_reason": inventory_reversal_reason,
        "received_by": {
            "user_id": row["received_by_user_id"],
            "username": row["received_by_username"] or "",
        },
        "received_by_user_id": row["received_by_user_id"],
        "received_by_username": row["received_by_username"] or "",
        "created_at": row["created_at"],
    }


def purchase_item_context(conn, purchase_item_id):
    return conn.execute(
        """
        SELECT
            pi.id,
            pi.order_id,
            pi.category,
            pi.name,
            pi.required_quantity,
            pi.stock_quantity,
            pi.purchase_quantity,
            pi.reason,
            pi.created_by AS purchase_created_by,
            pi.created_at AS purchase_item_created_at,
            po.upload_id,
            po.file_name,
            po.created_by AS order_created_by,
            po.created_at AS order_created_at
        FROM purchase_items pi
        JOIN purchase_orders po ON po.id = pi.order_id
        WHERE pi.id = ?
        """,
        (parse_int(purchase_item_id, 0),),
    ).fetchone()


def owner_username_for_purchase_item(row):
    if not row:
        return ""
    return row["purchase_created_by"] or row["order_created_by"] or ""


def receiving_priority_source_from_purchase_item(row):
    aliases = consumption_match_aliases([row["name"], row["reason"]])
    structured_values = unique_nonempty_values(extract_lcsc_codes(aliases))
    return {
        "category": row["category"],
        "name": row["name"],
        "aliases": aliases,
        "structured_values": structured_values,
        "lcsc_codes": extract_lcsc_codes(aliases + structured_values),
    }


def inventory_row_matches_purchase_item(inventory_row, purchase_item_row):
    if not inventory_row or not purchase_item_row:
        return False
    source = receiving_priority_source_from_purchase_item(purchase_item_row)
    source_category_key = normalize_key(source.get("category"))
    inventory_category_key = normalize_key(inventory_row["category"])
    category_compatible = (
        not source_category_key
        or source_category_key == "bom"
        or source_category_key == inventory_category_key
    )
    if not category_compatible:
        return False
    return inventory_row_matches_structured_terms(inventory_row, source) or inventory_row_matches_existing_bom_logic(
        inventory_row, source
    )


def purchase_receipt_summary_for_item(conn, purchase_item_id, purchase_quantity=None, recent_limit=5, pending_limit=5):
    purchase_item_id = parse_int(purchase_item_id, 0)
    if purchase_quantity is None:
        row = conn.execute("SELECT purchase_quantity FROM purchase_items WHERE id = ?", (purchase_item_id,)).fetchone()
        purchase_quantity = row["purchase_quantity"] if row else 0
    purchase_quantity = max(0, parse_int(purchase_quantity, 0))
    received_quantity = parse_int(
        conn.execute(
            """
            SELECT COALESCE(SUM(received_quantity), 0) AS received_quantity
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["received_quantity"],
        0,
    )
    receipt_count = parse_int(
        conn.execute(
            "SELECT COUNT(*) AS count FROM purchase_receipts WHERE purchase_item_id = ? AND " + PURCHASE_RECEIPT_ACTIVE_SQL,
            (purchase_item_id,),
        ).fetchone()["count"],
        0,
    )
    voided_receipt_count = parse_int(
        conn.execute(
            "SELECT COUNT(*) AS count FROM purchase_receipts WHERE purchase_item_id = ? AND " + PURCHASE_RECEIPT_VOIDED_SQL,
            (purchase_item_id,),
        ).fetchone()["count"],
        0,
    )
    voided_received_quantity = parse_int(
        conn.execute(
            """
            SELECT COALESCE(SUM(received_quantity), 0) AS received_quantity
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_VOIDED_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["received_quantity"],
        0,
    )
    finalized_quantity = parse_int(
        conn.execute(
            """
            SELECT COALESCE(SUM(received_quantity), 0) AS received_quantity
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
              AND """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["received_quantity"],
        0,
    )
    finalized_receipt_count = parse_int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
              AND """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["count"],
        0,
    )
    stocked_quantity = parse_int(
        conn.execute(
            """
            SELECT COALESCE(SUM(received_quantity), 0) AS received_quantity
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
              AND """ + PURCHASE_RECEIPT_STOCKED_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["received_quantity"],
        0,
    )
    stocked_receipt_count = parse_int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
              AND """ + PURCHASE_RECEIPT_STOCKED_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["count"],
        0,
    )
    reversed_quantity = parse_int(
        conn.execute(
            """
            SELECT COALESCE(SUM(received_quantity), 0) AS received_quantity
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
              AND """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
              AND """ + PURCHASE_RECEIPT_REVERSED_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["received_quantity"],
        0,
    )
    reversed_receipt_count = parse_int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
              AND """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
              AND """ + PURCHASE_RECEIPT_REVERSED_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["count"],
        0,
    )
    pending_stock_in_quantity = max(0, received_quantity - finalized_quantity)
    pending_stock_in_receipt_count = parse_int(
        conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM purchase_receipts
            WHERE purchase_item_id = ?
              AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
              AND NOT """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
            """,
            (purchase_item_id,),
        ).fetchone()["count"],
        0,
    )
    remaining_quantity = max(0, purchase_quantity - received_quantity)
    if received_quantity <= 0:
        receiving_status = "not_received"
    elif remaining_quantity <= 0:
        receiving_status = "received"
    else:
        receiving_status = "partially_received"
    recent_rows = conn.execute(
        """
        SELECT *
        FROM purchase_receipts
        WHERE purchase_item_id = ?
          AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (purchase_item_id, max(0, parse_int(recent_limit, 5))),
    ).fetchall()
    pending_rows = conn.execute(
        """
        SELECT *
        FROM purchase_receipts
        WHERE purchase_item_id = ?
          AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
          AND NOT """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (purchase_item_id, max(0, parse_int(pending_limit, 5))),
    ).fetchall()
    return {
        "purchase_quantity": purchase_quantity,
        "received_quantity": received_quantity,
        "active_received_quantity": received_quantity,
        "finalized_quantity": finalized_quantity,
        "stocked_quantity": stocked_quantity,
        "reversed_quantity": reversed_quantity,
        "pending_stock_in_quantity": pending_stock_in_quantity,
        "remaining_quantity": remaining_quantity,
        "receiving_status": receiving_status,
        "receipt_count": receipt_count,
        "active_receipt_count": receipt_count,
        "finalized_receipt_count": finalized_receipt_count,
        "stocked_receipt_count": stocked_receipt_count,
        "reversed_receipt_count": reversed_receipt_count,
        "pending_stock_in_receipt_count": pending_stock_in_receipt_count,
        "voided_receipt_count": voided_receipt_count,
        "voided_received_quantity": voided_received_quantity,
        "recent_receipts": [purchase_receipt_from_row(row) for row in recent_rows],
        "pending_stock_receipts": [purchase_receipt_from_row(row) for row in pending_rows],
    }


def purchase_receipt_filters(query=None):
    query = query or {}
    status = str(payload_value(query, "status") or PURCHASE_RECEIPT_ACTIVE_STATUS).strip().lower()
    if status not in PURCHASE_RECEIPT_STATUS_FILTERS:
        status = PURCHASE_RECEIPT_ACTIVE_STATUS
    return {
        "owner": str(payload_value(query, "owner") or "").strip(),
        "q": str(payload_value(query, "q") or "").strip(),
        "purchase_item_id": str(payload_value(query, "purchase_item_id") or "").strip(),
        "status": status,
    }


def purchase_receipt_matches_filters(receipt, filters):
    filters = filters or {}
    owner = filters.get("owner")
    if owner and normalize_key(receipt.get("owner_username")) != normalize_key(owner):
        return False
    purchase_item_id = filters.get("purchase_item_id")
    if purchase_item_id and str(receipt.get("purchase_item_id")) != str(parse_int(purchase_item_id, -1)):
        return False
    status_filter = filters.get("status") or PURCHASE_RECEIPT_ACTIVE_STATUS
    if status_filter != "all" and normalize_purchase_receipt_status(receipt.get("status")) != status_filter:
        return False
    q = filters.get("q")
    if q:
        haystack = " ".join(
            str(receipt.get(key) or "")
            for key in (
                "receipt_id",
                "owner_username",
                "category",
                "name",
                "note",
                "received_by_username",
                "status",
                "voided_at",
                "voided_by_username",
                "void_reason",
                "inventory_entry_id",
                "inventory_posted_at",
                "inventory_posted_by_username",
                "inventory_location",
                "inventory_note",
                "reversal_inventory_entry_id",
                "inventory_reversed_at",
                "inventory_reversed_by_username",
                "inventory_reversal_reason",
                "stock_status",
                "matched_soldering_job_id",
                "matched_bom_id",
            )
        ).lower()
        if str(q).strip().lower() not in haystack:
            return False
    return True


def admin_purchase_receipts_payload(query=None, limit=50):
    filters = purchase_receipt_filters(query or {})
    with db() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM purchase_receipts
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 200
            """
        ).fetchall()
        receipts = [purchase_receipt_from_row(row) for row in rows]
    receipts = [receipt for receipt in receipts if receipt and purchase_receipt_matches_filters(receipt, filters)]
    receipts = receipts[: max(0, parse_int(limit, 50))]
    summary = {
        "receipt_count": len(receipts),
        "received_quantity_total": sum(parse_int(receipt.get("received_quantity"), 0) for receipt in receipts),
        "active_receipt_count": sum(1 for receipt in receipts if receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS),
        "active_received_quantity_total": sum(
            parse_int(receipt.get("received_quantity"), 0)
            for receipt in receipts
            if receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS
        ),
        "voided_receipt_count": sum(1 for receipt in receipts if receipt.get("status") == PURCHASE_RECEIPT_VOIDED_STATUS),
        "voided_received_quantity_total": sum(
            parse_int(receipt.get("received_quantity"), 0)
            for receipt in receipts
            if receipt.get("status") == PURCHASE_RECEIPT_VOIDED_STATUS
        ),
        "finalized_receipt_count": sum(
            1
            for receipt in receipts
            if receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS and receipt.get("stock_finalized")
        ),
        "reversed_receipt_count": sum(
            1
            for receipt in receipts
            if receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS and receipt.get("stock_reversed")
        ),
        "reversed_quantity_total": sum(
            parse_int(receipt.get("received_quantity"), 0)
            for receipt in receipts
            if receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS and receipt.get("stock_reversed")
        ),
        "stocked_quantity_total": sum(
            parse_int(receipt.get("received_quantity"), 0)
            for receipt in receipts
            if (
                receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS
                and receipt.get("stock_finalized")
                and not receipt.get("stock_reversed")
            )
        ),
        "finalized_quantity_total": sum(
            parse_int(receipt.get("received_quantity"), 0)
            for receipt in receipts
            if receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS and receipt.get("stock_finalized")
        ),
        "pending_stock_in_receipt_count": sum(
            1
            for receipt in receipts
            if (
                receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS
                and not receipt.get("stock_finalized")
                and not receipt.get("stock_reversed")
            )
        ),
        "pending_stock_in_quantity_total": sum(
            parse_int(receipt.get("received_quantity"), 0)
            for receipt in receipts
            if (
                receipt.get("status") == PURCHASE_RECEIPT_ACTIVE_STATUS
                and not receipt.get("stock_finalized")
                and not receipt.get("stock_reversed")
            )
        ),
    }
    return {"filters": filters, "summary": summary, "receipts": receipts, "count": len(receipts)}


INVENTORY_ADJUSTMENT_TYPE_ALIASES = {
    "purchase_finalization": "purchase_finalization",
    "receipt_finalization": "purchase_finalization",
    "finalization": "purchase_finalization",
    "finalized": "purchase_finalization",
    "stock_in": "purchase_finalization",
    "purchase_stock_in": "purchase_finalization",
    "purchase_reversal": "purchase_reversal",
    "receipt_reversal": "purchase_reversal",
    "reversal": "purchase_reversal",
    "correction": "purchase_reversal",
    "soldering_consumption": "soldering_consumption",
    "soldering": "soldering_consumption",
    "consumption": "soldering_consumption",
    "manual_inventory": "manual_inventory",
    "manual": "manual_inventory",
    "ordinary": "manual_inventory",
    "inventory": "manual_inventory",
    "inventory_row": "manual_inventory",
}


def normalize_inventory_adjustment_type(value):
    key = normalize_key(str(value or "").replace("-", "_"))
    return INVENTORY_ADJUSTMENT_TYPE_ALIASES.get(key, "")


def inventory_adjustment_type_filter(value):
    raw = str(value or "").strip()
    if not raw or raw.lower() == "all":
        return []
    types = []
    for part in re.split(r"[, ]+", raw):
        normalized = normalize_inventory_adjustment_type(part)
        if normalized and normalized not in types:
            types.append(normalized)
    return types


def parse_inventory_adjustment_datetime(value, *, end_of_day=False):
    text = str(value or "").strip()
    if not text:
        return None
    normalized = text.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(normalized, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            if end_of_day:
                parsed = parsed.replace(hour=23, minute=59, second=59)
            else:
                parsed = parsed.replace(hour=0, minute=0, second=0)
        return parsed
    return None


def inventory_adjustment_filters(query=None):
    query = query or {}
    raw_type = str(payload_value(query, "type", "movement_type") or "").strip()
    status = str(payload_value(query, "status") or "all").strip().lower()
    if not status:
        status = "all"
    start_date = str(payload_value(query, "start_date", "date_from", "from") or "").strip()
    end_date = str(payload_value(query, "end_date", "date_to", "to") or "").strip()
    return {
        "owner": str(payload_value(query, "owner", "user", "username") or "").strip(),
        "type": raw_type,
        "types": inventory_adjustment_type_filter(raw_type),
        "status": status,
        "q": str(payload_value(query, "q", "query", "search") or "").strip(),
        "start_date": start_date,
        "end_date": end_date,
        "limit": clamp_int(payload_value(query, "limit"), 200, 1, 500),
    }


def inventory_adjustment_query(filters, extra=None):
    params = {}
    for key in ("owner", "type", "status", "q", "start_date", "end_date", "limit"):
        value = (filters or {}).get(key)
        if key == "status" and str(value or "").strip().lower() == "all":
            continue
        if key == "limit" and parse_int(value, 200) == 200:
            continue
        if str(value or "").strip():
            params[key] = str(value)
    for key, value in (extra or {}).items():
        if str(value or "").strip():
            params[key] = str(value)
    return urllib.parse.urlencode(params)


def inventory_adjustment_source_url(source_type, source_id, purchase_item_id=None):
    if source_type == "purchase_receipt" and purchase_item_id:
        return "/api/admin/purchase-receipts?" + urllib.parse.urlencode(
            {"purchase_item_id": purchase_item_id, "status": "all"}
        )
    if source_type == "soldering_job" and source_id:
        return f"/api/soldering/jobs/{urllib.parse.quote(str(source_id))}/consumption"
    return ""


def inventory_adjustment_row(
    *,
    movement_type,
    time,
    owner_username,
    category,
    part_name,
    quantity_delta,
    source_type,
    source_id,
    quantity_before=None,
    quantity_after=None,
    operator_username="",
    status="",
    reason="",
    inventory_entry_id=None,
    manual_adjustment_id=None,
    manual_action="",
    manual_source="",
    purchase_receipt_id="",
    purchase_item_id=None,
    purchase_order_id=None,
    soldering_job_id="",
    bom_id="",
    bom_upload_id=None,
    source_url="",
    current_inventory_snapshot=False,
):
    quantity_delta = parse_int(quantity_delta, 0)
    return {
        "time": str(time or ""),
        "movement_type": movement_type,
        "status": str(status or ""),
        "owner_username": str(owner_username or ""),
        "category": str(category or ""),
        "part_name": str(part_name or ""),
        "name": str(part_name or ""),
        "quantity_delta": quantity_delta,
        "quantity_before": None if quantity_before is None else parse_int(quantity_before, 0),
        "quantity_after": None if quantity_after is None else parse_int(quantity_after, 0),
        "source_type": str(source_type or ""),
        "source_id": str(source_id or ""),
        "source_url": source_url or inventory_adjustment_source_url(source_type, source_id, purchase_item_id),
        "operator_username": str(operator_username or ""),
        "reason": str(reason or ""),
        "inventory_entry_id": inventory_entry_id,
        "manual_adjustment_id": manual_adjustment_id,
        "manual_action": str(manual_action or ""),
        "manual_source": str(manual_source or ""),
        "purchase_receipt_id": str(purchase_receipt_id or ""),
        "purchase_item_id": purchase_item_id,
        "purchase_order_id": purchase_order_id,
        "soldering_job_id": str(soldering_job_id or ""),
        "bom_id": str(bom_id or ""),
        "bom_upload_id": bom_upload_id,
        "current_inventory_snapshot": bool(current_inventory_snapshot),
    }


def purchase_finalization_adjustment_rows(conn):
    rows = conn.execute(
        """
        SELECT
            pr.*,
            inv.id AS inv_id,
            inv.quantity AS inv_quantity,
            inv.location AS inv_location,
            inv.note AS inv_note,
            inv.created_by AS inv_created_by,
            inv.created_at AS inv_created_at,
            pi.reason AS purchase_reason
        FROM purchase_receipts pr
        LEFT JOIN inventory inv ON inv.id = pr.inventory_entry_id
        LEFT JOIN purchase_items pi ON pi.id = pr.purchase_item_id
        WHERE """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
          AND """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
        ORDER BY datetime(COALESCE(NULLIF(pr.inventory_posted_at, ''), pr.created_at)) DESC, pr.id DESC
        LIMIT 500
        """
    ).fetchall()
    adjustments = []
    for row in rows:
        receipt = purchase_receipt_from_row(row)
        reason = row_value(row, "inventory_note", "") or row_value(row, "inv_note", "") or row_value(row, "note", "")
        if row_value(row, "purchase_reason", "") and row_value(row, "purchase_reason", "") not in str(reason):
            reason = (str(reason or "") + "; " + str(row_value(row, "purchase_reason", ""))).strip("; ")
        adjustments.append(
            inventory_adjustment_row(
                movement_type="purchase_finalization",
                time=receipt.get("inventory_posted_at") or row_value(row, "inv_created_at", "") or receipt.get("created_at"),
                owner_username=receipt.get("owner_username"),
                category=receipt.get("category"),
                part_name=receipt.get("name"),
                quantity_delta=receipt.get("received_quantity"),
                source_type="purchase_receipt",
                source_id=receipt.get("receipt_id"),
                operator_username=receipt.get("inventory_posted_by_username")
                or row_value(row, "inv_created_by", "")
                or receipt.get("received_by_username"),
                status=receipt.get("stock_status") or "stocked",
                reason=reason,
                inventory_entry_id=receipt.get("inventory_entry_id") or row_value(row, "inv_id"),
                purchase_receipt_id=receipt.get("receipt_id"),
                purchase_item_id=receipt.get("purchase_item_id"),
                purchase_order_id=receipt.get("purchase_order_id"),
                soldering_job_id=receipt.get("matched_soldering_job_id"),
                bom_id=receipt.get("matched_bom_id"),
                bom_upload_id=receipt.get("matched_bom_upload_id"),
            )
        )
    return adjustments


def purchase_reversal_adjustment_rows(conn):
    rows = conn.execute(
        """
        SELECT
            pr.*,
            inv.id AS correction_inv_id,
            inv.quantity AS correction_quantity,
            inv.location AS correction_location,
            inv.note AS correction_note,
            inv.created_by AS correction_created_by,
            inv.created_at AS correction_created_at
        FROM purchase_receipts pr
        LEFT JOIN inventory inv ON inv.id = pr.reversal_inventory_entry_id
        WHERE """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
          AND """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
          AND """ + PURCHASE_RECEIPT_REVERSED_SQL + """
        ORDER BY datetime(COALESCE(NULLIF(pr.inventory_reversed_at, ''), inv.created_at, pr.created_at)) DESC, pr.id DESC
        LIMIT 500
        """
    ).fetchall()
    adjustments = []
    for row in rows:
        receipt = purchase_receipt_from_row(row)
        quantity_delta = parse_int(row_value(row, "correction_quantity", 0), 0)
        if quantity_delta >= 0:
            quantity_delta = -abs(parse_int(receipt.get("received_quantity"), 0))
        adjustments.append(
            inventory_adjustment_row(
                movement_type="purchase_reversal",
                time=receipt.get("inventory_reversed_at") or row_value(row, "correction_created_at", "") or receipt.get("created_at"),
                owner_username=receipt.get("owner_username"),
                category=receipt.get("category"),
                part_name=receipt.get("name"),
                quantity_delta=quantity_delta,
                source_type="purchase_receipt",
                source_id=receipt.get("receipt_id"),
                operator_username=receipt.get("inventory_reversed_by_username")
                or row_value(row, "correction_created_by", ""),
                status="reversed",
                reason=receipt.get("inventory_reversal_reason") or row_value(row, "correction_note", ""),
                inventory_entry_id=receipt.get("reversal_inventory_entry_id") or row_value(row, "correction_inv_id"),
                purchase_receipt_id=receipt.get("receipt_id"),
                purchase_item_id=receipt.get("purchase_item_id"),
                purchase_order_id=receipt.get("purchase_order_id"),
                soldering_job_id=receipt.get("matched_soldering_job_id"),
                bom_id=receipt.get("matched_bom_id"),
                bom_upload_id=receipt.get("matched_bom_upload_id"),
            )
        )
    return adjustments


def soldering_consumption_adjustment_rows(conn):
    rows = conn.execute(
        """
        SELECT
            sci.*,
            sj.owner_username AS job_owner_username,
            sj.created_by_username AS job_created_by_username,
            sj.started_by_username AS job_started_by_username,
            sj.status AS job_status,
            sj.finished_at AS job_finished_at,
            inv.name AS inventory_name
        FROM soldering_consumption_items sci
        LEFT JOIN soldering_jobs sj ON sj.job_id = sci.job_id
        LEFT JOIN inventory inv ON inv.id = sci.inventory_id
        WHERE COALESCE(sci.consumed_quantity, 0) > 0
        ORDER BY datetime(sci.created_at) DESC, sci.id DESC
        LIMIT 500
        """
    ).fetchall()
    adjustments = []
    for row in rows:
        reason_parts = [
            f"ledger status {row['status']}",
            f"match {row['match_key']}" if row["match_key"] else "",
            f"inventory {row['inventory_id']}" if row["inventory_id"] else "",
            f"source {row['source_row_ref']}" if row["source_row_ref"] else "",
        ]
        adjustments.append(
            inventory_adjustment_row(
                movement_type="soldering_consumption",
                time=row["created_at"],
                owner_username=row["job_owner_username"] or "",
                category=row["category"],
                part_name=row["name"] or row["inventory_name"] or "",
                quantity_delta=-parse_int(row["consumed_quantity"], 0),
                source_type="soldering_job",
                source_id=row["job_id"],
                operator_username=row["job_started_by_username"] or row["job_created_by_username"] or "",
                status=row["status"] or row["job_status"] or "consumed",
                reason="; ".join(part for part in reason_parts if part),
                inventory_entry_id=row["inventory_id"],
                soldering_job_id=row["job_id"],
                bom_id=row["bom_id"],
                bom_upload_id=row["bom_upload_id"],
            )
        )
    return adjustments


def manual_inventory_ledger_adjustment_rows(conn):
    rows = conn.execute(
        """
        SELECT *
        FROM manual_inventory_adjustments
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT 500
        """
    ).fetchall()
    adjustments = []
    for row in rows:
        reason = str(row_value(row, "reason", "") or "")
        note = str(row_value(row, "note_snapshot", "") or "")
        if note and note not in reason:
            reason = (reason + "; note: " + note).strip("; ")
        action = str(row_value(row, "action", "") or "adjust")
        source = str(row_value(row, "source", "") or "manual_inventory")
        adjustments.append(
            inventory_adjustment_row(
                movement_type="manual_inventory",
                time=row["created_at"],
                owner_username=row["owner_username"],
                category=row["category_snapshot"],
                part_name=row["name_snapshot"],
                quantity_delta=row["quantity_delta"],
                quantity_before=row_value(row, "quantity_before"),
                quantity_after=row_value(row, "quantity_after"),
                source_type=source,
                source_id=row["id"],
                operator_username=row["actor_username"],
                status=action,
                reason=reason,
                inventory_entry_id=row["inventory_id"],
                manual_adjustment_id=row["id"],
                manual_action=action,
                manual_source=source,
                current_inventory_snapshot=False,
            )
        )
    return adjustments


def manual_inventory_adjustment_rows(conn):
    rows = conn.execute(
        """
        SELECT inv.*
        FROM inventory inv
        WHERE NOT EXISTS (
            SELECT 1
            FROM purchase_receipts pr
            WHERE pr.inventory_entry_id = inv.id
               OR pr.reversal_inventory_entry_id = inv.id
        )
        AND NOT EXISTS (
            SELECT 1
            FROM manual_inventory_adjustments mia
            WHERE mia.inventory_id = inv.id
        )
        ORDER BY datetime(inv.created_at) DESC, inv.id DESC
        LIMIT 500
        """
    ).fetchall()
    adjustments = []
    for row in rows:
        quantity = parse_int(row["quantity"], 0)
        if quantity < 0:
            status = "current_negative"
        elif quantity == 0:
            status = "current_zero"
        else:
            status = "current_positive"
        adjustments.append(
            inventory_adjustment_row(
                movement_type="manual_inventory",
                time=row["created_at"],
                owner_username=row["created_by"],
                category=row["category"],
                part_name=row["name"],
                quantity_delta=quantity,
                source_type="inventory",
                source_id=row["id"],
                operator_username=row["created_by"],
                status=status,
                reason=row["note"] or "ordinary inventory row",
                inventory_entry_id=row["id"],
                quantity_before=None,
                quantity_after=quantity,
                current_inventory_snapshot=True,
            )
        )
    return adjustments


def admin_inventory_adjustments_csv(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=ADMIN_INVENTORY_ADJUSTMENT_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows or []:
        clean = {}
        for field in ADMIN_INVENTORY_ADJUSTMENT_CSV_FIELDS:
            if field == "timestamp":
                value = row.get("time", "")
            elif field == "note_reason":
                value = row.get("reason", "")
            else:
                value = row.get(field, "")
            clean[field] = "" if value is None else value
        writer.writerow(clean)
    return output.getvalue()


def inventory_adjustment_row_matches(row, filters):
    filters = filters or {}
    owner = filters.get("owner")
    if owner and normalize_key(row.get("owner_username")) != normalize_key(owner):
        return False
    types = filters.get("types") or []
    if types and row.get("movement_type") not in types:
        return False
    status = str(filters.get("status") or "all").strip().lower()
    quantity_delta = parse_int(row.get("quantity_delta"), 0)
    if status and status != "all":
        if status == "positive":
            if quantity_delta <= 0:
                return False
        elif status == "negative":
            if quantity_delta >= 0:
                return False
        elif status == "zero":
            if quantity_delta != 0:
                return False
        elif normalize_key(row.get("status")) != normalize_key(status):
            return False
    start_date = str(filters.get("start_date") or "").strip()
    end_date = str(filters.get("end_date") or "").strip()
    if start_date or end_date:
        row_time = parse_inventory_adjustment_datetime(row.get("time"))
        if not row_time:
            return False
        start_time = parse_inventory_adjustment_datetime(start_date)
        end_time = parse_inventory_adjustment_datetime(end_date, end_of_day=True)
        if start_time and row_time < start_time:
            return False
        if end_time and row_time > end_time:
            return False
    q = filters.get("q")
    if q:
        haystack = " ".join(str(value or "") for value in row.values()).lower()
        if str(q).strip().lower() not in haystack:
            return False
    return True


def inventory_adjustment_summary(rows):
    rows = rows or []
    return {
        "movement_count": len(rows),
        "total_positive_quantity": sum(max(0, parse_int(row.get("quantity_delta"), 0)) for row in rows),
        "total_negative_quantity": sum(abs(min(0, parse_int(row.get("quantity_delta"), 0))) for row in rows),
        "net_quantity_delta": sum(parse_int(row.get("quantity_delta"), 0) for row in rows),
        "finalized_receipt_count": len(
            {row.get("purchase_receipt_id") for row in rows if row.get("movement_type") == "purchase_finalization"}
        ),
        "reversed_receipt_count": len(
            {row.get("purchase_receipt_id") for row in rows if row.get("movement_type") == "purchase_reversal"}
        ),
        "soldering_consumption_count": sum(1 for row in rows if row.get("movement_type") == "soldering_consumption"),
        "manual_inventory_count": sum(1 for row in rows if row.get("movement_type") == "manual_inventory"),
        "manual_inventory_ledger_count": sum(
            1 for row in rows if row.get("movement_type") == "manual_inventory" and row.get("manual_adjustment_id")
        ),
        "manual_inventory_snapshot_count": sum(1 for row in rows if row.get("current_inventory_snapshot")),
    }


def admin_inventory_adjustments_payload(query=None):
    filters = inventory_adjustment_filters(query or {})
    with db() as conn:
        rows = []
        rows.extend(purchase_finalization_adjustment_rows(conn))
        rows.extend(purchase_reversal_adjustment_rows(conn))
        rows.extend(soldering_consumption_adjustment_rows(conn))
        rows.extend(manual_inventory_ledger_adjustment_rows(conn))
        rows.extend(manual_inventory_adjustment_rows(conn))
    rows = [row for row in rows if inventory_adjustment_row_matches(row, filters)]
    rows.sort(key=lambda row: (str(row.get("time") or ""), str(row.get("source_id") or "")), reverse=True)
    rows = rows[: filters["limit"]]
    return {
        "filters": filters,
        "summary": inventory_adjustment_summary(rows),
        "rows": rows,
        "count": len(rows),
        "limitations": [
            "Manual inventory ledger rows are used for future UI/API changes; older inventory rows without ledger coverage still use current inventory.quantity snapshots.",
        ],
    }


def purchase_receipt_history_for_item(conn, purchase_item_id, status="all", limit=80):
    purchase_item_id = parse_int(purchase_item_id, 0)
    status = str(status or "all").strip().lower()
    if status not in PURCHASE_RECEIPT_STATUS_FILTERS:
        status = "all"
    where = ["purchase_item_id = ?"]
    params = [purchase_item_id]
    if status == PURCHASE_RECEIPT_ACTIVE_STATUS:
        where.append(PURCHASE_RECEIPT_ACTIVE_SQL)
    elif status == PURCHASE_RECEIPT_VOIDED_STATUS:
        where.append(PURCHASE_RECEIPT_VOIDED_SQL)
    rows = conn.execute(
        """
        SELECT *
        FROM purchase_receipts
        WHERE """ + " AND ".join(where) + """
        ORDER BY datetime(created_at) DESC, id DESC
        LIMIT ?
        """,
        (*params, max(0, min(parse_int(limit, 80), 200))),
    ).fetchall()
    return [purchase_receipt_from_row(row) for row in rows]


def purchase_item_payload(row, receipt_summary=None):
    if not row:
        return None
    receipt_summary = receipt_summary or {}
    purchase_item_id = parse_int(row["id"], 0)
    item = {
        "id": purchase_item_id,
        "order_id": row["order_id"],
        "upload_id": row["upload_id"],
        "file_name": row["file_name"],
        "category": row["category"],
        "name": row["name"],
        "required_quantity": parse_int(row["required_quantity"], 0),
        "stock_quantity": parse_int(row["stock_quantity"], 0),
        "purchase_quantity": parse_int(row["purchase_quantity"], 0),
        "reason": row["reason"] or "",
        "created_by": owner_username_for_purchase_item(row),
        "purchase_created_by": row["purchase_created_by"] or "",
        "order_created_by": row["order_created_by"] or "",
        "created_at": row["purchase_item_created_at"] or row["order_created_at"] or "",
        "purchase_item_created_at": row["purchase_item_created_at"] or "",
        "order_created_at": row["order_created_at"] or "",
        "receipt_history_url": f"/api/admin/purchase-items/{purchase_item_id}/receipts?status=all",
        "receipt_list_url": f"/api/admin/purchase-receipts?purchase_item_id={purchase_item_id}&status=all",
        "receipt_create_url": f"/api/admin/purchase-items/{purchase_item_id}/receipts",
    }
    item.update(receipt_summary)
    item["active_received_quantity"] = parse_int(
        item.get("active_received_quantity", item.get("received_quantity")),
        0,
    )
    item["finalized_quantity"] = parse_int(item.get("finalized_quantity"), 0)
    item["stocked_quantity"] = parse_int(item.get("stocked_quantity", item.get("finalized_quantity")), 0)
    item["reversed_quantity"] = parse_int(item.get("reversed_quantity"), 0)
    item["pending_stock_in_quantity"] = parse_int(item.get("pending_stock_in_quantity"), 0)
    item["stock_status"] = (
        "stocked_with_reversal"
        if item["stocked_quantity"] > 0 and item["reversed_quantity"] > 0
        else "reversed"
        if item["reversed_quantity"] > 0 and item["pending_stock_in_quantity"] <= 0
        else "partial_reversal_pending"
        if item["reversed_quantity"] > 0 and item["pending_stock_in_quantity"] > 0
        else "stocked"
        if item["active_received_quantity"] > 0 and item["pending_stock_in_quantity"] <= 0
        else "pending_stock_in"
        if item["pending_stock_in_quantity"] > 0
        else item.get("receiving_status", "not_received")
    )
    return item


def admin_purchase_item_receipts_payload(purchase_item_id, query=None):
    query = query or {}
    status = str(payload_value(query, "status") or "all").strip().lower()
    if status not in PURCHASE_RECEIPT_STATUS_FILTERS:
        status = "all"
    limit = clamp_int(payload_value(query, "limit"), 80, 0, 200)
    with db() as conn:
        row = purchase_item_context(conn, purchase_item_id)
        if not row:
            return None
        summary = purchase_receipt_summary_for_item(conn, row["id"], row["purchase_quantity"])
        receipts = purchase_receipt_history_for_item(conn, row["id"], status=status, limit=limit)
        item = purchase_item_payload(row, summary)
    return {
        "filters": {"status": status, "limit": limit},
        "purchase_item": item,
        "purchase_item_receipt_summary": summary,
        "receipt_summary": summary,
        "receipt_history": receipts,
        "receipts": receipts,
        "count": len(receipts),
        "read_only": True,
    }


def admin_purchase_order_filters(query=None):
    query = query or {}
    return {
        "owner": str(payload_value(query, "owner") or "").strip(),
        "order_id": str(payload_value(query, "order_id") or "").strip(),
        "upload_id": str(payload_value(query, "upload_id") or payload_value(query, "bom_upload_id") or "").strip(),
        "status": str(payload_value(query, "status") or "").strip(),
        "q": str(payload_value(query, "q") or "").strip(),
    }


def purchase_item_matches_admin_order_filters(order, item, filters):
    filters = filters or {}
    owner = filters.get("owner")
    if owner and normalize_key(item.get("created_by")) != normalize_key(owner):
        return False
    order_id = parse_int(filters.get("order_id"), 0)
    if order_id and parse_int(item.get("order_id"), 0) != order_id:
        return False
    upload_id = parse_int(filters.get("upload_id"), 0)
    if upload_id and parse_int(item.get("upload_id"), 0) != upload_id:
        return False
    status = filters.get("status")
    if status and normalize_key(item.get("receiving_status")) != normalize_key(status):
        return False
    q = str(filters.get("q") or "").strip().lower()
    if q:
        haystack = " ".join(
            str(value or "")
            for value in (
                order.get("file_name"),
                order.get("created_by"),
                order.get("id"),
                order.get("upload_id"),
                item.get("category"),
                item.get("name"),
                item.get("reason"),
                item.get("receiving_status"),
                item.get("id"),
            )
        ).lower()
        if q not in haystack:
            return False
    return True


def admin_purchase_orders_payload(query=None, limit=25):
    filters = admin_purchase_order_filters(query or {})
    limit = clamp_int(payload_value(query or {}, "limit"), limit, 1, 200)
    orders = []
    flat_items = []
    with db() as conn:
        order_rows = conn.execute(
            """
            SELECT *
            FROM purchase_orders
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT 200
            """
        ).fetchall()
        for order_row in order_rows:
            order = {
                "id": order_row["id"],
                "upload_id": order_row["upload_id"],
                "file_name": order_row["file_name"],
                "created_by": order_row["created_by"],
                "item_count": parse_int(order_row["item_count"], 0),
                "total_quantity": parse_int(order_row["total_quantity"], 0),
                "created_at": order_row["created_at"],
                "download_url": f"/download?type=purchases&name={urllib.parse.quote(order_row['file_name'])}",
            }
            item_rows = conn.execute(
                """
                SELECT
                    pi.id,
                    pi.order_id,
                    pi.category,
                    pi.name,
                    pi.required_quantity,
                    pi.stock_quantity,
                    pi.purchase_quantity,
                    pi.reason,
                    pi.created_by AS purchase_created_by,
                    pi.created_at AS purchase_item_created_at,
                    po.upload_id,
                    po.file_name,
                    po.created_by AS order_created_by,
                    po.created_at AS order_created_at
                FROM purchase_items pi
                JOIN purchase_orders po ON po.id = pi.order_id
                WHERE pi.order_id = ?
                ORDER BY pi.id ASC
                """,
                (order_row["id"],),
            ).fetchall()
            enriched_items = []
            for item_row in item_rows:
                summary = purchase_receipt_summary_for_item(conn, item_row["id"], item_row["purchase_quantity"], recent_limit=3, pending_limit=3)
                item = purchase_item_payload(item_row, summary)
                if not purchase_item_matches_admin_order_filters(order, item, filters):
                    continue
                enriched_items.append(item)
                flat_items.append(item)
            if not enriched_items:
                continue
            order["items"] = enriched_items
            order["filtered_item_count"] = len(enriched_items)
            order["active_received_quantity"] = sum(parse_int(item.get("active_received_quantity"), 0) for item in enriched_items)
            order["finalized_quantity"] = sum(parse_int(item.get("finalized_quantity"), 0) for item in enriched_items)
            order["stocked_quantity"] = sum(parse_int(item.get("stocked_quantity"), 0) for item in enriched_items)
            order["reversed_quantity"] = sum(parse_int(item.get("reversed_quantity"), 0) for item in enriched_items)
            order["pending_stock_in_quantity"] = sum(parse_int(item.get("pending_stock_in_quantity"), 0) for item in enriched_items)
            order["remaining_quantity"] = sum(parse_int(item.get("remaining_quantity"), 0) for item in enriched_items)
            order["voided_receipt_count"] = sum(parse_int(item.get("voided_receipt_count"), 0) for item in enriched_items)
            order["reversed_receipt_count"] = sum(parse_int(item.get("reversed_receipt_count"), 0) for item in enriched_items)
            orders.append(order)
            if len(orders) >= limit:
                break
    summary = {
        "order_count": len(orders),
        "item_count": len(flat_items),
        "purchase_quantity_total": sum(parse_int(item.get("purchase_quantity"), 0) for item in flat_items),
        "active_received_quantity_total": sum(parse_int(item.get("active_received_quantity"), 0) for item in flat_items),
        "finalized_quantity_total": sum(parse_int(item.get("finalized_quantity"), 0) for item in flat_items),
        "stocked_quantity_total": sum(parse_int(item.get("stocked_quantity"), 0) for item in flat_items),
        "reversed_quantity_total": sum(parse_int(item.get("reversed_quantity"), 0) for item in flat_items),
        "pending_stock_in_quantity_total": sum(parse_int(item.get("pending_stock_in_quantity"), 0) for item in flat_items),
        "pending_stock_in_receipt_count": sum(parse_int(item.get("pending_stock_in_receipt_count"), 0) for item in flat_items),
        "finalized_receipt_count": sum(parse_int(item.get("finalized_receipt_count"), 0) for item in flat_items),
        "reversed_receipt_count": sum(parse_int(item.get("reversed_receipt_count"), 0) for item in flat_items),
        "remaining_quantity_total": sum(parse_int(item.get("remaining_quantity"), 0) for item in flat_items),
        "voided_receipt_count": sum(parse_int(item.get("voided_receipt_count"), 0) for item in flat_items),
    }
    return {
        "filters": filters,
        "summary": summary,
        "orders": orders,
        "items": flat_items,
        "count": len(orders),
        "item_count": len(flat_items),
        "read_only": False,
        "stock_finalization_enabled": True,
    }


def create_purchase_receipt(conn, purchase_item_row, actor, received_quantity, payload):
    purchase_item_id = parse_int(purchase_item_row["id"], 0)
    purchase_quantity = parse_int(purchase_item_row["purchase_quantity"], 0)
    summary = purchase_receipt_summary_for_item(conn, purchase_item_id, purchase_quantity, recent_limit=0)
    remaining_quantity = parse_int(summary.get("remaining_quantity"), 0)
    if received_quantity <= 0:
        raise ValueError("received_quantity must be greater than 0.")
    if received_quantity > remaining_quantity:
        raise OverflowError("received_quantity exceeds the remaining purchase quantity.")

    matched_inventory_id = payload_value(payload, "matched_inventory_id", default="")
    matched_inventory_id = parse_int(matched_inventory_id, 0) if str(matched_inventory_id or "").strip() else None
    if matched_inventory_id:
        inventory_row = conn.execute(
            """
            SELECT id, category, name, quantity, location, note, created_by, created_at
            FROM inventory
            WHERE id = ?
            """,
            (matched_inventory_id,),
        ).fetchone()
        if not inventory_row:
            raise LookupError("matched_inventory_id was not found.")
        if not inventory_row_matches_purchase_item(inventory_row, purchase_item_row):
            raise ValueError("matched_inventory_id is not a plausible match for this purchase item.")

    matched_job_id = str(payload_value(payload, "matched_soldering_job_id", "soldering_job_id") or "").strip()
    matched_bom_id = str(payload_value(payload, "matched_bom_id", "bom_id") or "").strip()
    matched_bom_upload_id = parse_int(payload_value(payload, "matched_bom_upload_id", "bom_upload_id"), 0) or None
    if matched_job_id:
        job = conn.execute("SELECT * FROM soldering_jobs WHERE job_id = ?", (matched_job_id,)).fetchone()
        if not job:
            raise LookupError("matched_soldering_job_id was not found.")
        matched_bom_id = matched_bom_id or job["bom_id"] or ""
        matched_bom_upload_id = matched_bom_upload_id or job["bom_upload_id"]

    now = now_text()
    receipt_id = f"receipt_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"
    owner_username = owner_username_for_purchase_item(purchase_item_row)
    note = str(payload_value(payload, "note") or "").strip()[:1000]
    conn.execute(
        """
        INSERT INTO purchase_receipts (
            receipt_id,
            purchase_item_id,
            purchase_order_id,
            owner_username,
            category_snapshot,
            name_snapshot,
            received_quantity,
            matched_inventory_id,
            matched_soldering_job_id,
            matched_bom_id,
            matched_bom_upload_id,
            status,
            note,
            received_by_user_id,
            received_by_username,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt_id,
            purchase_item_id,
            purchase_item_row["order_id"],
            owner_username,
            purchase_item_row["category"],
            purchase_item_row["name"],
            received_quantity,
            matched_inventory_id,
            matched_job_id,
            matched_bom_id,
            matched_bom_upload_id,
            PURCHASE_RECEIPT_ACTIVE_STATUS,
            note,
            actor["id"],
            actor["username"],
            now,
        ),
    )
    row = conn.execute("SELECT * FROM purchase_receipts WHERE receipt_id = ?", (receipt_id,)).fetchone()
    receipt = purchase_receipt_from_row(row)
    updated_summary = purchase_receipt_summary_for_item(conn, purchase_item_id, purchase_quantity)
    return receipt, updated_summary


def inventory_entry_from_row(row):
    if not row:
        return None
    return {
        "id": row["id"],
        "category": row["category"],
        "name": row["name"],
        "quantity": parse_int(row["quantity"], 0),
        "location": row["location"],
        "note": row["note"] or "",
        "created_by": row["created_by"],
        "created_at": row["created_at"],
    }


def actor_field(actor, key, default=None):
    if not actor:
        return default
    try:
        value = actor[key]
    except Exception:
        try:
            value = actor.get(key, default)
        except Exception:
            value = default
    return default if value is None else value


def record_manual_inventory_adjustment(
    conn,
    *,
    owner_username,
    actor,
    inventory_id=None,
    action,
    source="manual_inventory",
    category="",
    name="",
    location="",
    note="",
    quantity_before=None,
    quantity_after=None,
    quantity_delta=None,
    reason="",
    created_at=None,
):
    if quantity_delta is None and quantity_before is not None and quantity_after is not None:
        quantity_delta = parse_int(quantity_after, 0) - parse_int(quantity_before, 0)
    quantity_delta = parse_int(quantity_delta, 0)
    actor_username = str(actor_field(actor, "username", "") or "").strip()
    if not actor_username:
        actor_username = str(owner_username or "").strip()
    created_at = created_at or now_text()
    cur = conn.execute(
        """
        INSERT INTO manual_inventory_adjustments (
            owner_username, actor_user_id, actor_username, inventory_id, action, source,
            category_snapshot, name_snapshot, location_snapshot, note_snapshot,
            quantity_before, quantity_after, quantity_delta, reason, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            str(owner_username or actor_username),
            actor_field(actor, "id"),
            actor_username,
            inventory_id,
            str(action or "adjust").strip()[:60] or "adjust",
            str(source or "manual_inventory").strip()[:120] or "manual_inventory",
            str(category or ""),
            str(name or ""),
            str(location or ""),
            str(note or ""),
            None if quantity_before is None else parse_int(quantity_before, 0),
            None if quantity_after is None else parse_int(quantity_after, 0),
            quantity_delta,
            str(reason or note or "")[:1000],
            created_at,
        ),
    )
    return cur.lastrowid


def purchase_receipt_inventory_note(receipt, operator_note):
    parts = [
        f"Purchase receipt {receipt.get('receipt_id')}",
        f"purchase order {receipt.get('purchase_order_id')}",
        f"purchase item {receipt.get('purchase_item_id')}",
    ]
    receipt_note = str(receipt.get("note") or "").strip()
    if receipt_note:
        parts.append(f"receipt note: {receipt_note}")
    operator_note = str(operator_note or "").strip()
    if operator_note:
        parts.append(f"operator note: {operator_note}")
    return "; ".join(parts)[:800]


def purchase_receipt_reversal_inventory_note(receipt, original_inventory, reason):
    parts = [
        f"Reversal of purchase receipt {receipt.get('receipt_id')}",
        f"purchase order {receipt.get('purchase_order_id')}",
        f"purchase item {receipt.get('purchase_item_id')}",
        f"original inventory entry {receipt.get('inventory_entry_id') or '-'}",
    ]
    if original_inventory:
        parts.append(f"original location: {original_inventory['location']}")
    reason = str(reason or "").strip()
    if reason:
        parts.append(f"reason: {reason}")
    return "; ".join(parts)[:800]


def finalize_purchase_receipt_stock(conn, receipt_ref, actor, payload):
    payload = payload or {}
    confirmed = truthy_value(
        payload_value(payload, "confirm", "confirmed", "post_to_inventory", "finalize", default="")
    )
    if not confirmed:
        raise ValueError("confirm must be true to finalize purchase receipt stock.")
    row = purchase_receipt_context(conn, receipt_ref)
    if not row:
        raise LookupError("Purchase receipt not found.")
    receipt = purchase_receipt_from_row(row)
    if receipt.get("status") == PURCHASE_RECEIPT_VOIDED_STATUS:
        raise FileExistsError("Purchase receipt is voided and cannot be finalized into inventory.")
    if receipt.get("stock_finalized"):
        raise FileExistsError("Purchase receipt has already been finalized into inventory.")
    received_quantity = parse_int(receipt.get("received_quantity"), 0)
    if received_quantity <= 0:
        raise ValueError("received_quantity must be greater than 0 before stock finalization.")

    matched_inventory = None
    if receipt.get("matched_inventory_id"):
        matched_inventory = conn.execute(
            "SELECT id, category, name, quantity, location, note, created_by, created_at FROM inventory WHERE id = ?",
            (receipt.get("matched_inventory_id"),),
        ).fetchone()
    location = str(payload_value(payload, "location", "inventory_location") or "").strip()[:120]
    if not location and matched_inventory:
        location = str(matched_inventory["location"] or "").strip()[:120]
    if not location:
        location = "Purchase receiving"
    operator_note = str(payload_value(payload, "note", "inventory_note") or "").strip()[:500]
    note = purchase_receipt_inventory_note(receipt, operator_note)
    now = now_text()
    inventory_cur = conn.execute(
        """
        INSERT INTO inventory (category, name, quantity, location, note, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt.get("category_snapshot") or receipt.get("category") or "Purchase",
            receipt.get("name_snapshot") or receipt.get("name") or "",
            received_quantity,
            location,
            note,
            actor["username"],
            now,
        ),
    )
    inventory_entry_id = inventory_cur.lastrowid
    updated = conn.execute(
        """
        UPDATE purchase_receipts
        SET inventory_entry_id = ?,
            inventory_posted_at = ?,
            inventory_posted_by_user_id = ?,
            inventory_posted_by_username = ?,
            inventory_location = ?,
            inventory_note = ?
        WHERE id = ?
          AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
          AND NOT """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
        """,
        (
            inventory_entry_id,
            now,
            actor["id"],
            actor["username"],
            location,
            note,
            row["id"],
        ),
    )
    if updated.rowcount != 1:
        raise FileExistsError("Purchase receipt has already been finalized into inventory.")
    updated_row = conn.execute("SELECT * FROM purchase_receipts WHERE id = ?", (row["id"],)).fetchone()
    inventory_row = conn.execute(
        "SELECT id, category, name, quantity, location, note, created_by, created_at FROM inventory WHERE id = ?",
        (inventory_entry_id,),
    ).fetchone()
    purchase_item = purchase_item_context(conn, updated_row["purchase_item_id"])
    purchase_quantity = purchase_item["purchase_quantity"] if purchase_item else None
    summary = purchase_receipt_summary_for_item(conn, updated_row["purchase_item_id"], purchase_quantity)
    return purchase_receipt_from_row(updated_row), summary, inventory_entry_from_row(inventory_row)


def reverse_purchase_receipt_stock(conn, receipt_ref, actor, reason):
    reason = str(reason or "").strip()
    if len(reason) < 3:
        raise ValueError("reversal reason must be at least 3 characters.")
    reason = reason[:500]
    row = purchase_receipt_context(conn, receipt_ref)
    if not row:
        raise LookupError("Purchase receipt not found.")
    receipt = purchase_receipt_from_row(row)
    if receipt.get("status") == PURCHASE_RECEIPT_VOIDED_STATUS:
        raise FileExistsError("Purchase receipt is voided and cannot be reversed.")
    if not receipt.get("stock_finalized"):
        raise FileExistsError("Purchase receipt has not been finalized into inventory and cannot be reversed.")
    if receipt.get("stock_reversed"):
        raise FileExistsError("Purchase receipt stock-in has already been reversed.")
    received_quantity = parse_int(receipt.get("received_quantity"), 0)
    if received_quantity <= 0:
        raise ValueError("received_quantity must be greater than 0 before stock reversal.")

    original_inventory = None
    if receipt.get("inventory_entry_id"):
        original_inventory = conn.execute(
            "SELECT id, category, name, quantity, location, note, created_by, created_at FROM inventory WHERE id = ?",
            (receipt.get("inventory_entry_id"),),
        ).fetchone()
    original_location = ""
    if original_inventory:
        original_location = str(original_inventory["location"] or "").strip()
    original_location = original_location or str(receipt.get("inventory_location") or "").strip()
    location = f"Reversal: {original_location}"[:120] if original_location else "Purchase receiving reversal"
    note = purchase_receipt_reversal_inventory_note(receipt, original_inventory, reason)
    now = now_text()
    correction_cur = conn.execute(
        """
        INSERT INTO inventory (category, name, quantity, location, note, created_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            receipt.get("category_snapshot") or receipt.get("category") or "Purchase",
            receipt.get("name_snapshot") or receipt.get("name") or "",
            -received_quantity,
            location,
            note,
            actor["username"],
            now,
        ),
    )
    correction_entry_id = correction_cur.lastrowid
    updated = conn.execute(
        """
        UPDATE purchase_receipts
        SET reversal_inventory_entry_id = ?,
            inventory_reversed_at = ?,
            inventory_reversed_by_user_id = ?,
            inventory_reversed_by_username = ?,
            inventory_reversal_reason = ?
        WHERE id = ?
          AND """ + PURCHASE_RECEIPT_ACTIVE_SQL + """
          AND """ + PURCHASE_RECEIPT_FINALIZED_SQL + """
          AND NOT """ + PURCHASE_RECEIPT_REVERSED_SQL + """
        """,
        (
            correction_entry_id,
            now,
            actor["id"],
            actor["username"],
            reason,
            row["id"],
        ),
    )
    if updated.rowcount != 1:
        raise FileExistsError("Purchase receipt stock-in has already been reversed.")
    updated_row = conn.execute("SELECT * FROM purchase_receipts WHERE id = ?", (row["id"],)).fetchone()
    correction_row = conn.execute(
        "SELECT id, category, name, quantity, location, note, created_by, created_at FROM inventory WHERE id = ?",
        (correction_entry_id,),
    ).fetchone()
    purchase_item = purchase_item_context(conn, updated_row["purchase_item_id"])
    purchase_quantity = purchase_item["purchase_quantity"] if purchase_item else None
    summary = purchase_receipt_summary_for_item(conn, updated_row["purchase_item_id"], purchase_quantity)
    return purchase_receipt_from_row(updated_row), summary, inventory_entry_from_row(correction_row)


def purchase_receipt_context(conn, receipt_ref):
    receipt_ref = str(receipt_ref or "").strip()
    if not receipt_ref:
        return None
    row = conn.execute("SELECT * FROM purchase_receipts WHERE receipt_id = ?", (receipt_ref,)).fetchone()
    if row:
        return row
    numeric_id = parse_int(receipt_ref, 0)
    if numeric_id > 0:
        return conn.execute("SELECT * FROM purchase_receipts WHERE id = ?", (numeric_id,)).fetchone()
    return None


def void_purchase_receipt(conn, receipt_ref, actor, reason):
    reason = str(reason or "").strip()
    if len(reason) < 3:
        raise ValueError("void_reason must be at least 3 characters.")
    reason = reason[:500]
    row = purchase_receipt_context(conn, receipt_ref)
    if not row:
        raise LookupError("Purchase receipt not found.")
    receipt = purchase_receipt_from_row(row)
    if receipt.get("status") == PURCHASE_RECEIPT_VOIDED_STATUS:
        raise FileExistsError("Purchase receipt is already voided.")
    if receipt.get("stock_finalized"):
        raise FileExistsError(
            "Purchase receipt has already been finalized into inventory. Reversal is a separate future flow."
        )
    now = now_text()
    conn.execute(
        """
        UPDATE purchase_receipts
        SET status = ?,
            voided_at = ?,
            voided_by_user_id = ?,
            voided_by_username = ?,
            void_reason = ?
        WHERE id = ?
        """,
        (
            PURCHASE_RECEIPT_VOIDED_STATUS,
            now,
            actor["id"],
            actor["username"],
            reason,
            row["id"],
        ),
    )
    voided_row = conn.execute("SELECT * FROM purchase_receipts WHERE id = ?", (row["id"],)).fetchone()
    purchase_item = purchase_item_context(conn, voided_row["purchase_item_id"])
    purchase_quantity = purchase_item["purchase_quantity"] if purchase_item else None
    summary = purchase_receipt_summary_for_item(conn, voided_row["purchase_item_id"], purchase_quantity)
    return purchase_receipt_from_row(voided_row), summary


def receiving_priority_purchase_context(conn, source, limit=5):
    rows = conn.execute(
        """
        SELECT
            pi.id,
            pi.order_id,
            pi.category,
            pi.name,
            pi.required_quantity,
            pi.stock_quantity,
            pi.purchase_quantity,
            pi.reason,
            pi.created_by AS purchase_created_by,
            pi.created_at AS purchase_item_created_at,
            po.upload_id,
            po.file_name,
            po.created_by AS order_created_by,
            po.created_at AS order_created_at
        FROM purchase_items pi
        JOIN purchase_orders po ON po.id = pi.order_id
        ORDER BY datetime(po.created_at) DESC, datetime(pi.created_at) DESC, pi.id DESC
        """
    ).fetchall()
    matches = [row for row in rows if purchase_item_matches_receiving_priority(row, source)]
    latest = matches[0] if matches else None
    enriched_matches = []
    for row in matches:
        receipt_summary = purchase_receipt_summary_for_item(conn, row["id"], row["purchase_quantity"])
        item = {
            "id": row["id"],
            "order_id": row["order_id"],
            "upload_id": row["upload_id"],
            "file_name": row["file_name"],
            "category": row["category"],
            "name": row["name"],
            "purchase_quantity": parse_int(row["purchase_quantity"], 0),
            "created_at": row["order_created_at"] or row["purchase_item_created_at"] or "",
            "created_by": row["order_created_by"] or row["purchase_created_by"] or "",
        }
        item.update(receipt_summary)
        enriched_matches.append(item)
    recent_purchase_items = enriched_matches[:limit]
    return {
        "purchase_item_count": len(matches),
        "purchase_order_count": len({row["order_id"] for row in matches}),
        "purchase_required_quantity_total": sum(parse_int(row["required_quantity"], 0) for row in matches),
        "purchase_stock_quantity_snapshot_total": sum(parse_int(row["stock_quantity"], 0) for row in matches),
        "purchase_quantity_total": sum(parse_int(row["purchase_quantity"], 0) for row in matches),
        "purchase_received_quantity_total": sum(parse_int(row.get("received_quantity"), 0) for row in enriched_matches),
        "purchase_remaining_quantity_total": sum(parse_int(row.get("remaining_quantity"), 0) for row in enriched_matches),
        "purchase_receipt_count": sum(parse_int(row.get("receipt_count"), 0) for row in enriched_matches),
        "purchase_receiving_status_counts": {
            status: sum(1 for item in enriched_matches if item.get("receiving_status") == status)
            for status in ("not_received", "partially_received", "received")
        },
        "latest_purchase_at": (latest["order_created_at"] or latest["purchase_item_created_at"] or "") if latest else "",
        "latest_purchase_order_id": latest["order_id"] if latest else None,
        "latest_purchase_file": latest["file_name"] if latest else "",
        "recent_purchase_items": recent_purchase_items,
    }


def receiving_priority_action(
    shortage_quantity,
    current_stock,
    purchase_quantity_total,
    purchase_remaining_quantity_total=None,
    purchase_received_quantity_total=0,
):
    shortage_quantity = max(0, parse_int(shortage_quantity, 0))
    current_stock = max(0, parse_int(current_stock, 0))
    purchase_quantity_total = max(0, parse_int(purchase_quantity_total, 0))
    if purchase_remaining_quantity_total is None:
        purchase_remaining_quantity_total = purchase_quantity_total
    purchase_remaining_quantity_total = max(0, parse_int(purchase_remaining_quantity_total, 0))
    purchase_received_quantity_total = max(0, parse_int(purchase_received_quantity_total, 0))
    if shortage_quantity <= 0:
        return "no_shortage"
    if current_stock >= shortage_quantity:
        return "stock_available"
    if current_stock > 0:
        return "partially_available"
    if purchase_remaining_quantity_total > 0:
        return "awaiting_purchase_receipt"
    if purchase_received_quantity_total > 0:
        return "receipt_confirmed"
    if purchase_quantity_total > 0:
        return "awaiting_purchase_receipt"
    return "needs_purchase_order"


def admin_soldering_receiving_priority_payload(query=None):
    shortage_payload = admin_soldering_shortage_payload(query or {})
    rows = []
    with db() as conn:
        for shortage in shortage_payload["rows"]:
            source = receiving_priority_source_from_shortage(shortage)
            inventory_context = receiving_priority_inventory_context(conn, source)
            purchase_context = receiving_priority_purchase_context(conn, source)
            action = receiving_priority_action(
                shortage.get("shortage_quantity"),
                inventory_context["current_matching_stock"],
                purchase_context["purchase_quantity_total"],
                purchase_context["purchase_remaining_quantity_total"],
                purchase_context["purchase_received_quantity_total"],
            )
            enriched = dict(shortage)
            enriched.update(inventory_context)
            enriched.update(purchase_context)
            enriched.update(
                {
                    "receiving_priority_status": action,
                    "receiving_action": action,
                    "priority_rank": RECEIVING_PRIORITY_ACTION_RANKS.get(action, 9),
                    "receiving_priority_note": "Read-only prioritization context; no inventory or purchase receiving mutation is performed.",
                }
            )
            rows.append(enriched)
    rows.sort(
        key=lambda row: (
            parse_int(row.get("priority_rank"), 9),
            -parse_int(row.get("shortage_quantity"), 0),
            str(row.get("owner_username") or ""),
            str(row.get("item_name") or ""),
        )
    )
    action_counts = {}
    for row in rows:
        action = row.get("receiving_priority_status") or ""
        action_counts[action] = action_counts.get(action, 0) + 1
    summary = dict(shortage_payload["summary"])
    summary.update(
        {
            "current_matching_stock_total": sum(parse_int(row.get("current_matching_stock"), 0) for row in rows),
            "purchase_quantity_total": sum(parse_int(row.get("purchase_quantity_total"), 0) for row in rows),
            "purchase_received_quantity_total": sum(parse_int(row.get("purchase_received_quantity_total"), 0) for row in rows),
            "purchase_remaining_quantity_total": sum(parse_int(row.get("purchase_remaining_quantity_total"), 0) for row in rows),
            "purchase_receipt_count": sum(parse_int(row.get("purchase_receipt_count"), 0) for row in rows),
            "purchase_item_count": sum(parse_int(row.get("purchase_item_count"), 0) for row in rows),
            "receiving_priority_action_counts": action_counts,
            "read_only": True,
        }
    )
    status_counts = {}
    for row in rows:
        for status, count in (row.get("purchase_receiving_status_counts") or {}).items():
            status_counts[status] = status_counts.get(status, 0) + parse_int(count, 0)
    summary["purchase_receiving_status_counts"] = status_counts
    return {
        "filters": shortage_payload["filters"],
        "summary": summary,
        "rows": rows,
        "count": len(rows),
        "read_only": True,
    }


def admin_soldering_shortages_csv(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=ADMIN_SOLDERING_SHORTAGE_CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        clean = {}
        for field in ADMIN_SOLDERING_SHORTAGE_CSV_FIELDS:
            value = row.get(field, "")
            clean[field] = "" if value is None else value
        writer.writerow(clean)
    return output.getvalue()


def soldering_consumption_for_job_id(job_id, user=None):
    if not job_id:
        return None, None
    job = get_soldering_job(job_id)
    if not job:
        return None, None
    if user is not None and not user_can_access_soldering_job(job, user):
        return None, None
    with db() as conn:
        return job, soldering_consumption_summary(conn, job)


def soldering_consumption_summary_html(consumption, compact=False):
    if not isinstance(consumption, dict):
        return ""
    totals = normalized_consumption_totals(consumption.get("summary"))
    items = consumption.get("items") if isinstance(consumption.get("items"), list) else []
    ledger_items = consumption.get("ledger_items") if isinstance(consumption.get("ledger_items"), list) else []
    if not consumption.get("consumed") and not totals["item_count"] and not ledger_items:
        return ""
    purchase_status = consumption.get("purchase_status") if isinstance(consumption.get("purchase_status"), dict) else {}
    shortage_items = [item for item in items if parse_int(item.get("shortage_quantity"), 0) > 0]
    rows = []
    for item in shortage_items[:8]:
        inv_names = item.get("inventory_names") if isinstance(item.get("inventory_names"), list) else []
        inventory_name = ", ".join(str(value) for value in inv_names if value) or str(item.get("inventory_name") or "")
        bom_item = item.get("bom_item_id")
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('category') or 'BOM'))}</td>"
            f"<td>{html.escape(str(item.get('name') or ''))}</td>"
            f"<td>{parse_int(item.get('required_quantity'), 0)}</td>"
            f"<td>{parse_int(item.get('consumed_quantity'), 0)}</td>"
            f"<td>{parse_int(item.get('shortage_quantity'), 0)}</td>"
            f"<td>{html.escape(str(item.get('status') or ''))}</td>"
            f"<td>{html.escape(str(item.get('match_key') or ''))}</td>"
            f"<td>{html.escape(inventory_name or '-')}</td>"
            f"<td>{html.escape(str(bom_item if bom_item is not None else '-'))}</td>"
            "</tr>"
        )
    if rows:
        shortage_html = (
            '<div class="soldering-shortage-table"><table><thead><tr>'
            '<th>类别</th><th>名称</th><th>需用</th><th>已耗</th><th>缺口</th><th>状态</th><th>匹配</th><th>库存</th><th>BOM</th>'
            "</tr></thead><tbody>"
            + "".join(rows)
            + "</tbody></table></div>"
        )
    else:
        shortage_html = '<div class="soldering-shortage-empty">无缺口。</div>'
    more_text = ""
    if len(shortage_items) > 8:
        more_text = f'<small>还有 {len(shortage_items) - 8} 条缺口可通过接口查看。</small>'
    compact_class = " compact" if compact else ""
    return f"""
    <div class="soldering-consumption-summary{compact_class}">
      <div class="soldering-consumption-head">
        <strong>消耗/缺口</strong>
        <span>领料 {totals['consumed_quantity']} / 需用 {totals['required_quantity']}</span>
      </div>
      <div class="soldering-consumption-metrics">
        <span>已耗 {totals['consumed_quantity']}</span>
        <span>缺口 {totals['shortage_quantity']}</span>
        <span>缺口项 {totals['shortage_count']}</span>
        <span>采购待收 {parse_int(purchase_status.get('pending_count'), 0)}</span>
      </div>
      {shortage_html}
      {more_text}
    </div>
    """


def inventory_stock_snapshot(conn, category, name, aliases=None, package=None):
    return stock_for_item(conn, category, name, aliases or [], package=package)


def related_purchase_status(conn, record, consumption_rows):
    upload_id = parse_int(record.get("upload_id"), 0)
    exact_rows = []
    if upload_id:
        exact_rows = conn.execute(
            """
            SELECT pi.name, pi.category, pi.purchase_quantity, pi.created_at
            FROM purchase_items pi
            JOIN purchase_orders po ON po.id = pi.order_id
            WHERE po.upload_id = ?
            """,
            (upload_id,),
        ).fetchall()
    checks = []
    for row in exact_rows:
        stock = inventory_stock_snapshot(conn, row["category"], row["name"])
        checks.append(
            {
                "category": row["category"],
                "name": row["name"],
                "purchase_quantity": int(row["purchase_quantity"] or 0),
                "stock_quantity": stock,
                "received": stock >= int(row["purchase_quantity"] or 0),
            }
        )
    if not checks and consumption_rows:
        for item in consumption_rows:
            stock = inventory_stock_snapshot(conn, item["category"], item["name"], item.get("lcsc_codes", []))
            checks.append(
                {
                    "category": item["category"],
                    "name": item["name"],
                    "purchase_quantity": item["quantity"],
                    "stock_quantity": stock,
                    "received": stock >= item["quantity"],
                }
            )
    return {
        "checked": len(checks),
        "received_count": sum(1 for item in checks if item["received"]),
        "pending_count": sum(1 for item in checks if not item["received"]),
        "items": checks[:80],
    }


def finish_soldering_job_with_consumption(job, record, actor, notes=None):
    if not job:
        return {"job": None, "consumption": default_consumption_summary({}), "deducted": False, "error": "not_found"}
    now = now_text()
    export_needed = False
    result = None
    with db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM soldering_jobs WHERE job_id = ?", (job.get("job_id"),)).fetchone()
        if not row:
            result = {"job": None, "consumption": default_consumption_summary({}), "deducted": False, "error": "not_found"}
        else:
            current = soldering_job_from_row(row)
            purchase_status = current.get("purchase_check_json") or {"checked": 0, "received_count": 0, "pending_count": 0, "items": []}
            if int(current.get("inventory_consumed") or 0):
                if current.get("status") in SOLDERING_ACTIVE_STATUSES:
                    new_notes = current.get("notes") or ""
                    if notes is not None:
                        new_notes = str(notes or "")[:1000]
                    conn.execute(
                        """
                        UPDATE soldering_jobs
                        SET status = ?, finished_at = ?, notes = ?, updated_at = ?
                        WHERE job_id = ?
                        """,
                        (SOLDERING_COMPLETED_STATUS, current.get("finished_at") or now, new_notes, now, current["job_id"]),
                    )
                    current = soldering_job_from_row(
                        conn.execute("SELECT * FROM soldering_jobs WHERE job_id = ?", (current["job_id"],)).fetchone()
                    )
                result = {
                    "job": current,
                    "consumption": soldering_consumption_summary(conn, current, purchase_status),
                    "deducted": False,
                    "already_consumed": True,
                    "error": None,
                }
            elif current.get("status") not in SOLDERING_ACTIVE_STATUSES and current.get("status") != SOLDERING_COMPLETED_STATUS:
                result = {
                    "job": current,
                    "consumption": soldering_consumption_summary(conn, current, purchase_status),
                    "deducted": False,
                    "already_consumed": False,
                    "error": "not_active",
                }
            else:
                existing_ledger_count = conn.execute(
                    "SELECT COUNT(*) AS count FROM soldering_consumption_items WHERE job_id = ?",
                    (current["job_id"],),
                ).fetchone()["count"]
                if existing_ledger_count:
                    summary = soldering_consumption_summary(conn, current, purchase_status)
                else:
                    purchase_record = record if isinstance(record, dict) else {
                        "bom_id": current.get("bom_id"),
                        "upload_id": current.get("bom_upload_id"),
                    }
                    sources = load_soldering_consumption_sources(conn, purchase_record, current)
                    purchase_status = related_purchase_status(conn, purchase_record, sources)
                    apply_soldering_inventory_consumption(conn, current, sources, now)
                    summary_job = dict(current)
                    summary_job["inventory_consumed"] = 1
                    summary_job["purchase_check_json"] = purchase_status
                    summary = soldering_consumption_summary(conn, summary_job, purchase_status)
                    if not sources:
                        summary["status"] = "no_bom_items"
                    export_needed = summary.get("summary", {}).get("consumed_quantity", 0) > 0
                new_notes = current.get("notes") or ""
                if notes is not None and current.get("status") in SOLDERING_ACTIVE_STATUSES:
                    new_notes = str(notes or "")[:1000]
                finished_at = current.get("finished_at") or now
                conn.execute(
                    """
                    UPDATE soldering_jobs
                    SET status = ?,
                        finished_at = ?,
                        notes = ?,
                        inventory_consumed = 1,
                        inventory_consumption_json = ?,
                        purchase_check_json = ?,
                        updated_at = ?
                    WHERE job_id = ?
                    """,
                    (
                        SOLDERING_COMPLETED_STATUS,
                        finished_at,
                        new_notes,
                        json.dumps(summary, ensure_ascii=False),
                        json.dumps(purchase_status, ensure_ascii=False),
                        now,
                        current["job_id"],
                    ),
                )
                finished = soldering_job_from_row(
                    conn.execute("SELECT * FROM soldering_jobs WHERE job_id = ?", (current["job_id"],)).fetchone()
                )
                result = {
                    "job": finished,
                    "consumption": soldering_consumption_summary(conn, finished, purchase_status),
                    "deducted": bool(summary.get("summary", {}).get("consumed_quantity", 0)),
                    "already_consumed": bool(existing_ledger_count),
                    "error": None,
                }
    if export_needed:
        export_current_inventory()
    return result


def mark_bom_soldering_started(record, job):
    record["status"] = "soldering_active"
    record["active_soldering_job_id"] = job["job_id"]
    record["latest_soldering_job_id"] = job["job_id"]
    record["soldering_started_at"] = job.get("started_at") or now_text()
    record["soldering_updated_at"] = now_text()
    record["soldering_board_count"] = job.get("board_count")
    if job.get("pcb_file_id"):
        record["active_soldering_pcb_file_id"] = job.get("pcb_file_id")
    return record


def mark_bom_soldering_finished(record, job):
    record["status"] = "soldering_completed"
    record["active_soldering_job_id"] = None
    record["latest_soldering_job_id"] = job["job_id"]
    record["last_completed_soldering_job_id"] = job["job_id"]
    record["soldering_finished_at"] = job.get("finished_at") or now_text()
    record["soldering_updated_at"] = now_text()
    record["soldering_board_count"] = job.get("board_count")
    record["soldering_inventory_consumed"] = bool(int(job.get("inventory_consumed") or 0))
    record["soldering_inventory_consumption"] = job.get("inventory_consumption_json") or {}
    record["soldering_purchase_check"] = job.get("purchase_check_json") or {}
    return record


def payload_value(data, *keys, default=""):
    for key in keys:
        if key not in data:
            continue
        value = data.get(key)
        if isinstance(value, list):
            value = value[-1] if value else default
        if value is None:
            return default
        return value
    return default


def load_lcsc_cache():
    if not LCSC_CACHE_JSON.exists():
        return {}
    try:
        data = json.loads(read_data_text(LCSC_CACHE_JSON, encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_lcsc_cache(cache):
    LCSC_DIR.mkdir(parents=True, exist_ok=True)
    write_data_text(LCSC_CACHE_JSON, json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
    headers = [
        "lcsc_code",
        "product_id",
        "product_model",
        "product_name",
        "category",
        "brand",
        "package",
        "stock",
        "min_price",
        "product_url",
        "updated_at",
        "status",
    ]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=headers)
    writer.writeheader()
    for code in sorted(cache):
        row = {key: cache[code].get(key, "") for key in headers}
        writer.writerow(row)
    write_data_text(LCSC_CACHE_CSV, buffer.getvalue(), encoding="utf-8-sig")


def find_nested_value(obj, target_key):
    if isinstance(obj, dict):
        if target_key in obj:
            return obj[target_key]
        for value in obj.values():
            found = find_nested_value(value, target_key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_nested_value(value, target_key)
            if found is not None:
                return found
    return None


def parse_lcsc_search_html(code, text):
    match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
    if not match:
        return {"lcsc_code": code, "status": "not_parsed", "updated_at": now_text()}
    try:
        data = json.loads(html.unescape(match.group(1)))
    except Exception:
        return {"lcsc_code": code, "status": "json_error", "updated_at": now_text()}
    records = find_nested_value(data, "productRecordList") or []
    if not records:
        return {"lcsc_code": code, "status": "not_found", "updated_at": now_text()}
    product = None
    for record in records:
        candidate = record.get("productVO", record) if isinstance(record, dict) else {}
        if str(candidate.get("productCode", "")).upper() == code.upper():
            product = candidate
            break
    if product is None and isinstance(records[0], dict):
        product = records[0].get("productVO", records[0])
    prices = product.get("productPriceList") or []
    numeric_prices = []
    for price in prices:
        try:
            numeric_prices.append(float(price.get("productPrice")))
        except Exception:
            pass
    params = product.get("paramLinkedMap") or {}
    product_id = str(product.get("productId") or "")
    return {
        "lcsc_code": str(product.get("productCode") or code).upper(),
        "product_id": product_id,
        "product_model": product.get("productModel") or "",
        "product_name": product.get("productName") or "",
        "category": product.get("productType") or "",
        "brand": product.get("productGradePlateName") or "",
        "package": product.get("encapsulationModel") or "",
        "stock": product.get("stockNumber") or product.get("totalStockNumber") or "",
        "valid_stock": product.get("validStockNumber") or "",
        "min_price": min(numeric_prices) if numeric_prices else "",
        "product_url": f"https://item.szlcsc.com/{product_id}.html" if product_id else f"https://so.szlcsc.com/global.html?k={code}",
        "params": params,
        "updated_at": now_text(),
        "status": "ok",
    }


def fetch_lcsc_product(code):
    url = f"https://so.szlcsc.com/global.html?k={urllib.parse.quote(code)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarehouseInventory/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    )
    try:
        with safe_external_urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8", errors="replace")
        return parse_lcsc_search_html(code, body)
    except Exception as exc:
        return {"lcsc_code": code, "status": f"fetch_error: {exc}", "updated_at": now_text()}


def enrich_lcsc_codes(codes, force=False, max_codes=80):
    cache = load_lcsc_cache()
    fetched = []
    for code in sorted(set(codes))[:max_codes]:
        cached = cache.get(code)
        if cached and not force and cached.get("status") == "ok":
            continue
        product = fetch_lcsc_product(code)
        cache[code] = product
        fetched.append(code)
        time.sleep(0.35)
    save_lcsc_cache(cache)
    return {"cache": cache, "fetched": fetched}


def lcsc_aliases_for_codes(codes, cache=None):
    cache = cache if cache is not None else load_lcsc_cache()
    aliases = []
    for code in codes:
        product = cache.get(code)
        if not product:
            continue
        aliases.extend(
            [
                product.get("product_model", ""),
                product.get("product_name", ""),
                product.get("package", ""),
                product.get("category", ""),
            ]
        )
        params = product.get("params") if isinstance(product.get("params"), dict) else {}
        aliases.extend(str(value) for value in params.values())
    return [alias for alias in aliases if alias]


def simplify_catalog_node(node):
    children = node.get("sonCatalogList") or []
    return {
        "id": node.get("catalogId"),
        "name": node.get("catalogName") or "",
        "children": [simplify_catalog_node(child) for child in children if child.get("catalogName")],
    }


def default_lcsc_categories():
    return {
        "updated_at": now_text(),
        "source": "fallback",
        "categories": [
            {
                "id": 312,
                "name": "电容 / 电阻 / 电感",
                "children": [
                    {"id": 312, "name": "电容", "children": [{"id": 313, "name": "贴片电容(MLCC)", "children": []}, {"id": 315, "name": "铝电解电容", "children": []}]},
                    {"id": 308, "name": "电阻", "children": [{"id": 439, "name": "贴片电阻", "children": []}, {"id": 440, "name": "插件电阻", "children": []}]},
                    {"id": 316, "name": "电感", "children": [{"id": 317, "name": "功率电感", "children": []}, {"id": 318, "name": "磁珠", "children": []}]},
                ],
            },
            {
                "id": 320,
                "name": "连接器 / 端子 / 开关",
                "children": [
                    {"id": 321, "name": "连接器", "children": [{"id": 322, "name": "线对板连接器", "children": []}, {"id": 323, "name": "板对板连接器", "children": []}]},
                    {"id": 324, "name": "开关", "children": []},
                ],
            },
            {"id": 500, "name": "微控制器 / 逻辑器件", "children": [{"id": 501, "name": "MCU", "children": []}, {"id": 502, "name": "逻辑芯片", "children": []}]},
            {"id": 600, "name": "二极管 / 晶体管", "children": [{"id": 601, "name": "二极管", "children": []}, {"id": 602, "name": "MOSFET", "children": []}]},
        ],
    }


def fetch_lcsc_categories(force=False):
    if LCSC_CATEGORIES_JSON.exists() and not force:
        try:
            return json.loads(read_data_text(LCSC_CATEGORIES_JSON, encoding="utf-8"))
        except Exception:
            pass
    url = "https://www.szlcsc.com"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarehouseInventory/1.0",
            "Accept": "text/html,application/xhtml+xml",
            "Accept-Language": "zh-CN,zh;q=0.9",
        },
    )
    try:
        with safe_external_urlopen(req, timeout=20) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        match = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>', text, re.S)
        if not match:
            raise ValueError("missing __NEXT_DATA__")
        data = json.loads(html.unescape(match.group(1)))
        raw = data.get("props", {}).get("pageProps", {}).get("catalogList", {})
        if isinstance(raw, dict):
            categories = [simplify_catalog_node(value) for value in raw.values() if value.get("catalogName")]
        else:
            categories = [simplify_catalog_node(value) for value in raw if value.get("catalogName")]
        result = {"updated_at": now_text(), "source": url, "categories": categories}
    except Exception as exc:
        result = default_lcsc_categories()
        result["error"] = str(exc)
    LCSC_DIR.mkdir(parents=True, exist_ok=True)
    write_data_text(LCSC_CATEGORIES_JSON, json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def flatten_category_names(categories):
    names = []
    def walk(node, path=""):
        current = node.get("name", "")
        full = f"{path} / {current}" if path and current else current
        if full:
            names.append(full)
        for child in node.get("children", []):
            walk(child, full)
    for category in categories:
        walk(category)
    return names


def infer_category_from_product(product, fallback=""):
    if not product:
        return fallback
    category = product.get("category") or fallback
    name = product.get("product_name") or ""
    package = product.get("package") or ""
    if "电阻" in category or "电阻" in name:
        return "电容 / 电阻 / 电感 / 电阻 / 贴片电阻" if package else "电容 / 电阻 / 电感 / 电阻"
    if "电容" in category or "电容" in name:
        return "电容 / 电阻 / 电感 / 电容 / 贴片电容(MLCC)" if package else "电容 / 电阻 / 电感 / 电容"
    if "电感" in category:
        return "电容 / 电阻 / 电感 / 电感"
    if "连接器" in category or "连接器" in name:
        return "连接器 / 端子 / 开关 / 连接器"
    return category or fallback


def extract_lcsc_specs(product):
    if not product:
        return {}
    params = product.get("params") if isinstance(product.get("params"), dict) else {}
    haystack = " ".join(
        str(value or "")
        for value in [
            product.get("product_name"),
            product.get("product_model"),
            product.get("category"),
            product.get("package"),
            *params.keys(),
            *params.values(),
        ]
    )
    value_spec = ""
    for pattern in (
        r"(\d+(?:\.\d+)?\s*(?:mΩ|Ω|KΩ|kΩ|MΩ|R|K|M)\b)",
        r"(\d+(?:\.\d+)?\s*(?:pF|nF|uF|µF|μF)\b)",
        r"(\d+(?:\.\d+)?\s*(?:uH|µH|μH|mH|H)\b)",
    ):
        match = re.search(pattern, haystack, re.I)
        if match:
            value_spec = match.group(1).replace(" ", "")
            break
    voltage = ""
    voltage_match = re.search(r"(\d+(?:\.\d+)?\s*V)\b", haystack, re.I)
    if voltage_match:
        voltage = voltage_match.group(1).replace(" ", "")
    power = ""
    power_match = re.search(r"(\d+(?:\.\d+)?\s*(?:mW|W))\b", haystack, re.I)
    if power_match:
        power = power_match.group(1).replace(" ", "")
    return {
        "value_spec": value_spec,
        "package": product.get("package") or "",
        "voltage": voltage,
        "power": power,
        "brand": product.get("brand") or "",
        "category": infer_category_from_product(product, product.get("category") or ""),
        "url": product.get("product_url") or "",
    }


def _collect_model_links(value, found):
    if isinstance(value, dict):
        for item in value.values():
            _collect_model_links(item, found)
    elif isinstance(value, list):
        for item in value:
            _collect_model_links(item, found)
    elif isinstance(value, str):
        for match in re.findall(r"https?://[^\s\"'<>]+?\.(?:step|stp|stl|obj|glb|gltf)(?:\?[^\s\"'<>]*)?", value, re.I):
            found.append(match)


def _model_url_kind(url):
    text = str(url or "").lower()
    if ".step" in text or ".stp" in text or "/qaxj6khrdkw4blvcg8qjps7y/" in text:
        return "step"
    if ".obj" in text or "/3dmodel/" in text:
        return "obj"
    if ".glb" in text or ".gltf" in text:
        return "gltf"
    if ".stl" in text:
        return "stl"
    return "model"


def _dedupe_strings(values, limit=12):
    deduped = []
    seen = set()
    for value in values:
        clean = str(value or "").strip()
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
        if limit and len(deduped) >= limit:
            break
    return deduped


def easyeda_model_urls(uuid):
    clean_uuid = str(uuid or "").strip()
    if not clean_uuid:
        return {}
    return {
        "step_url": f"{EASYEDA_STEP_BASE_URL}/{urllib.parse.quote(clean_uuid)}",
        "obj_url": f"{EASYEDA_OBJ_BASE_URL}/{urllib.parse.quote(clean_uuid)}",
        "viewer_url": f"{EASYEDA_EDITOR_URL}#id=!{urllib.parse.quote(clean_uuid)}",
    }


def probe_model_url(url, timeout=8):
    if not url:
        return {"url": "", "ok": False, "status": "missing", "checked_at": now_text()}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarehouseInventory/1.0",
        "Accept": "*/*",
        "Range": "bytes=0-0",
    }
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with safe_external_urlopen(request, timeout=timeout) as resp:
            return {
                "url": url,
                "ok": 200 <= int(resp.status) < 400,
                "status": str(resp.status),
                "content_type": resp.headers.get("Content-Type", ""),
                "content_length": resp.headers.get("Content-Length", ""),
                "accept_ranges": resp.headers.get("Accept-Ranges", ""),
                "access_control_allow_origin": resp.headers.get("Access-Control-Allow-Origin", ""),
                "checked_at": now_text(),
            }
    except urllib.error.HTTPError as exc:
        return {
            "url": url,
            "ok": False,
            "status": str(exc.code),
            "reason": str(exc.reason or ""),
            "checked_at": now_text(),
        }
    except Exception as exc:
        return {
            "url": url,
            "ok": False,
            "status": "fetch_error",
            "reason": str(exc),
            "checked_at": now_text(),
        }


def fetch_lcsc_model_links(product):
    url = product.get("product_url") if product else ""
    if not url:
        return []
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarehouseInventory/1.0",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
        },
    )
    try:
        with safe_external_urlopen(req, timeout=12) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except Exception:
        return []
    links = []
    _collect_model_links(text, links)
    for raw in re.findall(r"['\"]([^'\"]+\.(?:step|stp|stl|obj|glb|gltf)(?:\?[^'\"]*)?)['\"]", text, re.I):
        links.append(urllib.parse.urljoin(url, html.unescape(raw)))
    deduped = []
    seen = set()
    for link in links:
        clean = link.strip()
        if clean and clean not in seen:
            seen.add(clean)
            deduped.append(clean)
    return deduped[:6]


def extract_easyeda_model_info(data):
    result = data.get("result") if isinstance(data, dict) else {}
    package_detail = result.get("packageDetail") if isinstance(result, dict) else {}
    shapes = []
    if isinstance(package_detail, dict):
        data_str = package_detail.get("dataStr")
        if isinstance(data_str, dict):
            raw_shapes = data_str.get("shape")
            if isinstance(raw_shapes, list):
                shapes = raw_shapes
    elif isinstance(package_detail, list):
        shapes = package_detail
    for shape in shapes:
        if not isinstance(shape, str) or not shape.startswith("SVGNODE~"):
            continue
        parts = shape.split("~", 2)
        if len(parts) < 2:
            continue
        try:
            svg_data = json.loads(parts[1])
        except Exception:
            continue
        attrs = svg_data.get("attrs") if isinstance(svg_data, dict) else {}
        if not isinstance(attrs, dict) or attrs.get("c_etype") != "outline3D":
            continue
        uuid = str(attrs.get("uuid") or "").strip()
        title = str(attrs.get("title") or "").strip()
        if uuid and title:
            return {"uuid": uuid, "title": title}
    return None


def fetch_easyeda_model_info(lcsc_code):
    code = str(lcsc_code or "").strip().upper()
    if not re.fullmatch(r"C\d{3,}", code):
        return {"status": "invalid_code", "download_status": "not_checked", "debug": {"lcsc_code": code}}
    url = f"https://easyeda.com/api/products/{urllib.parse.quote(code)}/components?version={EASYEDA_COMPONENT_API_VERSION}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) WarehouseInventory/1.0",
            "Accept": "application/json,*/*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
            "Referer": "https://easyeda.com/",
        },
    )
    try:
        with safe_external_urlopen(req, timeout=18) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as exc:
        return {
            "status": "api_error",
            "download_status": "not_checked",
            "source_url": url,
            "debug": {"http_status": exc.code, "reason": str(exc.reason or "")},
        }
    except Exception as exc:
        return {
            "status": "api_error",
            "download_status": "not_checked",
            "source_url": url,
            "debug": {"reason": str(exc)},
        }
    info = extract_easyeda_model_info(data)
    if not info:
        return {
            "status": "not_found",
            "download_status": "not_checked",
            "source_url": url,
            "debug": {"reason": "EasyEDA component payload did not contain an outline3D uuid."},
        }
    uuid = info["uuid"]
    urls = easyeda_model_urls(uuid)
    probes = {
        "step": probe_model_url(urls["step_url"]),
        "obj": probe_model_url(urls["obj_url"]),
    }
    download_status = "ok" if any(item.get("ok") for item in probes.values()) else "unreachable"
    return {
        "uuid": uuid,
        "title": info["title"],
        "status": "ok",
        "download_status": download_status,
        "probes": probes,
        "source_url": url,
        **urls,
    }


def lcsc_model_payload(product, specs=None):
    specs = specs or {}
    links = []
    _collect_model_links(product or {}, links)
    cached_links = product.get("model_links") if isinstance(product, dict) and isinstance(product.get("model_links"), list) else []
    links.extend(str(item) for item in cached_links if item)
    code = (product or {}).get("lcsc_code") or (product or {}).get("product_code") or ""
    easyeda_model = product.get("easyeda_model") if isinstance(product, dict) and isinstance(product.get("easyeda_model"), dict) else None
    easyeda_lookup = None
    if product and product.get("status") == "ok" and (not easyeda_model or not easyeda_model.get("uuid")):
        easyeda_lookup = fetch_easyeda_model_info(code)
        if easyeda_lookup and easyeda_lookup.get("uuid"):
            easyeda_model = easyeda_lookup
            product["easyeda_model"] = easyeda_model
    elif easyeda_model and easyeda_model.get("uuid"):
        urls = easyeda_model_urls(easyeda_model.get("uuid"))
        changed = False
        for key, value in urls.items():
            if value and not easyeda_model.get(key):
                easyeda_model[key] = value
                changed = True
        if not easyeda_model.get("status"):
            easyeda_model["status"] = "cached"
            changed = True
        if not easyeda_model.get("download_status"):
            probes = {
                "step": probe_model_url(easyeda_model.get("step_url")),
                "obj": probe_model_url(easyeda_model.get("obj_url")),
            }
            easyeda_model["probes"] = probes
            easyeda_model["download_status"] = "ok" if any(item.get("ok") for item in probes.values()) else "unreachable"
            changed = True
        if changed and isinstance(product, dict):
            product["easyeda_model"] = easyeda_model
    if easyeda_model and easyeda_model.get("step_url"):
        links.insert(0, easyeda_model["step_url"])
    if easyeda_model and easyeda_model.get("obj_url"):
        links.append(easyeda_model["obj_url"])
    if product and product.get("status") == "ok" and not links:
        fetched = fetch_lcsc_model_links(product)
        if fetched:
            product["model_links"] = fetched
            links.extend(fetched)
    deduped = _dedupe_strings(links)
    model_urls = []
    for link in deduped:
        model_urls.append({"url": link, "kind": _model_url_kind(link), "source": "easyeda" if "modules.easyeda.com" in link else "lcsc"})
    package = specs.get("package") or (product or {}).get("package") or ""
    category = specs.get("category") or (product or {}).get("category") or ""
    category_leaf = str(category or "").split("/")[-1].strip()
    name = " ".join(str(v or "") for v in [category_leaf, package, (product or {}).get("product_name"), (product or {}).get("product_model")]).lower()
    if "conn" in name or "connector" in name or "连接器" in name or "插头" in name:
        kind = "connector"
    elif "sop" in name or "qfn" in name or "ic" in name or "芯片" in name:
        kind = "ic"
    elif "电阻" in name or "resistor" in name or re.search(r"\d+\s*(r|k|m|ohm|Ω)", name, re.I):
        kind = "resistor"
    elif "电容" in name or "capacitor" in name or re.search(r"\d+\s*(pf|nf|uf)", name, re.I):
        kind = "capacitor"
    else:
        kind = "generic"
    source = "generated"
    if easyeda_model and easyeda_model.get("uuid"):
        source = "easyeda"
    elif deduped:
        source = "lcsc"
    download_status = "not_found"
    if easyeda_model:
        download_status = easyeda_model.get("download_status") or ("not_checked" if easyeda_model.get("uuid") else "not_found")
    elif deduped:
        download_status = "not_checked"
    fallback_reason = ""
    if not deduped:
        fallback_reason = "no_public_model_url"
    elif easyeda_model and easyeda_model.get("download_status") not in ("ok", "not_checked", None):
        fallback_reason = "model_url_probe_failed"
    debug = {
        "lcsc_code": code,
        "easyeda_status": easyeda_model.get("status") if easyeda_model else (easyeda_lookup or {}).get("status", ""),
        "easyeda_source_url": easyeda_model.get("source_url") if easyeda_model else (easyeda_lookup or {}).get("source_url", ""),
        "link_count": len(deduped),
    }
    if easyeda_lookup and not easyeda_lookup.get("uuid"):
        debug["easyeda_error"] = easyeda_lookup.get("debug") or {}
    return {
        "source": source,
        "status": "real_model" if deduped else "fallback",
        "download_status": download_status,
        "fallback_reason": fallback_reason,
        "links": deduped,
        "model_urls": model_urls,
        "uuid": easyeda_model.get("uuid") if easyeda_model else "",
        "step_url": easyeda_model.get("step_url") if easyeda_model else "",
        "obj_url": easyeda_model.get("obj_url") if easyeda_model else "",
        "viewer_url": easyeda_model.get("viewer_url") if easyeda_model else "",
        "model_title": easyeda_model.get("title") if easyeda_model else "",
        "probes": easyeda_model.get("probes", {}) if easyeda_model else {},
        "debug": debug,
        "kind": kind,
        "package": package,
        "category": category,
        "label": (product or {}).get("product_model") or (product or {}).get("product_name") or (product or {}).get("lcsc_code") or "",
    }


def compose_inventory_name(name, value_spec="", package="", voltage="", product=None, lcsc_code=""):
    if name:
        return name
    if product:
        return product.get("product_name") or product.get("product_model") or lcsc_code
    parts = [value_spec, package, voltage]
    generated = " / ".join(part for part in parts if part)
    return generated or lcsc_code


def build_product_note(base_note, product=None, extra=None):
    parts = [base_note.strip()] if base_note and base_note.strip() else []
    extra = extra or {}
    if product:
        fields = [
            ("LCSC", product.get("lcsc_code")),
            ("型号", product.get("product_model")),
            ("品牌", product.get("brand")),
            ("封装", product.get("package")),
            ("库存", product.get("stock")),
            ("最低价", product.get("min_price")),
            ("链接", product.get("product_url")),
        ]
        for label, value in fields:
            if value not in ("", None):
                parts.append(f"{label}:{value}")
    for label, value in extra.items():
        if value:
            parts.append(f"{label}:{value}")
    return " | ".join(dict.fromkeys(parts))


def is_noise_stat_value(value, category=""):
    text = str(value or "").strip()
    norm = normalized_header(text)
    cat = normalized_header(category)
    if not norm:
        return True
    bad_terms = {
        "reporttime",
        "projecttitle",
        "variant",
        "billofmaterials",
        "no",
        "quantity",
        "comment",
        "designator",
        "footprint",
        "manufacturerpart",
        "manufacturer",
        "supplierpart",
        "supplier",
        "lcscprice",
        "total",
        "未分类",
    }
    if norm in bad_terms or cat == "未分类" and norm in bad_terms:
        return True
    if norm in {"r0201", "r0402", "r0603", "r0805", "r1206", "c0201", "c0402", "c0603", "c0805", "c1206"}:
        return True
    if re.fullmatch(r"[rc]\d{4}", norm):
        return True
    if cat == "未分类" and re.match(
        r"^(CAP|RES|IND|CONN|SOT|SOD|SMA|SMB|QFN|QFP|LQFP|TSSOP|SOIC|FUSE|TEST)[-_]",
        text,
        re.I,
    ):
        return True
    if len(norm) <= 1:
        return True
    return False


def cleanup_analytics_noise():
    try:
        with db() as conn:
            search_rows = conn.execute("SELECT id, term FROM search_events").fetchall()
            bad_search_ids = [row["id"] for row in search_rows if is_noise_stat_value(row["term"])]
            for row_id in bad_search_ids:
                conn.execute("DELETE FROM search_events WHERE id = ?", (row_id,))
            purchase_rows = conn.execute("SELECT id, name, category FROM purchase_items").fetchall()
            bad_purchase_ids = [
                row["id"]
                for row in purchase_rows
                if is_noise_stat_value(row["name"], row["category"])
                or (normalized_header(row["category"]) == "未分类" and is_noise_stat_value(row["name"]))
            ]
            for row_id in bad_purchase_ids:
                conn.execute("DELETE FROM purchase_items WHERE id = ?", (row_id,))
            conn.execute("DELETE FROM access_logs WHERE datetime(created_at) < datetime('now', '-90 day')")
    except Exception as exc:
        print(f"[cleanup] analytics cleanup failed: {exc}")


def read_text_rows(path):
    return bom_tools.read_text_rows(path, read_bytes=read_data_bytes)


def read_xlsx_rows(path):
    return bom_tools.read_xlsx_rows(path, read_bytes=read_data_bytes)


def read_bom_file_rows(path):
    return bom_tools.read_bom_file_rows(path, read_bytes=read_data_bytes)


def parse_bom_file(path):
    return bom_tools.parse_bom_file(path, read_bytes=read_data_bytes)


def parse_bom_file_detail(path):
    return bom_tools.parse_bom_file_detail(path, read_bytes=read_data_bytes)


def component_match_key(value):
    text = str(value or "").lower()
    text = text.replace("wafer", "").replace("conn", "")
    text = text.replace("pin", "p").replace("plb", "p").replace("pwb", "p")
    return re.sub(r"[^a-z0-9]+", "", text)


def normalize_package_code(value):
    text = str(value or "").strip().upper()
    if not text:
        return ""
    compact = re.sub(r"[^A-Z0-9]+", "", text)
    if re.fullmatch(r"C\d{4,}", compact) and compact not in COMMON_PACKAGE_CODES:
        return ""

    def drop_lcsc_code(match):
        code = match.group(0)
        return code if code in COMMON_PACKAGE_CODES else ""

    compact = re.sub(r"C\d{4,}", drop_lcsc_code, compact)
    for code in sorted(PACKAGE_SIZE_CODES, key=len, reverse=True):
        if code in compact:
            return code
    match = re.search(r"(SOT|SOD|SOIC|SOP|TSSOP|MSOP|QFN|DFN|VQFN|LQFP|QFP|DIP|SON|TO)(\d+(?:X\d+)?[A-Z0-9]*)", compact)
    if match:
        family = match.group(1)
        if family == "VQFN":
            family = "QFN"
        return family + match.group(2)
    return ""


def extract_package_codes(values):
    codes = []
    for value in values:
        code = normalize_package_code(value)
        if code and code not in codes:
            codes.append(code)
    return codes


def package_only_candidate(value):
    text = str(value or "").strip()
    if not text:
        return False
    text = re.sub(r"^(?:封装|package|footprint|pcb\s*package|pcb\s*footprint)\s*[:：]?\s*", "", text, flags=re.I)
    compact = re.sub(r"[^A-Z0-9]+", "", text.upper())
    code = normalize_package_code(text)
    if not code:
        return False
    variants = {code, f"C{code}", f"R{code}"}
    return compact in variants


def stock_for_item(conn, category, name, aliases=None, package=None):
    candidates = [name] + list(aliases or [])
    candidates.extend(str(name).split("|"))
    requested_packages = set(extract_package_codes([package] + candidates))
    requested_lcsc_codes = set(extract_lcsc_codes(candidates))
    match_candidates = [value for value in candidates if not package_only_candidate(value)]
    normalized = {normalize_key(value) for value in match_candidates if str(value or "").strip()}
    component_keys = {component_match_key(value) for value in match_candidates if str(value or "").strip()}
    component_keys = {value for value in component_keys if len(value) >= 4}
    value_keys = {
        component_value_key(value, category)
        for value in match_candidates
        if str(value or "").strip() and component_value_key(value, category)
    }
    rows = conn.execute("SELECT category, name, quantity, note FROM inventory").fetchall()
    total = 0
    for row in rows:
        inv_name = row["name"]
        inv_note = row["note"] or ""
        inv_text_values = [inv_name, inv_note]
        inv_packages = set(extract_package_codes(inv_text_values))
        inv_lcsc_codes = set(extract_lcsc_codes(inv_text_values))
        inv_norm = normalize_key(inv_name)
        inv_key = component_match_key(inv_name)
        inv_value_key = component_value_key(inv_name, row["category"])
        exact_lcsc = bool(requested_lcsc_codes and requested_lcsc_codes.intersection(inv_lcsc_codes))
        if requested_packages and not exact_lcsc and not requested_packages.intersection(inv_packages):
            continue
        direct = inv_norm in normalized or any(
            len(candidate) >= 4 and (candidate in inv_norm or inv_norm in candidate) for candidate in normalized
        )
        component = len(inv_key) >= 4 and (
            inv_key in component_keys
            or any(len(candidate) >= 4 and (candidate in inv_key or inv_key in candidate) for candidate in component_keys)
        )
        value_match = inv_value_key and inv_value_key in value_keys
        if exact_lcsc or direct or component or value_match:
            total += int(row["quantity"] or 0)
    return total


def build_bom_record(path, original_name, user, detail, upload_id, purchase_file, purchase_rows, created_at_iso, lcsc_codes, lcsc_fetched):
    items = detail["items"]
    total_qty = sum(int(item.get("quantity") or 0) for item in items)
    purchase_total_qty = sum(int(row[4] or 0) for row in purchase_rows)
    bom_id = f"bom_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(6)}"
    return {
        "bom_id": bom_id,
        "upload_id": upload_id,
        "owner_user_id": user["id"],
        "owner_username": user["username"],
        "owner": {"user_id": user["id"], "username": user["username"]},
        "original_filename": original_name or "",
        "stored_filename": path.name,
        "created_at": created_at_iso,
        "status": "awaiting_pcb",
        "raw_rows": detail["raw_rows"],
        "component_rows": detail["component_rows"],
        "aggregated_items": items,
        "summary": {
            "raw_row_count": len(detail["raw_rows"]),
            "component_row_count": len(detail["component_rows"]),
            "aggregated_item_count": len(items),
            "total_quantity": total_qty,
            "purchase_item_count": len(purchase_rows),
            "purchase_total_quantity": purchase_total_qty,
            "lcsc_code_count": len(lcsc_codes),
        },
        "comparison": {
            "purchase_file": purchase_file,
            "purchase_rows": purchase_rows,
            "lcsc_codes": lcsc_codes,
            "lcsc_fetched": lcsc_fetched,
        },
    }


def persist_purchase_order(conn, upload_id, purchase_file, purchase_rows, user, created_at):
    if not purchase_rows:
        return None
    total_purchase_quantity = sum(parse_int(row[4], 0) for row in purchase_rows)
    cur = conn.execute(
        """
        INSERT INTO purchase_orders (upload_id, file_name, created_by, item_count, total_quantity, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (upload_id, purchase_file, user["username"], len(purchase_rows), total_purchase_quantity, created_at),
    )
    order_id = cur.lastrowid
    for row in purchase_rows:
        conn.execute(
            """
            INSERT INTO purchase_items (
                order_id, category, name, required_quantity, stock_quantity,
                purchase_quantity, reason, created_by, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                order_id,
                row[0],
                row[1],
                parse_int(row[2], 0),
                parse_int(row[3], 0),
                parse_int(row[4], 0),
                str(row[5] or ""),
                user["username"],
                created_at,
            ),
        )
    return order_id


SPARE_BOP_CATEGORIES = (
    "铝管",
    "碳板",
    "结构件",
    "紧固件",
    "线材",
    "电源模块",
    "控制模块",
    "传感器",
    "电机/执行器",
    "通信模块",
    "工具耗材",
    "其他",
)

SPARE_BOP_ROBOT_SCOPES = {
    "dual_robot": "双机器人",
    "robot_a": "机器人 1",
    "robot_b": "机器人 2",
    "pit_spares": "赛场通用备件",
}


def spare_bop_scope_label(value):
    return SPARE_BOP_ROBOT_SCOPES.get(str(value or "").strip(), str(value or "双机器人"))


def spare_bop_item_aliases(row):
    values = [row_value(row, "name", ""), row_value(row, "spec", "")]
    values.extend(extract_lcsc_codes(values))
    aliases = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in aliases:
            aliases.append(text)
    combined = " ".join(str(value or "") for value in values)
    for token in re.findall(r"[A-Za-z0-9][A-Za-z0-9._/-]{2,}", combined):
        if token not in aliases:
            aliases.append(token)
    return aliases[:20]


def parse_spare_bop_text(raw_text):
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("请填写至少一行备件 BOP 明细。")
    rows = []
    reader = csv.reader(io.StringIO(text))
    for line_no, parts in enumerate(reader, start=1):
        parts = [str(part or "").strip() for part in parts]
        if not parts or not any(parts):
            continue
        if line_no == 1 and any(token in parts[0] for token in ("机器人", "robot")) and len(parts) > 1 and "类别" in parts[1]:
            continue
        while len(parts) < 10:
            parts.append("")
        robot_name, category, name, spec, qty_per_robot, robot_count, spare_qty, unit, location_hint, note = parts[:10]
        category = category or "其他"
        name = name or spec
        if not name:
            raise ValueError(f"第 {line_no} 行缺少备件名称。")
        quantity_per_robot = max(0, parse_int(qty_per_robot, 0))
        robot_count_value = max(1, parse_int(robot_count, 1))
        spare_quantity = max(0, parse_int(spare_qty, 0))
        required_quantity = quantity_per_robot * robot_count_value + spare_quantity
        if required_quantity <= 0:
            raise ValueError(f"第 {line_no} 行需求数量必须大于 0。")
        rows.append(
            {
                "robot_name": robot_name or "通用",
                "category": category[:80],
                "name": name[:180],
                "spec": spec[:180],
                "unit": (unit or "个")[:20],
                "quantity_per_robot": quantity_per_robot,
                "robot_count": robot_count_value,
                "spare_quantity": spare_quantity,
                "required_quantity": required_quantity,
                "location_hint": location_hint[:120],
                "note": note[:300],
            }
        )
    if not rows:
        raise ValueError("未读取到有效备件行，请按示例用英文逗号分隔。")
    if len(rows) > 300:
        raise ValueError("单次最多录入 300 行备件明细，请拆分提交。")
    return rows


def create_spare_bop_list(user, title, season, robot_scope, items):
    title = str(title or "").strip()[:120]
    if not title:
        raise ValueError("请填写 BOP 标题。")
    robot_scope = str(robot_scope or "dual_robot").strip()
    if robot_scope not in SPARE_BOP_ROBOT_SCOPES:
        robot_scope = "dual_robot"
    now = now_text()
    bop_id = f"bop_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    with db() as conn:
        conn.execute(
            """
            INSERT INTO spare_bop_lists (bop_id, title, season, robot_scope, created_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (bop_id, title, str(season or "").strip()[:80], robot_scope, user["username"], now, now),
        )
        for item in items:
            conn.execute(
                """
                INSERT INTO spare_bop_items (
                    bop_id, robot_name, category, name, spec, unit, quantity_per_robot,
                    robot_count, spare_quantity, required_quantity, location_hint, note,
                    created_by, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bop_id,
                    item["robot_name"],
                    item["category"],
                    item["name"],
                    item["spec"],
                    item["unit"],
                    item["quantity_per_robot"],
                    item["robot_count"],
                    item["spare_quantity"],
                    item["required_quantity"],
                    item["location_hint"],
                    item["note"],
                    user["username"],
                    now,
                ),
            )
    return bop_id


def user_can_view_spare_bop(row, user):
    if not user or not row:
        return False
    return is_admin_role(user) or row_value(row, "created_by", "") == user["username"]


def spare_bop_rows(user, bop_id=None, limit=12):
    params = []
    where = []
    if bop_id:
        where.append("bop_id = ?")
        params.append(str(bop_id))
    if not is_admin_role(user):
        where.append("created_by = ?")
        params.append(user["username"])
    sql = "SELECT * FROM spare_bop_lists"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY datetime(updated_at) DESC, id DESC"
    if limit:
        sql += " LIMIT ?"
        params.append(max(1, parse_int(limit, 12)))
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def spare_bop_stock_payload(user, selected_bop_id=None):
    lists = spare_bop_rows(user, selected_bop_id, limit=80 if selected_bop_id else 12)
    bop_ids = [row["bop_id"] for row in lists]
    rows = []
    with db() as conn:
        for bop in lists:
            item_rows = conn.execute(
                """
                SELECT *
                FROM spare_bop_items
                WHERE bop_id = ?
                ORDER BY id ASC
                """,
                (bop["bop_id"],),
            ).fetchall()
            for item in item_rows:
                aliases = spare_bop_item_aliases(item)
                stock = inventory_stock_snapshot(conn, item["category"], item["name"], aliases)
                required = parse_int(item["required_quantity"], 0)
                shortage = max(0, required - stock)
                rows.append(
                    {
                        "bop_id": bop["bop_id"],
                        "bop_title": bop["title"],
                        "season": bop["season"],
                        "robot_scope": bop["robot_scope"],
                        "robot_scope_label": spare_bop_scope_label(bop["robot_scope"]),
                        "created_by": bop["created_by"],
                        "created_at": bop["created_at"],
                        "latest_purchase_order_id": row_value(bop, "latest_purchase_order_id"),
                        "latest_purchase_file": row_value(bop, "latest_purchase_file", ""),
                        "latest_purchase_at": row_value(bop, "latest_purchase_at", ""),
                        "item_id": item["id"],
                        "robot_name": item["robot_name"],
                        "category": item["category"],
                        "name": item["name"],
                        "spec": item["spec"],
                        "unit": item["unit"],
                        "quantity_per_robot": parse_int(item["quantity_per_robot"], 0),
                        "robot_count": parse_int(item["robot_count"], 0),
                        "spare_quantity": parse_int(item["spare_quantity"], 0),
                        "required_quantity": required,
                        "stock_quantity": stock,
                        "shortage_quantity": shortage,
                        "status": "shortage" if shortage > 0 else "stock_available",
                        "location_hint": item["location_hint"],
                        "note": item["note"],
                    }
                )
    summary = {
        "bop_count": len(bop_ids),
        "item_count": len(rows),
        "required_quantity": sum(row["required_quantity"] for row in rows),
        "stock_quantity": sum(row["stock_quantity"] for row in rows),
        "shortage_count": sum(1 for row in rows if row["shortage_quantity"] > 0),
        "shortage_quantity": sum(row["shortage_quantity"] for row in rows),
    }
    return {"lists": lists, "rows": rows, "summary": summary}


def create_spare_bop_purchase_order(user, bop_id):
    payload = spare_bop_stock_payload(user, selected_bop_id=bop_id)
    if not payload["lists"]:
        raise LookupError("BOP 清单不存在或无权访问。")
    shortages = [row for row in payload["rows"] if row["shortage_quantity"] > 0]
    if not shortages:
        raise ValueError("当前 BOP 备件库存充足，不需要生成采购单。")
    created_dt = datetime.now()
    created_at = created_dt.strftime("%Y-%m-%d %H:%M:%S")
    purchase_file = f"spare_bop_purchase_{created_dt.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.xlsx"
    purchase_rows = []
    for row in shortages:
        label = row["name"]
        if row.get("spec") and row["spec"] not in label:
            label = f"{label} | {row['spec']}"
        reason = f"比赛备件 BOP 缺口：{row['bop_title']} / {row['robot_name']}"
        purchase_rows.append(
            [
                row["category"],
                label,
                row["required_quantity"],
                row["stock_quantity"],
                row["shortage_quantity"],
                reason,
            ]
        )
    write_xlsx(
        PURCHASES_DIR / purchase_file,
        "SpareBOP",
        ["商品类别", "商品名称", "需求数量", "库存数量", "采购数量", "原因"],
        purchase_rows,
    )
    with db() as conn:
        order_id = persist_purchase_order(conn, 0, purchase_file, purchase_rows, user, created_at)
        conn.execute(
            """
            UPDATE spare_bop_lists
            SET latest_purchase_order_id = ?, latest_purchase_file = ?, latest_purchase_at = ?, updated_at = ?
            WHERE bop_id = ?
            """,
            (order_id, purchase_file, created_at, created_at, bop_id),
        )
    return {
        "order_id": order_id,
        "purchase_file": purchase_file,
        "purchase_rows": purchase_rows,
        "shortage_count": len(shortages),
        "shortage_quantity": sum(row["shortage_quantity"] for row in shortages),
    }


COMPETITION_MATERIAL_CATEGORIES = (
    "机械结构",
    "铝管/型材",
    "碳板/板材",
    "紧固件",
    "电控模块",
    "硬件器件",
    "线材/接插件",
    "传感器",
    "电机/执行器",
    "工具/耗材",
    "贵重物品",
    "其他",
)

COMPETITION_HARDWARE_CATEGORIES = {
    "硬件器件",
    "电控模块",
    "线材/接插件",
    "传感器",
    "电机/执行器",
}


def competition_material_should_compare(category, name="", spec=""):
    text = f"{category} {name} {spec}".lower()
    if category in COMPETITION_HARDWARE_CATEGORIES:
        return True
    return any(
        token in text
        for token in (
            "模块",
            "降压",
            "buck",
            "电阻",
            "电容",
            "芯片",
            "传感器",
            "连接器",
            "端子",
            "线",
            "xt",
            "gh",
            "xh",
            "can",
            "电机",
            "舵机",
        )
    )


def competition_material_aliases(row):
    values = [
        row_value(row, "name", ""),
        row_value(row, "spec", ""),
        row_value(row, "note", ""),
    ]
    values.extend(extract_lcsc_codes(values))
    aliases = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in aliases:
            aliases.append(text)
    return aliases[:20]


def normalize_competition_material_form(form, user):
    category = (form.get("category", [""])[0] or "").strip()[:80] or "其他"
    name = (form.get("name", [""])[0] or "").strip()[:180]
    spec = (form.get("spec", [""])[0] or "").strip()[:180]
    if not name:
        raise ValueError("请填写物资名称。")
    quantity = max(1, parse_int(form.get("quantity", ["1"])[0], 1))
    unit = (form.get("unit", ["个"])[0] or "个").strip()[:20]
    owner_name = (form.get("owner_name", [""])[0] or "").strip()[:80]
    owner_group = (form.get("owner_group", [""])[0] or "").strip()[:80]
    if not owner_name:
        raise ValueError("比赛物资必须填写责任人姓名。")
    if not owner_group:
        owner_group = str(user["team_group"] if "team_group" in user.keys() else "").strip() or "未分组"
    return {
        "source_type": "manual",
        "bom_id": "",
        "robot_name": (form.get("robot_name", [""])[0] or "").strip()[:80],
        "category": category,
        "name": name,
        "spec": spec,
        "quantity": quantity,
        "unit": unit,
        "owner_name": owner_name,
        "owner_group": owner_group,
        "responsible_by": (form.get("responsible_by", [""])[0] or "").strip()[:80],
        "note": (form.get("note", [""])[0] or "").strip()[:500],
        "hardware_compare": 1 if truthy_value(form.get("hardware_compare", [""])[0]) or competition_material_should_compare(category, name, spec) else 0,
    }


def insert_competition_material_item(conn, item, user, created_at=None):
    created_at = created_at or now_text()
    conn.execute(
        """
        INSERT INTO competition_material_items (
            source_type, bom_id, robot_name, category, name, spec, quantity, unit,
            owner_name, owner_group, responsible_by, note, hardware_compare,
            created_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item.get("source_type") or "manual",
            item.get("bom_id") or "",
            item.get("robot_name") or "",
            item.get("category") or "其他",
            item.get("name") or "",
            item.get("spec") or "",
            max(1, parse_int(item.get("quantity"), 1)),
            item.get("unit") or "个",
            item.get("owner_name") or "",
            item.get("owner_group") or "",
            item.get("responsible_by") or "",
            item.get("note") or "",
            1 if item.get("hardware_compare") else 0,
            user["username"],
            created_at,
        ),
    )


def create_competition_material_manual_item(user, form):
    item = normalize_competition_material_form(form, user)
    created_at = now_text()
    with db() as conn:
        insert_competition_material_item(conn, item, user, created_at)
    return item


def create_competition_material_bom(user, title, robot_name, season, owner_name, owner_group, uploaded):
    if not uploaded or not uploaded.get("filename"):
        raise ValueError("请选择机器人必备模块 BOM 文件。")
    filename = safe_name(uploaded["filename"])
    suffix = Path(filename).suffix.lower()
    if suffix not in (".csv", ".xlsx", ".txt"):
        raise ValueError("比赛物资 BOM 仅支持 csv、xlsx、txt 文件。")
    if len(uploaded.get("content") or b"") > BOM_UPLOAD_MAX_BYTES:
        raise ValueError("BOM 文件超过 12MB，请拆分后上传。")
    title = str(title or "").strip()[:120] or Path(filename).stem[:120] or "比赛物资 BOM"
    robot_name = str(robot_name or "").strip()[:80] or "未指定机器人"
    season = str(season or "").strip()[:80]
    stored = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}_{filename}"
    target = COMPETITION_MATERIALS_DIR / stored
    write_data_bytes(target, uploaded["content"])
    detail = parse_bom_file_detail(target)
    if not detail["items"]:
        raise ValueError("未读取到有效比赛物资 BOM 行。")
    now = now_text()
    bom_id = f"cmbom_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(5)}"
    owner_name = str(owner_name or "").strip()[:80] or str(user["username"])
    user_group = str(user["team_group"] if "team_group" in user.keys() else "").strip()
    owner_group = str(owner_group or "").strip()[:80] or user_group or "未分组"
    with db() as conn:
        conn.execute(
            """
            INSERT INTO competition_material_boms (
                bom_id, title, robot_name, season, original_name, stored_name,
                created_by, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (bom_id, title, robot_name, season, uploaded["filename"], stored, user["username"], now, now),
        )
        for parsed in detail["items"]:
            name = str(parsed.get("name") or "").strip()
            category = str(parsed.get("category") or "硬件器件").strip() or "硬件器件"
            aliases = parsed.get("aliases") if isinstance(parsed.get("aliases"), list) else []
            spec = " / ".join(str(value).strip() for value in aliases[:3] if str(value).strip())[:180]
            item = {
                "source_type": "bom",
                "bom_id": bom_id,
                "robot_name": robot_name,
                "category": category,
                "name": name,
                "spec": spec,
                "quantity": max(1, parse_int(parsed.get("quantity"), 1)),
                "unit": "个",
                "owner_name": owner_name,
                "owner_group": owner_group,
                "responsible_by": user["username"],
                "note": f"来自比赛物资 BOM：{title}",
                "hardware_compare": 1 if competition_material_should_compare(category, name, spec) else 0,
            }
            insert_competition_material_item(conn, item, user, now)
    return {"bom_id": bom_id, "title": title, "item_count": len(detail["items"])}


def competition_material_rows(query=None, limit=120):
    query = query or {}
    q = clean_inventory_search_value(payload_value(query, "q", "query", "search"), 200)
    params = []
    clauses = []
    if q:
        tokens = [token for token in re.split(r"[\s,，;；、]+", q) if inventory_search_token_is_specific(token)]
        for token in tokens[:8]:
            clause, clause_params = inventory_search_like_clause(
                ["category", "name", "spec", "owner_name", "owner_group", "robot_name", "note"],
                [token],
            )
            if clause:
                clauses.append(clause)
                params.extend(clause_params)
    sql = "SELECT * FROM competition_material_items"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    elif q:
        return []
    sql += " ORDER BY datetime(created_at) DESC, id DESC LIMIT ?"
    params.append(max(1, min(parse_int(limit, INVENTORY_SEARCH_RESULT_LIMIT), 300)))
    with db() as conn:
        return conn.execute(sql, params).fetchall()


def competition_material_boms(limit=12):
    with db() as conn:
        return conn.execute(
            """
            SELECT b.*,
                   COUNT(i.id) AS item_count,
                   COALESCE(SUM(i.quantity), 0) AS total_quantity
            FROM competition_material_boms b
            LEFT JOIN competition_material_items i ON i.bom_id = b.bom_id
            GROUP BY b.id
            ORDER BY datetime(b.created_at) DESC, b.id DESC
            LIMIT ?
            """,
            (max(1, parse_int(limit, 12)),),
        ).fetchall()


def competition_material_summary():
    with db() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS item_count,
                   COALESCE(SUM(quantity), 0) AS total_quantity,
                   COUNT(DISTINCT owner_name) AS owner_count,
                   COUNT(DISTINCT CASE WHEN bom_id != '' THEN bom_id END) AS bom_count
            FROM competition_material_items
            """
        ).fetchone()
    return {
        "item_count": parse_int(row["item_count"], 0) if row else 0,
        "total_quantity": parse_int(row["total_quantity"], 0) if row else 0,
        "owner_count": parse_int(row["owner_count"], 0) if row else 0,
        "bom_count": parse_int(row["bom_count"], 0) if row else 0,
    }


def competition_material_hardware_status(row):
    if not parse_int(row_value(row, "hardware_compare", 0), 0):
        return {"checked": False, "stock": 0, "status": "not_checked", "shortage": 0}
    with db() as conn:
        stock = inventory_stock_snapshot(conn, row["category"], row["name"], competition_material_aliases(row))
    required = max(1, parse_int(row["quantity"], 1))
    shortage = max(0, required - stock)
    return {
        "checked": True,
        "stock": stock,
        "status": "shortage" if shortage > 0 else "stock_available",
        "shortage": shortage,
    }


def competition_material_table_html(rows, include_hardware_compare=True):
    if not rows:
        return '<div class="empty">暂无比赛物资记录</div>'
    body_rows = []
    for row in rows:
        status = competition_material_hardware_status(row) if include_hardware_compare else {"checked": False}
        if status.get("checked"):
            compare_html = (
                f'<span class="spare-bop-status {"shortage" if status["shortage"] else "ok"}">'
                f'原仓库 {status["stock"]} / 缺口 {status["shortage"]}</span>'
            )
        else:
            compare_html = '<span class="muted">不对照</span>'
        source = "机器人 BOM" if row["source_type"] == "bom" else "物资表单"
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(row['created_at'])}</td>"
            f"<td>{html.escape(source)}</td>"
            f"<td>{html.escape(row['robot_name'] or '-')}</td>"
            f"<td>{html.escape(row['category'])}</td>"
            f"<td><strong>{html.escape(row['name'])}</strong><small>{html.escape(row['spec'] or '-')}</small></td>"
            f"<td>{parse_int(row['quantity'], 0)} {html.escape(row['unit'] or '个')}</td>"
            f"<td>{html.escape(row['owner_name'])}</td>"
            f"<td>{html.escape(row['owner_group'])}</td>"
            f"<td>{compare_html}</td>"
            f"<td>{html.escape(row['note'] or '-')}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table class="spare-bop-table"><thead><tr>'
        '<th>时间</th><th>来源</th><th>机器人</th><th>类别</th><th>物资/规格</th>'
        '<th>数量</th><th>位置/责任人</th><th>组别</th><th>原仓库对照</th><th>备注</th>'
        '</tr></thead><tbody>'
        + "".join(body_rows)
        + "</tbody></table></div>"
    )


def competition_material_search_table_html(rows):
    if not rows:
        return '<div class="empty">暂无比赛备件仓库匹配记录</div>'
    body_rows = []
    for row in rows:
        source = "机器人 BOM" if row["source_type"] == "bom" else "物资表单"
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(row['created_at'])}</td>"
            f"<td>{html.escape(row['category'])}</td>"
            f"<td><div class=\"item-title\">{html.escape(row['name'])}</div><div class=\"item-sub\">{html.escape(row['spec'] or '-')}</div></td>"
            f"<td>{parse_int(row['quantity'], 0)} {html.escape(row['unit'] or '个')}</td>"
            f"<td>{html.escape(row['owner_name'])}</td>"
            f"<td>{html.escape(row['owner_group'])}</td>"
            f"<td>{html.escape(row['robot_name'] or '-')}</td>"
            f"<td>{html.escape(source)}</td>"
            f"<td>{html.escape(row['note'] or '-')}</td>"
            "</tr>"
        )
    return (
        '<div class="table-wrap"><table><thead><tr><th>时间</th><th>类别</th><th>名称/规格</th>'
        '<th>数量</th><th>位置/责任人</th><th>组别</th><th>机器人</th><th>来源</th><th>备注</th></tr></thead><tbody>'
        + "".join(body_rows)
        + "</tbody></table></div>"
    )


def bom_component_match_tokens(item):
    values = []
    if isinstance(item, dict):
        values.extend(
            [
                item.get("normalized_name"),
                item.get("name"),
                item.get("part_name"),
                item.get("value"),
                item.get("package"),
                item.get("lcsc_code"),
                item.get("supplier_code"),
                item.get("manufacturer_part_number"),
            ]
        )
        values.extend(item.get("designators") if isinstance(item.get("designators"), list) else split_designators(item.get("designator")))
    tokens = []
    for value in values:
        token = normalize_key(value)
        if token and token not in tokens:
            tokens.append(token)
    return tokens


def safe_json_script_payload(payload):
    return json.dumps(payload if payload is not None else {}, ensure_ascii=False).replace("<", "\\u003c")


def fallback_interactive_bom_payload(record):
    decorated = decorate_bom_record(record)
    component_rows = decorated.get("component_rows") if isinstance(decorated.get("component_rows"), list) else []
    if not component_rows:
        component_rows = decorated.get("aggregated_items") if isinstance(decorated.get("aggregated_items"), list) else []
    components = []
    for idx, item in enumerate(component_rows, start=1):
        if not isinstance(item, dict):
            continue
        refs = item.get("designators") if isinstance(item.get("designators"), list) else split_designators(item.get("designator"))
        components.append(
            {
                "id": f"fallback-{idx}",
                "row_id": f"bomrow-{idx}",
                "index": idx,
                "refs": refs,
                "ref": refs[0] if refs else "",
                "name": item.get("part_name") or item.get("normalized_name") or item.get("name") or item.get("value") or "",
                "value": item.get("value") or "",
                "footprint": item.get("package") or item.get("footprint") or "",
                "quantity": parse_int(item.get("quantity"), 0),
                "lcsc_code": item.get("lcsc_code") or item.get("supplier_code") or "",
                "side": "top",
                "bbox": {
                    "x": ((idx * 37) % 92) + 4,
                    "y": ((idx * 53) % 84) + 8,
                    "width": 7,
                    "height": 5,
                    "source": "fallback",
                },
                "row_index": idx,
                "tokens": bom_component_match_tokens(item),
            }
        )
    return {
        "version": 1,
        "source": "app-fallback",
        "bom_id": str(decorated.get("bom_id") or ""),
        "original_filename": decorated.get("original_filename") or "",
        "components": components,
        "pcb_files": decorated.get("pcb_files") if isinstance(decorated.get("pcb_files"), list) else [],
        "latest_pcb_file": decorated.get("latest_pcb_file") or {},
    }


def interactive_bom_payload_for_record(record):
    if callable(_build_interactive_bom_payload):
        try:
            payload = _build_interactive_bom_payload(record)
            if isinstance(payload, dict):
                return payload
        except Exception as exc:
            bom_id = ""
            if isinstance(record, dict):
                bom_id = record.get("bom_id") or ""
            else:
                try:
                    bom_id = record["bom_id"] or ""
                except Exception:
                    pass
            print(f"[interactive bom] failed to build payload for {bom_id}: {exc}")
    return fallback_interactive_bom_payload(record)


def render_soldering_workbench(user, record, pcb_file=None):
    decorated = decorate_bom_record(record)
    bom_id = str(decorated.get("bom_id") or "")
    selected_pcb = pcb_file or decorated.get("latest_pcb_file") or {}
    selected_pcb_id = str(selected_pcb.get("pcb_id") or selected_pcb.get("pcb_file_id") or selected_pcb.get("id") or "")
    interactive_payload = interactive_bom_payload_for_record(record)
    interactive_payload_json = safe_json_script_payload(interactive_payload)
    pcb_options = []
    for item in decorated.get("pcb_files", []):
        pid = str(item.get("pcb_id") or "")
        status_text = item.get("status_text") or pcb_file_compact_status(item)
        label = item.get("original_filename") or pid
        option_label = f"{label} ({status_text})" if status_text else label
        pcb_options.append(
            f'<option value="{html.escape(pid)}" {"selected" if pid == selected_pcb_id else ""}>'
            f'{html.escape(option_label)}</option>'
        )
    active_job = decorated.get("active_soldering_job") or {}
    completed_consumption_html = ""
    if not active_job:
        completed_job_id = decorated.get("last_completed_soldering_job_id") or decorated.get("latest_soldering_job_id")
        completed_job, completed_consumption = soldering_consumption_for_job_id(completed_job_id, user)
        if completed_job and completed_job.get("status") == SOLDERING_COMPLETED_STATUS:
            completed_consumption_html = soldering_consumption_summary_html(completed_consumption)
    preview_html = ""
    if selected_pcb and selected_pcb.get("view_url"):
        preview_url = pcb_file_preview_url(bom_id, selected_pcb_id, user) if selected_pcb_id else selected_pcb["view_url"]
        preview_html = (
            '<div class="pcb-preview-actions">'
            '<div><strong>已关联 PCB 预览</strong>'
            '<small>在新标签页打开生成的 HTML / PDF / Gerber 预览文件。</small></div>'
            f'<a class="button" href="{html.escape(preview_url, quote=True)}" target="_blank" rel="noopener">打开预览</a>'
            "</div>"
        )
    else:
        preview_html = '<div class="empty">还没有关联 PCB / HTML / Gerber 文件。</div>'
    selected_label = selected_pcb.get("original_filename") or "未选择文件"
    selected_status_text = selected_pcb.get("status_text") or pcb_file_compact_status(selected_pcb)
    selected_analysis_action = ""
    selected_analysis_is_pdf = bool(selected_pcb_id and pcb_file_is_pdf_schematic(selected_pcb))
    selected_analysis_status_url = ""
    selected_analysis_job_id = ""
    selected_analysis_status = ""
    selected_analysis_active = False
    selected_analysis_detail_url = ""
    if selected_analysis_is_pdf:
        selected_analysis_status = str(selected_pcb.get("analysis_status") or "pending").strip().lower()
        selected_analysis_job_id = str(selected_pcb.get("analysis_job_id") or selected_pcb.get("latest_analysis_job_id") or "").strip()
        latest_job = find_latest_analysis_job_for_file(bom_id, selected_pcb_id)
        if latest_job:
            selected_analysis_job_id = str(latest_job.get("job_id") or selected_analysis_job_id).strip()
            selected_analysis_status = str(latest_job.get("status") or selected_analysis_status).strip().lower()
        selected_analysis_active = selected_analysis_status in ANALYSIS_JOB_ACTIVE_STATUSES
        selected_analysis_detail_url = analysis_job_detail_url(selected_analysis_job_id)
        disabled = " disabled" if selected_analysis_active else ""
        if selected_analysis_status == "queued":
            button_label = "已排队"
        elif selected_analysis_status == "running":
            button_label = "分析中"
        elif selected_analysis_status == "completed":
            button_label = "再次分析"
        else:
            button_label = "提交分析"
        selected_analysis_status_url = (
            f"/api/boms/{urllib.parse.quote(str(bom_id))}"
            f"/pcb/{urllib.parse.quote(str(selected_pcb_id))}/analysis"
        )
        selected_analysis_action = (
            f'<form class="pcb-analysis-form" method="post" action="{html.escape(selected_analysis_status_url)}">'
            f'<button class="button pcb-analysis-button" type="submit"{disabled}>{html.escape(button_label)}</button>'
            "</form>"
        )
        if selected_analysis_job_id:
            selected_analysis_action += (
                f'<a class="button pcb-analysis-button pcb-analysis-detail-link" '
                f'data-analysis-detail-link href="{html.escape(selected_analysis_detail_url, quote=True)}">查看分析</a>'
            )
        selected_analysis_action += (
            '<a class="button pcb-analysis-button pcb-analysis-detail-link" '
            'href="/analysis-jobs">分析历史</a>'
        )
    selected_meta_html = (
        f'<div class="pcb-selected-meta"><strong>{html.escape(str(selected_label))}</strong>'
        f'<span data-analysis-status>{html.escape(selected_status_text or "暂无附件元数据")}</span>'
        f'{selected_analysis_action}</div>'
    )
    pcb_upload_html = bom_pcb_upload_form_html(decorated)
    analysis_history_panel = analysis_workbench_history_panel_html(user, decorated, selected_pcb_id)
    analysis_root_attrs = ""
    if selected_analysis_is_pdf:
        analysis_root_attrs = (
            f' data-selected-pcb-id="{html.escape(selected_pcb_id, quote=True)}"'
            f' data-analysis-status-url="{html.escape(selected_analysis_status_url, quote=True)}"'
            f' data-analysis-job-id="{html.escape(selected_analysis_job_id, quote=True)}"'
            f' data-analysis-status="{html.escape(selected_analysis_status, quote=True)}"'
            f' data-analysis-active="{"true" if selected_analysis_active else "false"}"'
            f' data-analysis-detail-url="{html.escape(selected_analysis_detail_url, quote=True)}"'
        )
    body = f"""
    <header class="page-head">
      <div><p class="eyebrow">焊接工作台</p><h1>{html.escape(decorated.get("original_filename") or "BOM 焊接工作台")}</h1></div>
      <a class="button" href="/bom">返回 BOM</a>
    </header>
    <section class="soldering-workbench" data-bom-id="{html.escape(bom_id)}" data-active-job-id="{html.escape(str(active_job.get("job_id") or ""))}"{analysis_root_attrs}>
      <section class="panel soldering-controls">
        <div class="panel-head"><h2>焊接状态</h2><span id="soldering-status">{html.escape(active_job.get("status") or decorated.get("status") or "awaiting_start")}</span></div>
        <label>PCB 文件<select id="pcb-file-id">{''.join(pcb_options) or '<option value="">无 PCB 文件</option>'}</select></label>
        <div class="soldering-pcb-upload">
          <strong>上传 PCB / .epro</strong>
          {pcb_upload_html}
        </div>
        <label>本次制板数量<input id="board-count" type="number" min="1" value="{html.escape(str(active_job.get("board_count") or decorated.get("soldering_board_count") or 1))}"></label>
        <label class="wide">备注<input id="soldering-notes" value="{html.escape(str(active_job.get("notes") or ""))}"></label>
        <div class="wide soldering-actions">
          <button class="primary" type="button" id="start-soldering">开始/继续焊接</button>
          <button class="button" type="button" id="finish-soldering" {"disabled" if not active_job else ""}>结束并扣减库存</button>
        </div>
        <div id="soldering-feedback" class="lcsc-preview" data-soldering-status>进行中的焊接任务会保存在后端；重新登录后会继续显示。</div>
        <div id="soldering-consumption-summary">{completed_consumption_html}</div>
      </section>
      <section class="panel interactive-bom-panel pcb-visual-panel" data-interactive-bom-api="/api/boms/{urllib.parse.quote(str(bom_id))}/interactive-bom">
        <div class="panel-head"><h2>PCB-BOM 联动视图</h2><span>{html.escape(selected_pcb.get("original_filename") or "使用 BOM 坐标")}</span></div>
        {selected_meta_html}
        <div class="pcb-visual-stage">
          {preview_html}
        </div>
        <div class="interactive-bom-viewer" data-bom-id="{html.escape(bom_id, quote=True)}" data-selected-pcb-id="{html.escape(selected_pcb_id, quote=True)}">
          <script type="application/json" data-interactive-bom-payload>{interactive_payload_json}</script>
        </div>
      </section>
      {analysis_history_panel}
    </section>
    <script src="/static/soldering_workbench.js"></script>
    <script src="/static/interactive_bom.js"></script>
    """
    return render_layout("BOM 焊接工作台", body, user, "BOM对照")


def process_bom_upload(path, original_name, user, conn=None):
    detail = parse_bom_file_detail(path)
    items = detail["items"]
    lcsc_codes = extract_lcsc_codes(
        [item["name"] for item in items] + [alias for item in items for alias in item.get("aliases", [])]
    )
    # Keep BOM upload responsive: do not fetch LCSC over the network here.
    # Admins can refresh/populate the cache from the reports page when needed.
    lcsc_cache = load_lcsc_cache()
    for item in items:
        item_codes = extract_lcsc_codes([item["name"]] + item.get("aliases", []))
        item["aliases"] = list(dict.fromkeys(item.get("aliases", []) + lcsc_aliases_for_codes(item_codes, lcsc_cache)))
    created_dt = datetime.now()
    created_at = created_dt.strftime("%Y-%m-%d %H:%M:%S")
    created_at_iso = created_dt.isoformat(timespec="seconds")
    total_qty = sum(item["quantity"] for item in items)
    with sqlite_connection_context(conn) as conn:
        cur = conn.execute(
            """
            INSERT INTO bom_uploads (original_name, stored_name, uploaded_by, total_items, total_quantity, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (original_name, path.name, user["username"], len(items), total_qty, created_at),
        )
        upload_id = cur.lastrowid
        purchase_rows = []
        for item in items:
            conn.execute(
                """
                INSERT INTO bom_items (upload_id, category, name, quantity, uploaded_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (upload_id, item["category"], item["name"], item["quantity"], user["username"], created_at),
            )
            stock = stock_for_item(
                conn,
                item["category"],
                item["name"],
                item.get("aliases", []),
                package=item.get("package"),
            )
            if stock <= item["quantity"]:
                shortage = item["quantity"] - stock
                purchase_qty = shortage if shortage > 0 else item["quantity"]
                reason = "库存不足" if shortage > 0 else "库存等于需求，建议补货"
                if stock == 0:
                    reason = "仓库无此器件"
                purchase_rows.append([item["category"], item["name"], item["quantity"], stock, purchase_qty, reason])
        purchase_file = ""
        if purchase_rows:
            purchase_file = f"purchase_{created_dt.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}.xlsx"
            write_xlsx(
                PURCHASES_DIR / purchase_file,
                "Purchase",
                ["商品类别", "商品名称", "需求数量", "库存数量", "采购数量", "原因"],
                purchase_rows,
            )
            persist_purchase_order(conn, upload_id, purchase_file, purchase_rows, user, created_at)
            conn.execute("UPDATE bom_uploads SET purchase_file = ? WHERE id = ?", (purchase_file, upload_id))
    bom_record = build_bom_record(
        path,
        original_name,
        user,
        detail,
        upload_id,
        purchase_file,
        purchase_rows,
        created_at_iso,
        lcsc_codes,
        [],
    )
    try:
        append_bom_record(bom_record)
    except Exception as exc:
        print(f"[bom archive] failed to save {bom_record['bom_id']}: {exc}")
    return {
        "items": items,
        "purchase_rows": purchase_rows,
        "purchase_file": purchase_file,
        "upload_id": upload_id,
        "bom_id": bom_record["bom_id"],
        "lcsc_codes": lcsc_codes,
        "lcsc_fetched": [],
    }


def render_script_tags(scripts):
    if not scripts:
        return ""
    if isinstance(scripts, str):
        scripts = [scripts]
    tags = []
    for script in scripts:
        if isinstance(script, dict):
            src = script.get("src", "")
            script_type = script.get("type", "")
        else:
            src = str(script or "")
            script_type = ""
        if not src:
            continue
        type_attr = f' type="{html.escape(script_type, quote=True)}"' if script_type else ""
        tags.append(f'<script{type_attr} src="{html.escape(src, quote=True)}"></script>')
    return "\n  ".join(tags)


def changelog_sections():
    try:
        raw = CHANGELOG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    sections = []
    current = None
    for line in raw.splitlines():
        if line.startswith("## "):
            if current:
                sections.append(current)
            current = {"title": line[3:].strip(), "items": []}
            continue
        if current and line.lstrip().startswith("- "):
            current["items"].append(line.lstrip()[2:].strip())
    if current:
        sections.append(current)
    return sections


def changelog_section_meta(title):
    text = str(title or "").strip()
    match = re.match(r"^(\d{4}-\d{2}-\d{2})(?:\s+(.+))?$", text)
    if not match:
        return "", text or "系统公告"
    return match.group(1), (match.group(2) or "系统公告").strip()


def changelog_relative_time(date_text):
    try:
        date_value = datetime.strptime(str(date_text), "%Y-%m-%d").date()
    except Exception:
        return str(date_text or "最近更新")
    days = (datetime.now().date() - date_value).days
    if days <= 0:
        return "今天"
    if days == 1:
        return "1天前"
    return f"{days}天前"


def changelog_version(sections=None):
    payload = json.dumps(sections if sections is not None else changelog_sections(), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def changelog_timeline_entries(sections=None, limit=12):
    entries = []
    for section in sections if sections is not None else changelog_sections():
        date_text, group_title = changelog_section_meta(section.get("title"))
        items = section.get("items") or [group_title]
        for item in items:
            text = str(item or "").strip()
            if not text:
                continue
            entries.append(
                {
                    "title": text,
                    "group": group_title,
                    "date": date_text,
                    "time": changelog_relative_time(date_text),
                }
            )
    return entries[:limit] if limit else entries


def latest_changelog_entry():
    sections = changelog_sections()
    if not sections:
        return {
            "title": "暂无更新公告",
            "items": ["当前没有可展示的更新日志。"],
            "version": "empty",
        }
    latest = sections[0]
    latest["version"] = changelog_version(sections)
    return latest


def changelog_items_html(items):
    clean_items = [str(item).strip() for item in (items or []) if str(item).strip()]
    if not clean_items:
        return '<li>暂无详细更新说明。</li>'
    return "".join(f"<li>{html.escape(item)}</li>" for item in clean_items)


def changelog_page(user):
    sections = changelog_sections()
    entries = "".join(
        f"""
        <article class="changelog-entry">
          <h2>{html.escape(section["title"])}</h2>
          <ul>{changelog_items_html(section.get("items"))}</ul>
        </article>
        """
        for section in sections
    ) or '<div class="empty">暂无更新日志。</div>'
    body = f"""
    <header class="page-head"><div><p class="eyebrow">Release Notes</p><h1>更新日志</h1></div><a class="button" href="/inventory">返回仓库检索</a></header>
    <section class="panel changelog-panel">
      {entries}
    </section>
    """
    return render_layout("更新日志", body, user, "仓库检索")


def inventory_changelog_notice_html():
    sections = changelog_sections()
    version = html.escape(changelog_version(sections), quote=True)
    timeline = changelog_timeline_entries(sections, limit=10)
    timeline_html = "".join(
        f"""
        <li class="inventory-release-event {'is-left' if index % 2 else 'is-right'}">
          <span class="inventory-release-dot"></span>
          <article class="inventory-release-event-card">
            <strong>{html.escape(item["title"])}</strong>
            <time>{html.escape(item["time"])}</time>
          </article>
        </li>
        """
        for index, item in enumerate(timeline)
    ) or """
        <li class="inventory-release-event is-right">
          <span class="inventory-release-dot"></span>
          <article class="inventory-release-event-card"><strong>暂无系统公告</strong><time>今天</time></article>
        </li>
    """
    return f"""
    <div class="inventory-release-overlay" data-inventory-release-notice data-version="{version}" role="dialog" aria-modal="true" aria-labelledby="inventory-release-title">
      <div class="inventory-release-card inventory-release-system-card">
        <header class="inventory-release-header">
          <h2 id="inventory-release-title">系统公告</h2>
          <div class="inventory-release-tabs" aria-label="公告类型">
            <span class="inventory-release-tab">♧ 通知</span>
            <span class="inventory-release-tab is-active">▱ 系统公告</span>
            <button class="inventory-release-close" type="button" data-inventory-notice-close data-close-mode="today" aria-label="关闭更新公告">×</button>
          </div>
        </header>
        <div class="inventory-release-timeline-wrap">
          <ol class="inventory-release-timeline">{timeline_html}</ol>
        </div>
        <div class="inventory-release-actions">
          <button class="button" type="button" data-inventory-notice-close data-close-mode="today">今日关闭</button>
          <button class="primary" type="button" data-inventory-notice-close data-close-mode="version">关闭公告</button>
        </div>
      </div>
    </div>
    """


def render_layout(title, body, user=None, active="", scripts=None):
    config = get_config()
    nav = ""
    global_notice = ""
    if user:
        items = [
            ("/dashboard", "概览"),
            ("/inventory", "仓库检索"),
            ("/inventory/new", "填写表单"),
            ("/bom", "BOM对照"),
            ("/soldering/workbench", "焊接工作台"),
            ("/spares", "比赛备件"),
            ("/analytics", "统计排行"),
            ("/assistant", "智能助手"),
            ("/reports", "仓检报表"),
        ]
        if user["role"] == "admin":
            items.extend([("/users", "用户"), ("/admin", "后台配置")])
        links = "".join(
            f'<a class="nav-item {"active" if active == label else ""}" href="{href}">{label}</a>'
            for href, label in items
        )
        nav = f"""
        <header class="topbar">
          <div class="brand"><span class="brand-mark"></span><div><strong>仓管系统</strong><small>{html.escape(config["site_name"])}</small></div></div>
          <nav class="top-nav">{links}</nav>
          <div class="account"><span>{html.escape(user["username"])}</span><a href="/logout">退出</a></div>
        </header>
        """
        global_notice = inventory_changelog_notice_html()
    page_class = "app-shell top-shell" if user else "login-shell"
    page_scripts = render_script_tags([
        "/static/security.js",
        "/static/nav_slime.js",
        "/static/pcb_attach.js",
        "/static/notice.js",
        *(scripts or []),
    ])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="csrf-token" content="{{csrf_token}}">
  <title>{html.escape(title)} - Warehouse</title>
  <link rel="stylesheet" href="/static/app.css">
  {ui_theme_style(config)}
</head>
<body>
  <div class="{page_class}">
    {nav}
    <main class="main">{body}</main>
  </div>
  {global_notice}
  {page_scripts}
</body>
</html>"""


def render_public_layout(title, body, scripts=None):
    config = get_config()
    page_scripts = render_script_tags(["/static/security.js", *(scripts or [])])
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="csrf-token" content="{{csrf_token}}">
  <title>{html.escape(title)} - 沧溟战队仓管系统</title>
  <link rel="stylesheet" href="/static/app.css">
  {ui_theme_style(config)}
</head>
<body>
  <div class="public-shell">
    <div class="public-aura-video" aria-hidden="true">
      <video autoplay loop muted playsinline preload="auto" poster="/static/liquid-glass-bg.png">
        <source src="/static/home-bg-lite.webm" type="video/webm">
      </video>
    </div>
    {body}
  </div>
  {page_scripts}
</body>
</html>"""


def render_pcb_keyframe_page():
    config = get_config()
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="csrf-token" content="{{csrf_token}}">
  <title>PCB 3D Keyframe Lab - {html.escape(config.get("site_name", "Warehouse Inventory Server"))}</title>
  <link rel="stylesheet" href="/static/app.css">
  {ui_theme_style(config)}
</head>
<body class="pcb-keyframe-lab-page">
  <main class="pcb-keyframe-lab" data-model="/static/models/pcb_exploded_mesh.json">
    <section class="pcb-lab-stage">
      <div class="pcb-lab-viewport" id="pcb-lab-viewport">
        <div class="pcb-lab-loader">Loading PCB 3D model...</div>
      </div>
      <div class="pcb-lab-overlay" id="pcb-lab-overlay">
        <div class="pcb-lab-text-marker" id="pcb-lab-text-marker">
          <small id="pcb-lab-marker-kicker">01 / Close-up</small>
          <strong id="pcb-lab-marker-title">Power Input Protection</strong>
          <span id="pcb-lab-marker-body">Drag this label or edit it in the panel.</span>
        </div>
      </div>
      <div class="pcb-lab-topbar">
        <a href="/" class="pcb-lab-back">Back</a>
        <div>
          <p>PCB Keyframe Lab</p>
          <strong>拖动模型和文字，记录四个特写镜头</strong>
        </div>
      </div>
      <div class="pcb-lab-help">
        <span>左键旋转</span>
        <span>右键/Shift+拖动平移</span>
        <span>滚轮缩放</span>
        <span>拖动文字设置位置</span>
      </div>
    </section>
    <aside class="pcb-lab-panel">
      <div class="pcb-lab-panel-head">
        <p class="eyebrow">Shot Recorder</p>
        <h1>四个特写关键帧</h1>
        <p>保存后会写入 <code>data/pcb_keyframes.json</code>，后续正式页面可以直接读取这些参数做衔接。</p>
      </div>
      <div class="pcb-lab-shot-tabs" id="pcb-lab-shot-tabs"></div>
      <div class="pcb-lab-field-grid">
        <label>镜头名称<input id="pcb-lab-title" maxlength="80"></label>
        <label>副标题<input id="pcb-lab-kicker" maxlength="80"></label>
        <label class="pcb-lab-wide">说明文案<textarea id="pcb-lab-body" rows="4"></textarea></label>
        <label>文字运动
          <select id="pcb-lab-motion">
            <option value="slide-rise">滑入上扬</option>
            <option value="side-sweep">侧向扫入</option>
            <option value="magnetic-pull">吸附靠近</option>
            <option value="spring-settle">弹性落位</option>
          </select>
        </label>
        <label>文字侧
          <select id="pcb-lab-side">
            <option value="left">左侧</option>
            <option value="right">右侧</option>
          </select>
        </label>
        <label>文字 X<input id="pcb-lab-text-x" type="number" min="0" max="100" step="0.1"></label>
        <label>文字 Y<input id="pcb-lab-text-y" type="number" min="0" max="100" step="0.1"></label>
      </div>
      <div class="pcb-lab-slider-stack">
        <label>爆炸层高 <output id="pcb-lab-explode-out">0</output><input id="pcb-lab-explode" type="range" min="0" max="1" step="0.01"></label>
        <label>模型缩放 <output id="pcb-lab-scale-out">1.00</output><input id="pcb-lab-scale" type="range" min="0.12" max="2.8" step="0.01"></label>
        <label>X 旋转 <output id="pcb-lab-rx-out">0</output><input id="pcb-lab-rx" type="range" min="-180" max="180" step="0.1"></label>
        <label>Y 旋转 <output id="pcb-lab-ry-out">0</output><input id="pcb-lab-ry" type="range" min="-360" max="360" step="0.1"></label>
        <label>Z 旋转 <output id="pcb-lab-rz-out">0</output><input id="pcb-lab-rz" type="range" min="-180" max="180" step="0.1"></label>
        <label>X 平移 <output id="pcb-lab-x-out">0</output><input id="pcb-lab-x" type="range" min="-160" max="160" step="0.1"></label>
        <label>Y 平移 <output id="pcb-lab-y-out">0</output><input id="pcb-lab-y" type="range" min="-160" max="160" step="0.1"></label>
        <label>Z 平移 <output id="pcb-lab-z-out">0</output><input id="pcb-lab-z" type="range" min="-160" max="160" step="0.1"></label>
      </div>
      <div class="pcb-lab-actions">
        <button type="button" class="primary" id="pcb-lab-capture">记录当前镜头</button>
        <button type="button" id="pcb-lab-save">保存到项目</button>
        <button type="button" id="pcb-lab-copy">复制 JSON</button>
        <button type="button" id="pcb-lab-reset">重置视角</button>
      </div>
      <pre class="pcb-lab-json" id="pcb-lab-json"></pre>
      <p class="pcb-lab-status" id="pcb-lab-status">Ready.</p>
    </aside>
  </main>
  <script src="/static/security.js"></script>
  <script type="module" src="/static/pcb_keyframe_lab.js"></script>
</body>
</html>"""


def flash_box(message, kind="info"):
    if not message:
        return ""
    return f'<div class="flash {kind}">{html.escape(message)}</div>'


def landing_page():
    body = """
    <header class="public-topbar">
      <a class="public-brand" href="/"><span class="brand-mark"></span><strong>沧管 v1</strong></a>
      <nav class="public-nav">
        <a href="#ai">国产大模型</a>
        <a href="#lcsc">立创补全</a>
        <a href="#flow">出入库同步</a>
        <a href="#knowledge">硬件知识网络</a>
      </nav>
      <a class="public-login" href="/login">登录</a>
    </header>
    <main class="landing-main">
      <section class="landing-hero landing-poster-hero">
        <div class="landing-copy">
          <p class="eyebrow landing-kicker">Cangming Team Warehouse System</p>
          <h1><span>沧溟战队</span><span>仓管系统 v1</span></h1>
          <p class="landing-lede">1.0.1 公测预告。面向硬件组日常备赛、库存协作、器件识别与工程进度同步的下一代仓库管理系统。</p>
          <div class="landing-actions">
            <a class="primary button" href="/login">进入登录</a>
            <a class="button" href="#preview">查看公测预告</a>
          </div>
        </div>
        <div class="hero-product-stage" aria-label="产品 3D 展示">
          <article class="product-device product-device-main">
            <div class="device-toolbar"><span></span><span></span><span></span></div>
            <div class="device-grid">
              <section><small>Inventory</small><strong>12,840</strong><em>parts</em></section>
              <section><small>BOM</small><strong>96%</strong><em>matched</em></section>
              <section><small>LCSC</small><strong>Online</strong><em>sync</em></section>
              <section><small>AI</small><strong>Graph</strong><em>ready</em></section>
            </div>
          </article>
          <article class="product-chip product-chip-a"><span>C25803</span><strong>0603 / 100k</strong></article>
          <article class="product-chip product-chip-b"><span>BOM-0426</span><strong>BOM 缺口 18</strong></article>
          <article class="product-chip product-chip-c"><span>3D View</span><strong>库存类型分析</strong></article>
        </div>
      </section>
      <section class="pcb-explosion-scroll" id="pcb-explosion-scroll">
        <div class="pcb-explosion-sticky">
          <div class="pcb-explosion-copy">
            <p class="eyebrow">REMOVE-CORE Product Film</p>
            <h2>REMOVE-CORE 控制板</h2>
            <p>先进行垂直分层爆炸展示，再回到整板旋转；随后模块以平滑转场切入近景，配合特写说明重点电路与功能。</p>
            <div class="pcb-keyframe-bar" aria-hidden="true">
              <span>Explode</span><i></i><span>Assemble</span><i></i><span>Modules</span>
            </div>
          </div>
          <div class="pcb-explosion-stage">
            <aside class="pcb-data-callout pcb-data-callout-left">
              <span>503</span>
              <strong>independent meshes</strong>
              <em>器件白模拆分展示</em>
            </aside>
            <div class="pcb-explosion-viewer" id="pcb-explosion-viewer" data-model="/static/models/pcb_exploded_mesh.json">
              <div class="pcb-explosion-loader">Loading STEP mesh...</div>
            </div>
            <aside class="pcb-data-callout pcb-data-callout-right">
              <span>86 x 100</span>
              <strong>mm board span</strong>
              <em>主板停留后器件外扩</em>
            </aside>
            <div class="pcb-module-copy">
              <article class="pcb-module-card is-active" data-module="power">
                <p class="eyebrow">01 / Power Input Protection</p>
                <h3>电源输入与前级保护</h3>
                <p>XT60 主电源输入进入 VRBAT+ 与 CORE+24V 母线，配合 DMP6018LPSQ PMOS、SMBJ26CA TVS、JK30-900 自恢复保险和大容量输入电容，承担反接、浪涌、短路与热插拔冲击抑制。</p>
                <dl><div><dt>Key parts</dt><dd>Q1/Q7 PMOS, D4 SMBJ26CA, F7/F8 JK30-900</dd></div><div><dt>Purpose</dt><dd>让后级降压、执行端子和 CAN 保护模块获得稳定的 24V 输入边界。</dd></div></dl>
              </article>
              <article class="pcb-module-card" data-module="opto">
                <p class="eyebrow">02 / Opto Isolation</p>
                <h3>光耦隔离与开关指示</h3>
                <p>PICK_HEADER / PICK_BLOCK 输入输出链路通过隔离器件与 ISO_IN_GND 分域，LED2/LED3 提供开关状态指示，减少执行端高噪声对逻辑侧的干扰。</p>
                <dl><div><dt>Signals</dt><dd>PICK_HEADER_IN / OUT, PICK_BLOCK_IN / OUT</dd></div><div><dt>Benefit</dt><dd>输入状态可视化，故障定位更直接，隔离地提升抗干扰能力。</dd></div></dl>
              </article>
              <article class="pcb-module-card" data-module="buck">
                <p class="eyebrow">03 / Buck Converter</p>
                <h3>双路降压 BUCK 电源</h3>
                <p>两路 MP4462DN-LF-Z 同步降压从 CORE+24V 生成 +12V 与 +5V，外围包含 SS56 肖特基、15uH/27uH 电感、补偿网络和多颗 22uF/10uF 电容，给接口、隔离和逻辑电路供电。</p>
                <dl><div><dt>Controller</dt><dd>U4/U8/U28 MP4462DN-LF-Z</dd></div><div><dt>Rails</dt><dd>CORE+24V -> +12V / +5V</dd></div></dl>
              </article>
              <article class="pcb-module-card" data-module="can">
                <p class="eyebrow">04 / CAN Bus Protection</p>
                <h3>三路 CAN 总线保护</h3>
                <p>CAN1/CAN2/CAN3 使用 NUP2105LT1G TVS、ACT45B-510 共模电感、串联阻尼电阻与 27pF 滤波电容，对 CAN_H/CAN_L 做 ESD、共模噪声和尖峰抑制，面向底盘与外设总线可靠通信。</p>
                <dl><div><dt>Channels</dt><dd>CAN1_H/L, CAN2_H/L, CAN3_H/L</dd></div><div><dt>Protection</dt><dd>D1/D5/D7 + L3/L5/L6 + R/C filter</dd></div></dl>
              </article>
            </div>
          </div>
        </div>
      </section>
      <section class="product-scroll-showcase" id="product-scroll">
        <div class="product-scroll-sticky">
          <div class="product-scroll-copy">
            <p class="eyebrow">Product Flow</p>
            <h2>沧溟仓管产品流</h2>
          </div>
          <div class="product-birth-stage" aria-hidden="true">
            <div class="birth-core"><span></span><span></span><span></span></div>
            <div class="birth-scan"></div>
          </div>
          <div class="product-scroll-viewport">
            <div class="product-track" id="product-track">
              <article class="scroll-product-card">
                <div class="mini-screen inventory-screen"><span></span><span></span><span></span></div>
                <p>库存工作台</p><strong>快速定位器件、仓位和数量</strong>
              </article>
              <article class="scroll-product-card">
                <div class="mini-screen bom-screen"><i></i><i></i><i></i></div>
                <p>BOM 对照</p><strong>缺料、库存和采购建议同步出现</strong>
              </article>
              <article class="scroll-product-card">
                <div class="mini-screen lcsc-screen"><b>C</b><b>25803</b></div>
                <p>LCSC 补全</p><strong>编号拉取封装、品牌和链接</strong>
              </article>
              <article class="scroll-product-card">
                <div class="mini-screen graph-screen"><span></span><span></span><span></span><span></span></div>
                <p>AI 知识网</p><strong>问答历史沉淀为个人知识节点</strong>
              </article>
            </div>
          </div>
        </div>
      </section>
      <div class="landing-dark-tail">
      <section id="preview" class="promo-scroll-section">
        <div class="promo-sticky">
          <div class="promo-frame">
            <img id="promo-image" src="/static/cangming-v1-promo.png" alt="沧溟战队仓管系统 v1 公测预告">
          </div>
          <p class="scroll-hint">Cangming v1 public preview</p>
        </div>
      </section>
      <section class="feature-ribbon">
        <article id="ai"><span>01</span><h2>国产大模型接入</h2><p>检索器件时同步理解器件用法，围绕库存、BOM 缺口与电子设计进行实时答疑。</p></article>
        <article id="lcsc"><span>02</span><h2>立创在线商城接入</h2><p>输入 LCSC 编号即可补全商品名、分类、封装、品牌、链接，并直接进入库存录入流程。</p></article>
        <article id="flow"><span>03</span><h2>全新出入库功能</h2><p>硬件组员的入库、出库、调拨、盘点和 BOM 对照集中在同一核心界面，工程进度更透明。</p></article>
        <article id="knowledge"><span>04</span><h2>硬件笔记记忆</h2><p>助手历史沉淀为个人知识文档，并生成可交互知识网络，辅助快节奏备赛。</p></article>
      </section>
      <section class="core-console-preview">
        <div>
          <p class="eyebrow">Core Workspace</p>
          <h2>一个核心界面，聚合仓库管理能力</h2>
          <p>核心工作台把库存总览、器件检索、LCSC 补全、BOM 缺料、出入库同步、AI 问答与知识网络串成一个连续流程；每个入口都保留实时状态、待处理动作和最近结果，方便从一个界面判断下一步该做什么。</p>
        </div>
        <div class="console-grid">
          <span>库存概览</span><span>LCSC 补全</span><span>BOM 缺料</span><span>出入库同步</span><span>AI 助手</span><span>知识网络</span>
        </div>
      </section>
      </div>
    </main>
    """
    return render_public_layout("首页", body, scripts=[
        "/static/home.js",
        "/static/product_pcb_explosion.js",
    ])


def render_auth_legacy_unused(mode="login", error="", message=""):
    config = get_config()
    tunnel = f'<p class="muted">公网地址：{html.escape(TUNNEL_URL)}</p>' if TUNNEL_URL else ""
    is_register = mode == "register"
    action = "/register" if is_register else "/login"
    title = "注册新用户" if is_register else "登录"
    button = "创建账号" if is_register else "进入系统"
    extra = (
        f'<label>所属组别（可选）{team_group_select()}</label>'
        '<label>确认密码<input name="confirm" type="password" autocomplete="new-password" minlength="6" required></label>'
        if is_register
        else ""
    )
    tabs = f"""
      <div class="auth-tabs">
        <a class="{"active" if not is_register else ""}" href="/login">登录</a>
        <a class="{"active" if is_register else ""}" href="/register">注册</a>
      </div>
    """
    disabled = config.get("allow_registration") != "1"
    disabled_note = flash_box("管理员已关闭自助注册。", "error") if is_register and disabled else ""
    body = f"""
    <section class="login-hero">
      <div class="login-copy">
        <p class="eyebrow">Local inventory server</p>
        <h1>仓库表单与 BOM 对照系统</h1>
        <p>统一账号登录、入库填写、库存检索、BOM 缺料对照和每周自动汇总。所有数据保存在本机服务器目录。</p>
        {tunnel}
      </div>
      <form class="login-card" method="post" action="{action}">
        {tabs}
        <h2>{title}</h2>
        {flash_box(message, "ok")}{flash_box(error, "error")}{disabled_note}
        <label>账号<input name="username" autocomplete="username" required></label>
        <label>密码<input name="password" type="password" autocomplete="current-password" minlength="6" required></label>
        {extra}
        <button class="primary" type="submit" {"disabled" if is_register and disabled else ""}>{button}</button>
        <p class="hint">首次部署后请立即设置独立管理员密码，并关闭默认口令。</p>
      </form>
    </section>
    """
    return render_layout(title, body)


def render_auth(mode="login", error="", message=""):
    config = get_config()
    is_register = mode == "register"
    action = "/register" if is_register else "/login"
    title = "创建账号" if is_register else "登录系统"
    button = "创建账号" if is_register else "进入库存系统"
    disabled = config.get("allow_registration") != "1"
    disabled_attr = "disabled" if is_register and disabled else ""
    tunnel = f'<p class="auth-aura-footnote">公网地址：{html.escape(TUNNEL_URL)}</p>' if TUNNEL_URL else ""
    extra = (
        f'<label>所属分组 <span>用于区分团队或项目范围</span>{team_group_select()}</label>'
        '<label>确认密码 <span>再次输入同一密码</span><input name="confirm" type="password" autocomplete="new-password" minlength="6" required></label>'
        if is_register
        else ""
    )
    tabs = f"""
      <div class="auth-tabs">
        <a class="{"active" if not is_register else ""}" href="/login">登录</a>
        <a class="{"active" if is_register else ""}" href="/register">注册</a>
      </div>
    """
    disabled_note = flash_box("管理员已关闭自助注册。", "error") if is_register and disabled else ""
    body = f"""
    <div class="auth-aura">
      <div class="auth-aura-video" aria-hidden="true">
        <video autoplay loop muted playsinline preload="auto" poster="/static/liquid-glass-bg.png">
          <source src="/static/home-bg-lite.webm" type="video/webm">
        </video>
      </div>
      <div class="auth-aura-guide auth-aura-guide-left" aria-hidden="true"></div>
      <div class="auth-aura-guide auth-aura-guide-right" aria-hidden="true"></div>
      <svg class="auth-aura-noise" width="0" height="0" aria-hidden="true">
        <filter id="c3-noise">
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" stitchTiles="stitch"></feTurbulence>
          <feColorMatrix type="matrix" values="0 0 0 0 0  0 0 0 0 0  0 0 0 0 0  0 0 0 0.35 0"></feColorMatrix>
          <feComposite in2="SourceGraphic" operator="in" result="noise"></feComposite>
          <feBlend in="SourceGraphic" in2="noise" mode="multiply"></feBlend>
        </filter>
      </svg>

      <header class="auth-aura-nav">
        <a class="auth-logo" href="/" aria-label="Warehouse home">
          <svg viewBox="0 0 256 256" aria-hidden="true"><path d="M 0 128 C 70.692 128 128 185.308 128 256 L 64 256 C 64 220.654 35.346 192 0 192 Z M 256 192 C 220.654 192 192 220.654 192 256 L 128 256 C 128 185.308 185.308 128 256 128 Z M 128 0 C 128 70.692 70.692 128 0 128 L 0 64 C 35.346 64 64 35.346 64 0 Z M 192 0 C 192 35.346 220.654 64 256 64 L 256 128 C 185.308 128 128 70.692 128 0 Z"></path></svg>
        </a>
        <nav><a href="#solutions">工作台</a><a href="#blog">流程</a><a href="#docs">文档</a><a href="#careers">团队</a></nav>
        <a class="auth-apple-button" href="#auth-panel"><span>进入系统</span><span aria-hidden="true">></span></a>
      </header>

      <section class="auth-aura-hero">
        <div class="auth-aura-copy">
          <h1><span>库存管理。</span><span class="auth-shiny">焕新升级</span></h1>
          <p>面向硬件团队的库存、BOM 与项目资料入口。登录后统一处理器件检索、缺料对照和项目进度追踪。</p>
          <div class="auth-aura-actions">
            <a class="auth-apple-button" href="#auth-panel"><span>进入库存系统</span><span aria-hidden="true">></span></a>
            <small>本地服务器 · 数据保存在当前项目目录</small>
          </div>
        </div>
        <form id="auth-panel" class="login-card liquid-glass" method="post" action="{action}">
          {tabs}
          <h2>{title}</h2>
          {flash_box(message, "ok")}{flash_box(error, "error")}{disabled_note}
          <label>账号 <span>库存系统用户名</span><input name="username" autocomplete="username" required></label>
          <label>密码 <span>至少 6 位字符</span><input name="password" type="password" autocomplete="current-password" minlength="6" required></label>
          {extra}
          <button class="primary auth-submit" type="submit" {disabled_attr}>{button}<span aria-hidden="true">></span></button>
          <p class="hint">请使用管理员提供的账号登录。正式部署时建议关闭自助注册并启用 HTTPS。</p>
          {tunnel}
        </form>
      </section>

      <section class="auth-menu-strip" aria-label="macOS menu bar">
        <div><strong>库存系统</strong><span>入库</span><span>检索</span><span>BOM</span><span>附件</span><span>分析</span><span>后台</span></div>
        <div><span>搜索</span><span>周三 5月6日 13:09</span></div>
      </section>

      <section class="auth-inbox-mockup liquid-glass" id="solutions">
        <div class="auth-window-bar"><i></i><i></i><i></i><span>库存系统 - 工作台</span></div>
        <figure class="auth-real-window">
          <img class="auth-real-screenshot" src="/static/auth-real-dashboard.jpg" alt="真实库存系统工作台截图" loading="eager">
        </figure>
      </section>

      <section class="auth-feature-grid auth-real-pages">
        <div class="auth-feature-copy">
          <p class="auth-eyebrow"><span></span>工作分流 <b>库存原生</b></p>
          <h2>真实页面，<br>直接进入系统。</h2>
          <p>下面的小页展示全部来自当前库存管理系统的真实截图，登录后看到的就是这些工作界面。</p>
          <div><span>仓库检索</span><span>BOM 对照</span><span>仓检报表</span><span>统计排行</span></div>
        </div>
        <div class="auth-real-page-grid">
          <figure class="auth-real-page-card liquid-glass"><img src="/static/auth-real-inventory.jpg" alt="仓库检索真实截图" loading="lazy"><figcaption><strong>仓库检索</strong><span>按名称、类别、仓位和日期定位器件</span></figcaption></figure>
          <figure class="auth-real-page-card liquid-glass"><img src="/static/auth-real-bom.jpg" alt="BOM 对照真实截图" loading="lazy"><figcaption><strong>BOM 对照</strong><span>上传 BOM 后生成缺料与采购建议</span></figcaption></figure>
          <figure class="auth-real-page-card liquid-glass"><img src="/static/auth-real-reports.jpg" alt="仓检报表真实截图" loading="lazy"><figcaption><strong>仓检报表</strong><span>输出周报和历史库存对照</span></figcaption></figure>
          <figure class="auth-real-page-card liquid-glass"><img src="/static/auth-real-analytics.jpg" alt="统计排行真实截图" loading="lazy"><figcaption><strong>统计排行</strong><span>查看高需求器件与库存结构</span></figcaption></figure>
        </div>
      </section>

      <section class="auth-logo-cloud"><p>覆盖硬件团队的关键库存流程</p><div><span>入库</span><span>检索</span><span>BOM</span><span>PCB</span><span>采购</span><span>焊接</span><span>报表</span><span>后台</span></div></section>

      <section class="auth-testimonials">
        <figure class="liquid-glass"><blockquote>"BOM 上传后能直接看到缺料和采购建议，硬件组不用再反复翻表格。"</blockquote><figcaption><strong>硬件负责人</strong><span>库存与采购协同</span><b>WAREHOUSE</b></figcaption></figure>
        <figure class="liquid-glass"><blockquote>"焊接任务重新登录后还能继续显示，板数、开始时间和完成状态都能追踪。"</blockquote><figcaption><strong>焊接工程师</strong><span>PCB 工作台</span><b>SOLDERING</b></figcaption></figure>
        <figure class="liquid-glass"><blockquote>"管理员能集中查看用户、库存、BOM 和报表，权限边界比原来清楚很多。"</blockquote><figcaption><strong>系统管理员</strong><span>后台运营</span><b>ADMIN</b></figcaption></figure>
      </section>

      <section class="auth-final-cta liquid-glass">
        <h2>减少表格切换。<br>专注硬件交付。</h2>
        <p>从登录开始，把库存、BOM、采购、PCB 文件和焊接进度放进同一个可追踪的工作入口。</p>
        <div><a class="auth-apple-button" href="#auth-panel">进入库存系统 <span aria-hidden="true">></span></a><a href="/register">创建账号 <span aria-hidden="true">></span></a></div>
      </section>
    </div>
    """
    return render_layout(title, body)


def metric_card(label, value, note=""):
    return f'<div class="metric"><span>{label}</span><strong>{value}</strong><small>{note}</small></div>'


def soldering_bom_title(record, job=None):
    record = record or {}
    job = job or {}
    return (
        record.get("original_filename")
        or record.get("stored_filename")
        or job.get("bom_id")
        or record.get("bom_id")
        or "BOM"
    )


def active_soldering_resume_html(user):
    jobs = list_soldering_jobs(user, active_only=True)
    if not jobs:
        return ""
    cards = []
    for job in jobs:
        record = find_bom_record(job.get("bom_id")) or {}
        title = soldering_bom_title(record, job)
        cards.append(
            f"""
            <article class="soldering-active-card">
              <div>
                <span>进行中焊接</span>
                <strong>{html.escape(str(title))}</strong>
                <small>制板 {job.get('board_count', 0)} 片 · 开始 {html.escape(str(job.get('started_at') or ''))}</small>
              </div>
              <a class="button" href="/soldering/workbench">继续</a>
            </article>
            """
        )
    return f"""
    <section class="panel soldering-resume-panel">
      <div class="panel-head"><h2>未结束的焊接工作</h2><span>{len(jobs)} 个进行中</span></div>
      <div class="soldering-active-grid">{''.join(cards)}</div>
    </section>
    """


def soldering_pcb_select_html(record):
    pcb_files = record.get("pcb_files") if isinstance(record.get("pcb_files"), list) else []
    if not pcb_files:
        return '<input type="hidden" name="pcb_file_id" value="">'
    options = ['<option value="">不指定 PCB 文件</option>']
    for item in pcb_files:
        if not isinstance(item, dict):
            continue
        item = decorate_pcb_file(record, item)
        pcb_id = str(item.get("pcb_id") or item.get("pcb_file_id") or item.get("id") or "").strip()
        if not pcb_id:
            continue
        label = item.get("original_filename") or item.get("stored_filename") or pcb_id
        status_text = item.get("status_text") or pcb_file_compact_status(item)
        option_label = f"{label} ({status_text})" if status_text else label
        options.append(f'<option value="{html.escape(pcb_id)}">{html.escape(str(option_label))}</option>')
    return f'<label>PCB 文件<select name="pcb_file_id">{"".join(options)}</select></label>'


def bom_pcb_files_html(record):
    decorated = decorate_bom_record(record)
    pcb_files = decorated.get("pcb_files") if isinstance(decorated.get("pcb_files"), list) else []
    items = []
    for item in pcb_files[-4:]:
        if not isinstance(item, dict):
            continue
        label = item.get("original_filename") or item.get("stored_filename") or item.get("pcb_id") or "PCB"
        status_text = item.get("status_text") or pcb_file_compact_status(item)
        view_url = item.get("view_url") or "#"
        pcb_id = pcb_file_metadata_id(item)
        analysis_action = ""
        if pcb_id and pcb_file_is_pdf_schematic(item):
            analysis_status = str(item.get("analysis_status") or "pending").strip().lower()
            disabled = " disabled" if analysis_status in ANALYSIS_JOB_ACTIVE_STATUSES else ""
            if analysis_status == "queued":
                button_label = "已排队"
            elif analysis_status == "running":
                button_label = "分析中"
            elif analysis_status == "completed":
                button_label = "再次分析"
            else:
                button_label = "提交分析"
            action_url = (
                f"/api/boms/{urllib.parse.quote(str(decorated.get('bom_id') or ''))}"
                f"/pcb/{urllib.parse.quote(str(pcb_id))}/analysis"
            )
            analysis_action = (
                f'<form class="pcb-analysis-form" method="post" action="{html.escape(action_url)}">'
                f'<button class="button pcb-analysis-button" type="submit"{disabled}>{html.escape(button_label)}</button>'
                "</form>"
            )
        items.append(
            f'<li><span class="pcb-file-main"><strong>{html.escape(str(label))}</strong>'
            f'<small data-analysis-status>{html.escape(status_text)}</small></span>'
            f'<a href="{html.escape(view_url)}" target="_blank" rel="noopener">预览</a>'
            f'{analysis_action}</li>'
        )
    if not items:
        return '<div class="pcb-file-list empty">还没有关联 PCB/Gerber/HTML/PDF 文件。</div>'
    return '<ul class="pcb-file-list">' + "".join(items) + "</ul>"


def bom_pcb_upload_form_html(record):
    bom_id = str(record.get("bom_id") or "")
    if not bom_id:
        return ""
    escaped_bom_id = html.escape(bom_id)
    return f"""
      <form class="pcb-attach-form" method="post" action="/api/boms/{urllib.parse.quote(bom_id)}/pcb" data-bom-id="{escaped_bom_id}" enctype="multipart/form-data">
        <input id="pcb-file-{escaped_bom_id}" type="file" name="pcb_file" accept=".zip,.gbr,.ger,.gtl,.gbl,.gts,.gbs,.gto,.gbo,.gm1,.drl,.pcb,.kicad_pcb,.brd,.fbrd,.html,.htm,.pdf,.json,.txt,.csv,.epro" multiple>
        <small>支持 .epro；上传后会自动解包、识别 .epcb，并生成交互 BOM JSON 和 HTML。</small>
        <label for="pcb-file-{escaped_bom_id}">上传 PCB/Gerber/HTML/PDF/.epro</label>
        <button class="button" type="submit">关联</button>
      </form>
    """


def bom_attachment_panel_html(user):
    records = [record for record in load_bom_records() if user_can_view_bom(record, user)]
    records.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
    cards = []
    for record in records[:12]:
        bom_id = str(record.get("bom_id") or "")
        if not bom_id:
            continue
        title = soldering_bom_title(record)
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        pcb_files = record.get("pcb_files") if isinstance(record.get("pcb_files"), list) else []
        cards.append(
            f"""
            <article class="soldering-bom-card">
              <div class="soldering-card-head">
                <span>BOM</span>
                <strong>{html.escape(str(title))}</strong>
                <small>{html.escape(str(record.get('created_at') or ''))}</small>
              </div>
              <div class="soldering-card-meta">
                <span>器件 {summary.get('aggregated_item_count', 0)}</span>
                <span>总用量 {summary.get('total_quantity', 0)}</span>
                <span>附件 {len(pcb_files)}</span>
              </div>
              {bom_pcb_files_html(record)}
              {bom_pcb_upload_form_html(record)}
              {analysis_workbench_history_panel_html(user, record)}
            </article>
            """
        )
    return f"""
    <section class="panel bom-attachment-panel">
      <div class="panel-head"><h2>BOM 附件与 PDF 分析</h2><span>关联 PCB/Gerber/HTML/PDF 文件，PDF 可提交原理图分析</span></div>
      <div class="soldering-bom-grid">{''.join(cards) or '<div class="empty">暂无可关联附件的 BOM。</div>'}</div>
      <div class="lcsc-preview" data-attachment-status>等待操作。</div>
    </section>
    """


def soldering_workbench_html(user):
    records = [record for record in load_bom_records() if user_can_view_bom(record, user)]
    records.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
    active_jobs = list_soldering_jobs(user, active_only=True)
    active_cards = []
    for job in active_jobs:
        record = find_bom_record(job.get("bom_id")) or {}
        title = soldering_bom_title(record, job)
        active_cards.append(
            f"""
            <article class="soldering-job-card">
              <div>
                <span>进行中</span>
                <strong>{html.escape(str(title))}</strong>
                <small>制板 {job.get('board_count', 0)} 片 · 开始 {html.escape(str(job.get('started_at') or ''))}</small>
              </div>
              <button class="button soldering-finish" type="button" data-job-id="{html.escape(str(job.get('job_id') or ''))}">结束焊接</button>
            </article>
            """
        )
    record_cards = []
    for record in records[:12]:
        bom_id = str(record.get("bom_id") or "")
        if not bom_id:
            continue
        title = soldering_bom_title(record)
        active = find_active_soldering_job_for_bom(record)
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        pcb_files = record.get("pcb_files") if isinstance(record.get("pcb_files"), list) else []
        latest_pcb = record.get("latest_pcb_file") if isinstance(record.get("latest_pcb_file"), dict) else (pcb_files[-1] if pcb_files else {})
        latest_pcb_id = str(latest_pcb.get("pcb_id") or latest_pcb.get("pcb_file_id") or latest_pcb.get("id") or "")
        workbench_link = f'<a class="button" href="{html.escape(soldering_workbench_url(bom_id, latest_pcb_id))}">BOM/PCB 联动</a>'
        completed_consumption_html = ""
        if not active:
            completed_job_id = record.get("last_completed_soldering_job_id") or record.get("latest_soldering_job_id")
            completed_job, completed_consumption = soldering_consumption_for_job_id(completed_job_id, user)
            if completed_job and completed_job.get("status") == SOLDERING_COMPLETED_STATUS:
                completed_consumption_html = soldering_consumption_summary_html(completed_consumption, compact=True)
        if active:
            action = f"""
              <div class="soldering-inline-status">
                <strong>正在焊接</strong>
                <span>制板 {active.get('board_count', 0)} 片 · 开始 {html.escape(str(active.get('started_at') or ''))}</span>
              </div>
              <button class="button soldering-finish" type="button" data-job-id="{html.escape(str(active.get('job_id') or ''))}">结束焊接</button>
            """
        else:
            action = f"""
              <form class="soldering-start-form" data-bom-id="{html.escape(bom_id)}">
                <label>本次制板数量<input type="number" name="board_count" min="1" step="1" required placeholder="例如 3"></label>
                {soldering_pcb_select_html(record)}
                <label>备注<input name="notes" maxlength="200" placeholder="可选"></label>
                <button class="primary" type="submit">开始焊接</button>
              </form>
            """
        record_cards.append(
            f"""
            <article class="soldering-bom-card">
              <div class="soldering-card-head">
                <span>{html.escape(str(record.get('status') or 'awaiting_pcb'))}</span>
                <strong>{html.escape(str(title))}</strong>
                <small>{html.escape(str(record.get('created_at') or ''))}</small>
              </div>
              <div class="soldering-card-meta">
                <span>器件 {summary.get('aggregated_item_count', 0)}</span>
                <span>总用量 {summary.get('total_quantity', 0)}</span>
                <span>Files {len(pcb_files)}</span>
              </div>
              {bom_pcb_files_html(record)}
              <div class="soldering-card-actions">{workbench_link}</div>
              {bom_pcb_upload_form_html(record)}
              {action}
            </article>
            """
        )
    empty_records = '<div class="empty">暂无可开始焊接的 BOM。先上传 BOM，必要时关联 PCB 文件。</div>'
    return f"""
    <section class="panel soldering-workbench" data-soldering-workbench>
      <div class="panel-head"><h2>焊接工作台</h2><span>开始时记录制板数量和时间，只有结束按钮会完成流程</span></div>
      <div class="soldering-active-grid">{''.join(active_cards) or '<div class="empty">当前没有进行中的焊接工作。</div>'}</div>
      <div class="soldering-bom-grid">{''.join(record_cards) or empty_records}</div>
      <div class="lcsc-preview" data-soldering-status>等待操作。</div>
    </section>
    <script>
    (function () {{
      const root = document.querySelector("[data-soldering-workbench]");
      if (!root) return;
      const status = root.querySelector("[data-soldering-status]");
      function setStatus(text, isError) {{
        if (!status) return;
        status.textContent = text;
        status.classList.toggle("error", Boolean(isError));
      }}
      async function postJson(url, payload) {{
        const res = await fetch(url, {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify(payload || {{}})
        }});
        const data = await res.json().catch(() => ({{}}));
        if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
        return data;
      }}
      root.querySelectorAll(".soldering-start-form").forEach((form) => {{
        form.addEventListener("submit", async (event) => {{
          event.preventDefault();
          const payload = Object.fromEntries(new FormData(form).entries());
          payload.board_count = Number(payload.board_count || 0);
          if (!payload.board_count || payload.board_count <= 0) {{
            setStatus("请输入大于 0 的制板数量。", true);
            return;
          }}
          const bomId = form.dataset.bomId || "";
          setStatus("正在开始焊接...");
          try {{
            await postJson("/api/boms/" + encodeURIComponent(bomId) + "/soldering/start", payload);
            window.location.reload();
          }} catch (err) {{
            setStatus(err.message, true);
          }}
        }});
      }});
      root.querySelectorAll(".soldering-finish").forEach((button) => {{
        button.addEventListener("click", async () => {{
          const jobId = button.dataset.jobId || "";
          if (!jobId) return;
          if (!window.confirm("结束这单焊接吗？")) return;
          setStatus("正在结束焊接...");
          try {{
            await postJson("/api/soldering/jobs/" + encodeURIComponent(jobId) + "/finish", {{}});
            window.location.reload();
          }} catch (err) {{
            setStatus(err.message, true);
          }}
        }});
      }});
      root.querySelectorAll(".pcb-attach-form").forEach((form) => {{
        if (form.dataset.pcbAttachBound === "1") return;
        form.dataset.pcbAttachBound = "1";
        form.addEventListener("submit", async (event) => {{
          event.preventDefault();
          const bomId = form.dataset.bomId || "";
          const fileInput = form.querySelector('input[type="file"]');
          if (!bomId || !fileInput || !fileInput.files.length) {{
            setStatus("请选择 PCB、Gerber 或 HTML 文件。", true);
            return;
          }}
          setStatus("正在关联 PCB 文件...");
          try {{
            const res = await fetch("/api/boms/" + encodeURIComponent(bomId) + "/pcb", {{
              method: "POST",
              body: new FormData(form)
            }});
            const data = await res.json().catch(() => ({{}}));
            if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
            const latest = data.latest_pcb_file || {{}};
            if (latest.workbench_url) {{
              window.location.href = latest.workbench_url;
            }} else {{
              window.location.reload();
            }}
          }} catch (err) {{
            setStatus(err.message, true);
          }}
        }});
      }});
      root.querySelectorAll(".pcb-attach-form").forEach((form) => {{
        if (form.dataset.pcbAttachBound === "1") return;
        form.dataset.pcbAttachBound = "1";
        form.addEventListener("submit", async (event) => {{
          event.preventDefault();
          const bomId = form.dataset.bomId || "";
          const fileInput = form.querySelector('input[type="file"]');
          if (!bomId || !fileInput || !fileInput.files.length) {{
            setStatus("请选择 PCB、Gerber 或 HTML 文件。", true);
            return;
          }}
          setStatus("正在关联 PCB 文件...");
          try {{
            const res = await fetch("/api/boms/" + encodeURIComponent(bomId) + "/pcb", {{
              method: "POST",
              body: new FormData(form)
            }});
            const data = await res.json().catch(() => ({{}}));
            if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
            const latest = data.latest_pcb_file || {{}};
            if (latest.workbench_url) {{
              window.location.href = latest.workbench_url;
            }} else {{
              window.location.reload();
            }}
          }} catch (err) {{
            setStatus(err.message, true);
          }}
        }});
      }});
      root.querySelectorAll(".pcb-attach-form").forEach((form) => {{
        if (form.dataset.pcbAttachBound === "1") return;
        form.dataset.pcbAttachBound = "1";
        form.addEventListener("submit", async (event) => {{
          event.preventDefault();
          const bomId = form.dataset.bomId || "";
          const fileInput = form.querySelector('input[type="file"]');
          if (!bomId || !fileInput || !fileInput.files.length) {{
            setStatus("请选择 PCB、Gerber 或 HTML 文件。", true);
            return;
          }}
          setStatus("正在关联 PCB 文件...");
          try {{
            const res = await fetch("/api/boms/" + encodeURIComponent(bomId) + "/pcb", {{
              method: "POST",
              body: new FormData(form)
            }});
            const data = await res.json().catch(() => ({{}}));
            if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
            const latest = data.latest_pcb_file || {{}};
            if (latest.workbench_url) {{
              window.location.href = latest.workbench_url;
            }} else {{
              window.location.reload();
            }}
          }} catch (err) {{
            setStatus(err.message, true);
          }}
        }});
      }});
    }})();
    </script>
    """


def can_adjust_inventory_row(user, row):
    if not user or not row:
        return False
    if is_admin_role(user):
        return True
    return str(row["created_by"] or "") == str(user["username"] or "")


def manual_inventory_adjustment_history_for_rows(rows, user, limit_per_item=3):
    if not rows or not user:
        return {}
    visible_ids = []
    seen = set()
    for row in rows:
        inventory_id = parse_int(row_value(row, "id"), 0)
        if inventory_id <= 0 or inventory_id in seen:
            continue
        if not can_adjust_inventory_row(user, row):
            continue
        visible_ids.append(inventory_id)
        seen.add(inventory_id)
    if not visible_ids:
        return {}

    histories = {inventory_id: [] for inventory_id in visible_ids}
    limit_per_item = clamp_int(limit_per_item, 3, 1, 8)
    with db() as conn:
        for start in range(0, len(visible_ids), 800):
            chunk = visible_ids[start : start + 800]
            placeholders = ",".join("?" for _ in chunk)
            owner_clause = ""
            params = list(chunk)
            if not is_admin_role(user):
                owner_clause = "AND owner_username = ?"
                params.append(str(user["username"] or ""))
            params.append(limit_per_item)
            history_rows = conn.execute(
                f"""
                SELECT *
                FROM (
                    SELECT
                        id, owner_username, actor_username, inventory_id, action, source,
                        quantity_before, quantity_after, quantity_delta, reason,
                        note_snapshot, created_at,
                        ROW_NUMBER() OVER (
                            PARTITION BY inventory_id
                            ORDER BY datetime(created_at) DESC, id DESC
                        ) AS rn
                    FROM manual_inventory_adjustments
                    WHERE inventory_id IN ({placeholders})
                    {owner_clause}
                )
                WHERE rn <= ?
                ORDER BY inventory_id ASC, datetime(created_at) DESC, id DESC
                """,
                params,
            ).fetchall()
            for history_row in history_rows:
                inventory_id = parse_int(row_value(history_row, "inventory_id"), 0)
                if inventory_id in histories:
                    histories[inventory_id].append(history_row)
    return histories


def manual_inventory_adjustment_history_html(history_rows):
    if not history_rows:
        return """
              <details class="inventory-adjust-history">
                <summary>调整历史 (0)</summary>
                <div class="inventory-adjust-history-empty">暂无手动调整台账。</div>
              </details>
        """
    body_rows = []
    for row in history_rows:
        quantity_before = row_value(row, "quantity_before")
        quantity_after = row_value(row, "quantity_after")
        before_after = (
            f"{quantity_before if quantity_before is not None else '-'} -> "
            f"{quantity_after if quantity_after is not None else '-'}"
        )
        quantity_delta = parse_int(row_value(row, "quantity_delta"), 0)
        delta_label = f"{quantity_delta:+d}"
        action = str(row_value(row, "action", "") or "").strip()
        source = str(row_value(row, "source", "") or "").strip()
        action_source = " / ".join(zh_status(part) for part in (action, source) if part) or "-"
        reason = str(row_value(row, "reason", "") or "").strip()
        note = str(row_value(row, "note_snapshot", "") or "").strip()
        reason_note = reason
        if note and note not in reason_note:
            reason_note = (reason_note + "；备注：" + note).strip("； ")
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(str(row_value(row, 'created_at', '') or ''))}</td>"
            f"<td>{html.escape(str(row_value(row, 'actor_username', '') or ''))}</td>"
            f"<td>{html.escape(action_source)}</td>"
            f"<td>{html.escape(before_after)}</td>"
            f"<td>{html.escape(delta_label)}</td>"
            f"<td>{html.escape(reason_note or '-')}</td>"
            "</tr>"
        )
    return f"""
              <details class="inventory-adjust-history">
                <summary>调整历史 ({len(history_rows)})</summary>
                <table>
                  <thead><tr><th>时间</th><th>操作人</th><th>动作/来源</th><th>调整前/后</th><th>变化</th><th>原因/备注</th></tr></thead>
                  <tbody>{''.join(body_rows)}</tbody>
                </table>
              </details>
    """


def table_html(rows, user=None, show_adjustments=False):
    if not rows:
        return '<div class="empty">暂无记录</div>'
    adjustment_permissions = [can_adjust_inventory_row(user, r) for r in rows]
    show_adjustment_column = bool(show_adjustments and any(adjustment_permissions))
    adjustment_histories = manual_inventory_adjustment_history_for_rows(rows, user) if show_adjustment_column else {}
    trs = []
    for r, can_adjust in zip(rows, adjustment_permissions):
        note = r["note"] or ""
        link_match = re.search(r"链接:(https?://[^\s|]+)", note)
        lcsc_match = re.search(r"LCSC:(C\d+)", note, re.I)
        package_match = re.search(r"封装:([^|]+)", note)
        voltage_match = re.search(r"耐压:([^|]+)", note)
        inventory_id = parse_int(r["id"], 0)
        current_quantity = parse_int(r["quantity"], 0)
        value_key = component_value_key(r["name"], r["category"])
        group_label = value_key.split(":", 1)[1] if ":" in value_key else r["name"]
        meta = []
        if lcsc_match:
            meta.append(f"LCSC {lcsc_match.group(1).upper()}")
        if package_match:
            meta.append(f"封装 {html.escape(package_match.group(1).strip())}")
        if voltage_match:
            meta.append(f"耐压 {html.escape(voltage_match.group(1).strip())}")
        meta_html = "".join(f'<span class="meta-chip">{m}</span>' for m in meta)
        name_html = f'<div class="item-title">{html.escape(r["name"])}</div><div class="item-sub">同类键：{html.escape(group_label)}</div><div class="meta-row">{meta_html}</div>'
        if link_match:
            url = html.escape(link_match.group(1))
            name_html += f'<a class="mini-link" href="{url}" target="_blank" rel="noopener">打开 LCSC</a>'
        quantity_html = (
            f'<span class="inventory-quantity-value" '
            f'data-inventory-quantity="{inventory_id}">{current_quantity}</span>'
        )
        adjustment_cell = ""
        if show_adjustment_column:
            if can_adjust and inventory_id > 0:
                history_html = manual_inventory_adjustment_history_html(adjustment_histories.get(inventory_id, []))
                adjustment_cell = f"""
            <td class="inventory-adjust-cell">
              <form class="inventory-adjust-form" data-inventory-adjust-form data-inventory-id="{inventory_id}" action="/api/inventory/{inventory_id}/adjust">
                <div class="inventory-adjust-controls">
                  <select name="mode" aria-label="调整方式">
                    <option value="quantity_delta">数量增减</option>
                    <option value="quantity_after">调整后数量</option>
                  </select>
                  <input name="adjust_value" inputmode="numeric" pattern="-?\\d+" placeholder="+/- 数量" aria-label="数量值" required>
                  <input name="reason" maxlength="1000" placeholder="必须填写原因" aria-label="调整原因" required>
                  <button type="submit">应用</button>
                </div>
                <div class="inventory-adjust-status" role="status" aria-live="polite"></div>
              </form>
              {history_html}
            </td>
                """
            else:
                adjustment_cell = '<td class="inventory-adjust-view-only">-</td>'
        trs.append(
            "<tr>"
            f"<td>{html.escape(r['created_at'])}</td>"
            f"<td>{html.escape(r['category'])}</td>"
            f"<td>{name_html}</td>"
            f"<td>{quantity_html}</td>"
            f"<td>{html.escape(r['location'])}</td>"
            f"<td>{html.escape(note)}</td>"
            f"<td>{html.escape(r['created_by'])}</td>"
            f"{adjustment_cell}"
            "</tr>"
        )
    adjustment_header = "<th>调整</th>" if show_adjustment_column else ""
    return (
        '<div class="table-wrap"><table><thead><tr><th>时间</th><th>类别</th><th>名称</th><th>数量</th>'
        f'<th>位置</th><th>备注</th><th>录入</th>{adjustment_header}</tr></thead><tbody>'
        + "".join(trs)
        + "</tbody></table></div>"
    )


def clean_inventory_search_value(value, max_len=200):
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()[:max_len]


def inventory_like_value(value):
    return "%" + inventory_like_escape(value) + "%"


def inventory_like_escape(value):
    return (
        str(value or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )


def inventory_prefix_like_value(value):
    return inventory_like_escape(value) + "%"


def inventory_device_location_variants(box, strip=None, cell=None):
    try:
        box_number = int(box)
    except (TypeError, ValueError):
        return []
    if not 1 <= box_number <= 30:
        return []
    box_parts = [f"H{box_number}"]
    if box_number < 10:
        box_parts.append(f"H{box_number:02d}")
    variants = []
    if strip is None:
        return list(dict.fromkeys(box_parts))
    try:
        strip_number = int(strip)
    except (TypeError, ValueError):
        return []
    if not 1 <= strip_number <= 14:
        return []
    strip_parts = [str(strip_number), f"{strip_number:02d}"]
    if cell is None:
        for box_part in box_parts:
            for strip_part in strip_parts:
                variants.append(f"{box_part}-{strip_part}")
        return list(dict.fromkeys(variants))
    try:
        cell_number = int(cell)
    except (TypeError, ValueError):
        return []
    if not 1 <= cell_number <= 4:
        return []
    cell_parts = [str(cell_number), f"{cell_number:02d}"]
    for box_part in box_parts:
        for strip_part in strip_parts:
            for cell_part in cell_parts:
                variants.append(f"{box_part}-{strip_part}-{cell_part}")
    return list(dict.fromkeys(variants))


def normalize_inventory_device_location_token(token):
    text = str(token or "").strip().upper()
    text = re.sub(r"[＿_/\\\s]+", "-", text)
    text = text.replace("－", "-").replace("—", "-").replace("–", "-")
    text = re.sub(r"-+", "-", text).strip("-")
    match = re.fullmatch(r"H0*(\d{1,2})(?:-0*(\d{1,2})(?:-0*(\d{1,2}))?)?", text)
    if not match:
        return None
    box = parse_int(match.group(1), 0)
    strip = parse_int(match.group(2), 0) if match.group(2) is not None else None
    cell = parse_int(match.group(3), 0) if match.group(3) is not None else None
    if not 1 <= box <= 30:
        return None
    if strip is not None and not 1 <= strip <= 14:
        return None
    if cell is not None and not 1 <= cell <= 4:
        return None
    canonical = f"H{box}"
    level = "box"
    description = f"H{box}（{box} 号器件盒）"
    if strip is not None:
        canonical = f"{canonical}-{strip:02d}"
        level = "strip"
        description = f"{canonical}（{box} 号盒第 {strip} 条）"
    if cell is not None:
        canonical = f"{canonical}-{cell:02d}"
        level = "cell"
        description = f"{canonical}（{box} 号盒第 {strip} 条第 {cell} 格）"
    return {
        "mode": "device",
        "level": level,
        "box": box,
        "strip": strip,
        "cell": cell,
        "canonical": canonical,
        "description": description,
        "variants": inventory_device_location_variants(box, strip, cell),
    }


def inventory_location_search_clause(token):
    device = normalize_inventory_device_location_token(token)
    if not device:
        return "", [], None
    expression = "UPPER(TRIM(location))"
    clauses = []
    params = []
    for variant in device["variants"]:
        upper_variant = str(variant).upper()
        if device["level"] == "cell":
            clauses.append(f"{expression} = ?")
            params.append(upper_variant)
        else:
            clauses.append(f"({expression} = ? OR {expression} LIKE ? ESCAPE '\\')")
            params.extend([upper_variant, inventory_prefix_like_value(upper_variant + "-")])
    if not clauses:
        return "", [], None
    return "(" + " OR ".join(clauses) + ")", params, device


def inventory_search_token_is_specific(token):
    text = str(token or "").strip()
    if not text:
        return False
    normalized = re.sub(r"[%_\W]+", "", text, flags=re.UNICODE)
    if len(normalized) < INVENTORY_SEARCH_MIN_TOKEN_CHARS:
        return False
    return True


def normalize_inventory_search_date(value):
    text = str(value or "").strip().replace("/", "-").replace(".", "-")
    match = re.fullmatch(r"(20\d{2})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        return ""
    try:
        return datetime(int(match.group(1)), int(match.group(2)), int(match.group(3))).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def inventory_search_like_clause(fields, values):
    clean_values = [str(value or "").strip() for value in values if str(value or "").strip()]
    clean_values = list(dict.fromkeys(clean_values))[:8]
    if not clean_values:
        return "", []
    clauses = []
    params = []
    for value in clean_values:
        like = inventory_like_value(value)
        for field in fields:
            clauses.append(f"{field} LIKE ? ESCAPE '\\'")
            params.append(like)
    return "(" + " OR ".join(clauses) + ")", params


def inventory_search_token_kind(token):
    text = str(token or "").strip()
    upper = text.upper()
    lower = text.lower()
    if re.fullmatch(r"C\d{4,}", upper) and upper not in COMMON_PACKAGE_CODES:
        return "lcsc", "LCSC 编号"
    if normalize_inventory_device_location_token(text) or re.fullmatch(r"(?:[A-Z0-9]+-L[1-5](?:-\d{1,2})?|L[1-5])", upper):
        return "location", "仓位"
    if re.fullmatch(r"(?:[CR]?(?:0201|0402|0603|0805|1206|1210|1812|2010|2512)|SOT-?\d+|SOD-?\d+|SOIC-?\d+|TSSOP-?\d+|QFN-?\d*|QFP-?\d*|LQFP-?\d*|DIP-?\d+)", upper):
        return "package", "封装"
    if component_value_key(text, "") or re.search(r"\d+(?:\.\d+)?\s*(?:pf|nf|uf|μf|µf|uh|mh|Ω|ohm|kΩ|mΩ|r)\b", lower, re.I):
        return "value", "规格值"
    type_words = {
        "电阻",
        "电容",
        "电感",
        "二极管",
        "三极管",
        "mos",
        "mosfet",
        "继电器",
        "晶振",
        "光耦",
        "连接器",
        "端子",
        "保险",
        "tvs",
        "esd",
        "电源",
        "ldo",
        "pmic",
        "芯片",
    }
    if any(word in lower for word in type_words):
        return "type", "器件类型"
    return "keyword", "关键词"


def inventory_search_aliases(token, kind):
    text = str(token or "").strip()
    aliases = [text]
    upper = text.upper()
    if kind == "lcsc":
        aliases.append(upper)
    elif kind == "location":
        aliases.append(upper)
    elif kind == "package":
        compact = upper.replace("-", "")
        aliases.extend([upper, compact])
        package_match = re.fullmatch(r"([CR]?)(\d{4})", compact)
        if package_match:
            base = package_match.group(2)
            aliases.extend([base, f"C{base}", f"R{base}"])
        if compact.startswith("SOT") and "-" not in upper:
            aliases.append(upper.replace("SOT", "SOT-", 1))
    elif kind == "value":
        aliases.extend(component_value_aliases(text, "电阻"))
        aliases.extend(component_value_aliases(text, "电容"))
        aliases.extend([
            text.replace("μ", "u").replace("µ", "u"),
            text.replace("u", "μ"),
            text.replace("Ω", "欧姆"),
        ])
    return list(dict.fromkeys(alias for alias in aliases if alias))


def inventory_smart_search_plan(query):
    raw_q = clean_inventory_search_value(payload_value(query, "q", "query", "search"))
    legacy_keyword = clean_inventory_search_value(payload_value(query, "keyword"), 120)
    legacy_category = clean_inventory_search_value(payload_value(query, "category"), 120)
    legacy_location = clean_inventory_search_value(payload_value(query, "location"), 120)
    visible_q = raw_q or " ".join(part for part in (legacy_keyword, legacy_category, legacy_location) if part)
    date_from = normalize_inventory_search_date(payload_value(query, "from", "date_from", "start_date"))
    date_to = normalize_inventory_search_date(payload_value(query, "to", "date_to", "end_date"))

    labels = []
    filters = []
    params = []
    remaining = visible_q
    extracted_dates = []
    for match in re.finditer(r"\b20\d{2}[-/.]\d{1,2}[-/.]\d{1,2}\b", visible_q):
        parsed_date = normalize_inventory_search_date(match.group(0))
        if parsed_date:
            extracted_dates.append(parsed_date)
            remaining = remaining.replace(match.group(0), " ")
    if "今天" in visible_q:
        today = short_date()
        extracted_dates.append(today)
        remaining = remaining.replace("今天", " ")
    if "昨天" in visible_q:
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        extracted_dates.append(yesterday)
        remaining = remaining.replace("昨天", " ")
    if "本月" in visible_q and not date_from:
        now = datetime.now()
        date_from = now.replace(day=1).strftime("%Y-%m-%d")
        date_to = date_to or now.strftime("%Y-%m-%d")
        remaining = remaining.replace("本月", " ")

    if extracted_dates:
        extracted_dates = sorted(dict.fromkeys(extracted_dates))
        if len(extracted_dates) == 1:
            date_from = date_from or extracted_dates[0]
            date_to = date_to or extracted_dates[0]
        else:
            date_from = date_from or extracted_dates[0]
            date_to = date_to or extracted_dates[-1]

    tokens = [
        token.strip()
        for token in re.split(r"[\s,，;；、]+", remaining)
        if token.strip()
    ]
    token_plans = []
    for token in tokens:
        if not inventory_search_token_is_specific(token):
            continue
        kind, kind_label = inventory_search_token_kind(token)
        fields = ["name", "category", "location", "note"]
        if kind == "location":
            clause, clause_params, location_info = inventory_location_search_clause(token)
            if clause:
                filters.append(clause)
                params.extend(clause_params)
                token_plans.append(
                    {
                        "token": location_info["canonical"],
                        "kind": kind,
                        "label": kind_label,
                        "description": location_info["description"],
                    }
                )
                continue
            fields = ["location"]
        elif kind == "lcsc":
            fields = ["name", "note"]
        elif kind in ("package", "value"):
            fields = ["name", "category", "note"]
        clause, clause_params = inventory_search_like_clause(fields, inventory_search_aliases(token, kind))
        if clause:
            filters.append(clause)
            params.extend(clause_params)
            token_plans.append({"token": token, "kind": kind, "label": kind_label})

    if date_from:
        filters.append("date(created_at) >= date(?)")
        params.append(date_from)
        labels.append({"label": "开始日期", "value": date_from})
    if date_to:
        filters.append("date(created_at) <= date(?)")
        params.append(date_to)
        labels.append({"label": "结束日期", "value": date_to})
    for item in token_plans:
        labels.append({"label": item["label"], "value": item.get("description") or item["token"]})
    location_search = any(item.get("kind") == "location" for item in token_plans)

    return {
        "q": visible_q,
        "raw_q": raw_q,
        "filters": filters,
        "params": params,
        "labels": labels,
        "tokens": token_plans,
        "date_from": date_from,
        "date_to": date_to,
        "has_specific_token": bool(token_plans),
        "has_search": bool(filters) and bool(token_plans),
        "blocked_broad_search": bool(visible_q or filters) and not bool(token_plans),
        "limit": INVENTORY_SEARCH_RESULT_LIMIT,
        "location_search": location_search,
        "order_by": "location" if location_search else "recent",
    }


def inventory_smart_search_meta_html(plan):
    labels = plan.get("labels") or []
    if not plan.get("q") and not labels:
        return """
        <div class="smart-search-meta">
          <span>支持输入名称、规格、封装、仓位、LCSC 编号、日期，系统会自动分流到对应字段。</span>
        </div>
        """
    chips = "".join(
        f'<button type="button" class="smart-search-chip" data-value="{html.escape(str(item["value"]), quote=True)}"><b>{html.escape(item["label"])}</b>{html.escape(str(item["value"]))}</button>'
        for item in labels[:10]
    )
    if not chips:
        chips = '<span class="smart-search-chip"><b>关键词</b>全文匹配</span>'
    return f"""
        <div class="smart-search-meta">
          <span>智能识别</span>
          <div class="smart-search-chips">{chips}</div>
        </div>
    """


def search_history_html(user, limit=10):
    with db() as conn:
        history = conn.execute(
            """
            SELECT term, COUNT(*) AS hits, MAX(created_at) AS last_seen
            FROM search_events
            WHERE username = ? AND term != ''
            GROUP BY lower(term)
            ORDER BY datetime(last_seen) DESC
            LIMIT ?
            """,
            (user["username"], limit),
        ).fetchall()
    if not history:
        return '<div class="empty">暂无历史搜索。输入任意器件关键词、规格、封装或仓位后，这里会保留最近检索入口。</div>'
    cards = []
    for item in history:
        term = html.escape(item["term"])
        cards.append(
            f'<a class="history-search-card" href="/inventory?q={urllib.parse.quote(item["term"])}">'
            f'<strong>{term}</strong><span>{item["hits"]} 次检索</span><small>{html.escape(item["last_seen"] or "")}</small></a>'
        )
    return '<div class="history-search-grid">' + "".join(cards) + "</div>"


def inventory_group_html(rows):
    groups = {}
    for r in rows:
        key = component_value_key(r["name"], r["category"]) or normalize_key(r["name"])
        note = r["note"] or ""
        package_match = re.search(r"封装:([^|]+)", note)
        voltage_match = re.search(r"耐压:([^|]+)", note)
        variant = " / ".join(
            value.strip()
            for value in [
                package_match.group(1) if package_match else "",
                voltage_match.group(1) if voltage_match else "",
                r["location"],
            ]
            if value
        )
        groups.setdefault(
            key,
            {"label": r["name"], "category": r["category"], "quantity": 0, "variants": []},
        )
        groups[key]["quantity"] += int(r["quantity"] or 0)
        groups[key]["variants"].append((variant or "默认", r["quantity"], r["location"]))
    if not groups:
        return '<div class="empty">暂无可分组库存</div>'
    cards = []
    for group in sorted(groups.values(), key=lambda item: item["label"]):
        variants = "".join(
            f'<div class="variant-row"><span>{html.escape(v[0])}</span><strong>{v[1]}</strong></div>'
            for v in group["variants"][:6]
        )
        cards.append(
            f'<article class="variant-card"><div><span>{html.escape(group["category"])}</span><h3>{html.escape(group["label"])}</h3></div>'
            f'<strong class="variant-total">{group["quantity"]}</strong>{variants}</article>'
        )
    return '<div class="variant-grid">' + "".join(cards[:18]) + "</div>"


def cleanup_sessions(now=None):
    now = now or time.time()
    with SESSIONS_LOCK:
        expired = [token for token, data in SESSIONS.items() if now - data.get("created_at", 0) > SESSION_TTL_SECONDS]
        for token in expired:
            SESSIONS.pop(token, None)


def safe_cookie(header):
    try:
        return SimpleCookie(header or "")
    except Exception as exc:
        print(f"[cookie] ignored invalid Cookie header: {exc}")
        return SimpleCookie()


def session_user_id(handler):
    cookie = safe_cookie(handler.headers.get("Cookie", ""))
    token = cookie.get("session")
    if not token:
        return None
    cleanup_sessions()
    with SESSIONS_LOCK:
        session = SESSIONS.get(token.value)
        return session.get("user_id") if isinstance(session, dict) else session


def current_user(handler):
    uid = session_user_id(handler)
    if not uid:
        return None
    with db() as conn:
        return conn.execute("SELECT id, username, role, team_group FROM users WHERE id = ?", (uid,)).fetchone()


def existing_config_is_insecure_default(config):
    config = config or {}
    return (
        str(config.get("host", "")).strip() == "0.0.0.0"
        and str(config.get("tunnel_mode", "")).strip().lower() == "auto"
        and str(config.get("allow_registration", "")).strip() == "1"
    )


def dashboard_page(user):
    with db() as conn:
        total_qty = conn.execute("SELECT COALESCE(SUM(quantity), 0) AS v FROM inventory").fetchone()["v"]
        item_count = conn.execute("SELECT COUNT(*) AS v FROM inventory").fetchone()["v"]
        locations = conn.execute("SELECT COUNT(DISTINCT location) AS v FROM inventory").fetchone()["v"]
        bom_qty = conn.execute("SELECT COALESCE(SUM(total_quantity), 0) AS v FROM bom_uploads").fetchone()["v"]
        recent = conn.execute("SELECT * FROM inventory ORDER BY datetime(created_at) DESC, id DESC LIMIT 8").fetchall()
    lan_text = f"http://{local_ip()}:{PORT}" if HOST in ("0.0.0.0", "::") else "未启用，后台配置 host 为 0.0.0.0 后重启可开启"
    public = TUNNEL_URL or "未启用；后台显式开启内网穿透后可用"
    body = f"""
    <header class="page-head">
      <div><p class="eyebrow">Dashboard</p><h1>仓库概览</h1></div>
      <a class="button" href="/inventory/new">新增入库</a>
    </header>
    <section class="metrics">
      {metric_card("累计库存", total_qty, "所有表单记录")}
      {metric_card("入库记录", item_count, "仓库表单")}
      {metric_card("仓位数量", locations, "不同位置")}
      {metric_card("BOM用量", bom_qty, "所有用户累计")}
    </section>
    <section class="dashboard-core">
      <a href="/inventory"><span>01</span><strong>仓库检索</strong><small>按名称、类别、仓位和日期快速定位器件</small></a>
      <a href="/inventory/new"><span>02</span><strong>智能入库</strong><small>LCSC 编号补全分类、封装、品牌和链接</small></a>
      <a href="/bom"><span>03</span><strong>BOM 对照</strong><small>拖拽文件生成缺料和采购清单</small></a>
      <a href="/analytics"><span>04</span><strong>库存分析</strong><small>查看高需求、排行和 3D 类型分析</small></a>
      <a href="/assistant"><span>05</span><strong>库存助手</strong><small>围绕库存、BOM 和硬件知识实时问答</small></a>
      <a href="/reports"><span>06</span><strong>仓检报表</strong><small>每周汇总与历史对照表格</small></a>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>访问地址</h2></div>
      <div class="address-grid">
        <div><span>本机</span><strong>http://127.0.0.1:{PORT}</strong></div>
        <div><span>局域网</span><strong>{html.escape(lan_text)}</strong></div>
        <div><span>穿透</span><strong>{html.escape(public)}</strong></div>
      </div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>最近入库</h2><a href="/inventory">查看全部</a></div>
      {table_html(recent)}
    </section>
    """
    return render_layout("概览", body, user, "概览")


def inventory_page(user, query):
    search_plan = inventory_smart_search_plan(query)
    q = search_plan["q"]
    warehouse = (payload_value(query, "warehouse", "scope") or "main").strip().lower()
    if warehouse not in ("main", "competition", "both"):
        warehouse = "main"
    warehouse_labels = {"main": "原仓库", "competition": "比赛备件仓库", "both": "两个仓库"}
    has_search = search_plan["has_search"]
    rows = (
        inventory_rows(
            " AND ".join(search_plan["filters"]),
            search_plan["params"],
            limit=search_plan["limit"],
            order_by=search_plan.get("order_by", "recent"),
        )
        if has_search and warehouse in ("main", "both")
        else []
    )
    competition_rows = competition_material_rows(query, limit=search_plan["limit"]) if has_search and warehouse in ("competition", "both") else []
    if has_search and q and not is_noise_stat_value(q):
        with db() as conn:
            conn.execute(
                "INSERT INTO search_events (username, term, created_at) VALUES (?, ?, ?)",
                (user["username"], q[:120], now_text()),
            )
    search_meta = inventory_smart_search_meta_html(search_plan)
    if has_search:
        search_meta += f'<div class="smart-search-meta"><span>当前检索范围：{warehouse_labels[warehouse]}；为防止网页批量枚举原始数据库，每个仓库最多显示前 {search_plan["limit"]} 条具体关键词匹配结果。</span></div>'
    if has_search:
        grouped_section = f"""
    <section class="panel">
      <div class="panel-head"><h2>同类器件子层级</h2><span>按阻值、容值、封装、耐压聚合</span></div>
      {inventory_group_html(rows) if warehouse in ("main", "both") else '<div class="empty">比赛备件仓库按责任人管理，不做原仓位层级聚合。</div>'}
    </section>
        """
        competition_section = ""
        if warehouse in ("competition", "both"):
            competition_section = f"""
    <section class="panel inventory-results-panel">
      <div class="panel-head"><h2>比赛备件仓库</h2><span>匹配 {len(competition_rows)} 条；位置为责任人/组别</span></div>
      {competition_material_search_table_html(competition_rows)}
    </section>
            """
        result_section = f"""
    <section class="panel inventory-results-panel">
      <div class="panel-head"><h2>原仓库搜索结果</h2><span>匹配 {len(rows)} 条库存记录</span></div>
      {table_html(rows, user=user, show_adjustments=True) if warehouse in ("main", "both") else '<div class="empty">当前只搜索比赛备件仓库。</div>'}
    </section>
    {competition_section}
        """
    elif search_plan.get("blocked_broad_search"):
        grouped_section = """
    <section class="panel inventory-history-panel">
      <div class="panel-head"><h2>搜索范围过宽</h2><span>请输入更具体的器件信息</span></div>
      <div class="empty">库存搜索对所有登录用户开放，但不能用空值、通配符或仅日期条件批量枚举原始数据库。请加入器件名、封装、规格、LCSC 编号或仓位等具体关键词。</div>
    </section>
        """
        result_section = """
    <section class="panel inventory-results-panel">
      <div class="panel-head"><h2>搜索结果</h2><span>等待具体关键词</span></div>
      <div class="empty search-empty">示例：100nF 0603、C25803、H1、H1-07、H1-07-04、继电器、A1-L1-01。</div>
    </section>
        """
    else:
        grouped_section = f"""
    <section class="panel inventory-history-panel">
      <div class="panel-head"><h2>历史搜索结果</h2><span>点击后重新检索</span></div>
      {search_history_html(user)}
    </section>
        """
        result_section = """
    <section class="panel inventory-results-panel">
      <div class="panel-head"><h2>搜索结果</h2><span>等待检索</span></div>
      <div class="empty search-empty">请输入一个总检索词，例如“100nF 0603”“C25803”“H1”“H1-07”“H1-07-04”“继电器”“A1-L1-01”。系统会自动识别关键词并定位对应器件。</div>
    </section>
        """
    body = f"""
    <header class="page-head"><div><p class="eyebrow">Smart Search</p><h1>智能仓库检索</h1></div><div class="page-head-actions"><a class="button" href="/changelog">更新日志</a><a class="button" href="/inventory/new">填写表单</a></div></header>
    <section class="panel smart-search-panel">
      <form class="smart-search-form" method="get" action="/inventory" data-smart-inventory-search>
        <label class="smart-search-field smart-search-scope">
          <span>搜索仓库</span>
          <select name="warehouse">
            <option value="main" {"selected" if warehouse == "main" else ""}>原仓库</option>
            <option value="competition" {"selected" if warehouse == "competition" else ""}>比赛备件仓库</option>
            <option value="both" {"selected" if warehouse == "both" else ""}>两个仓库同时搜索</option>
          </select>
        </label>
        <label class="smart-search-field">
          <span>总检索窗口</span>
          <input name="q" value="{html.escape(q)}" autocomplete="off" placeholder="输入器件名、规格、封装、仓位、责任人、组别或 LCSC 编号，例如：100nF 0603 H1-07">
        </label>
        <button class="primary smart-search-submit" type="submit">智能检索</button>
      </form>
      {search_meta}
    </section>
    {grouped_section}
    {result_section}
    <script src="/static/inventory.js"></script>
    """
    return render_layout("智能仓库检索", body, user, "仓库检索")


def new_inventory_page(user, message="", error=""):
    categories = fetch_lcsc_categories(force=False)
    categories_json = html.escape(json.dumps(categories["categories"], ensure_ascii=False), quote=True)
    image_cfg = get_image_recognition_config(mask_key=True)
    image_enabled = image_cfg.get("enabled") == "1" and bool(image_cfg.get("endpoint") and image_cfg.get("model"))
    body = f"""
    <header class="page-head glass-hero">
      <div><p class="eyebrow">Liquid Inventory</p><h1>智能入库工作台</h1></div>
      <div class="hero-glass-stats">
        <span>LCSC Sync</span><strong>Online</strong>
      </div>
    </header>
    <section class="entry-console product-entry" data-categories="{categories_json}">
      <div class="form-panel entry-card">
        {flash_box(message, "ok")}{flash_box(error, "error")}
        <div class="prefill-strip"><span>预写入状态</span><strong id="prefill-status" data-mode="idle">等待 LCSC 编号或图片识别</strong></div>
        <div class="image-recognition-panel" data-image-recognition data-enabled="{"1" if image_enabled else "0"}">
          <div>
            <strong>拍照识别入库</strong>
            <span>{"已连接图片识别 API" if image_enabled else "后台未启用图片识别 API"}</span>
          </div>
          <div class="image-recognition-actions">
            <label class="button" for="inventory-camera-input">拍照</label>
            <label class="button" for="inventory-gallery-input">上传图片</label>
            <input id="inventory-camera-input" type="file" accept="image/*" capture="environment">
            <input id="inventory-gallery-input" type="file" accept="image/*">
          </div>
          <div class="image-recognition-status" id="image-recognition-status">手机端点击“拍照”可唤起相机；电脑端可上传本地图片。识别结果只会预填表单，需人工确认后保存。</div>
          <div class="image-recognition-results" id="image-recognition-results"></div>
        </div>
        <form class="entry-form liquid-form" method="post" action="/inventory/new">
        <label>LCSC 编号<input name="lcsc_code" id="lcsc-code" placeholder="例如：C25803，可一键联网补全"></label>
        <label class="inline-field"><span>在线补全</span><button class="button" type="button" id="lcsc-lookup">获取 LCSC 信息</button></label>
        <label>一级分类<select name="primary_category" id="primary-category"></select></label>
        <label>二级分类<select name="secondary_category" id="secondary-category"></select></label>
        <label class="wide">商品类别<input name="category" id="category" placeholder="可由上方分类或 LCSC 信息自动生成"></label>
        <label>商品名称<input name="name" id="product-name" placeholder="例如：100kΩ/欧姆、GH1.25-2pin；只填 C 编号也可自动生成"></label>
        <label>规格/阻容值<input name="value_spec" id="value-spec" placeholder="例如：100nF、100kΩ"></label>
        <label>封装<input name="package" id="package" placeholder="例如：C0603、R0603"></label>
        <label>耐压/额定值<input name="voltage" id="voltage" placeholder="例如：50V、35V、100mW"></label>
        <label>品牌<input name="brand" id="brand" placeholder="可由 LCSC 自动填入"></label>
        <label>数量<input name="quantity" id="quantity" type="number" min="1" value="1" required></label>
        <label>位置<input name="location" id="inventory-location" required placeholder="例如：H1-07-04 或 A1-L1-01"></label>
        <div class="wide location-picker-2d" data-location-picker>
          <div class="panel-head compact-head"><h2>2D 位置看板</h2><span id="location-picker-status">逐层点击，自动写入位置</span></div>
          <div class="location-picker-shell">
            <div class="location-stage-wrap">
              <div class="location-breadcrumb" id="location-breadcrumb">器件盒 / 选择 1-30 号器件盒</div>
              <div class="location-2d-stage" id="location-2d-stage" aria-label="2D 分层位置选择器"></div>
              <div class="location-scene-hint">器件盒：先选 1-30 号盒，再选 14 条中的一条，最后选 1-4 号格。柜内：先选 5 层柜子，再选该层 20 个纸盒栏位。</div>
            </div>
            <div class="location-picker-controls">
              <div class="location-mode-tabs" role="tablist" aria-label="位置类型">
                <button type="button" data-location-mode="device" class="active">器件盒</button>
                <button type="button" data-location-mode="paper">柜内纸箱</button>
              </div>
              <div class="location-back-row">
                <button type="button" id="location-back" disabled>返回上一级</button>
                <span id="location-back-note">当前在顶层视图</span>
              </div>
              <div class="location-config-group" id="location-device-config">
                <div class="location-config-note">器件盒和柜内纸箱是同级位置类型。器件盒固定 30 个，每个器件盒内部为 14 条 × 4 格，左侧 7 条、右侧 7 条。</div>
              </div>
              <div class="location-config-group" id="location-paper-config">
                <div class="location-config-grid">
                  <label>柜号<input id="location-cabinet-id" value="A1" placeholder="例如：A1"></label>
                  <label>固定层数<input id="location-paper-layers" value="5" readonly aria-readonly="true"></label>
                </div>
                <div class="location-config-note">柜内固定 5 层，每层 20 个纸盒栏位；L1、L2、L3、L4、L5 的布局一致。</div>
              </div>
              <div class="location-readout">
                <span>当前选位</span><strong id="location-current-code">未选择</strong>
              </div>
              <div class="location-detail-card">
                <span id="location-detail-label-main">主位置</span><strong id="location-detail-value-main">待选择</strong>
                <span id="location-detail-label-sub">次位置</span><strong id="location-detail-value-sub">待选择</strong>
                <span id="location-detail-label-cell">细分位置</span><strong id="location-detail-value-cell">待选择</strong>
                <span id="location-detail-label-final">最终编码</span><strong id="location-detail-value-final">待确认</strong>
              </div>
            </div>
          </div>
        </div>
        <label class="wide">LCSC 链接<input name="product_url" id="product-url" placeholder="联网补全后生成，可点击跳转"></label>
        <label class="wide">备注<textarea name="note" rows="4" placeholder="批次、供应商、状态等"></textarea></label>
        <div class="wide lcsc-preview" id="lcsc-preview">输入 C 编号后点击“获取 LCSC 信息”，系统会补全型号、分类、封装、品牌、库存和链接。</div>
          <button class="primary" type="submit">保存入库</button>
        </form>
      </div>
      <aside class="panel entry-side">
        <div class="panel-head"><h2>器件画像</h2><span>预写入</span></div>
        <div class="glass-preview-stack">
          <div><span>分类</span><strong>一级 / 二级 / 子类</strong></div>
          <div><span>变体</span><strong>阻容值 + 封装 + 额定值</strong></div>
          <div><span>来源</span><strong>LCSC 链接会随编号刷新</strong></div>
        </div>
        <div class="step-preview-panel" id="step-preview-panel" data-state="idle">
          <div class="panel-head compact-head"><h2>3D STEP 实时预览</h2><span id="step-preview-status">等待 C 编号</span></div>
          <div class="step-model-stage" id="step-model-stage">
            <div class="step-render model-generic">
              <span class="model-body"></span><span class="model-pin pin-a"></span><span class="model-pin pin-b"></span>
            </div>
          </div>
          <div class="step-model-meta" id="step-model-meta">获取 LCSC 信息后，将在这里根据 STEP 链接或封装参数实时渲染器件模型。</div>
        </div>
      </aside>
    </section>
    <script src="/static/inventory.js"></script>
    <script type="module" src="/static/location_2d.js"></script>
    """
    return render_layout("填写表单", body, user, "填写表单")


def soldering_bom_title(record, job=None):
    record = record or {}
    job = job or {}
    return (
        record.get("original_filename")
        or record.get("stored_filename")
        or job.get("bom_id")
        or record.get("bom_id")
        or "BOM"
    )


def active_soldering_resume_html(user):
    jobs = list_soldering_jobs(user, active_only=True)
    if not jobs:
        return ""
    cards = []
    for job in jobs:
        record = find_bom_record(job.get("bom_id")) or {}
        title = soldering_bom_title(record, job)
        cards.append(
            f"""
            <article class="soldering-active-card">
              <div>
                <span>进行中焊接</span>
                <strong>{html.escape(str(title))}</strong>
                <small>制板 {job.get('board_count', 0)} 片 · 开始 {html.escape(str(job.get('started_at') or ''))}</small>
              </div>
              <a class="button" href="/bom">继续</a>
            </article>
            """
        )
    return f"""
    <section class="panel soldering-resume-panel">
      <div class="panel-head"><h2>未结束的焊接工作</h2><span>{len(jobs)} 个进行中</span></div>
      <div class="soldering-active-grid">{''.join(cards)}</div>
    </section>
    """


def soldering_pcb_select_html(record):
    pcb_files = record.get("pcb_files") if isinstance(record.get("pcb_files"), list) else []
    if not pcb_files:
        return '<input type="hidden" name="pcb_file_id" value="">'
    options = ['<option value="">不指定 PCB 文件</option>']
    for item in pcb_files:
        if not isinstance(item, dict):
            continue
        item = decorate_pcb_file(record, item)
        pcb_id = str(item.get("pcb_id") or item.get("pcb_file_id") or item.get("id") or "").strip()
        if not pcb_id:
            continue
        label = item.get("original_filename") or item.get("stored_filename") or pcb_id
        status_text = item.get("status_text") or pcb_file_compact_status(item)
        option_label = f"{label} ({status_text})" if status_text else label
        options.append(f'<option value="{html.escape(pcb_id)}">{html.escape(str(option_label))}</option>')
    return f'<label>PCB 文件<select name="pcb_file_id">{"".join(options)}</select></label>'


def soldering_workbench_html(user):
    records = [record for record in load_bom_records() if user_can_view_bom(record, user)]
    records.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
    active_jobs = list_soldering_jobs(user, active_only=True)
    active_cards = []
    for job in active_jobs:
        record = find_bom_record(job.get("bom_id")) or {}
        title = soldering_bom_title(record, job)
        detail_url = soldering_workbench_url(job.get("bom_id") or "", job.get("pcb_file_id") or "")
        active_cards.append(
            f"""
            <article class="soldering-job-card">
              <div>
                <span>进行中</span>
                <strong>{html.escape(str(title))}</strong>
                <small>制板 {job.get('board_count', 0)} 片 · 开始 {html.escape(str(job.get('started_at') or ''))}</small>
              </div>
              <div class="soldering-card-actions">
                <a class="button" href="{html.escape(detail_url, quote=True)}">查看 BOM 明细</a>
                <button class="button soldering-finish" type="button" data-job-id="{html.escape(str(job.get('job_id') or ''))}">结束并扣减库存</button>
              </div>
            </article>
            """
        )
    record_cards = []
    for record in records[:24]:
        bom_id = str(record.get("bom_id") or "")
        if not bom_id:
            continue
        title = soldering_bom_title(record)
        active = find_active_soldering_job_for_bom(record)
        summary = record.get("summary") if isinstance(record.get("summary"), dict) else {}
        detail_url = soldering_workbench_url(bom_id)
        completed_consumption_html = ""
        if not active:
            completed_job_id = record.get("last_completed_soldering_job_id") or record.get("latest_soldering_job_id")
            completed_job, completed_consumption = soldering_consumption_for_job_id(completed_job_id, user)
            if completed_job and completed_job.get("status") == SOLDERING_COMPLETED_STATUS:
                completed_consumption_html = soldering_consumption_summary_html(completed_consumption, compact=True)
        if active:
            action = f"""
              <div class="soldering-inline-status">
                <strong>正在焊接</strong>
                <span>制板 {active.get('board_count', 0)} 片 · 开始 {html.escape(str(active.get('started_at') or ''))}</span>
              </div>
              <div class="soldering-card-actions">
                <a class="button" href="{html.escape(detail_url, quote=True)}">查看 BOM 明细</a>
                <button class="button soldering-finish" type="button" data-job-id="{html.escape(str(active.get('job_id') or ''))}">结束并扣减库存</button>
              </div>
            """
        else:
            action = f"""
              <form class="soldering-start-form" data-bom-id="{html.escape(bom_id)}">
                <label>本次制板数量<input type="number" name="board_count" min="1" step="1" required placeholder="例如 3"></label>
                <label>备注<input name="notes" maxlength="200" placeholder="可选"></label>
                <div class="soldering-card-actions">
                  <button class="primary" type="submit">选择此 BOM 开始焊接</button>
                  <a class="button" href="{html.escape(detail_url, quote=True)}">查看 BOM 明细</a>
                </div>
              </form>
            """
        record_cards.append(
            f"""
            <article class="soldering-bom-card">
              <div class="soldering-card-head">
                <span>{html.escape(str(record.get('status') or 'awaiting_start'))}</span>
                <strong>{html.escape(str(title))}</strong>
                <small>{html.escape(str(record.get('created_at') or ''))}</small>
              </div>
              <div class="soldering-card-meta">
                <span>器件 {summary.get('aggregated_item_count', 0)}</span>
                <span>总用量 {summary.get('total_quantity', 0)}</span>
                <span>BOM ID {html.escape(bom_id[-10:] if len(bom_id) > 10 else bom_id)}</span>
              </div>
              {completed_consumption_html}
              {action}
            </article>
            """
        )
    empty_records = '<div class="empty">暂无可开始焊接的 BOM。请先在 BOM 对照页上传 BOM。</div>'
    return f"""
    <header class="page-head">
      <div><p class="eyebrow">Soldering</p><h1>焊接工作台</h1></div>
      <a class="button" href="/bom">上传 BOM</a>
    </header>
    <section class="panel soldering-workbench" data-soldering-workbench>
      <div class="panel-head"><h2>未结束的焊接工作</h2><span>{len(active_jobs)} 个进行中</span></div>
      <div class="soldering-active-grid">{''.join(active_cards) or '<div class="empty">当前没有进行中的焊接工作。</div>'}</div>
    </section>
    <section class="panel soldering-workbench" data-soldering-workbench>
      <div class="panel-head"><h2>选择本次使用的 BOM</h2><span>结束焊接时按 BOM 用量和制板数量扣减库存</span></div>
      <div class="soldering-bom-grid">{''.join(record_cards) or empty_records}</div>
      <div class="lcsc-preview" data-soldering-status>等待操作。</div>
    </section>
    <script>
    (function () {{
      const roots = Array.from(document.querySelectorAll("[data-soldering-workbench]"));
      if (!roots.length) return;
      const status = document.querySelector("[data-soldering-status]");
      function setStatus(text, isError) {{
        if (!status) return;
        status.textContent = text;
        status.classList.toggle("error", Boolean(isError));
      }}
      async function postJson(url, payload) {{
        const res = await fetch(url, {{
          method: "POST",
          headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify(payload || {{}})
        }});
        const data = await res.json().catch(() => ({{}}));
        if (!res.ok) throw new Error(data.error || ("HTTP " + res.status));
        return data;
      }}
      document.querySelectorAll(".soldering-start-form").forEach((form) => {{
        form.addEventListener("submit", async (event) => {{
          event.preventDefault();
          const payload = Object.fromEntries(new FormData(form).entries());
          payload.board_count = Number(payload.board_count || 0);
          if (!payload.board_count || payload.board_count <= 0) {{
            setStatus("请输入大于 0 的制板数量。", true);
            return;
          }}
          const bomId = form.dataset.bomId || "";
          setStatus("正在开始焊接...");
          try {{
            await postJson("/api/boms/" + encodeURIComponent(bomId) + "/soldering/start", payload);
            window.location.reload();
          }} catch (err) {{
            setStatus(err.message, true);
          }}
        }});
      }});
      document.querySelectorAll(".soldering-finish").forEach((button) => {{
        button.addEventListener("click", async () => {{
          const jobId = button.dataset.jobId || "";
          if (!jobId) return;
          if (!window.confirm("结束这单焊接并按 BOM 用量扣减库存吗？")) return;
          setStatus("正在结束焊接并扣减库存...");
          try {{
            const data = await postJson("/api/soldering/jobs/" + encodeURIComponent(jobId) + "/finish", {{}});
            const totals = data && data.consumption && data.consumption.summary ? data.consumption.summary : {{}};
            setStatus("焊接已结束。需用：" + (totals.required_quantity || 0) + "；已耗：" + (totals.consumed_quantity || 0) + "；缺口：" + (totals.shortage_quantity || 0) + "。");
            window.setTimeout(() => window.location.reload(), 900);
          }} catch (err) {{
            setStatus(err.message, true);
          }}
        }});
      }});
    }})();
    </script>
    """

def analysis_detail_meta_item(label, value_html):
    return f"<div><dt>{html.escape(str(label))}</dt><dd>{value_html or '-'}</dd></div>"


STATUS_LABELS_ZH = {
    "pending": "待处理",
    "uploaded": "已上传",
    "not_applicable": "无需分析",
    "not_requested": "未请求",
    "queued": "排队中",
    "running": "运行中",
    "processing": "处理中",
    "analyzing": "分析中",
    "retrying": "重试排队中",
    "completed": "已完成",
    "failed": "失败",
    "canceled": "已取消",
    "cancelled": "已取消",
    "awaiting_pcb": "等待 PCB 文件",
    "stock_available": "库存可用",
    "partially_available": "部分可用",
    "receipt_confirmed": "已确认收货",
    "awaiting_purchase_receipt": "等待采购收货",
    "needs_purchase_order": "需要采购单",
    "soldering_active": "焊接中",
    "soldering_completed": "焊接完成",
    "completed_with_shortage": "已完成，有缺口",
    "consumed": "已消耗",
    "consumed_with_shortage": "已消耗，有缺口",
    "shortage": "库存不足",
    "unmatched": "未匹配库存",
    "not_listed": "未列入采购",
    "not_checked": "未检查",
    "received": "已收货",
    "partial": "部分满足",
    "active": "有效",
    "voided": "已作废",
    "reversed": "已反冲",
    "stocked": "已入库",
    "pending_stock_in": "待入库",
    "stocked_with_reversal": "已入库，有反冲",
    "partial_reversal_pending": "部分反冲，仍待入库",
    "purchase_finalization": "采购入库",
    "purchase_reversal": "采购反冲",
    "soldering_consumption": "焊接消耗",
    "manual_inventory": "手动库存",
    "purchase_receipt": "采购收货",
    "soldering_job": "焊接任务",
    "inventory": "库存行",
    "inventory_form": "库存录入",
    "inventory_adjust_api": "库存调整",
    "create": "创建",
    "adjust": "调整",
    "positive": "正向变动",
    "negative": "负向变动",
    "all": "全部",
}


STATUS_LABELS_ZH.update(
    {
        "converted": "已转换",
        "pending_external_converter": "等待外部转换器",
        "external_converter_config_error": "外部转换配置错误",
        "external_converter_failed": "外部转换失败",
        "external_converter_timeout": "外部转换超时",
        "external_converter_no_output": "外部转换无输出",
        "no_pcb_artifact": "未发现 PCB 产物",
        "unsupported": "不支持",
    }
)


def zh_status(value):
    text = str(value or "").strip()
    return STATUS_LABELS_ZH.get(text.lower(), text or "-")


def yes_no_zh(value):
    return "是" if value else "否"


def analysis_detail_list_section(title, values, empty_text="暂无记录。"):
    values = values if isinstance(values, list) else []
    items = []
    for value in values:
        text = safe_analysis_display_text(value, max_len=260)
        if text:
            items.append(f"<li>{html.escape(text)}</li>")
    body = (
        f'<ul class="analysis-detail-list">{"".join(items)}</ul>'
        if items
        else f'<p class="muted">{html.escape(empty_text)}</p>'
    )
    return f'<section class="analysis-detail-section"><h2>{html.escape(title)}</h2>{body}</section>'


def analysis_detail_modules_html(modules):
    modules = modules if isinstance(modules, list) else []
    rows = []
    for item in modules[:PDF_ANALYSIS_MAX_DETECTED_ITEMS]:
        if not isinstance(item, dict):
            continue
        name = safe_analysis_display_text(item.get("name"), max_len=140)
        if not name:
            continue
        matches = parse_int(item.get("matches"), 0)
        keywords = safe_analysis_display_list(item.get("keywords"), limit=8, max_len=60)
        details = []
        if matches:
            details.append(f"匹配 {matches} 项")
        if keywords:
            details.append("关键词：" + ", ".join(keywords))
        detail_text = "；".join(details) if details else "已识别"
        rows.append(f"<li><strong>{html.escape(name)}</strong><span>{html.escape(detail_text)}</span></li>")
    if not rows:
        return '<p class="muted">安全预览中暂无已识别模块。</p>'
    return f'<ul class="analysis-detail-module-list">{"".join(rows)}</ul>'


def analysis_detail_source_html(source):
    source = source if isinstance(source, dict) else {}
    rows = []
    for label, value in (
        ("文件名", safe_display_filename(source.get("filename"))),
        ("模式", safe_analysis_display_text(source.get("mode"), max_len=80)),
        ("提取器", safe_analysis_display_text(source.get("extractor"), max_len=80)),
    ):
        if value:
            rows.append(analysis_detail_meta_item(label, html.escape(str(value))))
    for label, key in (
        ("页数", "page_count"),
        ("字符数", "character_count"),
        ("已分析字符数", "analyzed_character_count"),
    ):
        count = parse_int(source.get(key), 0)
        if count:
            rows.append(analysis_detail_meta_item(label, html.escape(str(count))))
    if source:
        rows.append(analysis_detail_meta_item("文本是否截断", "是" if source.get("text_truncated") else "否"))
    if not rows:
        return '<p class="muted">安全预览中暂无来源元数据。</p>'
    return f'<dl class="analysis-detail-meta analysis-detail-source">{"".join(rows)}</dl>'


def analysis_history_status_options_html(selected):
    options = [("all", "全部状态")] + [(status, zh_status(status)) for status in ANALYSIS_JOB_STATUSES]
    return "".join(
        f'<option value="{html.escape(value)}" {"selected" if value == selected else ""}>{html.escape(label)}</option>'
        for value, label in options
    )


def analysis_workbench_job_result_text(job):
    status = str((job or {}).get("status") or "").strip().lower()
    if status == "failed":
        return sanitize_analysis_error_summary((job or {}).get("error_summary"), max_len=180) or "分析失败。"
    result = safe_analysis_display_text((job or {}).get("result_summary"), max_len=180)
    if result:
        return result
    if status == "queued":
        return "等待本地后台分析。"
    if status == "running":
        return "正在分析。"
    if status == "completed":
        return "已完成，但没有记录预览摘要。"
    return "暂无结果预览。"


def analysis_workbench_history_job_html(job):
    job = job or {}
    job_id = str(job.get("job_id") or "")
    status = safe_analysis_display_text(job.get("status"), max_len=80) or "-"
    progress = clamp_analysis_progress(job.get("progress"))
    detail_url = str(job.get("detail_url") or analysis_job_detail_url(job_id))
    file_label = (
        safe_display_filename(job.get("original_filename"))
        or safe_analysis_display_text(job.get("pcb_file_id"), max_len=140)
        or "原理图 PDF"
    )
    context_bits = []
    if job.get("is_selected_pcb"):
        context_bits.append("当前 PDF")
    for value in (job.get("file_kind"), job.get("document_type"), job.get("status_text")):
        text = safe_analysis_display_text(value, max_len=120)
        if text and text not in context_bits:
            context_bits.append(text)
    context_text = " | ".join(context_bits) if context_bits else "BOM 原理图上下文"
    result_text = analysis_workbench_job_result_text(job)
    status_class = re.sub(r"[^a-z0-9_-]+", "-", status.lower()).strip("-")
    detail_link = (
        f'<a class="button analysis-workbench-open" data-analysis-history-open href="{html.escape(detail_url, quote=True)}">详情</a>'
        if detail_url
        else ""
    )
    return f"""
    <article class="analysis-workbench-job analysis-workbench-job-{html.escape(status_class or 'unknown')}" data-analysis-history-job-id="{html.escape(job_id, quote=True)}" data-analysis-status="{html.escape(status, quote=True)}">
      <div class="analysis-workbench-job-top">
        <a class="analysis-history-detail-link" data-analysis-history-detail-link href="{html.escape(detail_url, quote=True)}"><code>{html.escape(job_id or "-")}</code></a>
        <span class="analysis-history-status status-{html.escape(status_class)}" data-analysis-history-status>{html.escape(zh_status(status))}</span>
        <span class="analysis-workbench-progress" data-analysis-history-progress>{progress}%</span>
      </div>
      <div class="analysis-workbench-source">
        <strong data-analysis-history-file>{html.escape(file_label)}</strong>
        <span data-analysis-history-context>{html.escape(context_text)}</span>
      </div>
      <p class="analysis-workbench-result" data-analysis-history-result>{html.escape(result_text)}</p>
      <div class="analysis-workbench-meta">
        <span data-analysis-history-updated>更新 {html.escape(str(job.get("updated_at") or "-"))}</span>
        <span data-analysis-history-created>创建 {html.escape(str(job.get("created_at") or "-"))}</span>
        {detail_link}
      </div>
    </article>
    """


def analysis_workbench_history_panel_html(user, record, selected_pcb_id=""):
    payload = analysis_workbench_history_payload(user, record, selected_pcb_id=selected_pcb_id)
    jobs = payload.get("jobs") or []
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else analysis_workbench_history_summary(jobs)
    rows_html = "".join(analysis_workbench_history_job_html(job) for job in jobs)
    history_link = '<a class="button analysis-workbench-open" href="/analysis-jobs">分析历史</a>'
    filtered_url = str(payload.get("filtered_history_url") or "/analysis-jobs")
    if filtered_url and filtered_url != "/analysis-jobs":
        history_link += f'<a class="button analysis-workbench-open" href="{html.escape(filtered_url, quote=True)}">只看当前 BOM</a>'
    empty_text = "这个 BOM 暂无原理图 PDF 分析任务。"
    if payload.get("cross_user_restricted"):
        empty_text = "跨用户分析历史可在后台或历史页面查看。"
    empty_html = f'<div class="analysis-workbench-empty" data-analysis-history-empty>{html.escape(empty_text)}</div>' if not rows_html else ""
    return f"""
    <section class="panel analysis-workbench-panel" data-analysis-history-panel>
      <div class="panel-head">
        <h2>原理图分析历史</h2>
        <span><span data-analysis-history-count>{html.escape(str(summary.get("count_text") or f"最近 {parse_int(payload.get('count'), 0)} 个"))}</span> | <span data-analysis-history-latest>{html.escape(str(summary.get("latest_text") or "暂无最新任务"))}</span></span>
      </div>
      <div class="analysis-workbench-list" data-analysis-history-list>
        {rows_html or empty_html}
      </div>
      <div class="analysis-workbench-links">{history_link}</div>
    </section>
    """


def analysis_history_job_row_html(job, show_owner=False):
    job = job or {}
    job_id = str(job.get("job_id") or "")
    status = safe_analysis_display_text(job.get("status"), max_len=80) or "-"
    progress = clamp_analysis_progress(job.get("progress"))
    detail_url = str(job.get("detail_url") or analysis_job_detail_url(job_id))
    job_html = f"<code>{html.escape(job_id or '-')}</code>"
    if detail_url:
        job_html = f'<a class="analysis-history-detail-link" href="{html.escape(detail_url, quote=True)}">{job_html}</a>'
    source_bits = []
    for value in (job.get("file_kind"), job.get("document_type"), job.get("status_text")):
        text = safe_analysis_display_text(value, max_len=140)
        if text and text not in source_bits:
            source_bits.append(text)
    preview = job.get("result_preview") if isinstance(job.get("result_preview"), dict) else {}
    source = preview.get("source") if isinstance(preview.get("source"), dict) else {}
    extractor = safe_analysis_display_text(source.get("extractor"), max_len=80)
    pages = parse_int(source.get("page_count"), 0)
    if extractor:
        source_bits.append(f"提取器：{extractor}")
    if pages:
        source_bits.append(f"{pages} 页")
    file_label = safe_display_filename(job.get("original_filename")) or safe_analysis_display_text(job.get("pcb_file_id"), max_len=140) or "-"
    source_html = (
        f'<strong>{html.escape(file_label)}</strong>'
        f'<span>{"；".join(html.escape(bit) for bit in source_bits) if source_bits else "来源元数据待生成"}</span>'
    )
    bom_parts = []
    if job.get("bom_upload_id"):
        bom_parts.append(f"上传 #{parse_int(job.get('bom_upload_id'), 0)}")
    if job.get("bom_filename"):
        bom_parts.append(safe_display_filename(job.get("bom_filename")))
    if job.get("bom_id"):
        bom_parts.append(f"<code>{html.escape(str(job.get('bom_id')))}</code>")
    if job.get("pcb_file_id"):
        bom_parts.append(f"PCB <code>{html.escape(str(job.get('pcb_file_id')))}</code>")
    bom_html = "<br>".join(part for part in bom_parts if part) or "-"
    if status == "failed":
        result_text = sanitize_analysis_error_summary(job.get("error_summary"), max_len=180) or "失败"
    else:
        result_text = safe_analysis_display_text(job.get("result_summary"), max_len=180) or "-"
    owner_td = f"<td>{html.escape(str(job.get('owner_username') or '-'))}</td>" if show_owner else ""
    return (
        "<tr>"
        f"<td>{job_html}</td>"
        f"{owner_td}"
        f'<td><span class="analysis-history-status status-{html.escape(status)}">{html.escape(zh_status(status))}</span></td>'
        f"<td>{progress}%</td>"
        f'<td class="analysis-history-source">{source_html}</td>'
        f'<td class="analysis-history-context">{bom_html}</td>'
        f'<td class="analysis-history-result">{html.escape(result_text)}</td>'
        f"<td>{html.escape(str(job.get('created_at') or '-'))}</td>"
        f"<td>{html.escape(str(job.get('updated_at') or '-'))}</td>"
        f'<td><a class="button analysis-history-open" href="{html.escape(detail_url, quote=True)}">查看</a></td>'
        "</tr>"
    )


def render_analysis_jobs_page(user, query=None):
    payload = analysis_history_jobs_payload(user, query or {})
    filters = payload["filters"]
    summary = payload["summary"]
    show_owner = payload["show_owner"]
    rows = [analysis_history_job_row_html(job, show_owner=show_owner) for job in payload["jobs"]]
    owner_filter = ""
    if show_owner:
        owner_filter = (
            f'<label>用户<input name="owner" value="{html.escape(filters["owner"], quote=True)}" '
            'maxlength="120" placeholder="用户名"></label>'
        )
    clear_href = "/analysis-jobs"
    admin_link = ""
    limited_note = ""
    if summary.get("candidate_limited"):
        limited_note = (
            f'<p class="muted">筛选前最多读取最近 {parse_int(summary.get("candidate_limit"), 0)} 个候选任务。</p>'
        )
    colspan = 10 if show_owner else 9
    owner_header = "<th>用户</th>" if show_owner else ""
    body = f"""
    <header class="page-head analysis-history-head">
      <div>
        <p class="eyebrow">PDF 原理图分析</p>
        <h1>分析历史</h1>
      </div>
      <div class="form-actions analysis-detail-nav">
        <a class="button" href="/bom">BOM 工作台</a>
        {admin_link}
      </div>
    </header>
    <section class="metrics analysis-history-summary">
      {metric_card("排队中", summary["queued_count"], "等待处理")}
      {metric_card("分析中", summary["running_count"], "已领取")}
      {metric_card("已完成", summary["completed_count"], "已有结果")}
      {metric_card("失败", summary["failed_count"], "需要复核")}
    </section>
    <section class="panel analysis-history-panel">
      <div class="panel-head">
        <h2>最近原理图任务</h2>
        <span>显示 {summary["shown_count"]} 条，共匹配 {summary["filtered_count"]} 条</span>
      </div>
      <form class="entry-form analysis-history-filters" method="get" action="/analysis-jobs">
        <label>状态<select name="status">{analysis_history_status_options_html(filters["status"])}</select></label>
        {owner_filter}
        <label class="wide">搜索<input name="q" value="{html.escape(filters["q"], quote=True)}" maxlength="120" placeholder="任务 ID、BOM ID、文件名、错误"></label>
        <label>数量<input name="limit" type="number" min="1" max="{ANALYSIS_HISTORY_MAX_LIMIT}" step="1" value="{parse_int(filters["limit"], ANALYSIS_HISTORY_DEFAULT_LIMIT)}"></label>
        <div class="wide form-actions">
          <button class="primary" type="submit">应用筛选</button>
          <a class="button" href="{clear_href}">清空</a>
        </div>
      </form>
      {limited_note}
      <div class="table-wrap">
        <table class="admin-soldering-table analysis-history-table">
          <thead>
            <tr>
              <th>任务 ID</th>
              {owner_header}
              <th>状态</th>
              <th>进度</th>
              <th>来源文件</th>
              <th>BOM/PCB 上下文</th>
              <th>结果/错误</th>
              <th>创建时间</th>
              <th>更新时间</th>
              <th>详情</th>
            </tr>
          </thead>
          <tbody>{''.join(rows) or f'<tr><td colspan="{colspan}">没有符合当前筛选条件的 PDF 原理图分析任务。</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    """
    return render_layout("分析历史", body, user, "BOM对照")


def render_analysis_job_detail_page(user, job):
    job = job or {}
    job_id = str(job.get("job_id") or "")
    status = safe_analysis_display_text(job.get("status"), max_len=80) or "-"
    progress = clamp_analysis_progress(job.get("progress"))
    owner_username = safe_analysis_display_text(job.get("owner_username"), max_len=140) or "-"
    bom_id = str(job.get("bom_id") or "")
    pcb_file_id = str(job.get("pcb_file_id") or "")
    record = find_bom_record(bom_id) if bom_id else None
    decorated = decorate_bom_record(record) if record and user_can_view_bom(record, user) else None
    pcb_file = {}
    if decorated:
        found_file = find_pcb_file_metadata(decorated, pcb_file_id)
        pcb_file = decorate_pcb_file(decorated, found_file) if found_file else {}

    bom_upload_id = parse_int((decorated or {}).get("upload_id"), 0)
    bom_name = safe_display_filename((decorated or {}).get("original_filename"))
    pdf_filename = safe_display_filename((pcb_file or {}).get("original_filename"))
    pdf_view_url = str((pcb_file or {}).get("view_url") or "") if decorated else ""
    workbench_url = str((pcb_file or {}).get("workbench_url") or "") if decorated else ""

    preview = safe_analysis_result_preview(job.get("result_json"))
    source = dict(preview.get("source") if isinstance(preview.get("source"), dict) else {})
    if pdf_filename:
        source["filename"] = pdf_filename
    else:
        source.pop("filename", None)

    nav_links = []
    nav_links.append('<a class="button" href="/bom">BOM 对照</a>')
    if workbench_url:
        nav_links.append(f'<a class="button" href="{html.escape(workbench_url, quote=True)}">BOM/PCB 工作台</a>')
    if pdf_view_url:
        nav_links.append(f'<a class="button" href="{html.escape(pdf_view_url, quote=True)}">打开 PDF 预览</a>')
    nav_links.append('<a class="button" href="/analysis-jobs">分析历史</a>')
    if is_admin_role(user):
        nav_links.append('<a class="button" href="/admin/soldering">焊接后台</a>')
    retry_payload = analysis_job_admin_payload(
        job,
        bom_lookup={bom_id: decorated} if decorated and bom_id else {},
        user=user,
        include_stored_filenames=False,
    )
    status_url = str(retry_payload.get("status_url") or "")
    detail_url = analysis_job_detail_url(job_id)
    retry_url = str(retry_payload.get("retry_url") or "") if retry_payload.get("can_retry") else ""
    if retry_url:
        nav_links.append(
            f'<form class="analysis-detail-retry-form" data-analysis-retry-form method="post" '
            f'action="{html.escape(retry_url, quote=True)}">'
            f'<button class="button analysis-detail-retry" data-analysis-retry-button '
            f'data-retry-url="{html.escape(retry_url, quote=True)}" type="submit">重试分析</button>'
            '<span class="muted analysis-detail-retry-feedback" data-analysis-retry-feedback '
            'aria-live="polite" hidden></span>'
            '<a class="button analysis-detail-retry-result" data-analysis-retry-result-link hidden>'
            "打开重试任务</a>"
            "</form>"
        )

    status_text_html = f'<span data-analysis-status-text>{html.escape(zh_status(status))}</span>'
    progress_text_html = f'<span data-analysis-progress-text>{progress}%</span>'
    meta_rows = [
        analysis_detail_meta_item("状态", status_text_html),
        analysis_detail_meta_item("进度", progress_text_html),
        analysis_detail_meta_item("用户", html.escape(owner_username)),
        analysis_detail_meta_item("BOM 上传", html.escape(f"#{bom_upload_id}" if bom_upload_id else "-")),
        analysis_detail_meta_item("BOM 名称", html.escape(bom_name or "-")),
        analysis_detail_meta_item("BOM ID", f"<code>{html.escape(bom_id or '-')}</code>"),
        analysis_detail_meta_item("PDF 文件", html.escape(pdf_filename or pcb_file_id or "-")),
        analysis_detail_meta_item(
            "任务类型",
            html.escape(safe_analysis_display_text(job.get("task_type"), max_len=120) or "-"),
        ),
        analysis_detail_meta_item("创建时间", html.escape(str(job.get("created_at") or "-"))),
        analysis_detail_meta_item("更新时间", html.escape(str(job.get("updated_at") or "-"))),
        analysis_detail_meta_item("开始时间", html.escape(str(job.get("started_at") or "-"))),
        analysis_detail_meta_item("完成时间", html.escape(str(job.get("finished_at") or "-"))),
    ]
    error = sanitize_analysis_error_summary(job.get("error_summary")) if str(job.get("status") or "") == "failed" else ""
    error_panel_hidden = "" if str(job.get("status") or "") == "failed" else " hidden"
    error_html = f"""
      <section class="panel analysis-detail-error" data-analysis-error-panel{error_panel_hidden}>
        <div class="panel-head"><h2>错误摘要</h2><span>已脱敏</span></div>
        <p data-analysis-error-text>{html.escape(error or "")}</p>
      </section>
    """

    summary = preview.get("summary") or ""
    result_intro = (
        f'<p data-analysis-result-summary>{html.escape(summary)}</p>'
        if summary
        else '<p class="muted" data-analysis-result-summary>暂无已完成的结果预览。</p>'
    )
    result_html = f"""
    <section class="panel analysis-detail-result">
      <div class="panel-head"><h2>结果预览</h2><span>只读</span></div>
      <section class="analysis-detail-section">
        <h2>摘要</h2>
        {result_intro}
      </section>
      <section class="analysis-detail-section">
        <h2>识别模块</h2>
        {analysis_detail_modules_html(preview.get("detected_modules"))}
      </section>
      {analysis_detail_list_section("修改建议", preview.get("recommendations"))}
      {analysis_detail_list_section("人工复核", preview.get("manual_review"))}
      {analysis_detail_list_section("风险点", preview.get("risks"))}
      {analysis_detail_list_section("备注", preview.get("notes"))}
      <section class="analysis-detail-section">
        <h2>来源元数据</h2>
        {analysis_detail_source_html(source)}
      </section>
    </section>
    """
    body = f"""
    <section class="analysis-detail-page"
      data-analysis-detail
      data-analysis-job-id="{html.escape(job_id, quote=True)}"
      data-analysis-status-url="{html.escape(status_url, quote=True)}"
      data-analysis-detail-url="{html.escape(detail_url, quote=True)}"
      data-analysis-status="{html.escape(status, quote=True)}"
      data-analysis-progress="{progress}">
      <header class="page-head analysis-detail-head">
        <div>
          <p class="eyebrow">PDF 原理图分析</p>
          <h1>分析任务 <code data-analysis-job-code>{html.escape(job_id)}</code></h1>
        </div>
        <div class="form-actions analysis-detail-nav">{"".join(nav_links)}</div>
      </header>
      <section class="analysis-detail-grid">
        <section class="panel analysis-detail-summary">
          <div class="panel-head"><h2>任务详情</h2><span class="analysis-detail-status-line" data-analysis-status-line aria-live="polite">{status_text_html} · {progress_text_html}</span></div>
          <dl class="analysis-detail-meta">{"".join(meta_rows)}</dl>
          <p class="muted analysis-detail-poll-feedback" data-analysis-poll-feedback aria-live="polite" hidden></p>
          <div class="form-actions analysis-detail-poll-actions">
            <a class="button analysis-detail-result-link" data-analysis-result-link href="{html.escape(detail_url, quote=True)}" hidden>打开最新结果</a>
            <a class="button analysis-detail-error-link" data-analysis-error-link href="{html.escape(detail_url, quote=True)}" hidden>查看失败原因</a>
          </div>
        </section>
        {error_html}
        {result_html}
      </section>
    </section>
    """
    return render_layout("分析任务", body, user, "BOM对照", scripts=["/static/analysis_detail.js"])


def admin_analysis_result_details_html(job):
    preview = job.get("result_preview") if isinstance(job.get("result_preview"), dict) else {}
    if not preview:
        return html.escape(str(job.get("result_summary") or "-"))
    summary = preview.get("summary") or job.get("result_summary") or "已有结果"
    blocks = [f"<p>{html.escape(str(summary))}</p>"]
    modules = preview.get("detected_modules") if isinstance(preview.get("detected_modules"), list) else []
    if modules:
        module_text = ", ".join(
            html.escape(str(item.get("name") or ""))
            for item in modules
            if isinstance(item, dict) and str(item.get("name") or "").strip()
        )
        if module_text:
            blocks.append(f"<p><strong>识别模块：</strong> {module_text}</p>")
    for label, key in (
        ("修改建议", "recommendations"),
        ("人工复核", "manual_review"),
        ("风险点", "risks"),
    ):
        values = preview.get(key) if isinstance(preview.get(key), list) else []
        if values:
            items = "".join(f"<li>{html.escape(str(value))}</li>" for value in values[:4])
            blocks.append(f"<strong>{label}</strong><ul>{items}</ul>")
    return f'<details class="admin-analysis-result"><summary>{html.escape(str(summary)[:120])}</summary>{"".join(blocks)}</details>'


def admin_analysis_job_row_html(job):
    bom_upload = f"#{job['bom_upload_id']}" if job.get("bom_upload_id") else "-"
    progress = f"{clamp_analysis_progress(job.get('progress'))}%"
    file_label = job.get("original_filename") or job.get("pcb_file_id") or "-"
    error_or_result = job.get("error_summary") if job.get("status") == "failed" else job.get("result_summary")
    result_html = admin_analysis_result_details_html(job) if job.get("result_preview") else html.escape(str(error_or_result or "-"))
    job_id = str(job.get("job_id") or "")
    detail_url = str(job.get("detail_url") or analysis_job_detail_url(job_id))
    job_link_html = f"<code>{html.escape(job_id)}</code>"
    if detail_url:
        job_link_html = (
            f'<a class="admin-analysis-detail-link" href="{html.escape(detail_url, quote=True)}">'
            f"{job_link_html}</a>"
        )
    retry_html = "-"
    if job.get("can_retry") and job.get("retry_url"):
        retry_html = (
            f'<button class="button admin-analysis-retry" type="button" '
            f'data-admin-analysis-retry data-retry-url="{html.escape(str(job.get("retry_url")), quote=True)}" '
            f'data-job-id="{html.escape(str(job.get("job_id") or ""), quote=True)}">重试</button>'
        )
    return (
        "<tr>"
        f"<td>{job_link_html}</td>"
        f"<td>{html.escape(str(job.get('owner_username') or ''))}</td>"
        f"<td>{html.escape(zh_status(job.get('status')))}</td>"
        f"<td>{progress}</td>"
        f"<td>{html.escape(str(bom_upload))}</td>"
        f"<td>{html.escape(str(file_label))}</td>"
        f"<td>{result_html}</td>"
        f"<td>{html.escape(str(job.get('updated_at') or ''))}</td>"
        f"<td>{retry_html}</td>"
        "</tr>"
    )


def admin_soldering_page(user, query=None):
    payload = admin_soldering_dashboard_payload()
    analysis_payload = admin_analysis_jobs_payload({"type": ANALYSIS_JOB_TASK_TYPE, "limit": 12})
    worker_status = analysis_worker_status_payload()
    shortage_payload = admin_soldering_shortage_payload(query or {})
    receiving_payload = admin_soldering_receiving_priority_payload(query or {})
    shortage_filters = shortage_payload["filters"]
    export_query = admin_soldering_shortage_query(shortage_filters, {"format": "csv"})
    export_href = "/api/admin/soldering/shortages" + (f"?{export_query}" if export_query else "")
    receiving_query = admin_soldering_shortage_query(shortage_filters)
    receiving_href = "/api/admin/soldering/receiving-priorities" + (f"?{receiving_query}" if receiving_query else "")
    receipt_filter_values = {"owner": shortage_filters.get("owner", ""), "q": shortage_filters.get("q", ""), "status": "all"}
    receipts_payload = admin_purchase_receipts_payload(receipt_filter_values, limit=8)
    receipts_query = admin_soldering_shortage_query(receipt_filter_values)
    receipts_href = "/api/admin/purchase-receipts" + (f"?{receipts_query}" if receipts_query else "")
    summary = payload["summary"]
    rows = []
    for job in payload["jobs"]:
        bom_upload = f"#{job['bom_upload_id']}" if job.get("bom_upload_id") else "-"
        rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(job.get('job_id') or ''))}</code></td>"
            f"<td>{html.escape(str(job.get('owner_username') or ''))}</td>"
            f"<td>{html.escape(bom_upload)}</td>"
            f"<td>{html.escape(str(job.get('bom_name') or '-'))}</td>"
            f"<td>{parse_int(job.get('board_count'), 0)}</td>"
            f"<td>{html.escape(zh_status(job.get('status')))}</td>"
            f"<td>{html.escape(str(job.get('started_at') or ''))}</td>"
            f"<td>{html.escape(str(job.get('finished_at') or ''))}</td>"
            f"<td>{yes_no_zh(job.get('inventory_consumed'))}</td>"
            f"<td>{parse_int(job.get('required_quantity_total'), 0)}</td>"
            f"<td>{parse_int(job.get('consumed_quantity_total'), 0)}</td>"
            f"<td>{parse_int(job.get('shortage_quantity_total'), 0)}</td>"
            f"<td>{parse_int(job.get('shortage_item_count'), 0)}</td>"
            f"<td>{parse_int(job.get('pending_purchase_count'), 0)}</td>"
            "</tr>"
        )
    analysis_rows = [admin_analysis_job_row_html(job) for job in analysis_payload["jobs"]]
    analysis_summary = analysis_payload["summary"]
    analysis_list_url = f"/api/admin/analysis-jobs?type={urllib.parse.quote(ANALYSIS_JOB_TASK_TYPE)}&limit=12"
    analysis_process_url = analysis_payload["actions"]["process_queued_url"]
    analysis_worker_url = "/api/admin/analysis-worker"
    worker_label = "enabled" if worker_status.get("enabled") else "paused"
    worker_thread = "started" if worker_status.get("started") else "not started"
    worker_error = worker_status.get("last_error_summary") or "none"
    shortage_rows = []
    for item in shortage_payload["rows"][:50]:
        bom_upload = f"#{item['bom_upload_id']}" if item.get("bom_upload_id") else "-"
        bom_item = item.get("bom_item_id")
        shortage_rows.append(
            "<tr>"
            f"<td><code>{html.escape(str(item.get('job_id') or ''))}</code></td>"
            f"<td>{html.escape(str(item.get('owner_username') or ''))}</td>"
            f"<td>{html.escape(bom_upload)}</td>"
            f"<td>{html.escape(str(item.get('bom_name') or '-'))}</td>"
            f"<td>{html.escape(zh_status(item.get('job_status')))}</td>"
            f"<td>{html.escape(str(item.get('item_category') or 'BOM'))}</td>"
            f"<td>{html.escape(str(item.get('item_name') or ''))}</td>"
            f"<td>{parse_int(item.get('required_quantity'), 0)}</td>"
            f"<td>{parse_int(item.get('consumed_quantity'), 0)}</td>"
            f"<td>{parse_int(item.get('shortage_quantity'), 0)}</td>"
            f"<td>{html.escape(zh_status(item.get('item_status')))}</td>"
            f"<td>{html.escape(str(bom_item if bom_item is not None else '-'))}</td>"
            f"<td>{parse_int(item.get('pending_purchase_count'), 0)}</td>"
            f"<td>{html.escape(zh_status(item.get('pending_purchase_status')))}</td>"
            "</tr>"
        )
    shortage_more = ""
    if shortage_payload["count"] > 50:
        shortage_more = f"<p class=\"muted\">当前显示 50 行，共 {shortage_payload['count']} 行缺口记录；导出 CSV 可查看全部。</p>"
    receiving_rows = []
    for item in receiving_payload["rows"][:12]:
        latest_purchase = item.get("latest_purchase_at") or "-"
        recent_inventory = item.get("recent_inventory") if isinstance(item.get("recent_inventory"), list) else []
        latest_inventory = recent_inventory[0] if recent_inventory else {}
        stock_detail = (
            f"{parse_int(item.get('current_matching_stock'), 0)}"
            + (f" @ {html.escape(str(latest_inventory.get('created_at') or ''))}" if latest_inventory else "")
        )
        receiving_rows.append(
            "<tr>"
            f"<td>{html.escape(str(item.get('owner_username') or ''))}</td>"
            f"<td>{html.escape(str(item.get('bom_name') or '-'))}</td>"
            f"<td>{html.escape(str(item.get('item_category') or 'BOM'))}</td>"
            f"<td>{html.escape(str(item.get('item_name') or ''))}</td>"
            f"<td>{parse_int(item.get('shortage_quantity'), 0)}</td>"
            f"<td>{stock_detail}</td>"
            f"<td>{parse_int(item.get('purchase_item_count'), 0)}</td>"
            f"<td>{parse_int(item.get('purchase_quantity_total'), 0)}</td>"
            f"<td>{parse_int(item.get('purchase_received_quantity_total'), 0)}</td>"
            f"<td>{parse_int(item.get('purchase_remaining_quantity_total'), 0)}</td>"
            f"<td>{html.escape(str(latest_purchase))}</td>"
            f"<td>{html.escape(zh_status(item.get('receiving_priority_status')))}</td>"
            "</tr>"
        )
    receipt_rows = []
    for receipt in receipts_payload["receipts"][:8]:
        matched_inventory = receipt.get("matched_inventory_id")
        status = receipt.get("status") or PURCHASE_RECEIPT_ACTIVE_STATUS
        void_review = "-"
        if status == PURCHASE_RECEIPT_VOIDED_STATUS:
            void_review = " / ".join(
                part
                for part in (
                    receipt.get("voided_at") or "",
                    receipt.get("voided_by_username") or "",
                    receipt.get("void_reason") or "",
                )
                if part
            ) or "已作废"
        elif receipt.get("stock_reversed"):
            void_review = " / ".join(
                part
                for part in (
                    receipt.get("inventory_reversed_at") or "",
                    receipt.get("inventory_reversed_by_username") or "",
                    receipt.get("inventory_reversal_reason") or "",
                )
                if part
            ) or "已反冲"
        elif receipt.get("stock_finalized"):
            void_review = " / ".join(
                part
                for part in (
                    receipt.get("inventory_posted_at") or "",
                    receipt.get("inventory_posted_by_username") or "",
                    f"inventory {receipt.get('inventory_entry_id') or ''}".strip(),
                )
                if part
            ) or "已入库"
        receipt_rows.append(
            "<tr>"
            f"<td>{html.escape(str(receipt.get('created_at') or ''))}</td>"
            f"<td>{html.escape(zh_status(status))}</td>"
            f"<td>{html.escape(zh_status(receipt.get('stock_status')))}</td>"
            f"<td>{html.escape(str(receipt.get('owner_username') or ''))}</td>"
            f"<td>{html.escape(str(receipt.get('purchase_item_id') or ''))}</td>"
            f"<td>{html.escape(str(receipt.get('category') or ''))}</td>"
            f"<td>{html.escape(str(receipt.get('name') or ''))}</td>"
            f"<td>{parse_int(receipt.get('received_quantity'), 0)}</td>"
            f"<td>{html.escape(str(matched_inventory if matched_inventory is not None else '-'))}</td>"
            f"<td>{html.escape(str(receipt.get('received_by_username') or ''))}</td>"
            f"<td>{html.escape(str(void_review))}</td>"
            "</tr>"
        )
    body = f"""
    <header class="page-head">
      <div><p class="eyebrow">后台焊接</p><h1>焊接看板</h1></div>
      <a class="button" href="/bom">BOM 工作台</a>
    </header>
    <section class="metrics admin-soldering-summary"
      data-active-count="{summary['active_count']}"
      data-completed-count="{summary['completed_count']}"
      data-shortage-total="{summary['shortage_quantity_total']}"
      data-shortage-item-count="{summary['shortage_item_count']}"
      data-pending-purchase-count="{summary['pending_purchase_count']}">
      {metric_card("进行中任务", summary["active_count"], "当前未结束")}
      {metric_card("已完成任务", summary["completed_count"], "历史记录")}
      {metric_card("缺口总量", summary["shortage_quantity_total"], "未解决数量")}
      {metric_card("缺口项", summary["shortage_item_count"], "BOM 行分组")}
      {metric_card("采购待收", summary["pending_purchase_count"], "已有检查")}
    </section>
    <section class="panel admin-analysis-queue"
      data-admin-analysis-panel
      data-list-url="{html.escape(analysis_list_url, quote=True)}"
      data-process-url="{html.escape(analysis_process_url, quote=True)}"
      data-worker-url="{html.escape(analysis_worker_url, quote=True)}">
      <div class="panel-head">
        <h2>PDF 分析队列</h2>
        <span data-admin-analysis-status>最近 {analysis_summary['filtered_count']} 个原理图任务</span>
      </div>
      <section class="metrics admin-analysis-summary">
        {metric_card("排队中", analysis_summary["queued_count"], "等待处理")}
        {metric_card("分析中", analysis_summary["running_count"], "已领取")}
        {metric_card("失败", analysis_summary["failed_count"], "可重试")}
        {metric_card("已完成", analysis_summary["completed_count"], "已有结果")}
      </section>
      <div class="admin-analysis-worker" data-admin-analysis-worker>
        <div class="admin-analysis-worker-state">
          <strong>本地后台任务</strong>
          <span data-admin-analysis-worker-state>
            {html.escape(zh_status(worker_label))}；{html.escape('已启动' if worker_thread == 'started' else '未启动')}；上次处理 {parse_int(worker_status.get('last_processed_count'), 0)}
          </span>
        </div>
        <label>轮询间隔（秒）
          <input type="number" min="{ANALYSIS_WORKER_MIN_INTERVAL_SECONDS}" max="{ANALYSIS_WORKER_MAX_INTERVAL_SECONDS}" step="1"
            value="{parse_int(worker_status.get('interval_seconds'), ANALYSIS_WORKER_DEFAULT_INTERVAL_SECONDS)}"
            data-admin-analysis-worker-interval>
        </label>
        <label>批处理上限
          <input type="number" min="{ANALYSIS_WORKER_MIN_BATCH_LIMIT}" max="{ANALYSIS_WORKER_MAX_BATCH_LIMIT}" step="1"
            value="{parse_int(worker_status.get('batch_limit'), ANALYSIS_WORKER_DEFAULT_BATCH_LIMIT)}"
            data-admin-analysis-worker-batch>
        </label>
        <button class="button" type="button" data-admin-analysis-worker-toggle data-enabled="{'1' if worker_status.get('enabled') else '0'}">
          {"暂停后台任务" if worker_status.get("enabled") else "恢复后台任务"}
        </button>
        <button class="button" type="button" data-admin-analysis-worker-save>保存设置</button>
        <span class="admin-analysis-worker-meta" data-admin-analysis-worker-meta>
          上次心跳 {html.escape(str(worker_status.get('last_tick_at') or '-'))}；错误 {html.escape(worker_error)}
        </span>
      </div>
      <div class="form-actions admin-analysis-actions">
        <button class="button" type="button" data-admin-analysis-refresh>刷新</button>
        <button class="primary" type="button" data-admin-analysis-process>处理下一个排队任务</button>
        <a class="button" href="/analysis-jobs">分析历史</a>
        <a class="button" href="{html.escape(analysis_list_url)}">打开 JSON 接口</a>
      </div>
      <div class="table-wrap">
        <table class="admin-soldering-table admin-analysis-table">
          <thead>
            <tr>
              <th>任务 ID</th>
              <th>用户</th>
              <th>状态</th>
              <th>进度</th>
              <th>BOM 上传</th>
              <th>PDF 文件</th>
              <th>结果/错误</th>
              <th>更新时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody data-admin-analysis-rows>{''.join(analysis_rows) or '<tr><td colspan="9">暂无 PDF 原理图分析任务。</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <section class="panel admin-soldering-receiving-priorities">
      <div class="panel-head">
        <h2>收货优先级</h2>
        <span>{receiving_payload['count']} 行，只读参考</span>
      </div>
      <p class="muted">这里只提供优先级参考，不会标记采购项已收货，也不会修改库存。</p>
      <div class="form-actions">
        <a class="button" href="{html.escape(receiving_href)}">打开 JSON 接口</a>
      </div>
      <div class="table-wrap">
        <table class="admin-soldering-table admin-soldering-receiving-table">
          <thead>
            <tr>
              <th>用户</th>
              <th>BOM 名称</th>
              <th>类别</th>
              <th>物料</th>
              <th>缺口</th>
              <th>当前库存</th>
              <th>采购行</th>
              <th>采购数</th>
              <th>已确认收货</th>
              <th>剩余</th>
              <th>最近采购</th>
              <th>处理状态</th>
            </tr>
          </thead>
          <tbody>{''.join(receiving_rows) or '<tr><td colspan="12">没有符合当前筛选条件的收货优先级记录。</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <section class="panel admin-soldering-receipts">
      <div class="panel-head">
        <h2>收货确认记录</h2>
        <span>最近 {receipts_payload['count']} 条，{parse_int(receipts_payload['summary'].get('active_receipt_count'), 0)} 条有效，{parse_int(receipts_payload['summary'].get('voided_receipt_count'), 0)} 条作废，{parse_int(receipts_payload['summary'].get('reversed_receipt_count'), 0)} 条反冲</span>
      </div>
      <p class="muted">收货记录保留审计轨迹；确认入库和反冲都会生成明确的库存流水。</p>
      <div class="form-actions">
        <a class="button" href="{html.escape(receipts_href)}">打开收货 JSON</a>
      </div>
      <div class="table-wrap">
        <table class="admin-soldering-table admin-soldering-receipts-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>状态</th>
              <th>入库状态</th>
              <th>用户</th>
              <th>采购项</th>
              <th>类别</th>
              <th>物料</th>
              <th>收货数</th>
              <th>匹配库存</th>
              <th>确认人</th>
              <th>入库/作废复核</th>
            </tr>
          </thead>
          <tbody>{''.join(receipt_rows) or '<tr><td colspan="11">暂无收货确认记录。</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    <section class="panel admin-soldering-shortages">
      <div class="panel-head">
        <h2>缺口明细</h2>
        <span>{shortage_payload['summary']['shortage_quantity_total']} 个总缺口，来自 {shortage_payload['count']} 行</span>
      </div>
      <form class="entry-form admin-soldering-shortage-filters" method="get" action="/admin/soldering">
        <label>用户<input name="owner" value="{html.escape(shortage_filters['owner'])}" placeholder="用户名"></label>
        <label>状态<input name="status" value="{html.escape(shortage_filters['status'])}" placeholder="completed, partial, unmatched"></label>
        <label>BOM ID<input name="bom_id" value="{html.escape(shortage_filters['bom_id'])}" placeholder="bom_..."></label>
        <label>BOM 上传<input name="bom_upload_id" value="{html.escape(shortage_filters['bom_upload_id'])}" placeholder="上传 ID"></label>
        <label class="wide">搜索<input name="q" value="{html.escape(shortage_filters['q'])}" placeholder="用户、BOM、任务 ID、物料"></label>
        <div class="wide form-actions">
          <button class="primary" type="submit">应用筛选</button>
          <a class="button" href="/admin/soldering">清空</a>
          <a class="button" href="{html.escape(export_href)}">导出 CSV</a>
        </div>
      </form>
      <div class="table-wrap">
        <table class="admin-soldering-table admin-soldering-shortage-table">
          <thead>
            <tr>
              <th>任务 ID</th>
              <th>用户</th>
              <th>BOM 上传</th>
              <th>BOM 名称</th>
              <th>任务状态</th>
              <th>类别</th>
              <th>物料</th>
              <th>需用</th>
              <th>已耗</th>
              <th>缺口</th>
              <th>物料状态</th>
              <th>BOM 项</th>
              <th>采购待收</th>
              <th>采购状态</th>
            </tr>
          </thead>
          <tbody>{''.join(shortage_rows) or '<tr><td colspan="14">没有符合当前筛选条件的缺口记录。</td></tr>'}</tbody>
        </table>
      </div>
      {shortage_more}
    </section>
    <section class="panel admin-soldering-dashboard">
      <div class="panel-head"><h2>全部焊接任务</h2><span>{payload['count']} 行</span></div>
      <div class="table-wrap">
        <table class="admin-soldering-table">
          <thead>
            <tr>
              <th>任务 ID</th>
              <th>用户</th>
              <th>BOM 上传</th>
              <th>BOM 名称</th>
              <th>板数</th>
              <th>状态</th>
              <th>开始时间</th>
              <th>结束时间</th>
              <th>已扣库存</th>
              <th>需用总数</th>
              <th>已耗总数</th>
              <th>缺口总数</th>
              <th>缺口项</th>
              <th>采购待收</th>
            </tr>
          </thead>
          <tbody>{''.join(rows) or '<tr><td colspan="14">暂无焊接任务。</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    """
    return render_layout("焊接后台", body, user, "焊接后台", scripts=["/static/admin_analysis_jobs.js"])


def admin_inventory_adjustments_page(user, query=None):
    payload = admin_inventory_adjustments_payload(query or {})
    filters = payload["filters"]
    json_query = inventory_adjustment_query(filters)
    json_href = "/api/admin/inventory-adjustments" + (f"?{json_query}" if json_query else "")
    csv_href = "/api/admin/inventory-adjustments.csv" + (f"?{json_query}" if json_query else "")
    rows = []
    for row in payload["rows"]:
        source = html.escape(str(row.get("source_id") or "-"))
        source_url = row.get("source_url") or ""
        if source_url:
            source = f'<a href="{html.escape(source_url)}">{source}</a>'
        ids = []
        if row.get("inventory_entry_id"):
            ids.append(f"库存 {row.get('inventory_entry_id')}")
        if row.get("manual_adjustment_id"):
            ids.append(f"手动调整 {row.get('manual_adjustment_id')}")
        if row.get("purchase_item_id"):
            ids.append(f"采购项 {row.get('purchase_item_id')}")
        if row.get("purchase_order_id"):
            ids.append(f"采购单 {row.get('purchase_order_id')}")
        if row.get("bom_upload_id"):
            ids.append(f"BOM 上传 {row.get('bom_upload_id')}")
        if row.get("soldering_job_id"):
            ids.append(f"任务 {row.get('soldering_job_id')}")
        limitation = " 当前行" if row.get("current_inventory_snapshot") else ""
        before = row.get("quantity_before")
        after = row.get("quantity_after")
        before_after = "-"
        if before is not None or after is not None:
            before_after = f"{'-' if before is None else parse_int(before, 0)} -> {'-' if after is None else parse_int(after, 0)}"
        rows.append(
            "<tr>"
            f"<td>{html.escape(str(row.get('time') or ''))}</td>"
            f"<td>{html.escape(zh_status(row.get('movement_type')))}{html.escape(limitation)}</td>"
            f"<td>{html.escape(str(row.get('owner_username') or ''))}</td>"
            f"<td>{html.escape(str(row.get('category') or ''))}</td>"
            f"<td>{html.escape(str(row.get('part_name') or ''))}</td>"
            f"<td>{parse_int(row.get('quantity_delta'), 0)}</td>"
            f"<td>{html.escape(before_after)}</td>"
            f"<td>{html.escape(zh_status(row.get('source_type')))}: {source}</td>"
            f"<td>{html.escape(str(row.get('operator_username') or ''))}</td>"
            f"<td>{html.escape(zh_status(row.get('status')))}</td>"
            f"<td>{html.escape(str(row.get('reason') or ''))}</td>"
            f"<td>{html.escape(', '.join(ids) or '-')}</td>"
            "</tr>"
        )
    summary = payload["summary"]
    body = f"""
    <header class="page-head">
      <div><p class="eyebrow">后台库存</p><h1>库存调整审计</h1></div>
      <a class="button" href="/admin/purchase-orders">采购单</a>
    </header>
    <section class="metrics admin-soldering-summary">
      {metric_card("库存流水", summary["movement_count"], "筛选结果")}
      {metric_card("增加数量", summary["total_positive_quantity"], "入库或当前正库存")}
      {metric_card("减少数量", summary["total_negative_quantity"], "库存扣减")}
      {metric_card("已入库收货", summary["finalized_receipt_count"], "入库流水")}
      {metric_card("已反冲收货", summary["reversed_receipt_count"], "修正流水")}
      {metric_card("焊接消耗", summary["soldering_consumption_count"], "台账行")}
      {metric_card("手动台账", summary["manual_inventory_ledger_count"], "手动变更")}
    </section>
    <section class="panel admin-inventory-adjustments">
      <div class="panel-head">
        <h2>影响库存的流水</h2>
        <span>包含采购入库、反冲、焊接消耗和普通库存行。</span>
      </div>
      <form class="entry-form admin-soldering-shortage-filters" method="get" action="/admin/inventory-adjustments">
        <label>用户<input name="owner" value="{html.escape(filters['owner'])}" placeholder="用户名"></label>
        <label>类型<input name="type" value="{html.escape(filters['type'])}" placeholder="purchase_finalization, purchase_reversal, soldering_consumption, manual_inventory"></label>
        <label>状态<input name="status" value="{html.escape(filters['status'])}" placeholder="all, reversed, consumed, positive, negative"></label>
        <label>开始日期<input name="start_date" type="date" value="{html.escape(filters['start_date'])}"></label>
        <label>结束日期<input name="end_date" type="date" value="{html.escape(filters['end_date'])}"></label>
        <label>数量<input name="limit" value="{html.escape(str(filters['limit']))}" type="number" min="1" max="500"></label>
        <label class="wide">搜索<input name="q" value="{html.escape(filters['q'])}" placeholder="物料、收货、任务、操作人、原因"></label>
        <div class="wide form-actions">
          <button class="primary" type="submit">应用筛选</button>
          <a class="button" href="/admin/inventory-adjustments">清空</a>
          <a class="button" href="{html.escape(json_href)}">打开 JSON 接口</a>
          <a class="button" href="{html.escape(csv_href)}">导出 CSV</a>
        </div>
      </form>
      <p class="muted">优先显示手动库存台账；没有台账覆盖的旧记录会保留为当前库存快照。</p>
      <div class="table-wrap">
        <table class="admin-soldering-table admin-inventory-adjustments-table">
          <thead>
            <tr>
              <th>时间</th>
              <th>流水类型</th>
              <th>用户</th>
              <th>类别</th>
              <th>物料/名称</th>
              <th>数量变化</th>
              <th>调整前/后</th>
              <th>来源对象</th>
              <th>操作人</th>
              <th>状态</th>
              <th>原因/状态详情</th>
              <th>链接/ID</th>
            </tr>
          </thead>
          <tbody>{''.join(rows) or '<tr><td colspan="12">没有符合当前筛选条件的库存流水。</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    """
    return render_layout("库存调整审计", body, user, "库存调整")


def admin_purchase_orders_page(user, query=None):
    payload = admin_purchase_orders_payload(query or {})
    filters = payload["filters"]
    json_query = admin_soldering_shortage_query(filters)
    json_href = "/api/admin/purchase-orders" + (f"?{json_query}" if json_query else "")
    rows = []
    for order in payload["orders"]:
        for item in order.get("items", []):
            history_url = item.get("receipt_history_url") or ""
            receipt_list_url = item.get("receipt_list_url") or ""
            pending_quantity = parse_int(item.get("pending_stock_in_quantity"), 0)
            stocked_quantity = parse_int(item.get("stocked_quantity"), 0)
            reversed_quantity = parse_int(item.get("reversed_quantity"), 0)
            pending_actions = []
            for receipt in item.get("pending_stock_receipts") or []:
                finalize_url = receipt.get("finalize_url") or ""
                pending_actions.append(
                    f'<form class="inline-form" method="post" action="{html.escape(finalize_url)}">'
                    '<input type="hidden" name="confirm" value="true">'
                    f'<button class="button" type="submit">确认入库 {parse_int(receipt.get("received_quantity"), 0)}</button>'
                    "</form>"
                )
            reversal_actions = []
            for receipt in item.get("recent_receipts") or []:
                if not receipt.get("stock_finalized") or receipt.get("stock_reversed"):
                    continue
                reverse_url = receipt.get("reverse_url") or ""
                reversal_actions.append(
                    f'<form class="inline-form" method="post" action="{html.escape(reverse_url)}">'
                    '<input name="reason" required placeholder="反冲原因">'
                    f'<button class="button" type="submit">反冲 {parse_int(receipt.get("received_quantity"), 0)}</button>'
                    "</form>"
                )
            stock_status = item.get("stock_status") or item.get("receiving_status") or ""
            action_html = (
                f'<a class="button" href="{html.escape(history_url)}">收货历史</a> '
                f'<a class="button" href="{html.escape(receipt_list_url)}">收货列表</a> '
                + (" ".join(pending_actions) if pending_actions else "")
                + ((" " + " ".join(reversal_actions)) if reversal_actions else "")
            )
            rows.append(
                "<tr>"
                f"<td>{html.escape(str(order.get('created_at') or ''))}</td>"
                f"<td>{html.escape(str(order.get('id') or ''))}</td>"
                f"<td>{html.escape(str(order.get('created_by') or ''))}</td>"
                f"<td>{html.escape(str(order.get('upload_id') or ''))}</td>"
                f"<td>{html.escape(str(order.get('file_name') or ''))}</td>"
                f"<td>{html.escape(str(item.get('id') or ''))}</td>"
                f"<td>{html.escape(str(item.get('category') or ''))}</td>"
                f"<td>{html.escape(str(item.get('name') or ''))}</td>"
                f"<td>{parse_int(item.get('purchase_quantity'), 0)}</td>"
                f"<td>{parse_int(item.get('active_received_quantity'), 0)}</td>"
                f"<td>{stocked_quantity}</td>"
                f"<td>{reversed_quantity}</td>"
                f"<td>{pending_quantity}</td>"
                f"<td>{parse_int(item.get('remaining_quantity'), 0)}</td>"
                f"<td>{parse_int(item.get('voided_receipt_count'), 0)}</td>"
                f"<td>{html.escape(zh_status(stock_status))}</td>"
                f"<td>{action_html}</td>"
                "</tr>"
            )
    body = f"""
    <header class="page-head">
      <div><p class="eyebrow">后台采购单</p><h1>采购收货复核</h1></div>
      <a class="button" href="/admin/soldering">焊接后台</a>
    </header>
    <section class="metrics admin-soldering-summary">
      {metric_card("采购单", payload["summary"]["order_count"], "筛选结果")}
      {metric_card("采购项", payload["summary"]["item_count"], "筛选行")}
      {metric_card("有效收货", payload["summary"]["active_received_quantity_total"], "收货确认")}
      {metric_card("已入库", payload["summary"]["stocked_quantity_total"], "已写入库存")}
      {metric_card("已反冲", payload["summary"]["reversed_quantity_total"], "审计修正")}
      {metric_card("待入库", payload["summary"]["pending_stock_in_quantity_total"], "需显式确认")}
      {metric_card("剩余未收", payload["summary"]["remaining_quantity_total"], "尚未收货")}
      {metric_card("作废收货", payload["summary"]["voided_receipt_count"], "保留复核")}
    </section>
    <section class="panel admin-purchase-orders">
      <div class="panel-head">
        <h2>采购项收货明细</h2>
        <span>采购行保持不变；入库和反冲都需要显式操作。</span>
      </div>
      <form class="entry-form admin-soldering-shortage-filters" method="get" action="/admin/purchase-orders">
        <label>用户<input name="owner" value="{html.escape(filters['owner'])}" placeholder="用户名"></label>
        <label>状态<input name="status" value="{html.escape(filters['status'])}" placeholder="not_received, partially_received, received"></label>
        <label>采购单 ID<input name="order_id" value="{html.escape(filters['order_id'])}" placeholder="采购单 ID"></label>
        <label>BOM 上传<input name="upload_id" value="{html.escape(filters['upload_id'])}" placeholder="上传 ID"></label>
        <label class="wide">搜索<input name="q" value="{html.escape(filters['q'])}" placeholder="文件、用户、物料、原因"></label>
        <div class="wide form-actions">
          <button class="primary" type="submit">应用筛选</button>
          <a class="button" href="/admin/purchase-orders">清空</a>
          <a class="button" href="{html.escape(json_href)}">打开 JSON 接口</a>
        </div>
      </form>
      <div class="table-wrap">
        <table class="admin-soldering-table admin-purchase-orders-table">
          <thead>
            <tr>
              <th>下单时间</th>
              <th>采购单</th>
              <th>用户</th>
              <th>BOM 上传</th>
              <th>文件</th>
              <th>采购项</th>
              <th>类别</th>
              <th>物料</th>
              <th>采购数</th>
              <th>有效收货</th>
              <th>已入库</th>
              <th>已反冲</th>
              <th>待入库</th>
              <th>剩余</th>
              <th>作废收货</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>{''.join(rows) or '<tr><td colspan="17">没有符合当前筛选条件的采购项。</td></tr>'}</tbody>
        </table>
      </div>
    </section>
    """
    return render_layout("采购收货复核", body, user, "采购单")


def spare_bop_page(user, query=None, message="", error="", purchase_result=None):
    summary = competition_material_summary()
    recent_rows = competition_material_rows({}, limit=80)
    boms = competition_material_boms(limit=10)
    category_options = "".join(
        f'<option value="{html.escape(category)}">{html.escape(category)}</option>'
        for category in COMPETITION_MATERIAL_CATEGORIES
    )
    group_default = html.escape(str(user["team_group"] if "team_group" in user.keys() else "") or "未分组")
    bom_rows = "".join(
        "<tr>"
        f"<td>{html.escape(row['created_at'])}</td>"
        f"<td>{html.escape(row['title'])}</td>"
        f"<td>{html.escape(row['robot_name'] or '-')}</td>"
        f"<td>{html.escape(row['created_by'])}</td>"
        f"<td>{parse_int(row['item_count'], 0)}</td>"
        f"<td>{parse_int(row['total_quantity'], 0)}</td>"
        f"<td>{html.escape(row['original_name'] or '-')}</td>"
        "</tr>"
        for row in boms
    )
    body = f"""
    <header class="page-head">
      <div><p class="eyebrow">Competition Materials</p><h1>比赛物资备件管理</h1></div>
      <div class="page-head-actions">
        <a class="button" href="/inventory?warehouse=competition">搜索比赛备件仓库</a>
        <a class="button" href="/inventory?warehouse=main">搜索原仓库</a>
      </div>
    </header>
    <section class="metrics spare-bop-metrics">
      {metric_card("物资记录", summary["item_count"], "独立比赛物资库")}
      {metric_card("总数量", summary["total_quantity"], "机器人所需物资")}
      {metric_card("责任人", summary["owner_count"], "专人专管")}
      {metric_card("机器人 BOM", summary["bom_count"], "独立于原 BOM 对照")}
    </section>
    <section class="spare-bop-layout">
      <section class="form-panel spare-bop-form-panel">
        {flash_box(message, "ok")}{flash_box(error, "error")}
        <div class="panel-head"><h2>上传机器人必备模块 BOM</h2><span>独立保存，不进入原 BOM 对照</span></div>
        <form class="entry-form spare-bop-form" method="post" action="/spares/bom" enctype="multipart/form-data">
          <label>BOM 标题<input name="title" required placeholder="例如：英雄机器人必备模块 BOM"></label>
          <label>机器人名称<input name="robot_name" required placeholder="例如：机器人1 / 英雄 / 步兵"></label>
          <label>赛季/场次<input name="season" placeholder="例如：2026 RM 分区赛"></label>
          <label>责任人<input name="owner_name" required placeholder="负责该机器人 BOM 的人"></label>
          <label>责任人组别<input name="owner_group" value="{group_default}" placeholder="机械组 / 电控组 / 硬件组"></label>
          <label class="wide">BOM 文件<input type="file" name="competition_bom_file" accept=".csv,.xlsx,.txt" required></label>
          <div class="wide form-actions">
            <button class="primary" type="submit">上传比赛物资 BOM</button>
          </div>
        </form>
      </section>
      <section class="form-panel spare-bop-form-panel">
        <div class="panel-head"><h2>填写比赛物资表单</h2><span>机械、电控、耗材、贵重物品专用</span></div>
        <form class="entry-form spare-bop-form" method="post" action="/spares">
          <label>机器人/用途<input name="robot_name" placeholder="例如：机器人1、机器人2、通用备件"></label>
          <label>类别<select name="category">{category_options}</select></label>
          <label>物资名称<input name="name" required placeholder="例如：碳板、降压模块、云台电机"></label>
          <label>规格型号<input name="spec" placeholder="例如：3K 2mm、24V转5V 5A"></label>
          <label>数量<input name="quantity" type="number" min="1" value="1" required></label>
          <label>单位<input name="unit" value="个" placeholder="个 / 根 / 片 / 套"></label>
          <label>责任人<input name="owner_name" required placeholder="专管责任人姓名"></label>
          <label>责任人组别<input name="owner_group" value="{group_default}" placeholder="机械组 / 电控组 / 硬件组"></label>
          <label class="wide inline-field"><span>硬件器件对照原仓库</span><input type="checkbox" name="hardware_compare" value="1" checked></label>
          <label class="wide">备注<textarea name="note" rows="3" placeholder="采购来源、贵重物品编号、保管要求等"></textarea></label>
          <div class="wide form-actions">
            <button class="primary" type="submit">保存比赛物资</button>
          </div>
        </form>
      </section>
    </section>
    <section class="panel spare-bop-detail">
      <div class="panel-head">
        <div><h2>比赛物资专用仓库</h2><span>位置字段在这里替换为责任人和组别</span></div>
        <a class="button" href="/inventory?warehouse=competition">检索比赛物资</a>
      </div>
      {competition_material_table_html(recent_rows)}
    </section>
    <section class="panel spare-bop-detail">
      <div class="panel-head"><h2>最近上传的机器人 BOM</h2><span>与原 BOM 对照隔离</span></div>
      <div class="table-wrap"><table><thead><tr><th>时间</th><th>标题</th><th>机器人</th><th>上传人</th><th>行数</th><th>总数量</th><th>原文件</th></tr></thead><tbody>{bom_rows or '<tr><td colspan="7">暂无比赛物资 BOM。</td></tr>'}</tbody></table></div>
    </section>
    """
    return render_layout("比赛物资备件管理", body, user, "比赛备件")


def bom_page(user, message="", error="", result=None):
    with db() as conn:
        uploads = conn.execute(
            "SELECT * FROM bom_uploads ORDER BY datetime(created_at) DESC, id DESC LIMIT 12"
        ).fetchall()
    upload_parts = []
    for r in uploads:
        upload_parts.append(
            "<tr>"
            f"<td>{html.escape(r['created_at'])}</td><td>{html.escape(r['original_name'])}</td>"
            f"<td>{html.escape(r['uploaded_by'])}</td><td>{r['total_items']}</td><td>{r['total_quantity']}</td>"
            "</tr>"
        )
    upload_rows = "".join(upload_parts)
    result_html = ""
    if result:
        purchase_rows = result["purchase_rows"]
        purchase_table = "".join(
            f"<tr><td>{html.escape(str(r[0]))}</td><td>{html.escape(str(r[1]))}</td><td>{r[2]}</td><td>{r[3]}</td><td>{r[4]}</td><td>{html.escape(str(r[5]))}</td></tr>"
            for r in purchase_rows
        )
        result_html = f"""
        <section class="panel">
          <div class="panel-head"><h2>本次对照结果</h2></div>
          <p class="muted">已读取 {len(result["items"])} 个器件，发现 {len(purchase_rows)} 个库存缺口项。</p>
          <div class="table-wrap"><table><thead><tr><th>类别</th><th>名称</th><th>BOM需求</th><th>库存</th><th>缺口数量</th><th>原因</th></tr></thead><tbody>{purchase_table or '<tr><td colspan="6">库存满足本次 BOM。</td></tr>'}</tbody></table></div>
        </section>
        """
    body = f"""
    <header class="page-head"><div><p class="eyebrow">BOM Compare</p><h1>BOM 文件对照</h1></div></header>
    <section class="form-panel">
      {flash_box(message, "ok")}{flash_box(error, "error")}
      <form class="upload-box" method="post" action="/bom" enctype="multipart/form-data">
        <input id="bom-file" type="file" name="bom_file" accept=".csv,.xlsx,.txt" required>
        <label class="drop-zone" for="bom-file">
          <strong>拖拽或点击上传 BOM 文件</strong>
          <span>仅接受 CSV、XLSX、TXT。需要包含器件名称和数量列，类别列可选。</span>
        </label>
        <button class="primary" type="submit">读取并对照库存</button>
      </form>
    </section>
    {result_html}
    <section class="panel">
      <div class="panel-head"><h2>最近 BOM</h2><span>{len(uploads)} 条</span></div>
      <div class="table-wrap"><table><thead><tr><th>时间</th><th>文件</th><th>用户</th><th>器件数</th><th>总用量</th></tr></thead><tbody>{upload_rows or '<tr><td colspan="5">暂无 BOM 上传记录。</td></tr>'}</tbody></table></div>
    </section>
    """
    return render_layout("BOM对照", body, user, "BOM对照")


def bar_row(label, value, max_value):
    width = 0 if max_value <= 0 else max(6, min(100, int(value * 100 / max_value)))
    return f'<div class="bar-row"><span>{html.escape(str(label))}</span><div><i style="width:{width}%"></i></div><strong>{value}</strong></div>'


def inventory_insight_payload():
    config = get_config()
    low_threshold = parse_int(config.get("low_stock_threshold"), 0)
    with db() as conn:
        categories = conn.execute(
            """
            SELECT category, SUM(quantity) AS qty, COUNT(*) AS entries
            FROM inventory
            GROUP BY category
            ORDER BY qty DESC
            LIMIT 12
            """
        ).fetchall()
        totals = conn.execute(
            """
            SELECT COALESCE(SUM(quantity), 0) AS qty,
                   COUNT(*) AS entries,
                   COUNT(DISTINCT category) AS categories,
                   COUNT(DISTINCT location) AS locations
            FROM inventory
            """
        ).fetchone()
        shortage_rows = conn.execute(
            """
            SELECT name, category, SUM(purchase_quantity) AS qty, COUNT(*) AS times
            FROM purchase_items
            GROUP BY lower(name), lower(category)
            ORDER BY qty DESC, times DESC
            LIMIT 8
            """
        ).fetchall()
        low_rows = []
        if low_threshold > 0:
            low_rows = conn.execute(
                """
                SELECT name, category, SUM(quantity) AS qty
                FROM inventory
                GROUP BY lower(name), lower(category)
                HAVING qty <= ?
                ORDER BY qty ASC
                LIMIT 8
                """,
                (low_threshold,),
            ).fetchall()
    cache_count = len(load_lcsc_cache())
    top = categories[0] if categories else None
    tips = []
    if top:
        tips.append(f"库存最高类型是 {top['category']}，当前 {top['qty']} 件。")
    if shortage_rows:
        tips.append(f"BOM 缺口最高的是 {shortage_rows[0]['name']}，累计缺口 {shortage_rows[0]['qty']} 件。")
    if low_rows:
        tips.append(f"有 {len(low_rows)} 类器件低于低库存阈值，建议先核对仓位。")
    tips.append(f"LCSC 本地缓存已有 {cache_count} 条，可继续通过 C 编号补全。")
    return {
        "categories": [
            {"label": row["category"], "qty": int(row["qty"] or 0), "entries": int(row["entries"] or 0)}
            for row in categories
        ],
        "totals": {
            "qty": int(totals["qty"] or 0),
            "entries": int(totals["entries"] or 0),
            "categories": int(totals["categories"] or 0),
            "locations": int(totals["locations"] or 0),
        },
        "shortages": [
            {
                "name": row["name"],
                "category": row["category"],
                "qty": int(row["qty"] or 0),
                "times": int(row["times"] or 0),
            }
            for row in shortage_rows
        ],
        "low_stock": [
            {"name": row["name"], "category": row["category"], "qty": int(row["qty"] or 0)}
            for row in low_rows
        ],
        "tips": tips,
        "updated_at": now_text(),
    }


def component_function_tags(name, category="", note=""):
    text = f"{name} {category} {note}".lower()
    tags = []
    if any(token in text for token in ("dcdc", "dc-dc", "buck", "boost", "电源", "ldo", "pmic")):
        tags.extend(["开关电源", "电源管理"])
    if any(token in text for token in ("100nf", "0.1uf", "0.1µf", "0.1μf", "104", "退耦", "decoupling")):
        tags.extend(["退耦电容", "噪声抑制"])
    if any(token in text for token in ("tvs", "esd", "保险", "压敏", "保护")):
        tags.extend(["电路保护", "浪涌/静电"])
    if any(token in text for token in ("conn", "connector", "gh", "xh", "ph", "端子", "连接器")):
        tags.extend(["连接接口", "线束/端子"])
    if any(token in text for token in ("mos", "mosfet", "三极管", "晶体管")):
        tags.extend(["功率开关", "半导体开关"])
    if any(token in text for token in ("晶振", "crystal", "osc")):
        tags.extend(["时钟源", "振荡"])
    if any(token in text for token in ("电阻", "resistor", "kω", "kohm", "反馈", "采样")):
        tags.extend(["偏置/反馈", "采样网络"])
    if any(token in text for token in ("电容", "capacitor", "uf", "nf", "pf", "mlcc")):
        tags.extend(["滤波储能", "阻容基础"])
    if not tags:
        tags.append("库存器件")
    return list(dict.fromkeys(tags))[:4]


def knowledge_graph_payload(username=None):
    insights = inventory_insight_payload()
    nodes = [{"id": "inventory", "label": "库存核心", "kind": "core", "value": insights["totals"]["qty"] or 1}]
    edges = []
    function_nodes = {}
    with db() as conn:
        rows = conn.execute(
            """
            SELECT category, name, note, SUM(quantity) AS qty
            FROM inventory
            GROUP BY lower(category), lower(name), lower(note)
            ORDER BY qty DESC
            LIMIT 60
            """
        ).fetchall()
    for category in insights["categories"]:
        cid = "cat:" + normalize_key(category["label"])[:60]
        nodes.append({"id": cid, "label": category["label"], "kind": "category", "value": category["qty"]})
        edges.append({"from": "inventory", "to": cid, "weight": max(1, category["qty"])})
    for row in rows:
        comp_id = "part:" + normalize_key(row["name"])[:80]
        cat_id = "cat:" + normalize_key(row["category"])[:60]
        qty = int(row["qty"] or 0)
        nodes.append({"id": comp_id, "label": row["name"], "kind": "component", "value": qty, "category": row["category"]})
        edges.append({"from": cat_id, "to": comp_id, "weight": max(1, qty)})
        for tag in component_function_tags(row["name"], row["category"], row["note"]):
            fid = "fn:" + normalize_key(tag)
            if fid not in function_nodes:
                function_nodes[fid] = {"id": fid, "label": tag, "kind": "function", "value": 0}
            function_nodes[fid]["value"] += max(1, qty)
            edges.append({"from": comp_id, "to": fid, "weight": max(1, min(qty, 30))})
    nodes.extend(function_nodes.values())
    shortage_nodes = []
    for item in insights["shortages"][:10]:
        sid = "short:" + normalize_key(item["name"])[:80]
        shortage_nodes.append({"id": sid, "label": item["name"], "kind": "shortage", "value": item["qty"], "category": item["category"]})
        edges.append({"from": "inventory", "to": sid, "weight": max(1, item["qty"]), "alert": True})
    nodes.extend(shortage_nodes)
    if username:
        memory_id = "memory:" + normalize_key(username)
        nodes.append({"id": memory_id, "label": f"{username} 的助手记忆", "kind": "memory", "value": 10})
        edges.append({"from": "inventory", "to": memory_id, "weight": 8})
        history = assistant_history(username, limit=60)
        for idx, item in enumerate(history[-40:]):
            note_id = f"note:{idx}:{normalize_key(item['content'])[:50]}"
            title = item["content"].strip().replace("\n", " ")
            nodes.append(
                {
                    "id": note_id,
                    "label": title[:34] or item["role"],
                    "kind": "note",
                    "value": 3 + min(12, len(title) // 80),
                    "detail": item["content"][:1000],
                    "created_at": item["created_at"],
                }
            )
            edges.append({"from": memory_id, "to": note_id, "weight": 2})
            for term in extract_knowledge_terms(item["content"]):
                term_id = "term:" + normalize_key(term)
                nodes.append({"id": term_id, "label": term, "kind": "term", "value": 5, "detail": f"来自 {username} 的助手历史"})
                edges.append({"from": note_id, "to": term_id, "weight": 2})
    dedup = {}
    for node in nodes:
        dedup[node["id"]] = node
    return {
        "nodes": list(dedup.values()),
        "edges": edges,
        "tips": insights["tips"],
        "updated_at": now_text(),
    }


def inventory_assistant_context():
    insights = inventory_insight_payload()
    graph = knowledge_graph_payload()
    with db() as conn:
        recent = conn.execute(
            "SELECT category, name, quantity, location, note, created_at FROM inventory ORDER BY datetime(created_at) DESC, id DESC LIMIT 40"
        ).fetchall()
    lines = [
        f"库存总量: {insights['totals']['qty']} 件; 记录: {insights['totals']['entries']} 条; 分类: {insights['totals']['categories']} 个; 仓位: {insights['totals']['locations']} 个。",
        "高库存分类: "
        + "; ".join(f"{item['label']}={item['qty']}" for item in insights["categories"][:8]),
        "BOM 缺口: "
        + ("; ".join(f"{item['name']}({item['category']}) 缺口 {item['qty']}" for item in insights["shortages"][:8]) or "暂无"),
        "知识网络功能节点: "
        + "; ".join(node["label"] for node in graph["nodes"] if node.get("kind") == "function")[:1000],
        "最近入库: "
        + "; ".join(
            f"{row['name']} / {row['category']} / {row['quantity']} / {row['location']} / {row['note']}"
            for row in recent[:20]
        ),
    ]
    return "\n".join(lines)


def user_memory_context(username):
    history = assistant_history(username, limit=24)
    if not history:
        return "该用户暂无历史助手记忆。"
    chunks = []
    for item in history[-12:]:
        content = item["content"].replace("\n", " ").strip()
        chunks.append(f"{item['created_at']} {item['role']}: {content[:420]}")
    return "\n".join(chunks)


def user_knowledge_folder(username):
    folder = USER_KNOWLEDGE_DIR / safe_name(username or "user")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def assistant_history(username, limit=80):
    with db() as conn:
        rows = conn.execute(
            """
            SELECT role, content, meta_json, created_at
            FROM assistant_messages
            WHERE username = ?
            ORDER BY datetime(created_at) DESC, id DESC
            LIMIT ?
            """,
            (username, limit),
        ).fetchall()
    history = []
    for row in reversed(rows):
        try:
            meta = json.loads(row["meta_json"] or "{}")
        except Exception:
            meta = {}
        history.append(
            {
                "role": row["role"],
                "content": row["content"],
                "meta": meta,
                "created_at": row["created_at"],
            }
        )
    return history


def extract_knowledge_terms(text):
    raw = str(text or "")
    terms = []
    patterns = [
        r"\bC\d{4,}\b",
        r"\b\d+(?:\.\d+)?\s*(?:pF|nF|uF|µF|μF|V|mW|W|Ω|kΩ|MΩ)\b",
        r"\b(?:DCDC|DC-DC|Buck|Boost|LDO|PMIC|MOSFET|TVS|ESD)\b",
    ]
    for pattern in patterns:
        terms.extend(match.group(0) for match in re.finditer(pattern, raw, re.I))
    keywords = [
        "退耦", "滤波", "开关电源", "保护", "采样", "反馈", "替代料", "封装", "数据手册",
        "连接器", "电阻", "电容", "电感", "晶振", "二极管", "MOS", "BOM", "BOM缺口",
    ]
    for word in keywords:
        if word.lower() in raw.lower():
            terms.append(word)
    return list(dict.fromkeys(term.strip() for term in terms if term.strip()))[:18]


def write_user_knowledge_docs(username):
    folder = user_knowledge_folder(username)
    history = assistant_history(username, limit=200)
    write_data_text(
        folder / "assistant_history.json",
        json.dumps(history, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [f"# {username} 的库存助手知识文档", "", f"更新时间：{now_text()}", ""]
    for item in history[-80:]:
        terms = extract_knowledge_terms(item["content"])
        lines.append(f"## {item['created_at']} / {item['role']}")
        if terms:
            lines.append("标签：" + "、".join(terms))
        lines.append("")
        lines.append(item["content"].strip())
        lines.append("")
    write_data_text(folder / "knowledge_notes.md", "\n".join(lines), encoding="utf-8")


def save_assistant_message(username, role, content, meta=None):
    created_at = now_text()
    with db() as conn:
        conn.execute(
            """
            INSERT INTO assistant_messages (username, role, content, meta_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (username, role, content, json.dumps(meta or {}, ensure_ascii=False), created_at),
        )
    write_user_knowledge_docs(username)


def assistant_base_url(endpoint):
    url = str(endpoint or "").strip().rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")]
    if url.endswith("/models"):
        url = url[: -len("/models")]
    return url.rstrip("/")


def assistant_chat_url(endpoint):
    base = assistant_base_url(endpoint)
    if not base:
        return ""
    return base + "/chat/completions"


def assistant_models_url(endpoint):
    base = assistant_base_url(endpoint)
    if not base:
        return ""
    return base + "/models"


def assistant_headers(api_key="", json_body=True):
    headers = {}
    if json_body:
        headers["Content-Type"] = "application/json"
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return headers


def assistant_http_json(url, api_key="", body=None, timeout=30):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers=assistant_headers(api_key, json_body=body is not None),
        method="GET" if body is None else "POST",
    )
    try:
        with safe_external_urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        detail = raw.strip()
        try:
            parsed = json.loads(detail)
            detail = parsed.get("error", {}).get("message") if isinstance(parsed.get("error"), dict) else parsed.get("error", detail)
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail[:800]}") from exc
    raw = raw.strip()
    if not raw:
        raise RuntimeError("API 返回空内容，请检查地址是否是兼容 OpenAI 的接口。")
    try:
        return json.loads(raw)
    except Exception as exc:
        raise RuntimeError(f"API 返回的不是 JSON：{raw[:500]}") from exc


def extract_assistant_text(data):
    if isinstance(data, dict):
        choices = data.get("choices") or []
        if choices and isinstance(choices[0], dict):
            message = choices[0].get("message") or {}
            content = message.get("content") if isinstance(message, dict) else ""
            if isinstance(content, list):
                parts = []
                for part in content:
                    if isinstance(part, dict):
                        parts.append(str(part.get("text") or part.get("content") or ""))
                    else:
                        parts.append(str(part))
                content = "\n".join(part for part in parts if part)
            return str(content or choices[0].get("text") or "")
        return str(data.get("answer") or data.get("content") or json.dumps(data, ensure_ascii=False))
    return json.dumps(data, ensure_ascii=False)


def inventory_image_error_message(exc):
    detail = str(exc or "").strip()
    lowered = detail.lower()
    if "image_url" in lowered and ("expected `text`" in lowered or "expected text" in lowered or "unknown variant" in lowered):
        return (
            "当前配置的图片识别 API 不支持直接接收图片。"
            "请在后台把“入库拍照识别 API”改成支持视觉/图片输入的模型接口，"
            "或先接入 OCR，把图片转成文字后再交给当前文本模型分析。"
        )
    if "api 返回空内容" in detail or "not json" in lowered:
        return detail
    if detail.startswith("HTTP 401") or detail.startswith("HTTP 403"):
        return "图片识别 API 鉴权失败，请检查后台配置的 API Key、接口地址和账号权限。"
    if detail.startswith("HTTP 404"):
        return "图片识别 API 地址或模型路径不存在，请检查后台配置的接口地址。"
    if detail.startswith("HTTP 429"):
        return "图片识别 API 请求过于频繁或额度不足，请稍后重试或检查服务额度。"
    return detail or "图片识别 API 调用失败，请检查后台配置。"


def call_inventory_image_recognition(image_bytes, mime_type, filename, user):
    cfg = get_image_recognition_config()
    if cfg.get("enabled") != "1":
        raise RuntimeError("图片识别 API 尚未启用，请管理员先在后台配置。")
    endpoint = assistant_chat_url(cfg.get("endpoint", ""))
    if not endpoint.startswith(("http://", "https://")):
        raise RuntimeError("图片识别 API 地址无效，只支持 http 或 https。")
    model = cfg.get("model", "").strip()
    if not model:
        raise RuntimeError("图片识别模型名为空。")
    encoded = base64.b64encode(image_bytes).decode("ascii")
    prompt = (
        "请识别这张入库图片中的电子元器件完整信息。只返回 JSON，不要返回 Markdown。"
        "JSON 格式：{\"items\":[{\"lcsc_code\":\"\",\"category\":\"\",\"name\":\"\","
        "\"value_spec\":\"\",\"package\":\"\",\"voltage\":\"\",\"brand\":\"\",\"quantity\":1,"
        "\"location\":\"\",\"product_url\":\"\",\"note\":\"\",\"confidence\":0.0}],"
        "\"summary\":\"\"}。数量请读取包装、标签、手写数量或图片中可见数量；不确定时填 1。"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": cfg.get("system_prompt") or DEFAULT_IMAGE_RECOGNITION_CONFIG["system_prompt"]},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{encoded}"}},
                ],
            },
        ],
        "temperature": float(cfg.get("temperature") or 0),
        "max_tokens": clamp_int(cfg.get("max_tokens"), 900, 100, 4000),
    }
    data = assistant_http_json(endpoint, cfg.get("api_key", ""), body, parse_int(cfg.get("timeout"), 45))
    answer = extract_assistant_text(data)
    parsed = extract_json_object(answer)
    items = normalize_inventory_image_result(parsed)
    return {
        "items": items,
        "raw_text": answer[:3000],
        "model": model,
        "filename": filename,
        "mime_type": mime_type,
        "updated_at": now_text(),
    }


def fetch_assistant_models(endpoint, api_key="", timeout=30):
    url = assistant_models_url(endpoint)
    if not url.startswith(("http://", "https://")):
        raise RuntimeError("模型列表地址无效，请填写 http 或 https API 地址。")
    data = assistant_http_json(url, api_key, None, timeout)
    raw_models = data.get("data", data if isinstance(data, list) else [])
    models = []
    for item in raw_models:
        if not isinstance(item, dict):
            continue
        model_id = item.get("id") or item.get("name")
        if not model_id:
            continue
        models.append(
            {
                "id": model_id,
                "name": item.get("name") or model_id,
                "context_length": item.get("context_length") or item.get("contextLength") or "",
                "description": (item.get("description") or "")[:240],
            }
        )
    return models


def assistant_max_tokens(cfg):
    requested = parse_int(cfg.get("max_tokens"), 900)
    clamped = max(64, min(requested, 1200))
    return clamped, requested != clamped


def call_inventory_assistant(question, user):
    cfg = get_assistant_config()
    if cfg.get("enabled") != "1":
        return {"error": "智能助手 API 尚未启用，请管理员先在后台配置。"}
    endpoint = assistant_chat_url(cfg.get("endpoint", ""))
    if not endpoint.startswith(("http://", "https://")):
        return {"error": "API 端口/地址无效，只支持 http 或 https。"}
    context = inventory_assistant_context()
    memory = user_memory_context(user["username"])
    system_prompt = cfg.get("system_prompt") or DEFAULT_ASSISTANT_CONFIG["system_prompt"]
    max_tokens, clamped = assistant_max_tokens(cfg)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": "以下是后端生成的库存上下文，只能围绕它和电子器件知识回答。\n" + context},
        {"role": "system", "content": "以下是当前用户自己的历史助手记忆，用于延续知识网络，不要泄露其他用户内容。\n" + memory},
        {"role": "user", "content": question[:3000]},
    ]
    body = {
        "model": cfg.get("model") or None,
        "messages": messages,
        "temperature": float(cfg.get("temperature") or 0.2),
        "max_tokens": max_tokens,
    }
    body = {k: v for k, v in body.items() if v not in ("", None)}
    try:
        data = assistant_http_json(endpoint, cfg.get("api_key", ""), body, parse_int(cfg.get("timeout"), 30))
        answer = ""
        if isinstance(data, dict):
            choices = data.get("choices") or []
            if choices:
                message = choices[0].get("message") or {}
                answer = message.get("content") or choices[0].get("text") or ""
            answer = answer or data.get("answer") or data.get("content") or json.dumps(data, ensure_ascii=False)
        else:
            answer = json.dumps(data, ensure_ascii=False)
        note = "已自动把 max_tokens 限制到 1200，避免上游 API 因额度或输出过长拒绝请求。" if clamped else ""
        return {"answer": answer, "note": note, "endpoint": endpoint, "updated_at": now_text()}
    except Exception as exc:
        return {"error": f"助手 API 调用失败：{exc}"}


def analytics_page(user):
    cleanup_analytics_noise()
    config = get_config()
    demand_threshold = parse_int(config.get("hot_demand_threshold"), 50)
    search_threshold = parse_int(config.get("hot_search_threshold"), 3)
    with db() as conn:
        hot_purchase = conn.execute(
            """
            SELECT name, category, SUM(purchase_quantity) AS qty, COUNT(*) AS times
            FROM purchase_items
            GROUP BY lower(name), lower(category)
            HAVING qty >= ? OR times >= 2
            ORDER BY qty DESC, times DESC
            LIMIT 12
            """,
            (demand_threshold,),
        ).fetchall()
        hot_search = conn.execute(
            """
            SELECT term, COUNT(*) AS hits
            FROM search_events
            GROUP BY lower(term)
            HAVING hits >= ?
            ORDER BY hits DESC
            LIMIT 12
            """,
            (search_threshold,),
        ).fetchall()
        ranks = conn.execute(
            """
            SELECT uploaded_by, SUM(quantity) AS qty, COUNT(DISTINCT upload_id) AS bom_count
            FROM bom_items
            GROUP BY uploaded_by
            ORDER BY qty DESC
            """
        ).fetchall()
    max_purchase = max([r["qty"] for r in hot_purchase], default=0)
    max_search = max([r["hits"] for r in hot_search], default=0)
    purchase_bars = "".join(bar_row(f"{r['name']} / {r['category']}", r["qty"], max_purchase) for r in hot_purchase)
    search_bars = "".join(bar_row(r["term"], r["hits"], max_search) for r in hot_search)
    if user["role"] == "admin":
        rank_rows = "".join(
            f"<tr><td>{idx}</td><td>{html.escape(r['uploaded_by'])}</td><td>{r['qty']}</td><td>{r['bom_count']}</td></tr>"
            for idx, r in enumerate(ranks, start=1)
        )
    else:
        own_index = next((idx for idx, r in enumerate(ranks, start=1) if r["uploaded_by"] == user["username"]), None)
        own_row = next((r for r in ranks if r["uploaded_by"] == user["username"]), None)
        rank_rows = (
            f"<tr><td>{own_index}</td><td>{html.escape(user['username'])}</td><td>{own_row['qty']}</td><td>{own_row['bom_count']}</td></tr>"
            if own_row
            else '<tr><td colspan="4">你还没有上传 BOM。</td></tr>'
        )
    body = f"""
    <header class="page-head"><div><p class="eyebrow">Analytics</p><h1>统计排行</h1></div></header>
    <section class="panel analytics-hero">
      <div>
        <p class="eyebrow">3D Inventory Map</p>
        <h2>库存类型立体分析</h2>
      </div>
      <div id="inventory-3d" class="inventory-3d" data-source="/api/inventory_insights"></div>
      <div id="inventory-tips" class="insight-list"></div>
    </section>
    <section class="analytics-grid">
      <div class="panel"><div class="panel-head"><h2>高需求缺料器件</h2><span>来自 BOM 缺口记录</span></div>{purchase_bars or '<div class="empty">暂无达到阈值的器件</div>'}</div>
      <div class="panel"><div class="panel-head"><h2>高频搜索器件</h2><span>来自检索记录</span></div>{search_bars or '<div class="empty">暂无高频搜索</div>'}</div>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>用户 BOM 用量排行</h2><span>{"管理员可见全部" if user["role"] == "admin" else "普通用户仅显示自己"}</span></div>
      <div class="table-wrap"><table><thead><tr><th>排名</th><th>用户</th><th>器件总用量</th><th>BOM数量</th></tr></thead><tbody>{rank_rows}</tbody></table></div>
    </section>
    <script src="/static/analytics3d.js"></script>
    """
    return render_layout("统计排行", body, user, "统计排行")


def assistant_page(user):
    cfg = get_assistant_config(mask_key=True)
    enabled = cfg.get("enabled") == "1"
    body = f"""
    <header class="page-head glass-hero">
      <div><p class="eyebrow">Inventory Copilot</p><h1>库存智能助手</h1></div>
      <div class="hero-glass-stats"><span>API</span><strong>{"已启用" if enabled else "未配置"}</strong></div>
    </header>
    <section class="assistant-console">
      <div class="panel chat-panel">
        <div class="panel-head"><h2>和库存助手对话</h2><span>历史会保存到个人知识文档</span></div>
        <div id="assistant-thread" class="assistant-thread">
          <div class="assistant-message bot welcome">我会基于当前库存、BOM 缺口、LCSC 信息、你的历史问答和器件知识网络回答。</div>
        </div>
        <div id="assistant-steps" class="assistant-steps"></div>
        <form id="assistant-form" class="assistant-form">
          <textarea id="assistant-question" rows="3" placeholder="例如：哪些 100nF 电容更可能用于 DCDC 退耦？当前缺料会影响哪些电源保护链路？"></textarea>
          <button class="primary" type="submit">发送</button>
        </form>
      </div>
      <div class="panel graph-panel">
        <div class="panel-head"><h2>文字知识网络</h2><span>点击节点查看详情</span></div>
        <div id="knowledge-text-network" class="knowledge-text-network" data-source="/api/knowledge_graph"></div>
        <div id="graph-detail" class="graph-detail">点击上方知识节点，在这里查看详情。你的助手历史会成为个人知识关联节点。</div>
      </div>
    </section>
    <script src="/static/knowledge_text.js"></script>
    <script src="/static/assistant.js"></script>
    """
    return render_layout("智能助手", body, user, "智能助手")


def reports_page(user, message=""):
    files = sorted(REPORTS_DIR.glob("*.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    items = "".join(
        f'<li><span>{html.escape(p.name)}</span><a href="/download?type=reports&name={urllib.parse.quote(p.name)}">下载</a></li>'
        for p in files
    ) or "<li><span>暂无报表，可点击立即生成</span></li>"
    csv_link = f'/download?type=forms&name={urllib.parse.quote(CSV_PATH.name)}'
    lcsc_link = f'/download?type=lcsc&name={urllib.parse.quote(LCSC_CACHE_CSV.name)}'
    body = f"""
    <header class="page-head"><div><p class="eyebrow">Reports</p><h1>仓检报表</h1></div></header>
    <section class="panel">
      {flash_box(message, "ok")}
      <form method="post" action="/reports/run" class="inline-actions">
        <button class="primary" type="submit">立即生成周检报表</button>
        <button class="button" type="submit" formaction="/lcsc/refresh">刷新 LCSC 缓存</button>
        <a class="button" href="{csv_link}">下载原始表单 CSV</a>
        <a class="button" href="{lcsc_link}">下载 LCSC 商品缓存</a>
      </form>
      <p class="muted">后台会在每周日自动汇总近 7 天入库数据，并同时生成全量库存对照表。</p>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>文件</h2><span>{len(files)} 个 XLSX</span></div>
      <ul class="file-list">{items}</ul>
    </section>
    """
    return render_layout("仓检报表", body, user, "仓检报表")


def users_page(user, message="", error=""):
    if user["role"] != "admin":
        return render_layout("无权限", '<section class="panel">只有管理员可以管理用户。</section>', user)
    with db() as conn:
        users = conn.execute("SELECT username, role, team_group, created_at FROM users ORDER BY id").fetchall()
    rows = "".join(
        f"<tr><td>{html.escape(r['username'])}</td><td>{html.escape(r['team_group'] or '未分组')}</td><td>{html.escape(r['role'])}</td><td>{html.escape(r['created_at'])}</td></tr>"
        for r in users
    )
    body = f"""
    <header class="page-head"><div><p class="eyebrow">Accounts</p><h1>用户系统</h1></div></header>
    <section class="form-panel">
      {flash_box(message, "ok")}{flash_box(error, "error")}
      <form class="entry-form compact" method="post" action="/users">
        <label>新账号<input name="username" required></label>
        <label>密码<input type="password" name="password" minlength="6" required></label>
        <label>组别{team_group_select()}</label>
        <label>角色<select name="role"><option value="user">普通用户</option><option value="admin">管理员</option></select></label>
        <button class="primary" type="submit">创建用户</button>
      </form>
    </section>
    <section class="panel"><div class="panel-head"><h2>账号列表</h2></div>
      <div class="table-wrap"><table><thead><tr><th>账号</th><th>组别</th><th>角色</th><th>创建时间</th></tr></thead><tbody>{rows}</tbody></table></div>
    </section>
    """
    return render_layout("用户", body, user, "用户")


def workflow_member_picker_html(users, current_user):
    groups = {group: [] for group in TEAM_GROUPS}
    groups.setdefault("未分组", [])
    for item in users:
        if item["username"] == current_user["username"]:
            continue
        group = item.get("team_group") or "未分组"
        groups.setdefault(group, []).append(item)
    blocks = []
    for group, members in groups.items():
        if not members:
            continue
        people = "".join(
            f"""
            <label class="member-option">
              <input type="checkbox" name="collaborator_{member['id']}" value="1">
              <span><strong>{html.escape(member['username'])}</strong><small>{html.escape(group)} · {html.escape(member['role'])}</small></span>
            </label>
            """
            for member in members
        )
        blocks.append(f'<section class="member-group"><h3>{html.escape(group)}</h3><div>{people}</div></section>')
    return "".join(blocks) or '<div class="empty">暂无可选择成员，管理员可以先创建组员账号。</div>'


def workflow_file_badge(file_meta):
    if not isinstance(file_meta, dict) or not file_meta.get("stored_filename"):
        return '<span class="muted">无附件</span>'
    label = html.escape(file_meta.get("original_filename") or file_meta.get("stored_filename") or "附件")
    url = file_meta.get("download_url") or f"/download?type=workflow&name={urllib.parse.quote(file_meta.get('stored_filename', ''))}"
    return f'<a class="workflow-file-link" href="{html.escape(url)}">{label}</a>'


def workflow_project_cards_html(projects):
    if not projects:
        return '<section class="panel workflow-empty"><h2>暂无工作流</h2><p>创建第一个项目后，组内成员和协作者会在这里看到实时进度。</p></section>'
    cards = []
    status_options = "".join(
        f'<option value="{key}">{html.escape(label)}</option>' for key, label in WORKFLOW_STATUS_LABELS.items()
    )
    for project in projects:
        collaborator_text = "、".join(project.get("collaborators") or []) or "未指定"
        ai_plan = project.get("ai_plan") if isinstance(project.get("ai_plan"), dict) else {}
        thinking = "".join(f"<li>{html.escape(step)}</li>" for step in (ai_plan.get("thinking") or [])[:8])
        stages = []
        for stage in project.get("stages", []):
            stage_status_options = status_options.replace(
                f'value="{html.escape(stage.get("status", ""))}"',
                f'value="{html.escape(stage.get("status", ""))}" selected',
            )
            stages.append(
                f"""
                <article class="workflow-stage-card" style="--stage-color:{html.escape(stage.get('color') or '#0a84ff')}">
                  <div class="workflow-stage-top">
                    <span>{html.escape(stage.get('category') or 'stage')}</span>
                    <strong>{html.escape(stage.get('title') or '')}</strong>
                    <em>{html.escape(stage.get('status_label') or '')}</em>
                  </div>
                  <p>{html.escape(stage.get('detail') or '')}</p>
                  <div class="workflow-stage-meta">
                    <span>负责人 {html.escape(stage.get('owner_username') or '-')}</span>
                    <span>第 {stage.get('start_day', 1)}-{stage.get('end_day', 1)} 天</span>
                    <span>{stage.get('progress', 0)}%</span>
                  </div>
                  <form class="workflow-update-form" method="post" action="/workflow/update" enctype="multipart/form-data">
                    <input type="hidden" name="workflow_id" value="{html.escape(project['workflow_id'])}">
                    <input type="hidden" name="stage_id" value="{stage['id']}">
                    <label>状态<select name="status">{stage_status_options}</select></label>
                    <label>进度<input type="range" min="0" max="100" name="progress" value="{stage.get('progress', 0)}"></label>
                    <label>番茄分钟<input type="number" min="0" max="240" name="pomodoro_minutes" value="25"></label>
                    <label class="wide">本次记录<textarea name="note" rows="2" placeholder="记录当前动作、问题或结论"></textarea></label>
                    <label class="wide">成果文件/截图<input type="file" name="result_file"></label>
                    <button class="primary" type="submit">写入进度</button>
                  </form>
                </article>
                """
            )
        updates = "".join(
            f"""
            <li>
              <strong>{html.escape(update.get('username') or '')}</strong>
              <span>{html.escape(update.get('created_at') or '')}</span>
              <p>{html.escape(update.get('note') or update.get('update_type') or '')}</p>
              {workflow_file_badge(update.get('file')) if update.get('file') else ''}
            </li>
            """
            for update in (project.get("updates") or [])[:6]
        )
        cards.append(
            f"""
            <section class="panel workflow-project-card" data-workflow-id="{html.escape(project['workflow_id'])}">
              <div class="workflow-project-head">
                <div>
                  <p class="eyebrow">{html.escape(project.get('owner_group') or '未分组')}</p>
                  <h2>{html.escape(project.get('name') or '')}</h2>
                  <p>{html.escape(project.get('detail') or '')}</p>
                </div>
                <div class="workflow-progress-pill"><strong>{project.get('progress', 0)}%</strong><span>{html.escape(project.get('status_label') or '')}</span></div>
              </div>
              <div class="workflow-project-meta">
                <span>负责人 {html.escape(project.get('owner_username') or '')}</span>
                <span>协作 {html.escape(collaborator_text)}</span>
                <span>周期 {project.get('duration_days', 0)} 天</span>
                <span>截止 {html.escape(project.get('due_at') or '')[:10]}</span>
                <span>附件 {workflow_file_badge(project.get('file'))}</span>
              </div>
              <div class="workflow-ai-log">
                <strong>AI 规划过程</strong>
                <p>{html.escape(ai_plan.get('summary') or '等待规划记录')}</p>
                <ol>{thinking}</ol>
              </div>
              <div class="workflow-stage-grid">{''.join(stages)}</div>
              <details class="workflow-updates"><summary>最近工作记录</summary><ul>{updates or '<li>暂无记录</li>'}</ul></details>
            </section>
            """
        )
    return "".join(cards)


def workflow_page(user, message="", error=""):
    users = workflow_user_options()
    projects = workflow_visible_projects(user)
    active_count = sum(1 for project in projects if project.get("status") in ("active", "blocked", "review"))
    avg_progress = round(sum(project.get("progress", 0) for project in projects) / len(projects)) if projects else 0
    body = f"""
    <header class="page-head workflow-head">
      <div>
        <p class="eyebrow">Workflow</p>
        <h1>工作流</h1>
        <p>组内成员和同一项目协作者共享项目文档、阶段进度、番茄记录和成果文件。</p>
      </div>
      <div class="workflow-head-metrics">
        <span><strong>{len(projects)}</strong> 可见项目</span>
        <span><strong>{active_count}</strong> 进行中</span>
        <span><strong>{avg_progress}%</strong> 平均完成</span>
      </div>
    </header>
    {flash_box(message, "ok")}{flash_box(error, "error")}
    <section class="workflow-console">
      <form class="panel workflow-form" method="post" action="/workflow" enctype="multipart/form-data">
        <div class="panel-head"><h2>创建工作过程计划</h2><span>{html.escape(user['team_group'] or '未分组')}</span></div>
        <div class="workflow-form-grid">
          <label>项目名称<input name="project_name" maxlength="120" required placeholder="例如：电源板视觉检测联调"></label>
          <label>项目持续时间（天）<input name="duration_days" type="number" min="1" max="365" value="14" required></label>
          <label class="wide">项目详细内容<textarea name="detail" rows="5" required placeholder="写清目标、模块、输入文件、预期交付和约束"></textarea></label>
          <label class="wide">项目文件（可选）<input type="file" name="project_file"></label>
        </div>
        <section class="member-picker">
          <div><h3>选择协作成员</h3><p>同组成员默认可见，勾选后跨组成员也能看到并更新该工作流。</p></div>
          <div class="member-picker-grid">{workflow_member_picker_html(users, user)}</div>
        </section>
        <section class="workflow-api-panel">
          <div><h3>项目 AI API（可选）</h3><p>填写后仅用于本次规划，不会保存密钥。也可以使用后台已配置的助手 API。</p></div>
          <div class="workflow-api-grid">
            <label>API 地址<input name="ai_endpoint" id="workflow-ai-endpoint" placeholder="https://api.example.com/v1"></label>
            <label>API Key<input name="ai_api_key" id="workflow-ai-key" type="password" autocomplete="off" placeholder="sk-..."></label>
            <label>模型<input name="ai_model" id="workflow-ai-model" list="workflow-models" placeholder="先获取模型或手动填写"></label>
            <datalist id="workflow-models"></datalist>
            <button class="button" type="button" id="workflow-model-probe">获取模型</button>
          </div>
          <div id="workflow-api-steps" class="api-step-log">等待模型探测。</div>
        </section>
        <button class="primary workflow-submit" type="submit">生成工作流</button>
      </form>
      <section class="panel workflow-board-panel">
        <div class="panel-head"><h2>3D 智能看板</h2><span>滚轮限幅缩放，点击阶段查看详情</span></div>
        <div id="workflow-3d-board" class="workflow-3d-board" data-source="/api/workflows">
          <div class="workflow-core-node"><strong>{avg_progress}%</strong><span>总完成度</span></div>
        </div>
        <div id="workflow-detail" class="workflow-detail">点击项目阶段查看具体周期、负责人和最近状态。</div>
      </section>
    </section>
    <section class="workflow-projects">
      {workflow_project_cards_html(projects)}
    </section>
    <script src="/static/workflow3d.js"></script>
    """
    return render_layout("工作流", body, user, "工作流")


def image_recognition_config_form_html(image_cfg):
    return f"""
      <form class="entry-form" method="post" action="/admin/image-recognition">
        <label>启用图片识别<select name="enabled">
          <option value="0" {"selected" if image_cfg['enabled'] != '1' else ""}>关闭</option>
          <option value="1" {"selected" if image_cfg['enabled'] == '1' else ""}>启用</option>
        </select></label>
        <label>API 地址<input name="endpoint" value="{html.escape(image_cfg['endpoint'])}" placeholder="例如：https://api.openai.com/v1"></label>
        <label>视觉模型<input name="model" value="{html.escape(image_cfg['model'])}" placeholder="支持图片输入的模型"></label>
        <label>API Key<input name="api_key" value="{html.escape(image_cfg['api_key'])}" placeholder="留空则保持原 Key"></label>
        <label>温度<input name="temperature" type="number" step="0.1" min="0" max="2" value="{html.escape(image_cfg['temperature'])}"></label>
        <label>最大输出 Token<input name="max_tokens" type="number" min="100" max="4000" value="{html.escape(image_cfg['max_tokens'])}"></label>
        <label>请求超时秒数<input name="timeout" type="number" min="5" max="180" value="{html.escape(image_cfg['timeout'])}"></label>
        <label class="wide">识别提示词<textarea name="system_prompt" rows="7">{html.escape(image_cfg['system_prompt'])}</textarea></label>
        <button class="primary wide" type="submit">保存图片识别 API 配置</button>
      </form>
    """


def image_recognition_config_page(user, message="", error=""):
    if user["role"] != "admin":
        return render_layout("无权限", '<section class="panel">只有管理员可以配置入库图片识别 API。</section>', user)
    image_cfg = get_image_recognition_config(mask_key=True)
    body = f"""
    <header class="page-head"><div><p class="eyebrow">Vision API</p><h1>入库图片识别 API</h1></div><a class="button" href="/admin">返回后台</a></header>
    <section class="form-panel">
      {flash_box(message, "ok")}{flash_box(error, "error")}
      <div class="panel-head"><h2>拍照识别配置</h2><span>保存到 data/image_recognition_config.json</span></div>
      {image_recognition_config_form_html(image_cfg)}
      <p class="muted">前端只把图片上传到本站后端；API Key 不会下发到浏览器。接口需兼容 OpenAI Chat Completions 的图片输入格式。</p>
    </section>
    """
    return render_layout("入库图片识别 API", body, user, "后台配置")


def admin_page(user, message="", error=""):
    if user["role"] != "admin":
        return render_layout("无权限", '<section class="panel">只有管理员可以访问后台配置。</section>', user)
    stored_config = get_config()
    config = runtime_config_for_display(stored_config)
    ui = ui_theme_values(config)
    assistant_cfg = get_assistant_config(mask_key=True)
    image_cfg = get_image_recognition_config(mask_key=True)
    with db() as conn:
        logs = conn.execute(
            "SELECT * FROM access_logs ORDER BY datetime(created_at) DESC, id DESC LIMIT 80"
        ).fetchall()
    log_rows = "".join(
        f"<tr><td>{html.escape(r['created_at'])}</td><td>{html.escape(r['username'] or '-')}</td><td>{html.escape(r['method'])}</td><td>{html.escape(r['path'])}</td><td>{r['status']}</td><td>{html.escape(r['ip'])}</td></tr>"
        for r in logs
    )
    body = f"""
    <header class="page-head"><div><p class="eyebrow">Admin</p><h1>后台配置</h1></div></header>
    <section class="form-panel">
      {flash_box(message, "ok")}{flash_box(error, "error")}
      <form class="entry-form" method="post" action="/admin">
        <label class="wide">允许访问 Host 白名单<input name="allowed_hosts" value="{html.escape(config.get('allowed_hosts', ''))}" placeholder="warehouse.example.com,192.168.1.20"></label>
        <label class="wide">外部 API 域名白名单<input name="allowed_api_hosts" value="{html.escape(config.get('allowed_api_hosts', ''))}" placeholder="api.openai.com,api.deepseek.com,openrouter.ai,www.szlcsc.com,easyeda.com,modules.easyeda.com"></label>
        <label>站点名称<input name="site_name" value="{html.escape(config['site_name'])}"></label>
        <label>监听主机<input name="host" value="{html.escape(config['host'])}"></label>
        <label>网站端口<input name="port" type="number" min="1" max="65535" value="{html.escape(config['port'])}"></label>
        <label class="wide">服务器数据目录<input name="data_dir" value="{html.escape(config['data_dir'])}"></label>
        <label>固定公网地址<input name="public_url" value="{html.escape(config['public_url'])}" placeholder="https://example.com"></label>
        <label>内网穿透<select name="tunnel_mode">
          <option value="auto" {"selected" if config['tunnel_mode'] == 'auto' else ""}>显式开启</option>
          <option value="off" {"selected" if config['tunnel_mode'] == 'off' else ""}>关闭</option>
        </select></label>
        <label>允许注册<select name="allow_registration">
          <option value="1" {"selected" if config['allow_registration'] == '1' else ""}>允许</option>
          <option value="0" {"selected" if config['allow_registration'] != '1' else ""}>关闭</option>
        </select></label>
        <label>高频搜索阈值<input name="hot_search_threshold" type="number" min="1" value="{html.escape(config['hot_search_threshold'])}"></label>
        <label>高需求采购阈值<input name="hot_demand_threshold" type="number" min="1" value="{html.escape(config['hot_demand_threshold'])}"></label>
        <label>低库存阈值<input name="low_stock_threshold" type="number" min="0" value="{html.escape(config['low_stock_threshold'])}"></label>
        <label>每周仓检日<select name="weekly_report_day">
          {''.join(f'<option value="{day}" {"selected" if config["weekly_report_day"] == day else ""}>{day}</option>' for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])}
        </select></label>
        <div class="wide display-tuner" data-display-tuner>
          <div class="panel-head"><h2>展示个性化面板</h2><span>保存后全站生效</span></div>
          <div class="display-tuner-grid">
            <label>渐变起点<input data-css-var="--accent-a" name="ui_accent_start" type="color" value="{ui['accent_start']}"></label>
            <label>渐变终点<input data-css-var="--accent-b" name="ui_accent_end" type="color" value="{ui['accent_end']}"></label>
            <label>玻璃透明度 <output data-output-for="ui_glass_opacity">{ui['glass_opacity']}</output><input data-css-var="--glass-alpha" name="ui_glass_opacity" type="range" min="0.22" max="0.86" step="0.01" value="{ui['glass_opacity']}"></label>
            <label>玻璃模糊 <output data-output-for="ui_glass_blur">{ui['glass_blur']}px</output><input data-css-var="--glass-blur" data-unit="px" name="ui_glass_blur" type="range" min="12" max="48" step="1" value="{ui['glass_blur']}"></label>
            <label>面板圆角 <output data-output-for="ui_panel_radius">{ui['panel_radius']}px</output><input data-css-var="--panel-radius" data-unit="px" name="ui_panel_radius" type="range" min="6" max="28" step="1" value="{ui['panel_radius']}"></label>
            <label>渐变角度 <output data-output-for="ui_gradient_angle">{ui['gradient_angle']}deg</output><input data-css-var="--gradient-angle" data-unit="deg" name="ui_gradient_angle" type="range" min="0" max="360" step="1" value="{ui['gradient_angle']}"></label>
            <label>3D 景深 <output data-output-for="ui_product_depth">{ui['product_depth']}px</output><input data-css-var="--product-depth" data-unit="px" name="ui_product_depth" type="range" min="120" max="360" step="1" value="{ui['product_depth']}"></label>
            <label>产品倾角 <output data-output-for="ui_product_tilt">{ui['product_tilt']}deg</output><input data-css-var="--product-tilt" data-unit="deg" name="ui_product_tilt" type="range" min="0" max="18" step="1" value="{ui['product_tilt']}"></label>
            <label>动效速度 <output data-output-for="ui_product_speed">{ui['product_speed']}s</output><input data-css-var="--product-speed" data-unit="s" name="ui_product_speed" type="range" min="8" max="40" step="1" value="{ui['product_speed']}"></label>
          </div>
          <div class="display-preview">
            <article class="display-preview-card"><span>Liquid Glass</span><strong>Warehouse Product</strong><small>Accent / Blur / 3D</small></article>
            <article class="display-preview-product"><span></span><span></span><span></span></article>
          </div>
        </div>
        <button class="primary" type="submit">保存配置</button>
      </form>
      <p class="muted">端口、数据目录和穿透方式保存后需要重启程序生效。这里没有暴露服务器命令行，避免网页被滥用执行系统命令。</p>
    </section>
    <section class="form-panel">
      <div class="panel-head"><h2>库存智能助手 API</h2><span>保存到 data/assistant_config.json</span></div>
      <form class="entry-form" method="post" action="/admin/assistant">
        <label>启用助手<select name="enabled">
          <option value="0" {"selected" if assistant_cfg['enabled'] != '1' else ""}>关闭</option>
          <option value="1" {"selected" if assistant_cfg['enabled'] == '1' else ""}>启用</option>
        </select></label>
        <label>API 端口 / 地址<input id="assistant-endpoint" name="endpoint" value="{html.escape(assistant_cfg['endpoint'])}" placeholder="例如：https://openrouter.ai/api/v1"></label>
        <label>模型名<input id="assistant-model" name="model" list="assistant-model-options" value="{html.escape(assistant_cfg['model'])}" placeholder="先获取模型列表，再选择模型"></label>
        <datalist id="assistant-model-options"></datalist>
        <label>API Key<input id="assistant-api-key" name="api_key" value="{html.escape(assistant_cfg['api_key'])}" placeholder="留空则保持原 Key"></label>
        <label>温度<input name="temperature" type="number" step="0.1" min="0" max="2" value="{html.escape(assistant_cfg['temperature'])}"></label>
        <label>最大输出 Token<input name="max_tokens" type="number" min="100" max="1200" value="{html.escape(assistant_cfg['max_tokens'])}"></label>
        <label>请求超时秒数<input name="timeout" type="number" min="5" value="{html.escape(assistant_cfg['timeout'])}"></label>
        <label>知识范围<select name="knowledge_depth">
          <option value="inventory_core" {"selected" if assistant_cfg['knowledge_depth'] == 'inventory_core' else ""}>库存核心</option>
          <option value="inventory_electronics" {"selected" if assistant_cfg['knowledge_depth'] == 'inventory_electronics' else ""}>库存 + 电子器件知识</option>
          <option value="bom_datasheet" {"selected" if assistant_cfg['knowledge_depth'] == 'bom_datasheet' else ""}>库存 + BOM + 数据手册研读</option>
        </select></label>
        <label class="wide">系统调教提示词<textarea name="system_prompt" rows="8">{html.escape(assistant_cfg['system_prompt'])}</textarea></label>
        <div class="wide assistant-config-actions">
          <button class="button" type="button" id="assistant-fetch-models">获取模型列表</button>
          <button class="button" type="button" id="assistant-test-api">检测 API</button>
          <button class="primary" type="submit">保存助手 API 配置</button>
        </div>
      </form>
      <div id="assistant-config-status" class="lcsc-preview">填写 API 地址和 Key 后，可以先获取模型列表，再选择模型并检测。</div>
      <p class="muted">前端用户只能通过库存助手接口提问。后端会自动附加库存摘要、BOM 缺口、知识网络节点和最近入库记录，避免助手变成无边界通用聊天。</p>
    </section>
    <section class="form-panel">
      <div class="panel-head"><h2>入库拍照识别 API</h2><span>保存到 data/image_recognition_config.json</span></div>
      {image_recognition_config_form_html(image_cfg)}
      <p class="muted">用于入库页面的手机拍照、相册图片和电脑图片上传识别。前端只上传图片到本站后端，API Key 不会下发到浏览器。</p>
    </section>
    <section class="panel">
      <div class="panel-head"><h2>访问记录日志</h2><span>最近 80 条</span></div>
      <div class="table-wrap"><table><thead><tr><th>时间</th><th>用户</th><th>方法</th><th>路径</th><th>状态</th><th>IP</th></tr></thead><tbody>{log_rows or '<tr><td colspan="6">暂无访问记录。</td></tr>'}</tbody></table></div>
    </section>
    """
    return render_layout("后台配置", body + '<script src="/static/admin_assistant.js"></script><script src="/static/display_tuner.js"></script>', user, "后台配置")


class WarehouseHandler(BaseHTTPRequestHandler):
    server_version = "WarehouseInventory/2.0"

    def setup(self):
        super().setup()
        self._cached_body = None

    def version_string(self):
        return self.server_version

    def log_message(self, fmt, *args):
        print(f"[{now_text()}] {self.address_string()} {fmt % args}")

    def send_response(self, code, message=None):
        self._security_headers_sent = False
        super().send_response(code, message)

    def reject_untrusted_host(self):
        host = self.headers.get("Host", "")
        if host_matches_allowed(host, configured_allowed_hosts()):
            return False
        payload = b"Bad Host Header"
        self.send_response(HTTPStatus.BAD_REQUEST)
        self.send_security_headers()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self.log_access_db(HTTPStatus.BAD_REQUEST)
        return True

    def end_headers(self):
        self.send_security_headers()
        super().end_headers()

    def is_https_request(self):
        forwarded_proto = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip().lower()
        return forwarded_proto == "https" or str(BOOT_CONFIG.get("public_url") or "").lower().startswith("https://")

    def session_cookie_value(self, token, max_age=None):
        parts = [f"session={token}", "HttpOnly", "Path=/", "SameSite=Lax"]
        if max_age is not None:
            parts.append(f"Max-Age={int(max_age)}")
        if self.is_https_request():
            parts.append("Secure")
        return "; ".join(parts)

    def csrf_cookie_value(self, token, max_age=None):
        parts = [f"{CSRF_COOKIE_NAME}={token}", "Path=/", "SameSite=Lax"]
        if max_age is not None:
            parts.append(f"Max-Age={int(max_age)}")
        if self.is_https_request():
            parts.append("Secure")
        return "; ".join(parts)

    def csrf_token(self):
        cookie = safe_cookie(self.headers.get("Cookie", ""))
        token = cookie.get(CSRF_COOKIE_NAME)
        value = token.value if token else ""
        if re.fullmatch(r"[A-Za-z0-9_\-]{32,128}", value or ""):
            return value
        return secrets.token_urlsafe(32)

    def inject_csrf_html(self, content, token):
        token_html = html.escape(token, quote=True)
        page = str(content or "").replace("{csrf_token}", token_html)
        hidden = f'<input type="hidden" name="{CSRF_FIELD_NAME}" value="{token_html}">'
        return re.sub(r"(<form\b(?=[^>]*\bmethod=[\"']?post\b)[^>]*>)", r"\1" + hidden, page, flags=re.I)

    def send_security_headers(self):
        if getattr(self, "_security_headers_sent", False):
            return
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "media-src 'self' data: blob:; "
            "connect-src 'self'; "
            "font-src 'self' data:; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'",
        )
        if self.is_https_request():
            self.send_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        self._security_headers_sent = True

    def auth_cache_headers(self):
        return {"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache"}

    def request_origin(self):
        header = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not header:
            return ""
        parsed = urllib.parse.urlparse(header)
        if not parsed.scheme or not parsed.netloc:
            return ""
        return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"

    def allowed_request_origins(self):
        origins = set(configured_allowed_origins())
        public_url = str(BOOT_CONFIG.get("public_url") or "").strip()
        if public_url:
            parsed = urllib.parse.urlparse(public_url)
            if parsed.scheme and parsed.netloc:
                origins.add(f"{parsed.scheme.lower()}://{parsed.netloc.lower()}")
        if TUNNEL_URL:
            parsed = urllib.parse.urlparse(TUNNEL_URL)
            if parsed.scheme and parsed.netloc:
                origins.add(f"{parsed.scheme.lower()}://{parsed.netloc.lower()}")
        return origins

    def verify_same_origin_post(self):
        origin = self.request_origin()
        return not origin or origin in self.allowed_request_origins()

    def read_cached_body(self):
        if self._cached_body is None:
            self._cached_body = self.rfile.read(self.request_body_size())
        return self._cached_body

    def csrf_token_from_request(self):
        token = str(self.headers.get(CSRF_HEADER_NAME, "")).strip()
        if token:
            return token
        ctype = self.headers.get("Content-Type", "").lower()
        if "application/x-www-form-urlencoded" in ctype or not ctype:
            try:
                form = urllib.parse.parse_qs(self.read_cached_body().decode("utf-8", errors="replace"))
            except Exception:
                form = {}
            return str(form.get(CSRF_FIELD_NAME, [""])[0]).strip()
        if "multipart/form-data" in ctype:
            try:
                fields, _ = self.parse_multipart_body(self.read_cached_body())
            except Exception:
                fields = {}
            return str(fields.get(CSRF_FIELD_NAME, "")).strip()
        return ""

    def verify_csrf_post(self):
        cookie = safe_cookie(self.headers.get("Cookie", ""))
        cookie_token = cookie.get(CSRF_COOKIE_NAME)
        request_token = self.csrf_token_from_request()
        return bool(cookie_token and request_token and hmac.compare_digest(cookie_token.value, request_token))

    def request_body_size(self):
        return parse_int(self.headers.get("Content-Length"), 0)

    def request_size_limit(self):
        ctype = self.headers.get("Content-Type", "").lower()
        if "multipart/form-data" in ctype:
            return MAX_MULTIPART_BODY_BYTES
        return MAX_FORM_BODY_BYTES

    def reject_oversized_request(self):
        size = self.request_body_size()
        limit = self.request_size_limit()
        if size > limit:
            if self.path.startswith("/api/"):
                self.send_json({"error": "Request body is too large."}, HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
                return True
            self.send_html(
                render_auth("login", "请求体过大，请缩小文件或内容后重试。"),
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                headers=self.auth_cache_headers(),
            )
            return True
        return False

    def login_rate_key(self, username):
        ip = self.client_address[0] if self.client_address else ""
        return f"{ip}|{str(username or '').lower()[:80]}"

    def login_block_seconds(self, username):
        key = self.login_rate_key(username)
        now = time.time()
        with LOGIN_FAILURES_LOCK:
            entry = LOGIN_FAILURES.get(key)
            if not entry:
                return 0
            if now - entry.get("first_at", 0) > LOGIN_FAILURE_WINDOW_SECONDS:
                LOGIN_FAILURES.pop(key, None)
                return 0
            locked_until = entry.get("locked_until", 0)
            if locked_until > now:
                return int(locked_until - now)
            return 0

    def record_login_failure(self, username):
        key = self.login_rate_key(username)
        now = time.time()
        with LOGIN_FAILURES_LOCK:
            entry = LOGIN_FAILURES.get(key)
            if not entry or now - entry.get("first_at", 0) > LOGIN_FAILURE_WINDOW_SECONDS:
                entry = {"count": 0, "first_at": now, "locked_until": 0}
            entry["count"] = int(entry.get("count", 0)) + 1
            if entry["count"] >= LOGIN_MAX_FAILURES:
                entry["locked_until"] = now + LOGIN_LOCKOUT_SECONDS
            LOGIN_FAILURES[key] = entry

    def clear_login_failures(self, username):
        with LOGIN_FAILURES_LOCK:
            LOGIN_FAILURES.pop(self.login_rate_key(username), None)

    def log_access_db(self, status):
        try:
            user = current_user(self)
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO access_logs (username, method, path, status, ip, user_agent, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        user["username"] if user else "",
                        self.command,
                        self.path[:500],
                        int(status),
                        self.client_address[0],
                        self.headers.get("User-Agent", "")[:500],
                        now_text(),
                    ),
                )
        except Exception:
            pass

    def handle_uncaught_exception(self, exc):
        trace = traceback.format_exc()
        log_written = False
        try:
            SERVER_ERRORS_LOG.parent.mkdir(parents=True, exist_ok=True)
            with SERVER_ERRORS_LOG.open("a", encoding="utf-8") as fh:
                fh.write(
                    "\n"
                    + "=" * 80
                    + f"\n{now_text()} {self.command} {self.path}\n"
                    + f"Client: {self.client_address[0] if self.client_address else ''}\n"
                    + f"User-Agent: {self.headers.get('User-Agent', '')}\n"
                    + trace
                )
            log_written = True
        except Exception:
            pass
        print(f"[request error] {self.command} {self.path}: {exc}")
        if self.path.startswith("/api/"):
            return self.send_json({"error": "服务器内部错误。"}, HTTPStatus.INTERNAL_SERVER_ERROR)
        message = "服务器内部错误，详情已写入 logs/server_errors.log。"
        if not log_written:
            message = "服务器内部错误，异常日志写入失败，请查看服务端控制台输出。"
        return self.send_text(
            message,
            status=HTTPStatus.INTERNAL_SERVER_ERROR,
        )

    def send_html(self, content, status=HTTPStatus.OK, headers=None):
        csrf_token = self.csrf_token()
        content = self.inject_csrf_html(content, csrf_token)
        payload = content.encode("utf-8")
        self.send_response(status)
        self.send_security_headers()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Set-Cookie", self.csrf_cookie_value(csrf_token, max_age=CSRF_TOKEN_TTL_SECONDS))
        self.send_header("Content-Length", str(len(payload)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)
        self.log_access_db(status)

    def send_json(self, payload, status=HTTPStatus.OK):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_security_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.log_access_db(status)

    def send_text(self, content, content_type="text/plain; charset=utf-8", status=HTTPStatus.OK, headers=None):
        data = str(content or "").encode("utf-8")
        self.send_response(status)
        self.send_security_headers()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)
        self.log_access_db(status)

    def redirect(self, location):
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_security_headers()
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()
        self.log_access_db(HTTPStatus.SEE_OTHER)

    def read_form(self):
        size = self.request_body_size()
        if size > MAX_FORM_BODY_BYTES:
            raise ValueError("Request body is too large.")
        raw = self.read_cached_body().decode("utf-8", errors="replace")
        return urllib.parse.parse_qs(raw)

    def read_json(self):
        size = self.request_body_size()
        if size > MAX_FORM_BODY_BYTES:
            raise ValueError("Request body is too large.")
        raw = self.read_cached_body().decode("utf-8", errors="replace")
        if not raw.strip():
            return {}
        return json.loads(raw)

    def read_api_payload(self):
        size = parse_int(self.headers.get("Content-Length"), 0)
        if size <= 0:
            return {}
        ctype = self.headers.get("Content-Type", "").lower()
        if "application/json" in ctype:
            try:
                data = self.read_json()
            except json.JSONDecodeError as exc:
                raise ValueError("Request JSON format is invalid.") from exc
            if not isinstance(data, dict):
                raise ValueError("Request JSON must be an object.")
            return data
        if "multipart/form-data" in ctype:
            fields, _ = self.read_multipart()
            return fields
        form = self.read_form()
        return {key: values[-1] if isinstance(values, list) and values else "" for key, values in form.items()}

    def parse_multipart_body(self, body):
        ctype = self.headers.get("Content-Type", "")
        match = re.search(r"boundary=(?P<boundary>[^;]+)", ctype)
        if not match:
            raise ValueError("上传格式不正确。")
        boundary = ("--" + match.group("boundary").strip('"')).encode()
        fields = {}
        files = {}
        for part in body.split(boundary):
            part = part.strip()
            if not part or part == b"--":
                continue
            if part.endswith(b"--"):
                part = part[:-2]
            header_blob, _, content = part.partition(b"\r\n\r\n")
            if not header_blob:
                continue
            headers = header_blob.decode("utf-8", errors="replace").split("\r\n")
            disposition = next((h for h in headers if h.lower().startswith("content-disposition:")), "")
            name_match = re.search(r'name="([^"]+)"', disposition)
            filename_match = re.search(r'filename="([^"]*)"', disposition)
            if not name_match:
                continue
            content = content.rstrip(b"\r\n")
            field_name = name_match.group(1)
            if filename_match:
                content_type = next(
                    (h.partition(":")[2].strip() for h in headers if h.lower().startswith("content-type:")),
                    "",
                )
                entry = {"filename": filename_match.group(1), "content": content, "content_type": content_type}
                if field_name in files:
                    if isinstance(files[field_name], list):
                        files[field_name].append(entry)
                    else:
                        files[field_name] = [files[field_name], entry]
                else:
                    files[field_name] = entry
            else:
                fields[field_name] = content.decode("utf-8", errors="replace")
        return fields, files

    def read_multipart(self):
        size = self.request_body_size()
        if size > MAX_MULTIPART_BODY_BYTES:
            raise ValueError("Request body is too large.")
        return self.parse_multipart_body(self.read_cached_body())

    def do_GET(self):
        try:
            return self._do_GET()
        except Exception as exc:
            return self.handle_uncaught_exception(exc)

    def _do_GET(self):
        if self.reject_untrusted_host():
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path.startswith("/static/"):
            return self.serve_static(path)
        if path in ("", "/"):
            return self.send_html(landing_page())
        if path == "/pcb-keyframes":
            return self.send_html(render_pcb_keyframe_page())
        if path == "/api/pcb-keyframes":
            return self.api_pcb_keyframes()
        if path == "/login":
            return self.send_html(render_auth("login"), headers=self.auth_cache_headers())
        if path == "/register":
            return self.send_html(render_auth("register"), headers=self.auth_cache_headers())
        if path == "/logout":
            return self.logout()
        if path == "/download":
            return self.download(query)
        user = current_user(self)
        if not user and path.startswith("/pcb/"):
            preview_token = (query.get("preview_token", [""])[0] or "").strip()
            if preview_token:
                return self.serve_pcb_file(None, path, preview_token=preview_token)
        if not user:
            if path.startswith("/api/"):
                return self.send_json({"error": "Login required."}, HTTPStatus.UNAUTHORIZED)
            return self.send_html(render_auth("login", "Session expired. Please sign in again."), headers=self.auth_cache_headers())
        if path == "/dashboard":
            return self.send_html(dashboard_page(user))
        if path == "/api/categories":
            return self.api_categories(query, user)
        if path == "/api/lcsc_lookup":
            return self.api_lcsc_lookup(query, user)
        if path == "/api/inventory_insights":
            return self.send_json(inventory_insight_payload())
        if path == "/api/knowledge_graph":
            return self.send_json(knowledge_graph_payload(user["username"]))
        if path == "/api/workflows":
            return self.send_json({"error": "工作流功能已删除。"}, HTTPStatus.NOT_FOUND)
        if path == "/api/boms":
            return self.api_boms(user)
        if path == "/api/admin/analysis-worker":
            return self.api_admin_analysis_worker(user)
        if path == "/api/admin/analysis-jobs":
            return self.api_admin_analysis_jobs(user, query)
        if path.startswith("/api/analysis-jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/api/analysis-jobs/")).strip()
            return self.api_analysis_job_status(user, job_id)
        if path == "/analysis-jobs":
            return self.analysis_jobs(user, query)
        if path.startswith("/analysis-jobs/"):
            job_id = urllib.parse.unquote(path.removeprefix("/analysis-jobs/")).strip()
            return self.analysis_job_detail(user, job_id)
        if path == "/api/soldering/active":
            return self.api_active_soldering_jobs(user)
        if path == "/soldering/workbench":
            return self.soldering_workbench(user, query)
        if path.startswith("/api/soldering/jobs/") and path.endswith("/consumption"):
            job_id = urllib.parse.unquote(path[len("/api/soldering/jobs/") : -len("/consumption")]).strip()
            return self.api_soldering_job_consumption(user, job_id)
        if path == "/api/admin/soldering":
            return self.api_admin_soldering_dashboard(user)
        if path == "/api/admin/soldering/shortages":
            return self.api_admin_soldering_shortages(user, query)
        if path == "/api/admin/soldering/receiving-priorities":
            return self.api_admin_soldering_receiving_priorities(user, query)
        if path == "/api/admin/purchase-receipts":
            return self.api_admin_purchase_receipts(user, query)
        if path == "/api/admin/purchase-orders":
            return self.api_admin_purchase_orders(user, query)
        if path.startswith("/api/admin/purchase-items/") and path.endswith("/receipts"):
            purchase_item_id = urllib.parse.unquote(path[len("/api/admin/purchase-items/") : -len("/receipts")]).strip()
            return self.api_admin_purchase_item_receipts(user, purchase_item_id, query)
        if path == "/api/admin/inventory-adjustments":
            return self.api_admin_inventory_adjustments(user, query)
        if path == "/api/admin/inventory-adjustments.csv":
            return self.api_admin_inventory_adjustments(user, query, force_csv=True)
        if (
            path == "/api/soldering/active"
            or path == "/soldering/workbench"
            or path.startswith("/api/soldering/")
            or path.startswith("/api/admin/soldering")
            or path == "/api/admin/purchase-receipts"
            or path == "/api/admin/purchase-orders"
            or path.startswith("/api/admin/purchase-items/")
            or path in ("/api/admin/inventory-adjustments", "/api/admin/inventory-adjustments.csv")
        ):
            return self.send_json({"error": "Feature removed."}, HTTPStatus.NOT_FOUND)
        if path.startswith("/pcb/"):
            return self.serve_pcb_file(user, path, preview_token=(query.get("preview_token", [""])[0] or "").strip())
        analysis_target = parse_bom_pcb_analysis_path(path)
        if analysis_target:
            bom_id, pcb_file_id = analysis_target
            return self.api_bom_pcb_analysis_status(user, bom_id, pcb_file_id)
        if path.startswith("/api/boms/") and path.endswith("/soldering"):
            bom_id = urllib.parse.unquote(path[len("/api/boms/") : -len("/soldering")]).strip()
            return self.api_bom_soldering_jobs(user, bom_id)
        if path.startswith("/api/boms/") and path.endswith("/interactive-bom"):
            bom_id = urllib.parse.unquote(path[len("/api/boms/") : -len("/interactive-bom")]).strip()
            return self.api_bom_interactive_bom(user, bom_id)
        if path.startswith("/api/boms/"):
            bom_id = urllib.parse.unquote(path.removeprefix("/api/boms/")).strip()
            return self.api_bom_detail(user, bom_id)
        if path == "/api/assistant/history":
            return self.send_json(
                {
                    "history": assistant_history(user["username"], limit=120),
                    "document_dir": str(user_knowledge_folder(user["username"])),
                }
            )
        if path == "/api/assistant/models":
            return self.api_assistant_models(query, user)
        if path == "/inventory":
            return self.send_html(inventory_page(user, query))
        if path == "/inventory/new":
            return self.send_html(new_inventory_page(user))
        if path == "/changelog":
            return self.send_html(changelog_page(user))
        if path == "/bom":
            return self.send_html(bom_page(user))
        if path == "/spares":
            return self.send_html(spare_bop_page(user, query))
        if path == "/workflow":
            return self.send_html(
                render_layout("未找到", '<section class="panel">工作流功能已删除。</section>', user),
                HTTPStatus.NOT_FOUND,
            )
        if path == "/analytics":
            return self.send_html(analytics_page(user))
        if path == "/assistant":
            return self.send_html(assistant_page(user))
        if path == "/reports":
            return self.send_html(reports_page(user))
        if path == "/users":
            return self.send_html(users_page(user))
        if path == "/admin/purchase-orders":
            if not is_admin_role(user):
                return self.send_html(render_layout("Forbidden", '<section class="panel">Forbidden.</section>', user), HTTPStatus.FORBIDDEN)
            return self.send_html(admin_purchase_orders_page(user, query))
        if path == "/admin/inventory-adjustments":
            if not is_admin_role(user):
                return self.send_html(render_layout("Forbidden", '<section class="panel">Forbidden.</section>', user), HTTPStatus.FORBIDDEN)
            return self.send_html(admin_inventory_adjustments_page(user, query))
        if path == "/admin/soldering":
            if not is_admin_role(user):
                return self.send_html(render_layout("Forbidden", '<section class="panel">Forbidden.</section>', user), HTTPStatus.FORBIDDEN)
            return self.send_html(admin_soldering_page(user, query))
        if path in ("/admin/purchase-orders", "/admin/inventory-adjustments", "/admin/soldering"):
            return self.send_html(
                render_layout("未找到", '<section class="panel">功能已删除。</section>', user),
                HTTPStatus.NOT_FOUND,
            )
        if path == "/admin/image-recognition":
            return self.send_html(image_recognition_config_page(user))
        if path == "/admin":
            return self.send_html(admin_page(user))
        return self.send_html(render_layout("未找到", '<section class="panel">页面不存在。</section>', user), HTTPStatus.NOT_FOUND)

    def do_POST(self):
        try:
            return self._do_POST()
        except Exception as exc:
            return self.handle_uncaught_exception(exc)

    def _do_POST(self):
        if self.reject_untrusted_host():
            return
        parsed = urllib.parse.urlparse(self.path)
        if self.reject_oversized_request():
            return
        if not self.verify_same_origin_post():
            if parsed.path.startswith("/api/"):
                return self.send_json({"error": "Cross-site request blocked."}, HTTPStatus.FORBIDDEN)
            return self.send_html(
                render_auth("login", "请求来源不可信，请从本站重新提交。"),
                HTTPStatus.FORBIDDEN,
                headers=self.auth_cache_headers(),
            )
        if not self.verify_csrf_post():
            if parsed.path.startswith("/api/"):
                return self.send_json({"error": "CSRF token missing or invalid."}, HTTPStatus.FORBIDDEN)
            return self.send_html(
                render_auth("login", "请求安全令牌无效，请刷新页面后重新提交。"),
                HTTPStatus.FORBIDDEN,
                headers=self.auth_cache_headers(),
            )
        if parsed.path == "/api/pcb-keyframes":
            user = current_user(self)
            if not is_admin_role(user):
                return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
            return self.api_save_pcb_keyframes(user)
        if parsed.path == "/login":
            return self.login()
        if parsed.path == "/register":
            return self.register()
        if parsed.path.startswith("/api/boms/") and parsed.path.endswith("/soldering/start"):
            bom_id = urllib.parse.unquote(parsed.path[len("/api/boms/") : -len("/soldering/start")]).strip()
            user = current_user(self)
            if not user:
                return self.send_json({"error": "Login required."}, HTTPStatus.UNAUTHORIZED)
            return self.api_start_soldering(user, bom_id)
        analysis_target = parse_bom_pcb_analysis_path(parsed.path)
        if analysis_target:
            bom_id, pcb_file_id = analysis_target
            user = current_user(self)
            if not user:
                return self.send_json({"error": "Login required."}, HTTPStatus.UNAUTHORIZED)
            return self.api_queue_bom_pcb_analysis(user, bom_id, pcb_file_id)
        if parsed.path.startswith("/api/soldering/jobs/") and parsed.path.endswith("/finish"):
            job_id = urllib.parse.unquote(parsed.path[len("/api/soldering/jobs/") : -len("/finish")]).strip()
            user = current_user(self)
            if not user:
                return self.send_json({"error": "Login required."}, HTTPStatus.UNAUTHORIZED)
            return self.api_finish_soldering_job(user, job_id)
        if parsed.path.startswith("/api/boms/") and parsed.path.endswith("/pcb"):
            bom_id = urllib.parse.unquote(parsed.path[len("/api/boms/") : -len("/pcb")]).strip()
            user = current_user(self)
            if not user:
                return self.send_json({"error": "Login required."}, HTTPStatus.UNAUTHORIZED)
            return self.api_attach_pcb(user, bom_id)
        user = current_user(self)
        if not user:
            if parsed.path.startswith("/api/"):
                return self.send_json({"error": "Login required."}, HTTPStatus.UNAUTHORIZED)
            return self.redirect("/login")
        retry_owner_analysis_job_id = parse_analysis_job_retry_path(parsed.path)
        if retry_owner_analysis_job_id:
            return self.api_retry_analysis_job(user, retry_owner_analysis_job_id)
        retry_analysis_job_id = parse_admin_analysis_job_retry_path(parsed.path)
        if retry_analysis_job_id:
            return self.api_admin_retry_analysis_job(user, retry_analysis_job_id)
        if parsed.path == "/api/admin/analysis-worker":
            return self.api_admin_analysis_worker(user)
        if parsed.path == "/api/admin/analysis-jobs/process-queued":
            return self.api_admin_process_analysis_jobs(user)
        if parsed.path.startswith("/api/admin/purchase-receipts/") and parsed.path.endswith("/finalize"):
            receipt_id = urllib.parse.unquote(parsed.path[len("/api/admin/purchase-receipts/") : -len("/finalize")]).strip()
            return self.api_admin_finalize_purchase_receipt(user, receipt_id)
        if parsed.path.startswith("/api/admin/purchase-receipts/") and parsed.path.endswith("/reverse"):
            receipt_id = urllib.parse.unquote(parsed.path[len("/api/admin/purchase-receipts/") : -len("/reverse")]).strip()
            return self.api_admin_reverse_purchase_receipt(user, receipt_id)
        if parsed.path.startswith("/api/admin/purchase-receipts/") and parsed.path.endswith("/void"):
            receipt_id = urllib.parse.unquote(parsed.path[len("/api/admin/purchase-receipts/") : -len("/void")]).strip()
            return self.api_admin_void_purchase_receipt(user, receipt_id)
        if parsed.path.startswith("/api/admin/purchase-items/") and parsed.path.endswith("/receipts"):
            purchase_item_id = urllib.parse.unquote(parsed.path[len("/api/admin/purchase-items/") : -len("/receipts")]).strip()
            return self.api_admin_record_purchase_receipt(user, purchase_item_id)
        if parsed.path.startswith("/api/admin/purchase-receipts/") or (
            parsed.path.startswith("/api/admin/purchase-items/") and parsed.path.endswith("/receipts")
        ):
            return self.send_json({"error": "Feature removed."}, HTTPStatus.NOT_FOUND)
        transition_job_id = parse_analysis_job_status_path(parsed.path)
        if transition_job_id:
            return self.api_admin_update_analysis_job_status(user, transition_job_id)
        if parsed.path.startswith("/api/inventory/") and parsed.path.endswith("/adjust"):
            inventory_id = urllib.parse.unquote(parsed.path[len("/api/inventory/") : -len("/adjust")]).strip()
            return self.api_adjust_inventory_quantity(user, inventory_id)
        if parsed.path == "/api/inventory/image-recognize":
            return self.api_inventory_image_recognize(user)
        if parsed.path == "/inventory/new":
            return self.create_inventory(user)
        if parsed.path == "/bom":
            return self.upload_bom(user)
        if parsed.path == "/spares":
            return self.create_competition_material_item(user)
        if parsed.path == "/spares/bom":
            return self.upload_competition_material_bom(user)
        if parsed.path == "/spares/purchase":
            return self.create_spare_bop_purchase(user)
        if parsed.path == "/workflow":
            return self.send_html(
                render_layout("未找到", '<section class="panel">工作流功能已删除。</section>', user),
                HTTPStatus.NOT_FOUND,
            )
        if parsed.path == "/workflow/update":
            return self.send_html(
                render_layout("未找到", '<section class="panel">工作流功能已删除。</section>', user),
                HTTPStatus.NOT_FOUND,
            )
        if parsed.path == "/api/workflow/models":
            return self.send_json({"error": "工作流功能已删除。"}, HTTPStatus.NOT_FOUND)
        if parsed.path == "/reports/run":
            generate_weekly_report(force=True)
            return self.send_html(reports_page(user, "报表已生成。"))
        if parsed.path == "/lcsc/refresh":
            return self.refresh_lcsc_cache(user)
        if parsed.path == "/users":
            return self.create_user(user)
        if parsed.path == "/admin":
            return self.save_admin(user)
        if parsed.path == "/admin/assistant":
            return self.save_assistant(user)
        if parsed.path == "/admin/image-recognition":
            return self.save_image_recognition(user)
        if parsed.path == "/api/assistant/chat":
            return self.api_assistant_chat(user)
        if parsed.path == "/api/assistant/test":
            return self.api_assistant_test(user)
        return self.redirect("/dashboard")

    def serve_static(self, path):
        rel = urllib.parse.unquote(path.removeprefix("/static/"))
        target = (STATIC_DIR / rel).resolve()
        if not path_is_relative_to(target, STATIC_DIR) or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            self.log_access_db(HTTPStatus.NOT_FOUND)
            return
        data = target.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_security_headers()
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        if target.suffix.lower() in (".css", ".js"):
            self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def api_pcb_keyframes(self):
        payload = {"shots": [], "updated_at": None}
        if PCB_KEYFRAMES_JSON.exists():
            try:
                payload = json.loads(read_data_text(PCB_KEYFRAMES_JSON, encoding="utf-8"))
            except Exception:
                payload = {"shots": [], "updated_at": None}
        return self.send_json(payload)

    def api_save_pcb_keyframes(self, user):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_json({"error": "请求 JSON 格式不正确。"}, HTTPStatus.BAD_REQUEST)
        if not isinstance(payload, dict):
            return self.send_json({"error": "请求体必须是对象。"}, HTTPStatus.BAD_REQUEST)
        shots = payload.get("shots")
        if not isinstance(shots, list) or len(shots) != 4:
            return self.send_json({"error": "需要恰好 4 个镜头。"}, HTTPStatus.BAD_REQUEST)
        clean_shots = []
        for index, shot in enumerate(shots, 1):
            if not isinstance(shot, dict):
                return self.send_json({"error": f"第 {index} 个镜头格式不正确。"}, HTTPStatus.BAD_REQUEST)
            clean_shot = {
                "id": shot.get("id") or f"shot-{index}",
                "title": str(shot.get("title") or "").strip(),
                "kicker": str(shot.get("kicker") or "").strip(),
                "body": str(shot.get("body") or "").strip(),
                "side": "right" if str(shot.get("side") or "left").lower() == "right" else "left",
                "motion": str(shot.get("motion") or "slide-rise").strip(),
                "camera": {
                    "position": [float(v) for v in (shot.get("camera", {}) or {}).get("position", [0, 0, 0])[:3]],
                    "rotation": [float(v) for v in (shot.get("camera", {}) or {}).get("rotation", [0, 0, 0])[:3]],
                    "target": [float(v) for v in (shot.get("camera", {}) or {}).get("target", [0, 0, 0])[:3]],
                    "zoom": float((shot.get("camera", {}) or {}).get("zoom", 1)),
                },
                "model": {
                    "position": [float(v) for v in (shot.get("model", {}) or {}).get("position", [0, 0, 0])[:3]],
                    "rotation": [float(v) for v in (shot.get("model", {}) or {}).get("rotation", [0, 0, 0])[:3]],
                    "scale": float((shot.get("model", {}) or {}).get("scale", 1)),
                    "explode": float((shot.get("model", {}) or {}).get("explode", 0)),
                },
                "text": {
                    "x": float((shot.get("text", {}) or {}).get("x", 12 if str(shot.get("side") or "left").lower() != "right" else 62)),
                    "y": float((shot.get("text", {}) or {}).get("y", 58)),
                },
            }
            clean_shots.append(clean_shot)
        payload = {
            "shots": clean_shots,
            "updated_at": now_text(),
            "source": "pcb-keyframe-lab",
        }
        write_data_text(PCB_KEYFRAMES_JSON, json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return self.send_json({"ok": True, "payload": payload})

    def api_categories(self, query, user):
        force = query.get("force", ["0"])[0] == "1" and user["role"] == "admin"
        return self.send_json(fetch_lcsc_categories(force=force))

    def api_lcsc_lookup(self, query, user):
        code = (query.get("code", [""])[0] or "").strip().upper()
        if not re.fullmatch(r"C\d{3,}", code):
            return self.send_json({"error": "请输入正确的 LCSC C 编号，例如 C25803。"}, HTTPStatus.BAD_REQUEST)
        force = query.get("force", ["0"])[0] == "1"
        result = enrich_lcsc_codes([code], force=force, max_codes=1)
        product = result["cache"].get(code) or {"lcsc_code": code, "status": "not_found"}
        specs = extract_lcsc_specs(product) if product.get("status") == "ok" else {}
        model_3d = lcsc_model_payload(product, specs) if product.get("status") == "ok" else {}
        if model_3d.get("links") or model_3d.get("uuid"):
            result["cache"][code] = product
            save_lcsc_cache(result["cache"])
        return self.send_json(
            {
                "product": product,
                "specs": specs,
                "model_3d": model_3d,
                "suggested_category": specs.get("category") or infer_category_from_product(product, ""),
                "fetched": code in result["fetched"],
            }
        )

    def serve_pcb_file(self, user, path, preview_token=""):
        parts = path.strip("/").split("/")
        if len(parts) != 3:
            self.send_error(HTTPStatus.NOT_FOUND)
            self.log_access_db(HTTPStatus.NOT_FOUND)
            return
        bom_id = urllib.parse.unquote(parts[1]).strip()
        pcb_file_id = urllib.parse.unquote(parts[2]).strip()
        record = find_bom_record(bom_id)
        token_allowed = bool(preview_token and verify_pcb_preview_token(preview_token, bom_id, pcb_file_id, user=user))
        if not record or not (user_can_view_bom(record, user) or token_allowed):
            self.send_error(HTTPStatus.FORBIDDEN if record else HTTPStatus.NOT_FOUND)
            self.log_access_db(HTTPStatus.FORBIDDEN if record else HTTPStatus.NOT_FOUND)
            return
        metadata = find_pcb_file_metadata(record, pcb_file_id)
        if not metadata:
            self.send_error(HTTPStatus.NOT_FOUND)
            self.log_access_db(HTTPStatus.NOT_FOUND)
            return
        relative = str(metadata.get("relative_path") or "")
        target = (DATA_DIR / relative).resolve()
        if not path_is_relative_to(target, PCB_UPLOADS_DIR) or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            self.log_access_db(HTTPStatus.NOT_FOUND)
            return
        data = read_data_bytes(target)
        extension = Path(str(metadata.get("stored_filename") or metadata.get("original_filename") or target.name)).suffix.lower()
        content_type = metadata.get("mime_type") or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        trusted_generated_html = pcb_file_is_internal_ibom_html(metadata, extension)
        if extension in (".html", ".htm"):
            content_type = "text/html; charset=utf-8" if trusted_generated_html else "text/plain; charset=utf-8"
        csp = (
            "default-src 'none'; "
            "script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; "
            "img-src data:; "
            "base-uri 'none'; "
            "form-action 'none'; "
            "frame-ancestors 'self'; "
            "sandbox allow-scripts"
            if trusted_generated_html
            else "default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox"
        )
        self.send_response(HTTPStatus.OK)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Type", content_type)
        self.send_header(
            "Content-Disposition",
            "inline; " + content_disposition_filename(metadata.get("original_filename") or target.name),
        )
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Security-Policy", csp)
        self._security_headers_sent = True
        self.end_headers()
        self.wfile.write(data)
        self.log_access_db(HTTPStatus.OK)

    def soldering_workbench(self, user, query):
        bom_id = (query.get("bom_id", [""])[0] or "").strip()
        pcb_file_id = (query.get("pcb_file_id", [""])[0] or "").strip()
        if not bom_id:
            return self.send_html(render_layout("焊接工作台", soldering_workbench_html(user), user, "焊接工作台"))
        record = find_bom_record(bom_id)
        if not record:
            return self.send_html(render_layout("BOM not found", '<section class="panel">BOM record not found.</section>', user), HTTPStatus.NOT_FOUND)
        if not user_can_view_bom(record, user):
            return self.send_html(render_layout("Forbidden", '<section class="panel">Forbidden.</section>', user), HTTPStatus.FORBIDDEN)
        pcb_file = find_pcb_file_metadata(record, pcb_file_id) if pcb_file_id else None
        if pcb_file:
            pcb_file = decorate_pcb_file(record, pcb_file)
        return self.send_html(render_soldering_workbench(user, record, pcb_file=pcb_file))

    def analysis_jobs(self, user, query):
        try:
            return self.send_html(render_analysis_jobs_page(user, query or {}))
        except ValueError as exc:
            return self.send_html(
                render_layout("Analysis history filters", f'<section class="panel">Invalid filters: {html.escape(str(exc))}</section>', user),
                HTTPStatus.BAD_REQUEST,
            )

    def analysis_job_detail(self, user, job_id):
        job = get_analysis_job(job_id)
        if not job:
            return self.send_html(
                render_layout("Analysis job not found", '<section class="panel">Analysis job not found.</section>', user),
                HTTPStatus.NOT_FOUND,
            )
        if not user_can_access_analysis_job(job, user):
            return self.send_html(
                render_layout("Forbidden", '<section class="panel">Forbidden.</section>', user),
                HTTPStatus.FORBIDDEN,
            )
        return self.send_html(render_analysis_job_detail_page(user, job))

    def api_boms(self, user):
        records = [decorate_bom_record(record) for record in load_bom_records() if user_can_view_bom(record, user)]
        records.sort(key=lambda record: str(record.get("created_at") or ""), reverse=True)
        return self.send_json({"records": records, "count": len(records)})

    def api_bom_detail(self, user, bom_id):
        if not bom_id:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        record = find_bom_record(bom_id)
        if not record:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_view_bom(record, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        return self.send_json({"record": decorate_bom_record(record)})

    def api_bom_interactive_bom(self, user, bom_id):
        if not bom_id:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        record = find_bom_record(bom_id)
        if not record:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_view_bom(record, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        return self.send_json(interactive_bom_payload_for_record(record))

    def api_attach_pcb(self, user, bom_id):
        if not bom_id:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        record = find_bom_record(bom_id)
        if not record:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_view_bom(record, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        if "multipart/form-data" not in self.headers.get("Content-Type", "").lower():
            return self.send_json({"error": "Request must be multipart/form-data."}, HTTPStatus.BAD_REQUEST)
        try:
            _, files = self.read_multipart()
            uploads = collect_uploaded_files(files, PCB_UPLOAD_FIELD_NAMES)
            pcb_files = store_pcb_uploads(bom_id, uploads, user)
            updated = update_bom_record(bom_id, lambda current: append_pcb_metadata(current, pcb_files))
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if not updated:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        decorated = decorate_bom_record(updated)
        decorated_files = decorated.get("pcb_files") if isinstance(decorated.get("pcb_files"), list) else []
        return self.send_json(
            {
                "record": decorated,
                "pcb_files": decorated_files,
                "latest_pcb_file": decorated.get("latest_pcb_file"),
                "count": len(pcb_files),
            },
            HTTPStatus.CREATED,
        )

    def api_queue_bom_pcb_analysis(self, user, bom_id, pcb_file_id):
        try:
            record, pcb_file = resolve_bom_pcb_file_for_user(user, bom_id, pcb_file_id)
            require_pdf_schematic_file(pcb_file)
            job, created = create_or_get_analysis_job(record, pcb_file)
            updated = find_bom_record(bom_id) or record
            decorated = decorate_bom_record(updated)
            decorated_file = decorate_pcb_file(decorated, find_pcb_file_metadata(decorated, pcb_file_id) or pcb_file)
        except LookupError:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        except PermissionError:
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        except FileNotFoundError:
            return self.send_json({"error": "PCB file does not belong to this BOM."}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self.send_json(
            {
                "job": safe_analysis_job_status_payload(job),
                "created": created,
                "active": job.get("status") in ANALYSIS_JOB_ACTIVE_STATUSES if job else False,
                "record": safe_bom_pcb_analysis_status_record(decorated),
                "pcb_file": safe_pcb_analysis_status_payload(decorated_file),
                "history": analysis_workbench_history_payload(user, decorated, selected_pcb_id=pcb_file_id),
            },
            HTTPStatus.CREATED if created else HTTPStatus.OK,
        )

    def api_bom_pcb_analysis_status(self, user, bom_id, pcb_file_id):
        try:
            record, pcb_file = resolve_bom_pcb_file_for_user(user, bom_id, pcb_file_id)
            require_pdf_schematic_file(pcb_file)
            job = find_latest_analysis_job_for_file(record.get("bom_id"), pcb_file_metadata_id(pcb_file))
            if job:
                mirror_analysis_job_to_bom(job)
            updated = find_bom_record(bom_id) or record
            decorated = decorate_bom_record(updated)
            decorated_file = decorate_pcb_file(decorated, find_pcb_file_metadata(decorated, pcb_file_id) or pcb_file)
        except LookupError:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        except PermissionError:
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        except FileNotFoundError:
            return self.send_json({"error": "PCB file does not belong to this BOM."}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self.send_json(
            {
                "job": safe_analysis_job_status_payload(job),
                "active": bool(job and job.get("status") in ANALYSIS_JOB_ACTIVE_STATUSES),
                "analysis_status": (job or {}).get("status") or decorated_file.get("analysis_status") or "pending",
                "record": safe_bom_pcb_analysis_status_record(decorated),
                "pcb_file": safe_pcb_analysis_status_payload(decorated_file),
                "history": analysis_workbench_history_payload(user, decorated, selected_pcb_id=pcb_file_id),
            }
        )

    def api_analysis_job_status(self, user, job_id):
        job = get_analysis_job(job_id)
        if not job:
            return self.send_json({"error": "Analysis job not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_access_analysis_job(job, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        record = find_bom_record(job.get("bom_id"))
        decorated = None
        pcb_file = None
        if record and user_can_view_bom(record, user):
            mirror_analysis_job_to_bom(job)
            decorated = decorate_bom_record(find_bom_record(job.get("bom_id")) or record)
            pcb_file = decorate_pcb_file(decorated, find_pcb_file_metadata(decorated, job.get("pcb_file_id")) or {})
        if not is_admin_role(user):
            selected_pcb_id = pcb_file_metadata_id(pcb_file) or str(job.get("pcb_file_id") or "")
            return self.send_json(
                {
                    "job": safe_analysis_job_status_payload(job),
                    "record": safe_bom_pcb_analysis_status_record(decorated) if decorated else None,
                    "pcb_file": safe_pcb_analysis_status_payload(pcb_file) if pcb_file else None,
                    "history": analysis_workbench_history_payload(user, decorated, selected_pcb_id=selected_pcb_id)
                    if decorated
                    else analysis_workbench_history_payload(user, {}, selected_pcb_id=selected_pcb_id),
                }
            )
        return self.send_json({"job": job, "record": decorated, "pcb_file": pcb_file})

    def api_retry_analysis_job(self, user, job_id):
        try:
            previous, job, created = retry_failed_analysis_job(job_id, user=user, require_user_checks=True)
        except PermissionError:
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        except LookupError:
            return self.send_json({"error": "Analysis job not found."}, HTTPStatus.NOT_FOUND)
        except FileNotFoundError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        record = find_bom_record(job.get("bom_id"))
        if not record:
            return self.send_json({"error": "Analysis job not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_access_analysis_job(job, user) or not user_can_view_bom(record, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        decorated = decorate_bom_record(record)
        pcb_file = decorate_pcb_file(decorated, find_pcb_file_metadata(decorated, job.get("pcb_file_id")) or {})
        selected_pcb_id = pcb_file_metadata_id(pcb_file) or str(job.get("pcb_file_id") or "")
        return self.send_json(
            {
                "job": safe_analysis_job_status_payload(job),
                "previous_job": safe_analysis_job_status_payload(previous),
                "created": created,
                "active": job.get("status") in ANALYSIS_JOB_ACTIVE_STATUSES if job else False,
                "record": safe_bom_pcb_analysis_status_record(decorated),
                "pcb_file": safe_pcb_analysis_status_payload(pcb_file),
                "history": analysis_workbench_history_payload(user, decorated, selected_pcb_id=selected_pcb_id),
            },
            HTTPStatus.CREATED if created else HTTPStatus.OK,
        )

    def api_admin_analysis_worker(self, user):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        if self.command == "POST":
            try:
                payload = self.read_api_payload()
                save_analysis_worker_config(payload)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self.send_json(analysis_worker_status_payload())

    def api_admin_analysis_jobs(self, user, query):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            payload = admin_analysis_jobs_payload(query or {})
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self.send_json(payload)

    def api_admin_update_analysis_job_status(self, user, job_id):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            payload = self.read_api_payload()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        status = str(payload_value(payload, "status") or "").strip()
        if not status:
            return self.send_json({"error": "status is required."}, HTTPStatus.BAD_REQUEST)
        progress_value = payload_value(payload, "progress", default=None) if "progress" in payload else None
        error_summary = payload_value(payload, "error_summary", "error", default="")
        result_json = payload.get("result_json") if isinstance(payload, dict) and "result_json" in payload else payload.get("result") if isinstance(payload, dict) and "result" in payload else None
        try:
            job = update_analysis_job_status(job_id, status, progress=progress_value, error_summary=error_summary, result_json=result_json)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        if not job:
            return self.send_json({"error": "Analysis job not found."}, HTTPStatus.NOT_FOUND)
        record = find_bom_record(job.get("bom_id"))
        decorated = decorate_bom_record(record) if record else None
        pcb_file = decorate_pcb_file(decorated, find_pcb_file_metadata(decorated, job.get("pcb_file_id")) or {}) if decorated else None
        return self.send_json({"job": job, "record": decorated, "pcb_file": pcb_file})

    def api_admin_retry_analysis_job(self, user, job_id):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            previous, job, created = retry_failed_analysis_job(job_id)
        except LookupError:
            return self.send_json({"error": "Analysis job not found."}, HTTPStatus.NOT_FOUND)
        except FileNotFoundError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        record = find_bom_record(job.get("bom_id"))
        decorated = decorate_bom_record(record) if record else None
        pcb_file = decorate_pcb_file(decorated, find_pcb_file_metadata(decorated, job.get("pcb_file_id")) or {}) if decorated else None
        return self.send_json(
            {
                "job": job,
                "previous_job": analysis_job_admin_payload(previous),
                "created": created,
                "active": job.get("status") in ANALYSIS_JOB_ACTIVE_STATUSES if job else False,
                "record": decorated,
                "pcb_file": pcb_file,
            },
            HTTPStatus.CREATED if created else HTTPStatus.OK,
        )

    def api_admin_process_analysis_jobs(self, user):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            payload = self.read_api_payload()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        limit = clamp_int(payload_value(payload, "limit", default=1), 1, 1, 10)
        result = process_queued_pdf_analysis_jobs(limit=limit)
        return self.send_json(result)

    def api_start_soldering(self, user, bom_id):
        if not bom_id:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        record = find_bom_record(bom_id)
        if not record:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_view_bom(record, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            data = self.read_api_payload()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        pcb_file_id = str(payload_value(data, "pcb_file_id", "pcb_id") or "").strip()
        if pcb_file_id and not find_pcb_file_metadata(record, pcb_file_id):
            return self.send_json({"error": "PCB file does not belong to this BOM."}, HTTPStatus.BAD_REQUEST)
        active = find_active_soldering_job_for_bom(record)
        if active:
            updated = update_bom_record(bom_id, lambda current: mark_bom_soldering_started(current, active)) or record
            return self.send_json({"job": active, "record": updated, "created": False, "resumed": True})
        board_count = parse_int(payload_value(data, "board_count"), 0)
        if board_count <= 0:
            return self.send_json({"error": "board_count must be greater than 0."}, HTTPStatus.BAD_REQUEST)
        notes = str(payload_value(data, "notes") or "").strip()[:1000]
        job = create_soldering_job(record, user, board_count, pcb_file_id=pcb_file_id, notes=notes)
        updated = update_bom_record(bom_id, lambda current: mark_bom_soldering_started(current, job)) or record
        return self.send_json({"job": job, "record": updated, "created": True, "resumed": False}, HTTPStatus.CREATED)

    def api_active_soldering_jobs(self, user):
        jobs = list_soldering_jobs(user, active_only=True)
        return self.send_json({"jobs": jobs, "count": len(jobs)})

    def api_admin_soldering_dashboard(self, user):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        return self.send_json(admin_soldering_dashboard_payload())

    def api_admin_soldering_shortages(self, user, query):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        payload = admin_soldering_shortage_payload(query or {})
        if str(payload_value(query or {}, "format") or "").strip().lower() == "csv":
            csv_text = admin_soldering_shortages_csv(payload["rows"])
            filename = f"soldering-shortages-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            return self.send_text(
                csv_text,
                "text/csv; charset=utf-8",
                headers={"Content-Disposition": "attachment; " + content_disposition_filename(filename)},
            )
        return self.send_json(payload)

    def api_admin_soldering_receiving_priorities(self, user, query):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        return self.send_json(admin_soldering_receiving_priority_payload(query or {}))

    def api_admin_purchase_receipts(self, user, query):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        return self.send_json(admin_purchase_receipts_payload(query or {}))

    def api_admin_purchase_orders(self, user, query):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        return self.send_json(admin_purchase_orders_payload(query or {}))

    def api_admin_inventory_adjustments(self, user, query, force_csv=False):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        payload = admin_inventory_adjustments_payload(query or {})
        if force_csv or str(payload_value(query or {}, "format") or "").strip().lower() == "csv":
            csv_text = admin_inventory_adjustments_csv(payload["rows"])
            filename = f"inventory-adjustments-{datetime.now().strftime('%Y%m%d-%H%M%S')}.csv"
            return self.send_text(
                csv_text,
                "text/csv; charset=utf-8",
                headers={"Content-Disposition": "attachment; " + content_disposition_filename(filename)},
            )
        return self.send_json(payload)

    def api_adjust_inventory_quantity(self, user, inventory_id):
        if "application/json" not in self.headers.get("Content-Type", "").lower():
            return self.send_json({"error": "Request must be application/json."}, HTTPStatus.BAD_REQUEST)
        try:
            payload = self.read_json()
        except json.JSONDecodeError:
            return self.send_json({"error": "Request JSON format is invalid."}, HTTPStatus.BAD_REQUEST)
        if not isinstance(payload, dict):
            return self.send_json({"error": "Request JSON must be an object."}, HTTPStatus.BAD_REQUEST)
        inventory_id = parse_int(inventory_id, 0)
        if inventory_id <= 0:
            return self.send_json({"error": "Inventory entry not found."}, HTTPStatus.NOT_FOUND)
        has_after = "quantity_after" in payload
        has_delta = "quantity_delta" in payload
        if has_after and has_delta:
            return self.send_json({"error": "Provide either quantity_after or quantity_delta, not both."}, HTTPStatus.BAD_REQUEST)
        if not has_after and not has_delta:
            return self.send_json({"error": "Provide quantity_after or quantity_delta."}, HTTPStatus.BAD_REQUEST)
        reason = str(payload.get("reason") if payload.get("reason") is not None else "").strip()
        if not reason:
            return self.send_json({"error": "reason is required."}, HTTPStatus.BAD_REQUEST)
        reason = reason[:1000]
        try:
            requested_after = parse_required_json_int(payload["quantity_after"], "quantity_after") if has_after else None
            requested_delta = parse_required_json_int(payload["quantity_delta"], "quantity_delta") if has_delta else None
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

        with db() as conn:
            row = conn.execute("SELECT * FROM inventory WHERE id = ?", (inventory_id,)).fetchone()
            if not row:
                return self.send_json({"error": "Inventory entry not found."}, HTTPStatus.NOT_FOUND)
            if not is_admin_role(user) and str(row["created_by"] or "") != str(user["username"] or ""):
                return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
            quantity_before = parse_int(row["quantity"], 0)
            if has_after:
                quantity_after = requested_after
                quantity_delta = quantity_after - quantity_before
            else:
                quantity_delta = requested_delta
                quantity_after = quantity_before + quantity_delta
            if quantity_delta == 0:
                return self.send_json({"error": "Adjustment would not change quantity."}, HTTPStatus.BAD_REQUEST)
            if quantity_after < 0:
                return self.send_json({"error": "Adjusted quantity cannot be below zero."}, HTTPStatus.BAD_REQUEST)
            adjusted_at = now_text()
            conn.execute(
                "UPDATE inventory SET quantity = ? WHERE id = ?",
                (quantity_after, inventory_id),
            )
            manual_adjustment_id = record_manual_inventory_adjustment(
                conn,
                owner_username=row["created_by"],
                actor=user,
                inventory_id=inventory_id,
                action="adjust",
                source="inventory_adjust_api",
                category=row["category"],
                name=row["name"],
                location=row["location"],
                note=row["note"] or "",
                quantity_before=quantity_before,
                quantity_after=quantity_after,
                quantity_delta=quantity_delta,
                reason=reason,
                created_at=adjusted_at,
            )
            updated_row = conn.execute("SELECT * FROM inventory WHERE id = ?", (inventory_id,)).fetchone()
            inventory_entry = inventory_entry_from_row(updated_row)
        export_current_inventory()
        return self.send_json(
            {
                "inventory_entry": inventory_entry,
                "inventory": inventory_entry,
                "manual_adjustment_id": manual_adjustment_id,
                "quantity_before": quantity_before,
                "quantity_after": quantity_after,
                "quantity_delta": quantity_delta,
            }
        )

    def api_admin_purchase_item_receipts(self, user, purchase_item_id, query):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        payload = admin_purchase_item_receipts_payload(purchase_item_id, query or {})
        if not payload:
            return self.send_json({"error": "Purchase item not found."}, HTTPStatus.NOT_FOUND)
        return self.send_json(payload)

    def api_admin_record_purchase_receipt(self, user, purchase_item_id):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        if parse_int(purchase_item_id, 0) <= 0:
            return self.send_json({"error": "Purchase item not found."}, HTTPStatus.NOT_FOUND)
        try:
            payload = self.read_api_payload()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        received_quantity = parse_int(payload_value(payload, "received_quantity", "quantity"), 0)
        with db() as conn:
            purchase_item = purchase_item_context(conn, purchase_item_id)
            if not purchase_item:
                return self.send_json({"error": "Purchase item not found."}, HTTPStatus.NOT_FOUND)
            try:
                receipt, summary = create_purchase_receipt(conn, purchase_item, user, received_quantity, payload)
            except OverflowError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
            except LookupError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self.send_json({"receipt": receipt, "purchase_item_receipt_summary": summary}, HTTPStatus.CREATED)

    def api_admin_finalize_purchase_receipt(self, user, receipt_id):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            payload = self.read_api_payload()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        try:
            with db() as conn:
                receipt, summary, inventory_entry = finalize_purchase_receipt_stock(conn, receipt_id, user, payload)
        except LookupError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except FileExistsError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        append_csv(
            [
                inventory_entry["created_at"],
                inventory_entry["category"],
                inventory_entry["name"],
                inventory_entry["quantity"],
                inventory_entry["location"],
                inventory_entry["note"],
                inventory_entry["created_by"],
            ]
        )
        export_current_inventory()
        return self.send_json(
            {
                "receipt": receipt,
                "purchase_item_receipt_summary": summary,
                "inventory_entry": inventory_entry,
            },
            HTTPStatus.CREATED,
        )

    def api_admin_reverse_purchase_receipt(self, user, receipt_id):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            payload = self.read_api_payload()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        reason = str(payload_value(payload, "reversal_reason", "reason") or "").strip()
        try:
            with db() as conn:
                receipt, summary, correction_entry = reverse_purchase_receipt_stock(conn, receipt_id, user, reason)
        except LookupError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
        except FileExistsError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        append_csv(
            [
                correction_entry["created_at"],
                correction_entry["category"],
                correction_entry["name"],
                correction_entry["quantity"],
                correction_entry["location"],
                correction_entry["note"],
                correction_entry["created_by"],
            ]
        )
        export_current_inventory()
        return self.send_json(
            {
                "receipt": receipt,
                "purchase_item_receipt_summary": summary,
                "inventory_entry": correction_entry,
                "correction_inventory_entry": correction_entry,
            },
            HTTPStatus.CREATED,
        )

    def api_admin_void_purchase_receipt(self, user, receipt_id):
        if not is_admin_role(user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        try:
            payload = self.read_api_payload()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        reason = str(payload_value(payload, "void_reason", "reason") or "").strip()
        with db() as conn:
            try:
                receipt, summary = void_purchase_receipt(conn, receipt_id, user, reason)
            except LookupError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.NOT_FOUND)
            except FileExistsError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.CONFLICT)
            except ValueError as exc:
                return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        return self.send_json({"receipt": receipt, "purchase_item_receipt_summary": summary})

    def api_bom_soldering_jobs(self, user, bom_id):
        if not bom_id:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        record = find_bom_record(bom_id)
        if not record:
            return self.send_json({"error": "BOM record not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_view_bom(record, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        jobs = list_soldering_jobs_for_bom(record, user)
        return self.send_json({"record": decorate_bom_record(record), "jobs": jobs, "count": len(jobs)})

    def api_soldering_job_consumption(self, user, job_id):
        if not job_id:
            return self.send_json({"error": "Soldering job not found."}, HTTPStatus.NOT_FOUND)
        job = get_soldering_job(job_id)
        if not job:
            return self.send_json({"error": "Soldering job not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_access_soldering_job(job, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        with db() as conn:
            consumption = soldering_consumption_summary(conn, job)
        return self.send_json(soldering_consumption_response(job, consumption))

    def api_finish_soldering_job(self, user, job_id):
        if not job_id:
            return self.send_json({"error": "Soldering job not found."}, HTTPStatus.NOT_FOUND)
        try:
            data = self.read_api_payload()
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        job = get_soldering_job(job_id)
        if not job:
            return self.send_json({"error": "Soldering job not found."}, HTTPStatus.NOT_FOUND)
        if not user_can_access_soldering_job(job, user):
            return self.send_json({"error": "Forbidden."}, HTTPStatus.FORBIDDEN)
        if job.get("status") not in SOLDERING_ACTIVE_STATUSES and job.get("status") != SOLDERING_COMPLETED_STATUS:
            return self.send_json({"error": "Soldering job is not active.", "job": job}, HTTPStatus.CONFLICT)
        notes = None
        if "notes" in data:
            notes = str(payload_value(data, "notes") or "").strip()
        record = find_bom_record(job.get("bom_id"))
        finish_result = finish_soldering_job_with_consumption(job, record, user, notes=notes)
        if finish_result.get("error") == "not_found":
            return self.send_json({"error": "Soldering job not found."}, HTTPStatus.NOT_FOUND)
        if finish_result.get("error") == "not_active":
            return self.send_json({"error": "Soldering job is not active.", "job": finish_result.get("job")}, HTTPStatus.CONFLICT)
        finished = finish_result["job"]
        consumption = finish_result["consumption"]
        record = find_bom_record(finished.get("bom_id"))
        updated = None
        if record and user_can_view_bom(record, user):
            updated = update_bom_record(
                finished.get("bom_id"),
                lambda current: mark_bom_soldering_finished(current, finished),
            )
        consumption_payload = soldering_consumption_response(finished, consumption)
        return self.send_json(
            {
                "job": finished,
                "record": decorate_bom_record(updated) if updated else updated,
                "inventory_consumed": bool(int(finished.get("inventory_consumed") or 0)),
                "consumption": consumption,
                "summary": consumption_payload["summary"],
                "totals": consumption_payload["totals"],
                "items": consumption_payload["items"],
                "ledger_items": consumption_payload["ledger_items"],
                "purchase_status": consumption.get("purchase_status", {}),
                "already_consumed": bool(finish_result.get("already_consumed")),
            }
        )

    def api_assistant_models(self, query, user):
        if user["role"] != "admin":
            return self.send_json({"error": "只有管理员可以获取模型列表。"}, HTTPStatus.FORBIDDEN)
        cfg = get_assistant_config()
        endpoint = (query.get("endpoint", [""])[0] or cfg.get("endpoint", "")).strip()
        api_key = cfg.get("api_key", "")
        try:
            models = fetch_assistant_models(endpoint, api_key, parse_int(cfg.get("timeout"), 30))
            return self.send_json(
                {
                    "models": models,
                    "count": len(models),
                    "models_url": assistant_models_url(endpoint),
                    "chat_url": assistant_chat_url(endpoint),
                }
            )
        except Exception as exc:
            return self.send_json({"error": public_error_message(exc, "模型列表读取失败，请检查 API 地址、白名单和网络。"), "models_url": assistant_models_url(endpoint)}, HTTPStatus.BAD_GATEWAY)

    def login(self):
        form = self.read_form()
        username = form.get("username", [""])[0].strip()
        password = form.get("password", [""])[0]
        if DEFAULT_PASSWORD_IN_USE and APP_ENV in ("production", "prod"):
            return self.send_html(
                render_auth("login", "生产环境必须先设置 WAREHOUSE_ADMIN_PASSWORD，不能使用默认管理员密码。"),
                HTTPStatus.SERVICE_UNAVAILABLE,
                headers=self.auth_cache_headers(),
            )
        blocked_for = self.login_block_seconds(username)
        if blocked_for > 0:
            return self.send_html(
                render_auth("login", f"登录尝试过多，请 {max(1, blocked_for // 60)} 分钟后再试。"),
                HTTPStatus.TOO_MANY_REQUESTS,
                headers=self.auth_cache_headers(),
            )
        with db() as conn:
            user = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
            if not user or not verify_password(password, user["password_hash"]):
                self.record_login_failure(username)
                time.sleep(0.35)
                return self.send_html(
                    render_auth("login", "账号或密码不正确。"),
                    HTTPStatus.UNAUTHORIZED,
                    headers=self.auth_cache_headers(),
                )
            if password_hash_needs_upgrade(user["password_hash"]):
                conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (hash_password(password), user["id"]))
        self.clear_login_failures(username)
        token = secrets.token_urlsafe(32)
        with SESSIONS_LOCK:
            cleanup_sessions()
            SESSIONS[token] = {"user_id": user["id"], "created_at": time.time()}
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_security_headers()
        self.send_header("Location", "/dashboard")
        self.send_header("Set-Cookie", self.session_cookie_value(token, max_age=SESSION_TTL_SECONDS))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", "0")
        self.end_headers()
        self.log_access_db(HTTPStatus.SEE_OTHER)

    def register(self):
        config = get_config()
        if config.get("allow_registration") != "1":
            return self.send_html(
                render_auth("register", "管理员已关闭自助注册。"),
                HTTPStatus.FORBIDDEN,
                headers=self.auth_cache_headers(),
            )
        form = self.read_form()
        username = form.get("username", [""])[0].strip()[:60]
        password = form.get("password", [""])[0]
        confirm = form.get("confirm", [""])[0]
        team_group = normalize_team_group(form.get("team_group", [""])[0])
        policy_error = password_policy_error(password, "user", username)
        if policy_error:
            return self.send_html(
                render_auth("register", policy_error),
                HTTPStatus.BAD_REQUEST,
                headers=self.auth_cache_headers(),
            )
        if not username or len(password) < 6:
            return self.send_html(
                render_auth("register", "账号不能为空，密码至少 6 位。"),
                HTTPStatus.BAD_REQUEST,
                headers=self.auth_cache_headers(),
            )
        if password != confirm:
            return self.send_html(
                render_auth("register", "两次密码不一致。"),
                HTTPStatus.BAD_REQUEST,
                headers=self.auth_cache_headers(),
            )
        try:
            with db() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, team_group, created_at) VALUES (?, ?, 'user', ?, ?)",
                    (username, hash_password(password), team_group, now_text()),
                )
        except sqlite3.IntegrityError:
            return self.send_html(
                render_auth("register", "账号已存在。"),
                HTTPStatus.CONFLICT,
                headers=self.auth_cache_headers(),
            )
        return self.send_html(render_auth("login", message="注册成功，请登录。"), headers=self.auth_cache_headers())

    def logout(self):
        cookie = safe_cookie(self.headers.get("Cookie", ""))
        token = cookie.get("session")
        if token:
            with SESSIONS_LOCK:
                SESSIONS.pop(token.value, None)
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_security_headers()
        self.send_header("Location", "/login")
        self.send_header("Set-Cookie", self.session_cookie_value("", max_age=0))
        self.send_header("Cache-Control", "no-store, max-age=0")
        self.send_header("Content-Length", "0")
        self.end_headers()
        self.log_access_db(HTTPStatus.SEE_OTHER)

    def create_workflow(self, user):
        try:
            if "multipart/form-data" in self.headers.get("Content-Type", "").lower():
                fields, files = self.read_multipart()
            else:
                raw = self.read_form()
                fields = {key: values[-1] if values else "" for key, values in raw.items()}
                files = {}
            name = str(fields.get("project_name") or fields.get("name") or "").strip()[:120]
            detail = str(fields.get("detail") or "").strip()[:4000]
            duration_days = clamp_int(fields.get("duration_days"), 14, 1, 365)
            if not name or not detail:
                return self.send_html(workflow_page(user, error="请填写项目名称和项目详细内容。"), HTTPStatus.BAD_REQUEST)
            collaborators = [item for item in workflow_collaborators_from_fields(fields) if item != user["username"]]
            workflow_id = f"wf_{datetime.now().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
            uploads = collect_uploaded_files(files, ("project_file",))
            file_meta = store_workflow_upload(uploads[0], workflow_id, user, "project") if uploads else {}
            ai_plan = build_workflow_ai_plan(name, detail, duration_days, collaborators, user, fields)
            stages = ai_plan.get("stages") or workflow_stage_plan(name, detail, duration_days, collaborators, user["username"])
            now = now_text()
            due_at = (datetime.now() + timedelta(days=duration_days)).strftime("%Y-%m-%d %H:%M:%S")
            with db() as conn:
                conn.execute(
                    """
                    INSERT INTO workflow_projects (
                        workflow_id, name, detail, duration_days, owner_username, owner_group,
                        collaborators_json, file_json, ai_plan_json, status, progress,
                        started_at, due_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'planned', 0, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        name,
                        detail,
                        duration_days,
                        user["username"],
                        user["team_group"] or "",
                        json.dumps(collaborators, ensure_ascii=False),
                        json.dumps(file_meta, ensure_ascii=False),
                        json.dumps({key: value for key, value in ai_plan.items() if key != "stages"}, ensure_ascii=False),
                        now,
                        due_at,
                        now,
                        now,
                    ),
                )
                for stage in stages:
                    conn.execute(
                        """
                        INSERT INTO workflow_stages (
                            workflow_id, title, category, detail, owner_username, start_day,
                            end_day, status, progress, color, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            workflow_id,
                            stage.get("title", "阶段"),
                            stage.get("category", "stage"),
                            stage.get("detail", ""),
                            stage.get("owner_username") or user["username"],
                            parse_int(stage.get("start_day"), 1),
                            parse_int(stage.get("end_day"), duration_days),
                            stage.get("status", "planned"),
                            clamp_int(stage.get("progress"), 0, 0, 100),
                            stage.get("color", WORKFLOW_STAGE_COLORS[0]),
                            now,
                            now,
                        ),
                    )
                conn.execute(
                    """
                    INSERT INTO workflow_updates (
                        workflow_id, stage_id, username, update_type, status, progress,
                        note, file_json, pomodoro_minutes, created_at
                    ) VALUES (?, NULL, ?, 'create', 'active', 0, ?, ?, 0, ?)
                    """,
                    (
                        workflow_id,
                        user["username"],
                        "创建工作流并写入 AI 阶段规划。",
                        json.dumps(file_meta, ensure_ascii=False),
                        now,
                    ),
                )
                update_workflow_project_rollup(conn, workflow_id)
            return self.send_html(workflow_page(user, message="工作流已创建，组内成员和协作者现在可以查看并更新进度。"))
        except Exception as exc:
            return self.send_html(workflow_page(user, error=str(exc)), HTTPStatus.BAD_REQUEST)

    def update_workflow(self, user):
        try:
            if "multipart/form-data" in self.headers.get("Content-Type", "").lower():
                fields, files = self.read_multipart()
            else:
                raw = self.read_form()
                fields = {key: values[-1] if values else "" for key, values in raw.items()}
                files = {}
            workflow_id = str(fields.get("workflow_id") or "").strip()
            stage_id = parse_int(fields.get("stage_id"), 0)
            status = str(fields.get("status") or "active").strip()
            if status not in WORKFLOW_STATUS_LABELS:
                status = "active"
            progress = clamp_int(fields.get("progress"), 0, 0, 100)
            if status == "done":
                progress = max(progress, 100)
            note = str(fields.get("note") or "").strip()[:1200]
            pomodoro_minutes = clamp_int(fields.get("pomodoro_minutes"), 0, 0, 240)
            with db() as conn:
                row = conn.execute("SELECT * FROM workflow_projects WHERE workflow_id = ?", (workflow_id,)).fetchone()
                if not row:
                    return self.send_html(workflow_page(user, error="工作流不存在。"), HTTPStatus.NOT_FOUND)
                project = workflow_project_from_row(row)
                if not workflow_user_can_edit(project, user):
                    return self.send_html(workflow_page(user, error="无权限更新该工作流。"), HTTPStatus.FORBIDDEN)
                stage = conn.execute(
                    "SELECT * FROM workflow_stages WHERE id = ? AND workflow_id = ?",
                    (stage_id, workflow_id),
                ).fetchone()
                if not stage:
                    return self.send_html(workflow_page(user, error="阶段不存在。"), HTTPStatus.NOT_FOUND)
                uploads = collect_uploaded_files(files, ("result_file",))
                file_meta = store_workflow_upload(uploads[0], workflow_id, user, "result") if uploads else {}
                now = now_text()
                conn.execute(
                    """
                    UPDATE workflow_stages
                    SET status = ?, progress = ?, updated_at = ?
                    WHERE id = ? AND workflow_id = ?
                    """,
                    (status, progress, now, stage_id, workflow_id),
                )
                conn.execute(
                    """
                    INSERT INTO workflow_updates (
                        workflow_id, stage_id, username, update_type, status, progress,
                        note, file_json, pomodoro_minutes, created_at
                    ) VALUES (?, ?, ?, 'stage_update', ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        workflow_id,
                        stage_id,
                        user["username"],
                        status,
                        progress,
                        note or f"更新阶段：{workflow_status_label(status)} {progress}%",
                        json.dumps(file_meta, ensure_ascii=False),
                        pomodoro_minutes,
                        now,
                    ),
                )
                update_workflow_project_rollup(conn, workflow_id)
            return self.send_html(workflow_page(user, message="工作流状态已实时写入。"))
        except Exception as exc:
            return self.send_html(workflow_page(user, error=str(exc)), HTTPStatus.BAD_REQUEST)

    def api_workflow_models(self, user):
        try:
            data = self.read_api_payload()
        except Exception:
            data = {}
        endpoint = str(payload_value(data, "endpoint") or "").strip()
        api_key = str(payload_value(data, "api_key") or "").strip()
        if not endpoint:
            return self.send_json({"error": "请填写 API 地址。"}, HTTPStatus.BAD_REQUEST)
        steps = [
            "读取用户输入的 API 地址",
            "拼接兼容 OpenAI 的 /models 端点",
            "携带临时密钥请求模型列表",
        ]
        try:
            models = fetch_assistant_models(endpoint, api_key, 20)
            steps.append(f"发现 {len(models)} 个可用模型")
            return self.send_json(
                {
                    "models": models,
                    "count": len(models),
                    "steps": steps,
                    "models_url": assistant_models_url(endpoint),
                    "chat_url": assistant_chat_url(endpoint),
                }
            )
        except Exception as exc:
            steps.append("模型探测失败，保留手动填写模型名入口")
            return self.send_json(
                {"error": str(exc), "steps": steps, "models_url": assistant_models_url(endpoint)},
                HTTPStatus.BAD_GATEWAY,
            )

    def create_inventory(self, user):
        form = self.read_form()
        try:
            category = (form.get("category", [""])[0] or "").strip()
            name = (form.get("name", [""])[0] or "").strip()
            quantity = max(1, parse_int(form.get("quantity", ["1"])[0], 1))
            location = require_text(form, "location")
            note = (form.get("note", [""])[0] or "").strip()[:300]
        except ValueError:
            return self.send_html(new_inventory_page(user, error="请完整填写必填项。"), HTTPStatus.BAD_REQUEST)
        lcsc_code = (form.get("lcsc_code", [""])[0] or "").strip().upper()
        value_spec = (form.get("value_spec", [""])[0] or "").strip()
        package = (form.get("package", [""])[0] or "").strip()
        voltage = (form.get("voltage", [""])[0] or "").strip()
        brand = (form.get("brand", [""])[0] or "").strip()
        product_url = (form.get("product_url", [""])[0] or "").strip()
        product = None
        if lcsc_code:
            result = enrich_lcsc_codes([lcsc_code], force=False, max_codes=1)
            product = result["cache"].get(lcsc_code)
            if product and product.get("status") == "ok":
                specs = extract_lcsc_specs(product)
                package = package or str(specs.get("package") or "")
                brand = brand or str(specs.get("brand") or "")
                product_url = product_url or str(specs.get("url") or "")
                category = category or str(specs.get("category") or "")
                params = product.get("params") if isinstance(product.get("params"), dict) else {}
                value_spec = value_spec or str(specs.get("value_spec") or params.get("阻值") or params.get("容值") or "")
                voltage = voltage or str(specs.get("voltage") or specs.get("power") or "")
                name = compose_inventory_name(name, value_spec, package, voltage, product, lcsc_code)
        name = compose_inventory_name(name, value_spec, package, voltage, None, lcsc_code)
        if not category:
            category = infer_category(value_spec or name, "", package)
        if not name or not category:
            return self.send_html(new_inventory_page(user, error="请至少填写商品名称或 LCSC 编号。"), HTTPStatus.BAD_REQUEST)
        extra_note = {
            "规格": value_spec,
            "封装": package,
            "耐压": voltage,
            "品牌": brand,
            "LCSC": lcsc_code,
            "链接": product_url,
        }
        note = build_product_note(note, product, extra_note)[:800]
        created_at = now_text()
        with db() as conn:
            cur = conn.execute(
                """
                INSERT INTO inventory (category, name, quantity, location, note, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (category, name, quantity, location, note, user["username"], created_at),
            )
            inventory_id = cur.lastrowid
            record_manual_inventory_adjustment(
                conn,
                owner_username=user["username"],
                actor=user,
                inventory_id=inventory_id,
                action="create",
                source="inventory_form",
                category=category,
                name=name,
                location=location,
                note=note,
                quantity_before=0,
                quantity_after=quantity,
                quantity_delta=quantity,
                reason=note,
                created_at=created_at,
            )
        append_csv([created_at, category, name, quantity, location, note, user["username"]])
        export_current_inventory()
        return self.send_html(new_inventory_page(user, message="已保存入库记录。"))

    def upload_bom(self, user):
        try:
            _, files = self.read_multipart()
            uploaded_files = collect_uploaded_files(files, ("bom_file",))
            uploaded = uploaded_files[0] if uploaded_files else None
            if not uploaded or not uploaded["filename"]:
                raise ValueError("请选择 BOM 文件。")
            filename = safe_name(uploaded["filename"])
            suffix = Path(filename).suffix.lower()
            if suffix not in (".csv", ".xlsx", ".txt"):
                raise ValueError("仅支持 csv、xlsx、txt 文件。")
            if len(uploaded.get("content") or b"") > BOM_UPLOAD_MAX_BYTES:
                raise ValueError("BOM 文件超过 12MB，请拆分或压缩后再上传。")
            stored = f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}_{filename}"
            target = UPLOADS_DIR / stored
            with db() as conn:
                write_data_bytes(target, uploaded["content"])
                result = process_bom_upload(target, uploaded["filename"], user, conn=conn)
            return self.send_html(bom_page(user, message="BOM 已读取并完成库存对照。", result=result))
        except Exception as exc:
            return self.send_html(bom_page(user, error=str(exc)), HTTPStatus.BAD_REQUEST)

    def create_spare_bop(self, user):
        try:
            form = self.read_form()
            title = require_text(form, "title", max_len=120)
            season = (form.get("season", [""])[0] or "").strip()[:80]
            robot_scope = (form.get("robot_scope", ["dual_robot"])[0] or "dual_robot").strip()
            items = parse_spare_bop_text(form.get("items_text", [""])[0])
            bop_id = create_spare_bop_list(user, title, season, robot_scope, items)
            query = {"bop_id": [bop_id]}
            return self.send_html(spare_bop_page(user, query, message=f"已保存 {len(items)} 行备件 BOP，并完成库存缺口检查。"))
        except Exception as exc:
            return self.send_html(spare_bop_page(user, {}, error=str(exc)), HTTPStatus.BAD_REQUEST)

    def create_competition_material_item(self, user):
        try:
            form = self.read_form()
            item = create_competition_material_manual_item(user, form)
            return self.send_html(spare_bop_page(user, message=f"已保存比赛物资：{item['name']}。"))
        except Exception as exc:
            return self.send_html(spare_bop_page(user, error=str(exc)), HTTPStatus.BAD_REQUEST)

    def upload_competition_material_bom(self, user):
        try:
            fields, files = self.read_multipart()
            uploaded_files = collect_uploaded_files(files, ("competition_bom_file", "bom_file"))
            uploaded = uploaded_files[0] if uploaded_files else None
            result = create_competition_material_bom(
                user,
                fields.get("title", ""),
                fields.get("robot_name", ""),
                fields.get("season", ""),
                fields.get("owner_name", ""),
                fields.get("owner_group", ""),
                uploaded,
            )
            return self.send_html(
                spare_bop_page(user, message=f"已上传比赛物资 BOM：{result['title']}，写入 {result['item_count']} 行。")
            )
        except Exception as exc:
            return self.send_html(spare_bop_page(user, error=str(exc)), HTTPStatus.BAD_REQUEST)

    def create_spare_bop_purchase(self, user):
        return self.send_html(
            spare_bop_page(user, error="比赛物资备件已改为独立仓库，不再写入原 BOM 采购单。请在比赛物资表单中维护责任人与组别。"),
            HTTPStatus.BAD_REQUEST,
        )

    def create_user(self, user):
        form = self.read_form()
        if user["role"] != "admin":
            return self.send_html(users_page(user, error="无权限。"), HTTPStatus.FORBIDDEN)
        username = form.get("username", [""])[0].strip()[:60]
        password = form.get("password", [""])[0]
        role = form.get("role", ["user"])[0]
        team_group = normalize_team_group(form.get("team_group", [""])[0])
        policy_error = password_policy_error(password, role, username)
        if policy_error:
            return self.send_html(users_page(user, error=policy_error), HTTPStatus.BAD_REQUEST)
        if not username or len(password) < 6 or role not in ("user", "admin"):
            return self.send_html(users_page(user, error="账号不能为空，密码至少 6 位。"), HTTPStatus.BAD_REQUEST)
        try:
            with db() as conn:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, team_group, created_at) VALUES (?, ?, ?, ?, ?)",
                    (username, hash_password(password), role, team_group, now_text()),
                )
        except sqlite3.IntegrityError:
            return self.send_html(users_page(user, error="账号已存在。"), HTTPStatus.CONFLICT)
        return self.send_html(users_page(user, message="用户已创建。"))

    def save_admin(self, user):
        if user["role"] != "admin":
            return self.send_html(admin_page(user, error="无权限。"), HTTPStatus.FORBIDDEN)
        form = self.read_form()
        config = save_config({key: form.get(key, [DEFAULT_CONFIG.get(key, "")])[0] for key in DEFAULT_CONFIG})
        global TUNNEL_URL
        TUNNEL_URL = config.get("public_url") or TUNNEL_URL
        return self.send_html(admin_page(user, message="配置已保存。端口、数据目录和穿透方式重启后生效。"))

    def save_assistant(self, user):
        if user["role"] != "admin":
            return self.send_html(admin_page(user, error="无权限。"), HTTPStatus.FORBIDDEN)
        form = self.read_form()
        values = {key: form.get(key, [DEFAULT_ASSISTANT_CONFIG.get(key, "")])[0] for key in DEFAULT_ASSISTANT_CONFIG}
        try:
            if str(values.get("enabled", "")).strip() == "1" or str(values.get("endpoint", "")).strip():
                validate_external_api_url(assistant_chat_url(values.get("endpoint", "")))
        except Exception as exc:
            return self.send_html(admin_page(user, error=public_error_message(exc, "API 配置未通过安全校验。")), HTTPStatus.BAD_REQUEST)
        save_assistant_config(values)
        return self.send_html(admin_page(user, message="库存智能助手 API 配置已保存。"))

    def save_image_recognition(self, user):
        if user["role"] != "admin":
            return self.send_html(admin_page(user, error="无权限。"), HTTPStatus.FORBIDDEN)
        form = self.read_form()
        values = {
            key: form.get(key, [DEFAULT_IMAGE_RECOGNITION_CONFIG.get(key, "")])[0]
            for key in DEFAULT_IMAGE_RECOGNITION_CONFIG
        }
        try:
            if str(values.get("enabled", "")).strip() == "1" or str(values.get("endpoint", "")).strip():
                validate_external_api_url(assistant_chat_url(values.get("endpoint", "")))
        except Exception as exc:
            return self.send_html(image_recognition_config_page(user, error=public_error_message(exc, "图片识别 API 配置未通过安全校验。")), HTTPStatus.BAD_REQUEST)
        save_image_recognition_config(values)
        return self.send_html(image_recognition_config_page(user, message="入库图片识别 API 配置已保存。"))

    def api_inventory_image_recognize(self, user):
        try:
            _, files = self.read_multipart()
            uploads = collect_uploaded_files(files, INVENTORY_IMAGE_FIELD_NAMES)
            upload = uploads[0] if uploads else None
            if not upload:
                return self.send_json({"error": "请上传一张器件图片。"}, HTTPStatus.BAD_REQUEST)
            filename = safe_name(upload.get("filename") or "inventory_image")
            content = upload.get("content") or b""
            if not content:
                return self.send_json({"error": "图片内容为空。"}, HTTPStatus.BAD_REQUEST)
            if len(content) > INVENTORY_IMAGE_MAX_BYTES:
                return self.send_json({"error": "图片超过 8MB，请压缩后再上传。"}, HTTPStatus.BAD_REQUEST)
            mime_type = str(upload.get("content_type") or mimetypes.guess_type(filename)[0] or "").split(";", 1)[0].lower()
            if mime_type not in INVENTORY_IMAGE_ALLOWED_MIME_TYPES:
                return self.send_json({"error": "仅支持 JPG、PNG、WEBP 或 GIF 图片。"}, HTTPStatus.BAD_REQUEST)
            result = call_inventory_image_recognition(content, mime_type, filename, user)
            status = HTTPStatus.OK if result.get("items") else HTTPStatus.UNPROCESSABLE_ENTITY
            if not result.get("items"):
                result["error"] = "AI 未能从图片中识别出可入库器件，请换一张更清晰的图片或手动录入。"
            return self.send_json(result, status)
        except ValueError as exc:
            return self.send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            return self.send_json({"error": inventory_image_error_message(exc)}, HTTPStatus.BAD_GATEWAY)

    def api_assistant_chat(self, user):
        try:
            data = self.read_json()
        except Exception:
            return self.send_json({"error": "请求 JSON 格式不正确。"}, HTTPStatus.BAD_REQUEST)
        question = str(data.get("question", "")).strip()
        if not question:
            return self.send_json({"error": "请输入问题。"}, HTTPStatus.BAD_REQUEST)
        steps = [
            "读取库存摘要与 BOM 缺口",
            "载入当前用户历史知识文档",
            "组装受限系统提示词",
            "调用已配置模型 API",
        ]
        save_assistant_message(user["username"], "user", question, {"source": "chat"})
        result = call_inventory_assistant(question, user)
        if result.get("answer"):
            save_assistant_message(
                user["username"],
                "assistant",
                result["answer"],
                {"note": result.get("note", ""), "endpoint": result.get("endpoint", "")},
            )
            steps.append("写入个人知识文档与动态图谱")
        elif result.get("error"):
            save_assistant_message(user["username"], "error", result["error"], {"source": "assistant_api"})
            steps.append("记录错误到个人助手历史")
        result["steps"] = steps
        status = HTTPStatus.BAD_GATEWAY if result.get("error", "").startswith("助手 API 调用失败") else HTTPStatus.OK
        return self.send_json(result, status)

    def api_assistant_test(self, user):
        if user["role"] != "admin":
            return self.send_json({"error": "只有管理员可以测试助手 API。"}, HTTPStatus.FORBIDDEN)
        try:
            data = self.read_json()
        except Exception:
            return self.send_json({"error": "请求 JSON 格式不正确。"}, HTTPStatus.BAD_REQUEST)
        cfg = get_assistant_config()
        endpoint = data.get("endpoint") or cfg.get("endpoint", "")
        api_key = data.get("api_key") or cfg.get("api_key", "")
        model = data.get("model") or cfg.get("model", "")
        if not model:
            return self.send_json({"error": "请先选择或填写模型名。"}, HTTPStatus.BAD_REQUEST)
        body = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "temperature": 0,
            "max_tokens": 32,
        }
        try:
            data = assistant_http_json(assistant_chat_url(endpoint), api_key, body, parse_int(cfg.get("timeout"), 30))
            return self.send_json(
                {
                    "ok": True,
                    "chat_url": assistant_chat_url(endpoint),
                    "models_url": assistant_models_url(endpoint),
                    "model": model,
                    "sample": json.dumps(data, ensure_ascii=False)[:500],
                }
            )
        except Exception as exc:
            return self.send_json(
                {"ok": False, "error": str(exc), "chat_url": assistant_chat_url(endpoint), "model": model},
                HTTPStatus.BAD_GATEWAY,
            )

    def refresh_lcsc_cache(self, user):
        if user["role"] != "admin":
            return self.send_html(reports_page(user, "只有管理员可以刷新 LCSC 缓存。"), HTTPStatus.FORBIDDEN)
        values = []
        with db() as conn:
            values.extend(row["name"] for row in conn.execute("SELECT name FROM bom_items").fetchall())
            values.extend(row["name"] for row in conn.execute("SELECT name FROM purchase_items").fetchall())
        codes = extract_lcsc_codes(values)
        result = enrich_lcsc_codes(codes, force=False)
        return self.send_html(reports_page(user, f"LCSC 缓存已刷新：识别 {len(codes)} 个 C 编号，本次联网抓取 {len(result['fetched'])} 个。"))

    def download(self, query):
        user = current_user(self)
        if not user:
            return self.redirect("/login")
        if not is_admin_role(user):
            self.send_error(HTTPStatus.FORBIDDEN)
            self.log_access_db(HTTPStatus.FORBIDDEN)
            return
        kind = query.get("type", [""])[0]
        name = Path(query.get("name", [""])[0]).name
        bases = {
            "reports": REPORTS_DIR,
            "forms": FORMS_DIR,
            "uploads": UPLOADS_DIR,
            "purchases": PURCHASES_DIR,
            "lcsc": LCSC_DIR,
        }
        base = bases.get(kind)
        if not base:
            self.send_error(HTTPStatus.BAD_REQUEST)
            self.log_access_db(HTTPStatus.BAD_REQUEST)
            return
        target = (base / name).resolve()
        if not path_is_relative_to(target, base) or not target.exists() or not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            self.log_access_db(HTTPStatus.NOT_FOUND)
            return
        data = read_data_bytes(target)
        self.send_response(HTTPStatus.OK)
        self.send_security_headers()
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Disposition", "attachment; " + content_disposition_filename(target.name))
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        self.log_access_db(HTTPStatus.OK)


def main():
    global HOST, BOOT_CONFIG
    enforce_production_secrets()
    ensure_crypto_ready()
    if DATA_ENCRYPTION_ENABLED:
        print("[security] Data encryption at rest is enabled (AES-256-GCM).")
    init_db()
    migrate_runtime_files_to_encryption()
    reencrypt_secret_config_files()
    if existing_config_is_insecure_default(BOOT_CONFIG) and not os.environ.get("WAREHOUSE_HOST"):
        BOOT_CONFIG = save_config({**BOOT_CONFIG, "host": "127.0.0.1", "tunnel_mode": "off", "allow_registration": "0"})
        HOST = "127.0.0.1"
        print("[security] Migrated legacy default network settings to localhost, tunnel off, registration off.")
    threading.Thread(target=report_scheduler, daemon=True).start()
    start_analysis_worker_scheduler()
    tunnel_setting = os.environ.get("WAREHOUSE_TUNNEL", get_config().get("tunnel_mode", "off")).lower()
    if tunnel_setting in ("1", "true", "on", "auto"):
        start_tunnel()
    server = ThreadingHTTPServer((HOST, PORT), WarehouseHandler)
    url = f"http://127.0.0.1:{PORT}"
    print("")
    print("Warehouse Inventory Server")
    print(f"Bind:    {HOST}:{PORT}")
    print(f"Local:   {url}")
    if HOST in ("0.0.0.0", "::"):
        print(f"LAN:     http://{local_ip()}:{PORT}")
    else:
        print("LAN:     disabled (set WAREHOUSE_HOST=0.0.0.0 or update admin host and restart to enable)")
    print(f"Data:    {DATA_DIR}")
    print(f"Login:   {DEFAULT_ADMIN} / <configured password>")
    if DEFAULT_PASSWORD_IN_USE:
        print("Warning: default admin password is active. Set WAREHOUSE_ADMIN_PASSWORD before real deployment.")
    if TUNNEL_URL:
        print(f"Tunnel:  {TUNNEL_URL}")
    print("Press Ctrl+C to stop.")
    if os.environ.get("WAREHOUSE_OPEN_BROWSER", "1") != "0":
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        STOP_EVENT.set()
        server.server_close()
        if TUNNEL_PROCESS:
            TUNNEL_PROCESS.send_signal(signal.SIGTERM)


if __name__ == "__main__":
    main()
