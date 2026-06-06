import csv
import io
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path


def normalize_key(value):
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def normalize_ohm_text(value):
    text = str(value or "").strip().lower()
    return (
        text.replace("ω", "Ω")
        .replace("ohms", "Ω")
        .replace("ohm", "Ω")
        .replace("欧姆", "Ω")
        .replace("欧", "Ω")
    )


def format_resistance(ohms):
    value = float(ohms)
    if value >= 1_000_000 and value % 1_000_000 == 0:
        return f"{int(value / 1_000_000)}MΩ"
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}MΩ"
    if value >= 1_000 and value % 1_000 == 0:
        return f"{int(value / 1_000)}kΩ"
    if value >= 1_000:
        return f"{value / 1_000:g}kΩ"
    return f"{value:g}Ω"


def parse_resistance_value(value):
    text = normalize_ohm_text(value)
    match = re.search(r"(\d+(?:\.\d+)?)([kmr]?)(?:Ω|r\b)?", text, re.I)
    if match and ("Ω" in text or match.group(2).lower() in ("k", "m", "r")):
        number = float(match.group(1))
        unit = match.group(2).lower()
        if unit == "m":
            number *= 1_000_000
        elif unit == "k":
            number *= 1_000
        return round(number, 6)
    compact = re.search(r"(\d+)r(\d+)", text, re.I)
    if compact:
        return float(f"{compact.group(1)}.{compact.group(2)}")
    compact = re.search(r"(\d+)k(\d+)", text, re.I)
    if compact:
        return float(f"{compact.group(1)}.{compact.group(2)}") * 1_000
    compact = re.search(r"(\d+)m(\d+)", text, re.I)
    if compact:
        return float(f"{compact.group(1)}.{compact.group(2)}") * 1_000_000
    return None


def decode_resistor_code(value):
    text = str(value or "").upper()
    ignored_packages = {"0201", "0402", "0603", "0805", "1206", "1210", "1812", "2512"}
    codes = [m.group(0) for m in re.finditer(r"\d{3,4}", text) if m.group(0) not in ignored_packages]
    if not codes:
        return None
    code = codes[-1]
    if len(code) == 3:
        base = int(code[:2])
        multiplier = int(code[2])
    else:
        base = int(code[:3])
        multiplier = int(code[3])
    return float(base * (10 ** multiplier))


def decode_capacitor_code(value):
    text = str(value or "").upper()
    ignored_packages = {"0201", "0402", "0603", "0805", "1206", "1210", "1812"}
    codes = [m.group(0) for m in re.finditer(r"\d{3}", text) if m.group(0) not in ignored_packages]
    if not codes:
        return None
    code = codes[-1]
    pf = int(code[:2]) * (10 ** int(code[2]))
    if pf >= 1_000_000:
        return f"{pf / 1_000_000:g}uF"
    if pf >= 1_000:
        return f"{pf / 1_000:g}nF"
    return f"{pf:g}pF"


def component_value_key(value, category=""):
    text = normalize_ohm_text(value)
    cat = str(category or "")
    resistance = parse_resistance_value(text)
    if resistance is None and ("电阻" in cat or re.search(r"(^|[^A-Z])R\d{4}", str(value or "").upper())):
        resistance = decode_resistor_code(value)
    if resistance is not None:
        return f"R:{resistance:g}"
    cap_match = re.search(r"(\d+(?:\.\d+)?)(p|n|u|µ)f", text, re.I)
    if cap_match:
        number = float(cap_match.group(1))
        unit = cap_match.group(2).lower().replace("µ", "u")
        if unit == "p":
            farads = number * 1e-12
        elif unit == "n":
            farads = number * 1e-9
        else:
            farads = number * 1e-6
        return f"C:{farads:.12g}"
    decoded_cap = decode_capacitor_code(value) if "电容" in cat else None
    if decoded_cap:
        return component_value_key(decoded_cap, "电容")
    return ""


def component_value_aliases(value, category=""):
    aliases = []
    resistance = parse_resistance_value(value)
    if resistance is None and "电阻" in str(category or ""):
        resistance = decode_resistor_code(value)
    if resistance is not None:
        aliases.append(format_resistance(resistance))
        aliases.append(format_resistance(resistance).replace("Ω", "欧姆"))
        if resistance >= 1_000:
            aliases.append(f"{resistance / 1_000:g}K")
        else:
            aliases.append(f"{resistance:g}R")
    decoded_cap = decode_capacitor_code(value) if "电容" in str(category or "") else None
    if decoded_cap:
        aliases.append(decoded_cap)
    return aliases


COMMON_PACKAGE_CODES = {
    "C0201",
    "C0402",
    "C0603",
    "C0805",
    "C1206",
    "C1210",
    "C1812",
    "C2220",
}

PACKAGE_SIZE_CODES = {
    "0201",
    "0402",
    "0603",
    "0805",
    "1206",
    "1210",
    "1812",
    "2010",
    "2512",
    "2220",
}


def extract_lcsc_codes(values):
    codes = set()
    for value in values:
        for match in re.findall(r"\bC\d{4,}\b", str(value or ""), re.I):
            code = match.upper()
            if code in COMMON_PACKAGE_CODES:
                continue
            codes.add(code)
    return sorted(codes)

def read_text_rows(path, read_bytes=None):
    read_bytes = read_bytes or (lambda target: Path(target).read_bytes())
    data = read_bytes(path)
    text = None
    for encoding in ("utf-8-sig", "gbk", "utf-16"):
        try:
            text = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("utf-8", errors="replace")
    sample = text[:2048]
    if "\t" in sample:
        delimiter = "\t"
    elif "," in sample:
        delimiter = ","
    else:
        delimiter = None
    if delimiter:
        return [row for row in csv.reader(text.splitlines(), delimiter=delimiter) if any(c.strip() for c in row)]
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            rows.append(re.split(r"\s{2,}|\s+\|\s+|\s*,\s*", line))
    return rows


def read_xlsx_rows(path, read_bytes=None):
    read_bytes = read_bytes or (lambda target: Path(target).read_bytes())
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(io.BytesIO(read_bytes(path))) as zf:
        shared = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for si in root.findall("m:si", ns):
                texts = [t.text or "" for t in si.findall(".//m:t", ns)]
                shared.append("".join(texts))
        sheet_name = "xl/worksheets/sheet1.xml"
        if sheet_name not in zf.namelist():
            sheet_name = next((n for n in zf.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")), "")
        if not sheet_name:
            return []
        root = ET.fromstring(zf.read(sheet_name))
    rows = []
    for row in root.findall(".//m:row", ns):
        values = []
        max_col = 0
        for cell in row.findall("m:c", ns):
            ref = cell.attrib.get("r", "")
            letters = "".join(ch for ch in ref if ch.isalpha())
            col = 0
            for ch in letters:
                col = col * 26 + ord(ch.upper()) - 64
            max_col = max(max_col, col)
            while len(values) < col - 1:
                values.append("")
            ctype = cell.attrib.get("t")
            value = ""
            if ctype == "inlineStr":
                texts = [t.text or "" for t in cell.findall(".//m:t", ns)]
                value = "".join(texts)
            else:
                node = cell.find("m:v", ns)
                if node is not None and node.text is not None:
                    value = shared[int(node.text)] if ctype == "s" and node.text.isdigit() else node.text
            values.append(value)
        if any(str(v).strip() for v in values):
            rows.append(values[:max_col])
    return rows


def read_bom_file_rows(path, read_bytes=None):
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return read_xlsx_rows(path, read_bytes=read_bytes)
    if suffix in (".csv", ".txt"):
        return read_text_rows(path, read_bytes=read_bytes)
    raise ValueError("\u4ec5\u652f\u6301 csv\u3001xlsx\u3001txt \u6587\u4ef6\u3002")


def find_quantity(value):
    text = str(value or "").replace(",", "").strip()
    match = re.search(r"-?\d+", text)
    return max(0, int(match.group(0))) if match else 0


def normalized_header(value):
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def first_value(row, mapping, keys):
    for key in keys:
        idx = mapping.get(key)
        if idx is not None and 0 <= idx < len(row):
            value = str(row[idx] or "").strip()
            if value:
                return value
    return ""


BOM_HEADER_ALIASES = {
    "category": {"category", "class", "type", "\u7c7b\u522b", "\u5546\u54c1\u7c7b\u522b", "\u7c7b\u578b"},
    "name": {
        "name",
        "item",
        "part",
        "part name",
        "component",
        "description",
        "\u540d\u79f0",
        "\u5546\u54c1\u540d\u79f0",
        "\u5668\u4ef6",
        "\u5668\u4ef6\u540d\u79f0",
        "\u7269\u6599",
        "\u7269\u6599\u540d\u79f0",
        "\u578b\u53f7",
    },
    "quantity": {"qty", "quantity", "count", "number", "\u6570\u91cf", "\u9700\u6c42\u6570\u91cf", "\u7528\u91cf"},
    "comment": {"comment", "comments", "remark", "remarks", "note", "\u5907\u6ce8", "\u6ce8\u91ca", "\u89c4\u683c", "\u53c2\u6570", "value"},
    "value": {"value", "\u503c", "\u53c2\u6570\u503c"},
    "designator": {"designator", "designators", "reference", "references", "ref", "refs", "refdes", "ref des", "\u4f4d\u53f7", "\u6807\u53f7", "\u7f16\u53f7"},
    "footprint": {"footprint", "footprints", "package", "pcb footprint", "pcb package", "\u5c01\u88c5", "pcb\u5c01\u88c5"},
    "manufacturer_part": {
        "manufacturer part",
        "manufacturerpart",
        "mfr part",
        "mfr pn",
        "mpn",
        "part number",
        "partnumber",
        "\u5236\u9020\u5546\u7f16\u53f7",
        "\u5382\u5bb6\u578b\u53f7",
        "\u578b\u53f7",
    },
    "manufacturer": {"manufacturer", "mfr", "brand", "\u5382\u5bb6", "\u5236\u9020\u5546", "\u54c1\u724c"},
    "supplier_part": {
        "supplier part",
        "supplierpart",
        "supplier code",
        "lcsc",
        "lcsc part",
        "lcscpart",
        "lcsc code",
        "jlcpcb part",
        "\u7acb\u521b\u7f16\u53f7",
        "\u7acb\u521b\u5546\u57ce\u7f16\u53f7",
        "\u4f9b\u5e94\u5546\u7f16\u53f7",
        "\u5546\u57ce\u7f16\u53f7",
    },
    "supplier": {"supplier", "\u4f9b\u5e94\u5546"},
}


def detect_bom_table(rows):
    normalized_aliases = {key: {normalized_header(a) for a in aliases} for key, aliases in BOM_HEADER_ALIASES.items()}
    best = {"score": -1, "row_index": -1, "mapping": {}}
    for row_index, row in enumerate(rows[:30]):
        mapping = {}
        for col_index, cell in enumerate(row):
            header = normalized_header(cell)
            if not header:
                continue
            for key, aliases in normalized_aliases.items():
                if header in aliases:
                    mapping.setdefault(key, col_index)
        score = len(mapping)
        if "quantity" in mapping:
            score += 4
        if any(key in mapping for key in ("comment", "name", "manufacturer_part", "supplier_part")):
            score += 3
        if "designator" in mapping:
            score += 1
        if score > best["score"]:
            best = {"score": score, "row_index": row_index, "mapping": mapping}

    mapping = best["mapping"]
    header_index = best["row_index"]
    data_start = header_index + 1 if header_index >= 0 and "quantity" in mapping else 0
    if "quantity" not in mapping or not any(key in mapping for key in ("comment", "name", "manufacturer_part", "supplier_part")):
        mapping = {"name": 0, "quantity": 1, "category": 2}
        header_index = -1
        data_start = 0
        for idx, row in enumerate(rows[:12]):
            numeric_cols = [i for i, cell in enumerate(row) if find_quantity(cell) > 0]
            text_cols = [i for i, cell in enumerate(row) if str(cell).strip() and i not in numeric_cols]
            if numeric_cols and text_cols:
                mapping = {
                    "name": text_cols[0],
                    "quantity": numeric_cols[0],
                    "category": text_cols[1] if len(text_cols) > 1 else -1,
                }
                data_start = idx
                break
    headers = rows[header_index] if 0 <= header_index < len(rows) else []
    return {"mapping": mapping, "header_index": header_index, "data_start": data_start, "headers": headers}


def bom_original_row_data(row, headers):
    result = {}
    for idx, value in enumerate(row):
        key = str(headers[idx]).strip() if idx < len(headers) and str(headers[idx]).strip() else f"column_{idx + 1}"
        if key in result:
            key = f"{key}_{idx + 1}"
        result[key] = value
    return result


def split_designators(value):
    text = str(value or "").strip()
    if not text:
        return []
    text = text.replace("\uff0c", ",").replace("\u3001", ",").replace(";", ",")
    return [part.strip() for part in re.split(r"[\s,]+", text) if part.strip()]


def normalize_bom_component_rows(rows):
    table = detect_bom_table(rows)
    mapping = table["mapping"]
    headers = table["headers"]
    components = []
    for offset, row in enumerate(rows[table["data_start"] :]):
        row_index = table["data_start"] + offset
        qty_idx = mapping.get("quantity", 1)
        if qty_idx >= len(row):
            continue
        quantity = find_quantity(row[qty_idx])
        if quantity <= 0:
            continue
        normalized_name = build_bom_item_name(row, mapping)
        if not normalized_name or normalized_header(normalized_name) in (
            "name",
            "\u540d\u79f0",
            "\u5546\u54c1\u540d\u79f0",
            "\u5668\u4ef6\u540d\u79f0",
            "comment",
            "manufacturerpart",
        ):
            continue
        designator = first_value(row, mapping, ["designator", "reference"])
        value = first_value(row, mapping, ["value", "comment"])
        part_name = first_value(row, mapping, ["name", "comment", "manufacturer_part", "supplier_part"])
        package = first_value(row, mapping, ["footprint", "package"])
        manufacturer_part = first_value(row, mapping, ["manufacturer_part", "part_number"])
        supplier_part = first_value(row, mapping, ["supplier_part", "lcsc_part"])
        lcsc_codes = extract_lcsc_codes([supplier_part] + [str(cell) for cell in row])
        category = first_value(row, mapping, ["category"]) or infer_category(value or part_name, designator, package)
        components.append(
            {
                "row_index": row_index + 1,
                "designators": split_designators(designator),
                "designator": designator,
                "part_name": part_name,
                "value": value,
                "package": package,
                "quantity": quantity,
                "lcsc_code": lcsc_codes[0] if lcsc_codes else "",
                "supplier_code": supplier_part,
                "manufacturer_part_number": manufacturer_part,
                "manufacturer": first_value(row, mapping, ["manufacturer"]),
                "category": category,
                "normalized_name": normalized_name,
                "aliases": [
                    alias
                    for alias in [
                        value,
                        part_name,
                        manufacturer_part,
                        supplier_part,
                    ]
                    if alias
                ],
                "original_row": {
                    "values": row,
                    "data": bom_original_row_data(row, headers),
                },
            }
        )
    return components


def infer_category(comment="", designator="", footprint=""):
    designator = str(designator or "").strip().upper()
    footprint = str(footprint or "").strip().upper()
    comment = str(comment or "").strip().upper()
    token = ""
    if designator:
        token = re.split(r"[\s,\-]+", designator)[0]
    if "TEST-POINT" in footprint or "TEST-POINT" in comment:
        return "测试点"
    if token.startswith(("CN", "J", "P")) or "CONN" in footprint or "WAFER" in comment:
        return "连接器"
    if token.startswith("C") or footprint.startswith("C0") or "CAP" in footprint:
        return "电容"
    if token.startswith("R") or footprint.startswith("R0") or re.search(r"\d+(\.\d+)?[KMR]?Ω", comment):
        return "电阻"
    if token.startswith("L"):
        return "电感"
    if token.startswith("D"):
        return "二极管"
    if token.startswith("U") or token.startswith("IC"):
        return "IC"
    if token.startswith("Q"):
        return "晶体管"
    if token.startswith("F") or "FUSE" in footprint:
        return "保险丝"
    if token.startswith("Y") or "CRYSTAL" in footprint:
        return "晶振"
    return "未分类"


def build_bom_item_name(row, mapping):
    comment = first_value(row, mapping, ["comment", "value", "name"])
    manufacturer_part = first_value(row, mapping, ["manufacturer_part", "part_number", "name"])
    supplier_part = first_value(row, mapping, ["supplier_part", "lcsc_part"])
    footprint = first_value(row, mapping, ["footprint", "package"])
    designator = first_value(row, mapping, ["designator", "reference"])
    category = infer_category(comment, designator, footprint)
    base = manufacturer_part or comment or supplier_part
    parts = [base]
    translated = component_value_aliases(manufacturer_part, category)
    if translated and translated[0] not in (comment, base):
        parts.append(f"转译:{translated[0]}")
    if comment and comment != base:
        parts.append(f"规格:{comment}")
    if footprint:
        parts.append(f"封装:{footprint}")
    if supplier_part:
        parts.append(f"LCSC:{supplier_part}")
    if designator:
        parts.append(f"位号:{designator}")
    return " | ".join(part for part in parts if part)


def bom_group_key(row, mapping):
    supplier_part = first_value(row, mapping, ["supplier_part", "lcsc_part"])
    manufacturer_part = first_value(row, mapping, ["manufacturer_part", "part_number"])
    comment = first_value(row, mapping, ["comment", "value", "name"])
    footprint = first_value(row, mapping, ["footprint", "package"])
    if supplier_part:
        return ("supplier", normalize_key(supplier_part))
    if manufacturer_part:
        return ("mpn", normalize_key(manufacturer_part), normalize_key(footprint))
    return ("generic", normalize_key(comment), normalize_key(footprint))


def bom_match_identity(row, mapping, category):
    value = first_value(row, mapping, ["value", "comment", "name"])
    part_name = first_value(row, mapping, ["name", "comment", "manufacturer_part", "supplier_part"])
    package = first_value(row, mapping, ["footprint", "package"])
    manufacturer_part = first_value(row, mapping, ["manufacturer_part", "part_number"])
    supplier_part = first_value(row, mapping, ["supplier_part", "lcsc_part"])
    aliases = [
        value,
        part_name,
        manufacturer_part,
        supplier_part,
    ] + component_value_aliases(
        " ".join([value, manufacturer_part, package]),
        category,
    )
    return {
        "part_name": part_name,
        "value": value,
        "package": package,
        "manufacturer_part_number": manufacturer_part,
        "supplier_code": supplier_part,
        "lcsc_code": (extract_lcsc_codes([supplier_part]) or [""])[0],
        "aliases": list(dict.fromkeys(alias for alias in aliases if alias)),
    }


def parse_bom_rows(rows):
    if not rows:
        return []
    table = detect_bom_table(rows)
    mapping = table["mapping"]
    data_rows = rows[table["data_start"] :]
    aggregated = {}
    for row in data_rows:
        qty_idx = mapping.get("quantity", 1)
        if qty_idx >= len(row):
            continue
        qty = find_quantity(row[qty_idx])
        if qty <= 0:
            continue
        name = build_bom_item_name(row, mapping)
        if not name or normalized_header(name) in ("name", "名称", "商品名称", "器件名称", "comment", "manufacturerpart"):
            continue
        category = first_value(row, mapping, ["category"])
        if not category:
            category = infer_category(
                first_value(row, mapping, ["comment", "value", "name"]),
                first_value(row, mapping, ["designator", "reference"]),
                first_value(row, mapping, ["footprint", "package"]),
            )
        key = bom_group_key(row, mapping)
        identity = bom_match_identity(row, mapping, category)
        if key not in aggregated:
            aggregated[key] = {
                "category": category or "未分类",
                "name": name,
                "quantity": 0,
                "part_name": identity["part_name"],
                "value": identity["value"],
                "package": identity["package"],
                "lcsc_code": identity["lcsc_code"],
                "supplier_code": identity["supplier_code"],
                "manufacturer_part_number": identity["manufacturer_part_number"],
                "aliases": identity["aliases"],
            }
        aggregated[key]["quantity"] += qty
    return list(aggregated.values())


def parse_bom_file(path, read_bytes=None):
    rows = read_bom_file_rows(path, read_bytes=read_bytes)
    items = parse_bom_rows(rows)
    if not items:
        raise ValueError("未读取到有效器件，请确认文件包含名称和数量列。")
    return items


def parse_bom_file_detail(path, read_bytes=None):
    rows = read_bom_file_rows(path, read_bytes=read_bytes)
    items = parse_bom_rows(rows)
    if not items:
        raise ValueError("未读取到有效器件，请确认文件包含名称和数量列。")
    return {
        "raw_rows": rows,
        "component_rows": normalize_bom_component_rows(rows),
        "items": items,
    }
