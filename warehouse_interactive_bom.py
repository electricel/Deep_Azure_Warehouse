"""Lightweight InteractiveHtmlBom-style payload adapter.

This module intentionally has no dependency on app.py.  It converts the BOM
record dictionaries persisted by the warehouse app into a stable,
JSON-serializable structure that front-end code can use for row-to-board
highlighting even when real PCB coordinates are not available yet.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any


_REF_SPLIT_RE = re.compile(r"[\s,;，；、]+")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_+#.%/-]+")
_REF_RE = re.compile(r"^[A-Za-z]{1,4}\d+[A-Za-z0-9._-]*$")
_KNOWN_SIDE_VALUES = {
    "top": "top",
    "front": "top",
    "f": "top",
    "f.cu": "top",
    "f_silk": "top",
    "f.silk": "top",
    "bottom": "bottom",
    "back": "bottom",
    "b": "bottom",
    "b.cu": "bottom",
    "b_silk": "bottom",
    "b.silk": "bottom",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _text(item.get(key))
        if value:
            return value
    return ""


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _text(value)
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
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


def _quantity(value: Any) -> int:
    number = _number(value, 0)
    if number <= 0:
        return 0
    return int(number) if number.is_integer() else int(math.ceil(number))


def component_refs(item: dict[str, Any]) -> list[str]:
    """Return normalized reference designators from a BOM row-like dict."""

    values: list[Any] = []
    for key in ("refs", "references", "designators"):
        raw = item.get(key)
        if isinstance(raw, (list, tuple, set)):
            values.extend(raw)
        elif raw:
            values.append(raw)
    for key in ("ref", "reference", "designator"):
        if item.get(key):
            values.append(item.get(key))

    refs: list[str] = []
    for value in values:
        for part in _REF_SPLIT_RE.split(_text(value)):
            part = part.strip()
            if part:
                refs.append(part)
    return _dedupe(refs)


def component_tokens(item: dict[str, Any]) -> list[str]:
    """Return searchable tokens used to link BOM rows, board marks, and filters."""

    values = [
        item.get("ref"),
        item.get("name"),
        item.get("part_name"),
        item.get("normalized_name"),
        item.get("value"),
        item.get("footprint"),
        item.get("package"),
        item.get("lcsc_code"),
        item.get("supplier_code"),
        item.get("manufacturer_part_number"),
        item.get("manufacturer"),
        item.get("category"),
    ]
    values.extend(component_refs(item))
    aliases = item.get("aliases")
    if isinstance(aliases, list):
        values.extend(aliases)

    tokens: list[str] = []
    for value in values:
        text = _text(value)
        if not text:
            continue
        tokens.append(text)
        tokens.extend(match.group(0) for match in _TOKEN_RE.finditer(text))
    return _dedupe(tokens)


def _component_side(item: dict[str, Any], index: int) -> str:
    raw = _first_text(item, ("side", "layer", "board_side", "pcb_side")).casefold()
    if raw in _KNOWN_SIDE_VALUES:
        return _KNOWN_SIDE_VALUES[raw]
    if raw.startswith("b.") or raw.startswith("bottom"):
        return "bottom"
    if raw.startswith("f.") or raw.startswith("top"):
        return "top"
    return "top"


def _component_name(item: dict[str, Any]) -> str:
    return _first_text(
        item,
        (
            "name",
            "part_name",
            "normalized_name",
            "manufacturer_part_number",
            "value",
            "lcsc_code",
            "supplier_code",
        ),
    )


def _component_value(item: dict[str, Any]) -> str:
    return _first_text(item, ("value", "comment", "part_name", "name", "normalized_name"))


def _component_footprint(item: dict[str, Any]) -> str:
    return _first_text(item, ("footprint", "package", "pcb_footprint"))


def _stable_component_id(bom_id: str, row_index: int, refs: list[str], item: dict[str, Any]) -> str:
    seed = "|".join(
        [
            bom_id,
            str(row_index),
            ",".join(refs),
            _component_name(item),
            _component_value(item),
            _component_footprint(item),
            _first_text(item, ("lcsc_code", "supplier_code")),
        ]
    )
    return "cmp_" + hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _placeholder_bbox(index: int, count: int, side: str) -> dict[str, Any]:
    columns = max(1, math.ceil(math.sqrt(max(1, count))))
    rows = max(1, math.ceil(max(1, count) / columns))
    cell_w = 10.0
    cell_h = 8.0
    gap_x = 3.0
    gap_y = 3.0
    margin = 8.0
    row = index // columns
    col = index % columns
    x = margin + col * (cell_w + gap_x)
    y = margin + row * (cell_h + gap_y)
    if side == "bottom":
        board_width = margin * 2 + columns * cell_w + max(0, columns - 1) * gap_x
        x = board_width - x - cell_w
    return {
        "x": round(x, 3),
        "y": round(y, 3),
        "width": cell_w,
        "height": cell_h,
        "cx": round(x + cell_w / 2, 3),
        "cy": round(y + cell_h / 2, 3),
        "rotation": 0,
        "side": side,
        "source": "placeholder",
        "grid": {"row": row, "column": col, "rows": rows, "columns": columns},
    }


def _board_for_count(count: int) -> dict[str, Any]:
    columns = max(1, math.ceil(math.sqrt(max(1, count))))
    rows = max(1, math.ceil(max(1, count) / columns))
    width = 16.0 + columns * 10.0 + max(0, columns - 1) * 3.0
    height = 16.0 + rows * 8.0 + max(0, rows - 1) * 3.0
    return {
        "units": "mm",
        "width": round(width, 3),
        "height": round(height, 3),
        "bbox": {"x": 0, "y": 0, "width": round(width, 3), "height": round(height, 3)},
        "coordinate_source": "placeholder",
        "placeholder": True,
    }


def _ibom_edges(board: dict[str, Any]) -> list[dict[str, Any]]:
    width = float(board.get("width") or 0)
    height = float(board.get("height") or 0)
    if width <= 0 or height <= 0:
        return []
    return [
        {"type": "segment", "start": [0, 0], "end": [width, 0], "width": 0.1},
        {"type": "segment", "start": [width, 0], "end": [width, height], "width": 0.1},
        {"type": "segment", "start": [width, height], "end": [0, height], "width": 0.1},
        {"type": "segment", "start": [0, height], "end": [0, 0], "width": 0.1},
    ]


def _ibom_side(side: str) -> str:
    return "B" if side == "bottom" else "F"


def _ibom_ref_offsets(count: int) -> list[tuple[float, float]]:
    if count <= 1:
        return [(0.0, 0.0)]
    columns = max(1, math.ceil(math.sqrt(count)))
    rows = max(1, math.ceil(count / columns))
    step_x = 2.4
    step_y = 2.0
    offsets: list[tuple[float, float]] = []
    for index in range(count):
        col = index % columns
        row = index // columns
        offsets.append(((col - (columns - 1) / 2) * step_x, (row - (rows - 1) / 2) * step_y))
    return offsets


def _ibom_payload(components: list[dict[str, Any]], board: dict[str, Any], title: str, nets: list[Any]) -> dict[str, Any]:
    footprints: list[dict[str, Any]] = []
    bom_both: list[list[list[Any]]] = []
    bom_front: list[list[list[Any]]] = []
    bom_back: list[list[list[Any]]] = []
    fields: dict[str, list[str]] = {}

    for component in components:
        refs = component.get("refs") if isinstance(component.get("refs"), list) else []
        bbox = component.get("bbox") if isinstance(component.get("bbox"), dict) else {}
        side = _ibom_side(str(component.get("side") or "top"))
        ref_offsets = _ibom_ref_offsets(len(refs))
        bom_row: list[list[Any]] = []
        for ref_index, ref in enumerate(refs):
            footprint_id = len(footprints)
            dx, dy = ref_offsets[ref_index] if ref_index < len(ref_offsets) else (0.0, 0.0)
            width = float(bbox.get("width") or bbox.get("w") or 4.0)
            height = float(bbox.get("height") or bbox.get("h") or 2.5)
            center = [round(float(bbox.get("cx") or bbox.get("x") or 0) + dx, 3), round(float(bbox.get("cy") or bbox.get("y") or 0) + dy, 3)]
            footprints.append(
                {
                    "ref": str(ref),
                    "center": center,
                    "bbox": {
                        "pos": center,
                        "angle": float(bbox.get("rotation") or 0),
                        "relpos": [round(-width / 2, 3), round(-height / 2, 3)],
                        "size": [round(width, 3), round(height, 3)],
                    },
                    "pads": [],
                    "drawings": [],
                    "layer": side,
                }
            )
            fields[str(footprint_id)] = [
                str(component.get("name") or ""),
                str(component.get("value") or ""),
                str(component.get("footprint") or ""),
                str(component.get("lcsc_code") or ""),
            ]
            bom_row.append([str(ref), footprint_id])
        if bom_row:
            bom_both.append(bom_row)
            if side == "B":
                bom_back.append(bom_row)
            else:
                bom_front.append(bom_row)

    width = float(board.get("width") or 0)
    height = float(board.get("height") or 0)
    return {
        "edges_bbox": {"minx": 0, "miny": 0, "maxx": width, "maxy": height},
        "edges": _ibom_edges(board),
        "drawings": {"silkscreen": {"F": [], "B": []}, "fabrication": {"F": [], "B": []}},
        "footprints": footprints,
        "tracks": {"F": [], "B": []},
        "zones": {"F": [], "B": []},
        "nets": nets,
        "metadata": {"title": title},
        "bom": {"both": bom_both, "F": bom_front, "B": bom_back, "skipped": [], "fields": fields},
        "font_data": {},
    }


def _record_title(record: dict[str, Any], bom_id: str) -> str:
    return (
        _first_text(record, ("title", "name", "bom_name", "original_filename", "stored_filename"))
        or bom_id
        or "BOM"
    )


def _record_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("component_rows", "aggregated_items", "items", "components"):
        rows = record.get(key)
        if isinstance(rows, list) and rows:
            return [row for row in rows if isinstance(row, dict)]
    return []


def _record_nets(record: dict[str, Any]) -> list[Any]:
    for key in ("nets", "netlist"):
        nets = record.get(key)
        if isinstance(nets, list):
            return nets
    for key in ("pcb_analysis", "analysis", "interactive_bom"):
        nested = record.get(key)
        if isinstance(nested, dict):
            nets = nested.get("nets") or nested.get("netlist")
            if isinstance(nets, list):
                return nets
    return []


def build_interactive_bom_payload(record: dict[str, Any]) -> dict[str, Any]:
    """Build a front-end friendly interactive BOM payload from a BOM record."""

    if not isinstance(record, dict):
        record = {}
    bom_id = _text(record.get("bom_id") or record.get("id"))
    rows = _record_rows(record)
    components: list[dict[str, Any]] = []

    for index, item in enumerate(rows):
        row_index = int(_number(item.get("row_index"), index + 1) or index + 1)
        refs = component_refs(item)
        if not refs:
            refs = [f"ROW{row_index}"]
        side = _component_side(item, index)
        component = {
            "id": _stable_component_id(bom_id, row_index, refs, item),
            "refs": refs,
            "ref": refs[0] if refs else "",
            "name": _component_name(item),
            "value": _component_value(item),
            "footprint": _component_footprint(item),
            "quantity": _quantity(item.get("quantity")),
            "lcsc_code": _first_text(item, ("lcsc_code", "supplier_code")),
            "side": side,
            "bbox": _placeholder_bbox(index, len(rows), side),
            "row_index": row_index,
            "tokens": [],
        }
        component["tokens"] = component_tokens({**item, **component})
        components.append(component)

    title = _record_title(record, bom_id)
    board = _board_for_count(len(components))
    nets = _record_nets(record)
    return {
        "bom_id": bom_id,
        "title": title,
        "components": components,
        "nets": nets,
        "board": board,
        "pcbdata": _ibom_payload(components, board, title, nets),
        "config": {
            "dark_mode": False,
            "show_pads": True,
            "show_fabrication": False,
            "show_silkscreen": True,
            "highlight_pin1": "selected",
            "redraw_on_drag": True,
            "board_rotation": 0,
            "checkboxes": "",
            "bom_view": "left-right",
            "layer_view": "FB",
            "extra_fields": ["Name", "Value", "Footprint", "LCSC"],
        },
        "meta": {
            "format": "warehouse_interactive_bom",
            "version": 1,
            "component_count": len(components),
            "coordinate_source": "placeholder",
        },
    }


__all__ = [
    "build_interactive_bom_payload",
    "component_refs",
    "component_tokens",
]
