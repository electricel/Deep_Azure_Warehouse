"""Helpers for EasyEDA Pro .epro project uploads.

The official EasyEDA Pro format converter is a desktop tool and does not expose
stable public CLI flags in the project codebase.  This module therefore supports
three deterministic paths:

1. Inspect an .epro ZIP and extract already-converted PCB artifacts when present.
2. Parse EasyEDA Pro .epcb JSON Lines into lightweight Interactive BOM JSON.
3. Optionally call a user-configured converter command and collect its outputs.

Set WAREHOUSE_EPRO_CONVERTER_CMD to a command containing {input} and
{output_dir} placeholders to enable path 3.
"""

from __future__ import annotations

import io
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


SUPPORTED_OUTPUT_EXTENSIONS = {".kicad_pcb", ".json", ".brd", ".fbrd"}
PREFERRED_OUTPUT_EXTENSIONS = (".kicad_pcb", ".json", ".brd", ".fbrd")
MAX_EXTRACTED_FILE_BYTES = 80 * 1024 * 1024
_REF_RE = re.compile(r"^[A-Za-z]{1,5}\d+[A-Za-z0-9._-]*$")
_STANDARD_CHIP_RE = re.compile(r"(?<!\d)(0201|0402|0603|0805|1206|1210|1812|2512)(?!\d)")


_STANDALONE_IBOM_CSS = """
:root {
  color-scheme: light;
  --bg: #f5f7fb;
  --panel: #ffffff;
  --ink: #111827;
  --muted: #64748b;
  --line: #d8dee9;
  --accent: #2563eb;
  --accent-2: #0f766e;
  --danger: #b45309;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font: 14px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}
.page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 18px;
}
header {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
}
h1 {
  margin: 0;
  font-size: 22px;
  line-height: 1.2;
}
.meta {
  margin-top: 4px;
  color: var(--muted);
  font-size: 13px;
}
.toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
}
input[type="search"] {
  width: min(360px, 48vw);
  min-width: 220px;
  border: 1px solid var(--line);
  border-radius: 6px;
  padding: 9px 11px;
  background: #fff;
  color: var(--ink);
}
.count {
  color: var(--muted);
  white-space: nowrap;
}
.interactive-bom-viewer {
  flex: 1;
  min-height: 0;
  display: grid;
  grid-template-columns: minmax(360px, 1.1fr) minmax(360px, 0.9fr);
  gap: 16px;
}
.board-pane,
.table-pane {
  min-height: 0;
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  overflow: hidden;
}
.board {
  position: relative;
  height: 100%;
  min-height: 520px;
  background:
    linear-gradient(90deg, rgba(255,255,255,0.07) 1px, transparent 1px),
    linear-gradient(rgba(255,255,255,0.07) 1px, transparent 1px),
    #154a40;
  background-size: 24px 24px;
}
.board:before {
  content: "";
  position: absolute;
  inset: 18px;
  border: 2px solid rgba(255,255,255,0.35);
  border-radius: 10px;
  pointer-events: none;
}
.marker {
  position: absolute;
  z-index: 2;
  border: 1px solid rgba(255,255,255,0.82);
  border-radius: 4px;
  background: rgba(248, 250, 252, 0.86);
  color: #09251f;
  font-weight: 700;
  font-size: 11px;
  line-height: 1;
  overflow: hidden;
  cursor: pointer;
}
.marker[data-side="bottom"] {
  background: rgba(254, 243, 199, 0.9);
  color: #713f12;
}
.marker:hover,
.marker.active {
  background: #ffffff;
  border-color: #facc15;
  box-shadow: 0 0 0 3px rgba(250, 204, 21, 0.42);
}
.table-pane {
  overflow: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
}
th,
td {
  padding: 9px 10px;
  border-bottom: 1px solid #e5e7eb;
  text-align: left;
  vertical-align: top;
}
th {
  position: sticky;
  top: 0;
  z-index: 1;
  background: #f8fafc;
  color: #475569;
  font-size: 12px;
  text-transform: uppercase;
}
tr {
  cursor: pointer;
}
tr:hover,
tr.active {
  background: #eff6ff;
}
[hidden] {
  display: none !important;
}
.empty {
  padding: 28px;
  color: var(--muted);
}
@media (max-width: 900px) {
  .page { padding: 12px; }
  header { align-items: stretch; flex-direction: column; }
  .toolbar { align-items: stretch; flex-direction: column; }
  input[type="search"] { width: 100%; min-width: 0; }
  .interactive-bom-viewer { grid-template-columns: 1fr; }
  .board { min-height: 360px; }
}
"""


_STANDALONE_IBOM_JS = """
(function () {
  "use strict";

  function parsePayload() {
    var script = document.querySelector("script[data-interactive-bom-payload]");
    if (!script) return {};
    try {
      return JSON.parse(script.textContent || "{}");
    } catch (_err) {
      return {};
    }
  }

  function text(value) {
    return value == null ? "" : String(value).trim();
  }

  function asArray(value) {
    if (Array.isArray(value)) return value;
    if (value == null || value === "") return [];
    return [value];
  }

  function footprintBox(footprint) {
    if (!footprint) return null;
    var bbox = footprint.bbox || {};
    var pos = Array.isArray(bbox.pos) ? bbox.pos : footprint.center;
    var relpos = Array.isArray(bbox.relpos) ? bbox.relpos : [-1.5, -1.2];
    var size = Array.isArray(bbox.size) ? bbox.size : [3, 2.4];
    if (!Array.isArray(pos) || pos.length < 2 || !Array.isArray(size) || size.length < 2) return null;
    var x = Number(pos[0]) + Number(relpos[0] || 0);
    var y = Number(pos[1]) + Number(relpos[1] || 0);
    var w = Number(size[0]);
    var h = Number(size[1]);
    if (![x, y, w, h].every(Number.isFinite)) return null;
    return { x: x, y: y, w: Math.max(w, 0.2), h: Math.max(h, 0.2) };
  }

  function normalizeComponents(payload) {
    var pcbdata = payload && payload.pcbdata ? payload.pcbdata : {};
    var footprints = Array.isArray(pcbdata.footprints) ? pcbdata.footprints : [];
    var bom = pcbdata.bom || {};
    var rows = Array.isArray(bom.both) && bom.both.length
      ? bom.both
      : footprints.map(function (fp, index) { return [[text(fp.ref) || ("FP" + (index + 1)), index]]; });
    var fields = bom.fields && typeof bom.fields === "object" ? bom.fields : {};

    return rows.map(function (row, index) {
      var pairs = Array.isArray(row) ? row : [];
      var ids = pairs.map(function (pair) {
        return Array.isArray(pair) ? Number(pair[1]) : NaN;
      }).filter(function (id) {
        return Number.isInteger(id) && id >= 0;
      });
      var refs = pairs.map(function (pair) {
        return Array.isArray(pair) ? text(pair[0]) : text(pair);
      }).filter(Boolean);
      var footprint = footprints[ids[0]] || {};
      var rowFields = asArray(fields[String(ids[0])]).map(text);
      return {
        id: "cmp-" + index,
        refs: refs.length ? refs : [text(footprint.ref) || ("FP" + (index + 1))],
        name: rowFields[0] || text(footprint.ref),
        value: rowFields[1] || "",
        footprint: rowFields[2] || "",
        lcsc: rowFields[3] || "",
        side: text(footprint.layer) === "B" ? "bottom" : "top",
        bbox: footprintBox(footprint)
      };
    });
  }

  function boundsFor(components) {
    var boxes = components.map(function (item) { return item.bbox; }).filter(Boolean);
    if (!boxes.length) return null;
    var minX = Math.min.apply(null, boxes.map(function (box) { return box.x; }));
    var minY = Math.min.apply(null, boxes.map(function (box) { return box.y; }));
    var maxX = Math.max.apply(null, boxes.map(function (box) { return box.x + box.w; }));
    var maxY = Math.max.apply(null, boxes.map(function (box) { return box.y + box.h; }));
    var width = maxX - minX;
    var height = maxY - minY;
    if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) return null;
    return { minX: minX, minY: minY, width: width, height: height };
  }

  function clamp(value, min, max) {
    return Math.min(Math.max(value, min), max);
  }

  function rectFor(component, bounds, index, total) {
    if (component.bbox && bounds) {
      return {
        left: clamp(((component.bbox.x - bounds.minX) / bounds.width) * 100, 1, 97),
        top: clamp(((component.bbox.y - bounds.minY) / bounds.height) * 100, 1, 97),
        width: clamp((component.bbox.w / bounds.width) * 100, 2.2, 24),
        height: clamp((component.bbox.h / bounds.height) * 100, 2.2, 16)
      };
    }
    var cols = Math.ceil(Math.sqrt(Math.max(total, 1)));
    var rows = Math.ceil(total / cols);
    var col = index % cols;
    var row = Math.floor(index / cols);
    return {
      left: col * (100 / cols) + 4,
      top: row * (100 / rows) + 5,
      width: Math.max(100 / cols - 8, 4),
      height: Math.max(100 / rows - 10, 3)
    };
  }

  function appendCell(row, value) {
    var cell = document.createElement("td");
    cell.textContent = value || "-";
    row.appendChild(cell);
  }

  function init() {
    var payload = parsePayload();
    var components = normalizeComponents(payload);
    var viewer = document.querySelector(".interactive-bom-viewer");
    var board = document.querySelector(".board");
    var tbody = document.querySelector("tbody");
    var search = document.querySelector("input[type='search']");
    var count = document.querySelector(".count");
    if (!viewer || !board || !tbody) return;
    if (!components.length) {
      board.innerHTML = "<div class='empty'>暂无可解析的元件坐标数据。</div>";
      if (count) count.textContent = "0 个元件";
      return;
    }

    var bounds = boundsFor(components);
    var markers = {};
    var rows = {};
    components.forEach(function (component, index) {
      var label = component.refs.join(", ");
      var rect = rectFor(component, bounds, index, components.length);
      var marker = document.createElement("button");
      marker.type = "button";
      marker.className = "marker";
      marker.dataset.id = component.id;
      marker.dataset.side = component.side;
      marker.style.left = rect.left + "%";
      marker.style.top = rect.top + "%";
      marker.style.width = rect.width + "%";
      marker.style.height = rect.height + "%";
      marker.textContent = label;
      marker.title = [label, component.value, component.footprint, component.lcsc].filter(Boolean).join(" | ");
      board.appendChild(marker);
      markers[component.id] = marker;

      var row = document.createElement("tr");
      row.dataset.id = component.id;
      appendCell(row, label);
      appendCell(row, component.name);
      appendCell(row, component.value);
      appendCell(row, component.footprint);
      appendCell(row, String(component.refs.length || 1));
      appendCell(row, component.lcsc);
      appendCell(row, component.side);
      tbody.appendChild(row);
      rows[component.id] = row;

      marker.addEventListener("click", function () { activate(component.id, true); });
      row.addEventListener("click", function () { activate(component.id, false); });
    });

    function activate(id, scrollRow) {
      Object.keys(markers).forEach(function (key) {
        var active = key === id;
        markers[key].classList.toggle("active", active);
        rows[key].classList.toggle("active", active);
      });
      if (scrollRow && rows[id]) rows[id].scrollIntoView({ block: "nearest" });
    }

    function matches(component, query) {
      if (!query) return true;
      return [
        component.refs.join(" "),
        component.name,
        component.value,
        component.footprint,
        component.lcsc,
        component.side
      ].join(" ").toLowerCase().indexOf(query) !== -1;
    }

    function applyFilter() {
      var query = search ? search.value.trim().toLowerCase() : "";
      var visible = 0;
      components.forEach(function (component) {
        var show = matches(component, query);
        visible += show ? 1 : 0;
        markers[component.id].hidden = !show;
        rows[component.id].hidden = !show;
      });
      if (count) count.textContent = visible + " / " + components.length + " 个元件";
    }

    if (search) search.addEventListener("input", applyFilter);
    applyFilter();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
"""


@dataclass
class EproGeneratedFile:
    filename: str
    content: bytes
    source: str
    note: str = ""


@dataclass
class EproConversionResult:
    status: str
    message: str
    generated_files: list[EproGeneratedFile] = field(default_factory=list)
    diagnostics: dict[str, object] = field(default_factory=dict)


def _safe_member_name(name: str) -> str:
    clean = Path(str(name or "")).name.strip()
    return re.sub(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+", "_", clean).strip("._") or "epro_output"


def _load_json_object(payload: bytes) -> dict[str, object] | None:
    try:
        obj = json.loads(payload.decode("utf-8-sig"))
    except Exception:
        return None
    if not isinstance(obj, dict):
        return None
    return obj


def _is_easyeda_standard_pcb_json(payload: bytes) -> bool:
    obj = _load_json_object(payload)
    if not obj:
        return False
    head = obj.get("head")
    if not isinstance(head, dict):
        return False
    return str(head.get("docType") or "") == "3" and "canvas" in obj and isinstance(obj.get("shape"), list)


def _is_interactive_bom_generic_json(payload: bytes) -> bool:
    obj = _load_json_object(payload)
    return bool(obj and isinstance(obj.get("pcbdata"), dict))


def _is_supported_pcb_json(payload: bytes) -> bool:
    return _is_easyeda_standard_pcb_json(payload) or _is_interactive_bom_generic_json(payload)


def _looks_like_easyeda_pro_pcb_jsonl(payload: bytes) -> bool:
    sample = payload[: min(len(payload), 1024 * 1024)].decode("utf-8", errors="ignore")
    if not sample.strip():
        return False
    markers = ("DOCTYPE_PCB", "PCB", "FOOTPRINT", "PAD", "TRACK", "Copper")
    return sample.count("\n") > 0 and any(marker in sample for marker in markers)


def _decode_epcb_text(payload: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return (payload or b"").decode(encoding)
        except UnicodeDecodeError:
            continue
    return (payload or b"").decode("utf-8", errors="ignore")


def _parse_json_records(payload: bytes) -> list[Any]:
    text = _decode_epcb_text(payload).strip()
    if not text:
        return []

    try:
        loaded = json.loads(text)
    except Exception:
        loaded = None
    if isinstance(loaded, list):
        if loaded and isinstance(loaded[0], str):
            return [loaded]
        return loaded
    if isinstance(loaded, dict):
        for key in ("records", "objects", "items", "shape", "shapes", "data"):
            value = loaded.get(key)
            if isinstance(value, list):
                return value
        return [loaded]

    records: list[Any] = []
    for line in text.splitlines():
        line = line.strip().rstrip(",")
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, list) and item and all(isinstance(child, (dict, list)) for child in item):
            records.extend(item)
        else:
            records.append(item)
    return records


def _key_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _text(value: Any) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        parts = [_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    return str(value).strip()


def _number(value: Any, default: float | None = None) -> float | None:
    if value is None or isinstance(value, bool):
        return default
    if isinstance(value, (int, float)):
        return float(value)
    text = _text(value)
    if not text:
        return default
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return default
    try:
        return float(match.group(0))
    except ValueError:
        return default


def _iter_nested_containers(value: Any, depth: int = 0) -> list[Any]:
    if depth >= 4:
        return []
    containers: list[Any] = []
    if isinstance(value, dict):
        containers.extend(child for child in value.values() if isinstance(child, (dict, list, tuple)))
    elif isinstance(value, (list, tuple)):
        containers.extend(child for child in value if isinstance(child, (dict, list, tuple)))
    return containers


def _lookup_values(value: Any, aliases: tuple[str, ...], depth: int = 0) -> list[Any]:
    alias_tokens = {_key_token(alias) for alias in aliases}
    found: list[Any] = []
    if isinstance(value, dict):
        for key, child in value.items():
            if _key_token(key) in alias_tokens:
                found.append(child)
        for child in _iter_nested_containers(value, depth):
            found.extend(_lookup_values(child, aliases, depth + 1))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.extend(_lookup_values(child, aliases, depth + 1))
    return found


def _first_lookup_text(record: Any, aliases: tuple[str, ...]) -> str:
    for value in _lookup_values(record, aliases):
        text = _text(value)
        if text:
            return text
    return ""


def _first_lookup_number(record: Any, aliases: tuple[str, ...]) -> float | None:
    for value in _lookup_values(record, aliases):
        number = _number(value)
        if number is not None:
            return number
    return None


def _record_type(record: Any) -> str:
    if isinstance(record, (list, tuple)) and record:
        first = _text(record[0])
        if first:
            return first.upper()
    raw = _first_lookup_text(
        record,
        ("type", "_type", "recordType", "objectType", "shapeType", "itemType", "kind"),
    )
    return raw.upper()


def _record_id(record: Any) -> str:
    if isinstance(record, (list, tuple)) and len(record) > 1:
        return _text(record[1])
    return _first_lookup_text(record, ("id", "uuid", "uid", "compId", "componentId"))


def _looks_like_ref(value: str) -> bool:
    text = _text(value)
    if not text:
        return False
    if _REF_RE.match(text):
        return True
    return bool(re.match(r"^[A-Za-z]{1,5}\?$", text))


def _find_ref_token(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    for token in re.split(r"[\s,;:|/\\]+", text):
        token = token.strip()
        if _looks_like_ref(token):
            return token
    match = re.search(r"\b[A-Za-z]{1,5}\d+[A-Za-z0-9._-]*\b", text)
    return match.group(0) if match else ""


def _component_ref(record: Any) -> str:
    preferred_aliases = (
        "ref",
        "Ref",
        "reference",
        "Reference",
        "refdes",
        "RefDes",
        "refDes",
        "designator",
        "Designator",
        "componentRef",
        "partRef",
        "mark",
        "Mark",
    )
    for value in _lookup_values(record, preferred_aliases):
        token = _find_ref_token(value)
        if token:
            return token

    record_type = _record_type(record)
    if any(marker in record_type for marker in ("COMPONENT", "FOOTPRINT", "DEVICE", "PART")):
        for value in _lookup_values(record, ("name", "Name", "displayName", "label", "Label", "id")):
            token = _find_ref_token(value)
            if token:
                return token
    return ""


def _componentish_record(record: Any) -> bool:
    record_type = _record_type(record)
    if any(blocked in record_type for blocked in ("PAD", "TRACK", "VIA", "NET", "TEXT", "COPPER", "ARC", "CIRCLE")):
        return False
    if any(marker in record_type for marker in ("COMPONENT", "FOOTPRINT", "DEVICE", "PART", "MODULE")):
        return True
    return bool(_component_ref(record))


def _coordinates_from_value(value: Any) -> tuple[float | None, float | None]:
    if isinstance(value, dict):
        x = _first_lookup_number(value, ("x", "X", "cx", "centerX", "posX", "originX"))
        y = _first_lookup_number(value, ("y", "Y", "cy", "centerY", "posY", "originY"))
        return x, y
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return _number(value[0]), _number(value[1])
    numbers = re.findall(r"-?\d+(?:\.\d+)?", _text(value))
    if len(numbers) >= 2:
        return float(numbers[0]), float(numbers[1])
    return None, None


def _component_center(record: Any) -> tuple[float, float]:
    x = _first_lookup_number(
        record,
        ("x", "X", "cx", "centerX", "posX", "positionX", "originX", "left"),
    )
    y = _first_lookup_number(
        record,
        ("y", "Y", "cy", "centerY", "posY", "positionY", "originY", "top"),
    )
    if x is not None and y is not None:
        return x, y

    for value in _lookup_values(
        record,
        ("pos", "position", "center", "origin", "location", "coordinate", "coordinates", "xy"),
    ):
        vx, vy = _coordinates_from_value(value)
        if vx is not None and vy is not None:
            return vx, vy
    return 0.0, 0.0


def _component_rotation(record: Any) -> float:
    return float(
        _first_lookup_number(
            record,
            ("rotation", "rotate", "rot", "angle", "Angle", "orientation"),
        )
        or 0.0
    )


def _layer_to_side(value: Any) -> str:
    text = _text(value).casefold()
    if not text:
        return "F"
    normalized = re.sub(r"[\s_.-]+", "", text)
    if normalized in {"b", "back", "bottom", "bottomlayer", "bottomcopper", "bcu", "2", "bottomside"}:
        return "B"
    if "bottom" in normalized or normalized.startswith("bcu"):
        return "B"
    return "F"


def _component_side(record: Any) -> str:
    value = _first_lookup_text(
        record,
        ("layer", "Layer", "side", "Side", "boardSide", "pcbSide", "sideCode", "placementLayer"),
    )
    return _layer_to_side(value)


def _normalize_attr_map(raw: Any) -> dict[str, str]:
    attrs: dict[str, str] = {}
    if not isinstance(raw, dict):
        return attrs
    for key, value in raw.items():
        token = _key_token(key)
        if not token:
            continue
        if isinstance(value, dict):
            for value_key in ("value", "Value", "text", "Text", "name", "Name"):
                if value_key in value and _text(value.get(value_key)):
                    attrs[token] = _text(value.get(value_key))
                    break
            else:
                attrs[token] = _text(value)
        else:
            attrs[token] = _text(value)
    return attrs


def _attr_get(attrs: dict[str, str], aliases: tuple[str, ...]) -> str:
    alias_tokens = {_key_token(alias) for alias in aliases}
    for key, value in attrs.items():
        if key in alias_tokens and value:
            return value
    for key, value in attrs.items():
        if any(alias and alias in key for alias in alias_tokens) and value:
            return value
    return ""


def _array_attr_parts(record: Any) -> tuple[str, str, str] | None:
    if not isinstance(record, (list, tuple)) or _record_type(record) != "ATTR":
        return None
    candidates: list[tuple[Any, Any, Any]] = []
    if len(record) > 8:
        candidates.append((record[3], record[7], record[8]))
    if len(record) > 4:
        candidates.append((record[2], record[3], record[4]))
    for parent_id, key, value in candidates:
        parent = _text(parent_id)
        attr_key = _text(key)
        if not parent or not attr_key:
            continue
        if re.fullmatch(r"-?\d+(?:\.\d+)?", attr_key):
            continue
        return parent, attr_key, _text(value)
    return None


def _dict_attr_parts(record: Any) -> tuple[str, str, str] | None:
    if not isinstance(record, dict) or _record_type(record) != "ATTR":
        return None
    parent = _first_lookup_text(record, ("parentId", "parent", "componentId", "compId", "ownerId"))
    key = _first_lookup_text(record, ("key", "attrKey", "name", "Name"))
    value = _first_lookup_text(record, ("value", "attrValue", "text", "Text"))
    if parent and key:
        return parent, key, value
    return None


def _epcb_attr_map(records: list[Any]) -> dict[str, dict[str, str]]:
    attrs_by_parent: dict[str, dict[str, str]] = {}
    for record in records:
        parts = _array_attr_parts(record) or _dict_attr_parts(record)
        if parts is None:
            continue
        parent_id, key, value = parts
        token = _key_token(key)
        if not token:
            continue
        attrs_by_parent.setdefault(parent_id, {})[token] = value
    return attrs_by_parent


def _package_size(footprint: str) -> tuple[float, float]:
    text = footprint.upper()
    match = _STANDARD_CHIP_RE.search(text)
    if match:
        return {
            "0201": (0.6, 0.3),
            "0402": (1.0, 0.5),
            "0603": (1.6, 0.8),
            "0805": (2.0, 1.25),
            "1206": (3.2, 1.6),
            "1210": (3.2, 2.5),
            "1812": (4.5, 3.2),
            "2512": (6.3, 3.2),
        }.get(match.group(1), (3.0, 2.4))
    if any(marker in text for marker in ("LQFP", "TQFP", "QFP", "BGA")):
        return (10.0, 10.0)
    if any(marker in text for marker in ("QFN", "DFN")):
        return (5.0, 5.0)
    if any(marker in text for marker in ("SOIC", "SOP", "SSOP", "TSSOP")):
        return (6.0, 5.0)
    if any(marker in text for marker in ("SOT", "SOD")):
        return (3.0, 1.8)
    if any(marker in text for marker in ("CONN", "HEADER", "JST", "USB")):
        return (8.0, 4.0)
    return (3.0, 2.4)


def _component_size(record: Any, footprint: str) -> tuple[float, float]:
    width = _first_lookup_number(record, ("width", "Width", "w", "W", "boxWidth", "bodyWidth"))
    height = _first_lookup_number(record, ("height", "Height", "h", "H", "boxHeight", "bodyHeight"))

    for value in _lookup_values(record, ("bbox", "box", "bounds", "boundingBox")):
        if isinstance(value, dict):
            x1 = _first_lookup_number(value, ("minx", "minX", "left", "x1"))
            y1 = _first_lookup_number(value, ("miny", "minY", "top", "y1"))
            x2 = _first_lookup_number(value, ("maxx", "maxX", "right", "x2"))
            y2 = _first_lookup_number(value, ("maxy", "maxY", "bottom", "y2"))
            if width is None and x1 is not None and x2 is not None:
                width = abs(x2 - x1)
            if height is None and y1 is not None and y2 is not None:
                height = abs(y2 - y1)

    fallback_width, fallback_height = _package_size(footprint)
    width = width if width and width > 0 else fallback_width
    height = height if height and height > 0 else fallback_height
    return float(width), float(height)


def _component_from_epcb_array(record: Any, attrs_by_parent: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    if not isinstance(record, (list, tuple)) or _record_type(record) != "COMPONENT" or len(record) < 7:
        return None

    component_id = _record_id(record)
    linked_attrs = attrs_by_parent.get(component_id, {})
    local_attrs = _normalize_attr_map(record[7]) if len(record) > 7 else {}
    attrs = {**linked_attrs, **local_attrs}

    ref = _find_ref_token(_attr_get(attrs, ("designator", "ref", "reference", "refdes")))
    if not ref:
        ref = _find_ref_token(_attr_get(attrs, ("name", "Name")))
    if not ref:
        for value in attrs.values():
            ref = _find_ref_token(value)
            if ref:
                break
    if not ref:
        return None

    value = _attr_get(attrs, ("value", "val", "comment", "device", "part", "componentValue", "name"))
    footprint = _attr_get(attrs, ("footprint", "package", "packageName", "footprintName"))
    lcsc = _attr_get(attrs, ("lcsc", "lcsc_code", "lcscCode", "supplierCode", "supplierPart", "manufacturerPartNumber"))
    name = _attr_get(attrs, ("name", "device", "part", "value")) or value or ref
    width, height = _package_size(footprint)

    return {
        "ref": ref,
        "name": name,
        "value": value,
        "footprint": footprint,
        "lcsc_code": lcsc,
        "center": [round(float(_number(record[4], 0.0) or 0.0), 3), round(float(_number(record[5], 0.0) or 0.0), 3)],
        "rotation": round(float(_number(record[6], 0.0) or 0.0), 3),
        "side": _layer_to_side(record[3]),
        "size": [round(width, 3), round(height, 3)],
    }


def _component_from_epcb_record(
    record: Any,
    attrs_by_parent: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any] | None:
    attrs_by_parent = attrs_by_parent or {}
    array_component = _component_from_epcb_array(record, attrs_by_parent)
    if array_component:
        return array_component

    if not _componentish_record(record):
        return None
    attrs = attrs_by_parent.get(_record_id(record), {})
    ref = _component_ref(record) or _find_ref_token(_attr_get(attrs, ("designator", "ref", "reference", "refdes")))
    if not ref:
        return None

    value = _first_lookup_text(
        record,
        (
            "value",
            "Value",
            "comment",
            "Comment",
            "device",
            "Device",
            "part",
            "Part",
            "componentValue",
            "supplierPart",
            "mpn",
        ),
    ) or _attr_get(attrs, ("value", "val", "comment", "device", "part", "componentValue", "name"))
    footprint = _first_lookup_text(
        record,
        (
            "footprint",
            "Footprint",
            "package",
            "Package",
            "packageName",
            "PackageName",
            "footprintName",
            "lib",
            "library",
        ),
    ) or _attr_get(attrs, ("footprint", "package", "packageName", "footprintName"))
    lcsc = _first_lookup_text(record, ("lcsc", "lcsc_code", "lcscCode", "LCSC", "supplierCode", "SupplierCode")) or _attr_get(
        attrs,
        ("lcsc", "lcsc_code", "lcscCode", "supplierCode", "supplierPart", "manufacturerPartNumber"),
    )
    name = _first_lookup_text(record, ("name", "Name", "displayName", "title", "Title")) or _attr_get(
        attrs,
        ("name", "device", "part", "value"),
    ) or value or ref
    x, y = _component_center(record)
    width, height = _component_size(record, footprint)

    return {
        "ref": ref,
        "name": name,
        "value": value,
        "footprint": footprint,
        "lcsc_code": lcsc,
        "center": [round(x, 3), round(y, 3)],
        "rotation": round(_component_rotation(record), 3),
        "side": _component_side(record),
        "size": [round(width, 3), round(height, 3)],
    }


def _merge_components(components: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for component in components:
        ref = str(component.get("ref") or "").strip()
        if not ref:
            continue
        key = ref.casefold()
        if key not in merged:
            merged[key] = dict(component)
            order.append(key)
            continue
        current = merged[key]
        for field in ("name", "value", "footprint", "lcsc_code"):
            if not current.get(field) and component.get(field):
                current[field] = component[field]
        if current.get("center") == [0.0, 0.0] and component.get("center") != [0.0, 0.0]:
            current["center"] = component["center"]
        if not current.get("size") and component.get("size"):
            current["size"] = component["size"]
    return [merged[key] for key in order]


def _extract_nets(records: list[Any]) -> list[dict[str, Any]]:
    nets: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        record_type = _record_type(record)
        if "NET" not in record_type:
            continue
        name = _first_lookup_text(record, ("name", "Name", "net", "Net", "netName", "NetName"))
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        nets.append({"name": name})
    return nets


def _edge_segments_from_bbox(minx: float, miny: float, maxx: float, maxy: float) -> list[dict[str, Any]]:
    return [
        {"type": "segment", "start": [minx, miny], "end": [maxx, miny], "width": 0.1},
        {"type": "segment", "start": [maxx, miny], "end": [maxx, maxy], "width": 0.1},
        {"type": "segment", "start": [maxx, maxy], "end": [minx, maxy], "width": 0.1},
        {"type": "segment", "start": [minx, maxy], "end": [minx, miny], "width": 0.1},
    ]


def _component_sort_key(component: dict[str, Any]) -> tuple[str, int, str]:
    ref = str(component.get("ref") or "")
    prefix = re.sub(r"\d.*$", "", ref).upper()
    number_match = re.search(r"\d+", ref)
    number = int(number_match.group(0)) if number_match else 0
    return prefix, number, ref


def convert_epcb_to_generic_json(payload: bytes, source_name: str = "board.epcb") -> EproGeneratedFile | None:
    """Convert a parseable EasyEDA Pro .epcb JSONL file to Generic Interactive BOM JSON.

    This is intentionally a lightweight warehouse parser.  It extracts enough
    component placement data for BOM-to-board linking, while leaving detailed
    pad/track/zone geometry to a full ECAD importer such as KiCad.
    """

    records = _parse_json_records(payload)
    if not records:
        return None

    attrs_by_parent = _epcb_attr_map(records)
    components = _merge_components(
        [
            component
            for component in (_component_from_epcb_record(record, attrs_by_parent) for record in records)
            if component is not None
        ]
    )
    if not components:
        return None
    components.sort(key=_component_sort_key)

    footprints: list[dict[str, Any]] = []
    bom_both: list[list[list[Any]]] = []
    bom_front: list[list[list[Any]]] = []
    bom_back: list[list[list[Any]]] = []
    fields: dict[str, list[str]] = {}

    minx = miny = float("inf")
    maxx = maxy = float("-inf")
    for component in components:
        footprint_id = len(footprints)
        cx, cy = component["center"]
        width, height = component["size"]
        side = component["side"]
        relpos = [round(-width / 2, 3), round(-height / 2, 3)]
        size = [round(width, 3), round(height, 3)]
        footprint = {
            "ref": component["ref"],
            "center": [cx, cy],
            "bbox": {
                "pos": [cx, cy],
                "angle": component["rotation"],
                "relpos": relpos,
                "size": size,
            },
            "pads": [],
            "drawings": [],
            "layer": side,
        }
        footprints.append(footprint)
        fields[str(footprint_id)] = [
            str(component.get("name") or component.get("value") or component.get("ref") or ""),
            str(component.get("value") or ""),
            str(component.get("footprint") or ""),
            str(component.get("lcsc_code") or ""),
        ]
        row = [[component["ref"], footprint_id]]
        bom_both.append(row)
        if side == "B":
            bom_back.append(row)
        else:
            bom_front.append(row)

        minx = min(minx, cx - width / 2)
        miny = min(miny, cy - height / 2)
        maxx = max(maxx, cx + width / 2)
        maxy = max(maxy, cy + height / 2)

    if not all(value != float("inf") and value != float("-inf") for value in (minx, miny, maxx, maxy)):
        minx, miny, maxx, maxy = 0.0, 0.0, 20.0, 20.0
    padding = max(maxx - minx, maxy - miny, 10.0) * 0.08
    minx = round(minx - padding, 3)
    miny = round(miny - padding, 3)
    maxx = round(maxx + padding, 3)
    maxy = round(maxy + padding, 3)

    source_stem = Path(source_name).stem or "board"
    pcbdata = {
        "edges_bbox": {"minx": minx, "miny": miny, "maxx": maxx, "maxy": maxy},
        "edges": _edge_segments_from_bbox(minx, miny, maxx, maxy),
        "drawings": {"silkscreen": {"F": [], "B": []}, "fabrication": {"F": [], "B": []}},
        "footprints": footprints,
        "tracks": {"F": [], "B": []},
        "zones": {"F": [], "B": []},
        "nets": _extract_nets(records),
        "metadata": {
            "title": source_stem,
            "source_format": "easyeda_pro_epcb",
            "component_count": len(footprints),
        },
        "bom": {"both": bom_both, "F": bom_front, "B": bom_back, "skipped": [], "fields": fields},
        "font_data": {},
    }
    content = json.dumps({"pcbdata": pcbdata}, ensure_ascii=False, indent=2).encode("utf-8")
    filename = _safe_member_name(f"{source_stem}.ibom.json")
    return EproGeneratedFile(
        filename=filename,
        content=content,
        source="internal_epcb_parser",
        note="Internal EasyEDA Pro .epcb parser generated Interactive BOM JSON.",
    )


def _standalone_interactive_bom_html(payload_json: str, title: str, component_count: int) -> bytes:
    safe_payload = str(payload_json or "{}").replace("</", "<\\/")
    safe_title = html.escape(str(title or "交互式 BOM"), quote=True)
    count_text = f"{int(component_count or 0)} 个元件"
    content = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title} - 交互式 BOM</title>
  <style>{_STANDALONE_IBOM_CSS}</style>
</head>
<body>
  <div class="page">
    <header>
      <div>
        <h1>{safe_title}</h1>
        <div class="meta">由仓库系统内置解析器根据 EasyEDA Pro .epcb 数据生成。</div>
      </div>
      <div class="toolbar">
        <input type="search" placeholder="搜索 Ref / 名称 / 数值 / LCSC" autocomplete="off" aria-label="搜索 BOM">
        <span class="count">{html.escape(count_text)}</span>
      </div>
    </header>
    <main class="interactive-bom-viewer">
      <section class="board-pane" aria-label="PCB 元件图">
        <div class="board" role="img" aria-label="PCB 元件图"></div>
      </section>
      <section class="table-pane" aria-label="BOM 表格">
        <table>
          <thead>
            <tr>
              <th>位号</th>
              <th>名称</th>
              <th>数值</th>
              <th>封装</th>
              <th>数量</th>
              <th>LCSC</th>
              <th>板面</th>
            </tr>
          </thead>
          <tbody></tbody>
        </table>
      </section>
    </main>
  </div>
  <script type="application/json" data-interactive-bom-payload>{safe_payload}</script>
  <script>{_STANDALONE_IBOM_JS}</script>
</body>
</html>
"""
    return content.encode("utf-8")


def convert_epcb_to_generic_files(payload: bytes, source_name: str = "board.epcb") -> list[EproGeneratedFile]:
    json_file = convert_epcb_to_generic_json(payload, source_name)
    if json_file is None:
        return []

    try:
        parsed = json.loads(json_file.content.decode("utf-8"))
    except Exception:
        parsed = {}
    pcbdata = parsed.get("pcbdata") if isinstance(parsed, dict) else {}
    footprints = pcbdata.get("footprints") if isinstance(pcbdata, dict) else []
    component_count = len(footprints) if isinstance(footprints, list) else 0
    title = (
        pcbdata.get("metadata", {}).get("title")
        if isinstance(pcbdata, dict) and isinstance(pcbdata.get("metadata"), dict)
        else ""
    ) or Path(source_name).stem or "board"

    source_stem = Path(source_name).stem or "board"
    html_file = EproGeneratedFile(
        filename=_safe_member_name(f"{source_stem}.ibom.html"),
        content=_standalone_interactive_bom_html(json_file.content.decode("utf-8"), title, component_count),
        source="internal_epcb_parser",
        note="Internal EasyEDA Pro .epcb parser generated standalone Interactive BOM HTML.",
    )
    return [json_file, html_file]


def _read_zip_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes | None:
    if info.is_dir() or info.file_size <= 0 or info.file_size > MAX_EXTRACTED_FILE_BYTES:
        return None
    with zf.open(info) as fp:
        return fp.read(MAX_EXTRACTED_FILE_BYTES + 1)


def inspect_epro_archive(content: bytes) -> EproConversionResult:
    """Inspect .epro bytes and extract supported child artifacts if present."""

    if not zipfile.is_zipfile(io.BytesIO(content or b"")):
        return EproConversionResult(
            status="unsupported",
            message=".epro 文件不是可识别的 ZIP 工程包，无法自动解包。",
            diagnostics={"is_zip": False},
        )

    generated: list[EproGeneratedFile] = []
    diagnostics: dict[str, object] = {"is_zip": True, "members": [], "easyeda_pro_pcb_members": []}
    epcb_candidates: list[tuple[str, bytes]] = []

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        infos = zf.infolist()
        diagnostics["member_count"] = len(infos)
        diagnostics["members"] = [info.filename for info in infos[:80]]
        for info in infos:
            suffix = Path(info.filename).suffix.lower()
            payload = _read_zip_member(zf, info)
            if payload is None:
                continue
            if suffix in (".epcb", ".jsonl") and (suffix == ".epcb" or _looks_like_easyeda_pro_pcb_jsonl(payload)):
                diagnostics.setdefault("easyeda_pro_pcb_members", []).append(info.filename)
                epcb_candidates.append((info.filename, payload))
            if suffix not in SUPPORTED_OUTPUT_EXTENSIONS:
                continue
            if suffix == ".json" and not _is_supported_pcb_json(payload):
                continue
            generated.append(
                EproGeneratedFile(
                    filename=_safe_member_name(info.filename),
                    content=payload,
                    source="archive",
                    note="从 .epro 工程包内发现的可用 PCB 文件",
                )
            )

    generated.sort(key=lambda item: PREFERRED_OUTPUT_EXTENSIONS.index(Path(item.filename).suffix.lower()))
    if generated:
        return EproConversionResult(
            status="converted",
            message=f"已从 .epro 工程包中提取 {len(generated)} 个可用 PCB 文件。",
            generated_files=generated,
            diagnostics=diagnostics,
        )

    epcb_generated: list[EproGeneratedFile] = []
    failed_epcb_members: list[str] = []
    for member_name, payload in epcb_candidates:
        converted = convert_epcb_to_generic_files(payload, member_name)
        if not converted:
            failed_epcb_members.append(member_name)
            continue
        epcb_generated.extend(converted)

    if epcb_generated:
        diagnostics["internal_epcb_parser_members"] = [name for name, _payload in epcb_candidates]
        if failed_epcb_members:
            diagnostics["internal_epcb_parser_failed_members"] = failed_epcb_members
        return EproConversionResult(
            status="converted",
            message=f"已从 .epro 内的 EasyEDA Pro .epcb 数据生成 {len(epcb_generated)} 个交互 BOM 文件。",
            generated_files=epcb_generated,
            diagnostics=diagnostics,
        )

    if diagnostics.get("easyeda_pro_pcb_members"):
        return EproConversionResult(
            status="pending_external_converter",
            message=".epro 内包含嘉立创 EDA 专业版 PCB 数据，但需要外部格式转换器导出 KiCad/Eagle/标准版 JSON。",
            diagnostics=diagnostics,
        )

    return EproConversionResult(
        status="no_pcb_artifact",
        message=".epro 工程包内没有发现可直接使用的 KiCad、Eagle/Fusion 或 EasyEDA 标准版 JSON PCB 文件。",
        diagnostics=diagnostics,
    )


def _collect_output_files(output_dir: Path) -> list[EproGeneratedFile]:
    files: list[EproGeneratedFile] = []
    for path in output_dir.rglob("*"):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix not in SUPPORTED_OUTPUT_EXTENSIONS:
            continue
        payload = path.read_bytes()
        if suffix == ".json" and not _is_supported_pcb_json(payload):
            continue
        files.append(
            EproGeneratedFile(
                filename=_safe_member_name(path.name),
                content=payload,
                source="external_converter",
                note="外部转换器生成的 PCB 文件",
            )
        )
    files.sort(key=lambda item: PREFERRED_OUTPUT_EXTENSIONS.index(Path(item.filename).suffix.lower()))
    return files


def _converter_timeout_seconds() -> int:
    try:
        return max(1, int(os.environ.get("WAREHOUSE_EPRO_CONVERTER_TIMEOUT", "120")))
    except (TypeError, ValueError):
        return 120


def run_external_converter(content: bytes, original_filename: str, command_template: str) -> EproConversionResult:
    """Run a configured converter command and collect supported outputs."""

    command_template = str(command_template or "").strip()
    if not command_template:
        return EproConversionResult(status="not_configured", message="未配置外部 .epro 转换命令。")

    work_dir = Path(tempfile.mkdtemp(prefix="warehouse_epro_"))
    try:
        input_path = work_dir / _safe_member_name(original_filename or "project.epro")
        output_dir = work_dir / "out"
        output_dir.mkdir(parents=True, exist_ok=True)
        input_path.write_bytes(content or b"")
        try:
            command = command_template.format(input=str(input_path), output_dir=str(output_dir))
        except (KeyError, IndexError, ValueError) as exc:
            return EproConversionResult(
                status="external_converter_config_error",
                message="外部 .epro 转换命令模板无效，请检查 {input} 和 {output_dir} 占位符。",
                diagnostics={"error": f"{type(exc).__name__}: {exc}", "command_template": command_template},
            )
        timeout_seconds = _converter_timeout_seconds()
        try:
            completed = subprocess.run(
                command,
                cwd=str(work_dir),
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            return EproConversionResult(
                status="external_converter_timeout",
                message="外部 .epro 转换命令执行超时。",
                diagnostics={
                    "command": command,
                    "timeout_seconds": timeout_seconds,
                    "stdout": str(exc.stdout or "")[-4000:],
                    "stderr": str(exc.stderr or "")[-4000:],
                },
            )
        except Exception as exc:
            return EproConversionResult(
                status="external_converter_failed",
                message="外部 .epro 转换命令执行异常。",
                diagnostics={"command": command, "error": f"{type(exc).__name__}: {exc}"},
            )
        diagnostics = {
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
            "command": command,
        }
        if completed.returncode != 0:
            return EproConversionResult(
                status="external_converter_failed",
                message="外部 .epro 转换命令执行失败。",
                diagnostics=diagnostics,
            )
        generated = _collect_output_files(output_dir)
        if not generated:
            generated = _collect_output_files(work_dir)
        if generated:
            return EproConversionResult(
                status="converted",
                message=f"外部转换器生成了 {len(generated)} 个可用 PCB 文件。",
                generated_files=generated,
                diagnostics=diagnostics,
            )
        return EproConversionResult(
            status="external_converter_no_output",
            message="外部转换器执行完成，但没有生成可识别的 KiCad/Eagle/标准版 JSON PCB 文件。",
            diagnostics=diagnostics,
        )
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def process_epro_upload(content: bytes, original_filename: str, command_template: str = "") -> EproConversionResult:
    """Process an EasyEDA Pro project upload."""

    inspected = inspect_epro_archive(content)
    if inspected.generated_files:
        return inspected

    converted = run_external_converter(content, original_filename, command_template)
    if converted.status == "converted":
        converted.diagnostics = {**inspected.diagnostics, **converted.diagnostics}
        return converted
    if converted.status != "not_configured":
        converted.diagnostics = {**inspected.diagnostics, **converted.diagnostics}
        return converted
    return inspected


__all__ = [
    "EproConversionResult",
    "EproGeneratedFile",
    "process_epro_upload",
    "inspect_epro_archive",
    "run_external_converter",
    "convert_epcb_to_generic_files",
    "convert_epcb_to_generic_json",
]
