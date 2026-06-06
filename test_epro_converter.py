import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile

from warehouse_epro_converter import inspect_epro_archive, process_epro_upload, run_external_converter


def make_zip(entries):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


def easyeda_standard_json():
    return json.dumps(
        {
            "head": {"docType": "3", "editorVersion": "test"},
            "canvas": "~".join(["0"] * 18),
            "shape": [],
            "BBox": {"x": 0, "y": 0, "width": 10, "height": 10},
        }
    ).encode("utf-8")


class EproConverterTest(unittest.TestCase):
    def test_extracts_supported_easyeda_json_from_epro_zip(self):
        payload = make_zip(
            {
                "project/readme.txt": b"ignored",
                "exports/board.json": easyeda_standard_json(),
            }
        )

        result = inspect_epro_archive(payload)

        self.assertEqual(result.status, "converted")
        self.assertEqual(len(result.generated_files), 1)
        self.assertEqual(result.generated_files[0].filename, "board.json")
        self.assertEqual(result.generated_files[0].source, "archive")

    def test_extracts_interactive_html_bom_generic_json_from_epro_zip(self):
        payload = make_zip(
            {
                "exports/ibom-data.json": json.dumps({"pcbdata": {"footprints": [], "bom": {}}}).encode("utf-8"),
            }
        )

        result = inspect_epro_archive(payload)

        self.assertEqual(result.status, "converted")
        self.assertEqual(len(result.generated_files), 1)
        self.assertEqual(result.generated_files[0].filename, "ibom-data.json")

    def test_extracts_eagle_fusion_board_file_from_epro_zip(self):
        payload = make_zip({"exports/board.brd": b"<eagle><drawing /></eagle>"})

        result = inspect_epro_archive(payload)

        self.assertEqual(result.status, "converted")
        self.assertEqual(len(result.generated_files), 1)
        self.assertEqual(result.generated_files[0].filename, "board.brd")

    def test_converts_easyeda_pro_epcb_to_generic_interactive_bom_json(self):
        payload = make_zip(
            {
                "project/main.epcb": b"\n".join(
                    [
                        b'{"type":"DOCTYPE_PCB","title":"Demo Board"}',
                        b'{"type":"COMPONENT","ref":"U1","value":"MCU","footprint":"LQFP-48","x":12.5,"y":8.0,"rotation":90,"layer":"top"}',
                        b'{"type":"COMPONENT","designator":"R1","Value":"10k","package":"0603","posX":20,"posY":10,"Layer":"bottom"}',
                    ]
                ),
            }
        )

        result = process_epro_upload(payload, "demo.epro", "")

        self.assertEqual(result.status, "converted")
        self.assertEqual(len(result.generated_files), 2)
        generated = next(item for item in result.generated_files if item.filename.endswith(".json"))
        generated_html = next(item for item in result.generated_files if item.filename.endswith(".html"))
        self.assertEqual(generated.filename, "main.ibom.json")
        self.assertEqual(generated.source, "internal_epcb_parser")
        self.assertEqual(generated_html.filename, "main.ibom.html")
        self.assertEqual(generated_html.source, "internal_epcb_parser")
        parsed = json.loads(generated.content.decode("utf-8"))
        pcbdata = parsed["pcbdata"]
        self.assertEqual([footprint["ref"] for footprint in pcbdata["footprints"]], ["R1", "U1"])
        self.assertEqual(len(pcbdata["bom"]["both"]), 2)
        self.assertEqual(pcbdata["footprints"][0]["layer"], "B")
        self.assertEqual(pcbdata["footprints"][1]["layer"], "F")
        html_text = generated_html.content.decode("utf-8")
        self.assertIn("interactive-bom-viewer", html_text)
        self.assertIn("data-interactive-bom-payload", html_text)
        self.assertIn("U1", html_text)

    def test_converts_easyeda_pro_jsonl_array_records_with_attrs(self):
        epcb_lines = [
            ["DOCTYPE_PCB", "doc-1"],
            ["COMPONENT", "cmp-u1", "", "F", 12.5, 8.0, 90, {}],
            ["ATTR", "attr-u1-ref", "", "cmp-u1", "F", 0, 0, "Designator", "U1"],
            ["ATTR", "attr-u1-val", "", "cmp-u1", "F", 0, 0, "Value", "MCU"],
            ["ATTR", "attr-u1-fp", "", "cmp-u1", "F", 0, 0, "Package", "LQFP-48"],
            ["COMPONENT", "cmp-r1", "", "B", 20, 10, 180, {"Designator": "R1", "Value": "10k", "Package": "0603"}],
        ]
        payload = make_zip(
            {
                "project/main.epcb": "\n".join(json.dumps(line) for line in epcb_lines).encode("utf-8"),
            }
        )

        result = process_epro_upload(payload, "demo.epro", "")

        self.assertEqual(result.status, "converted")
        generated = next(item for item in result.generated_files if item.filename.endswith(".json"))
        parsed = json.loads(generated.content.decode("utf-8"))
        footprints = parsed["pcbdata"]["footprints"]
        fields = parsed["pcbdata"]["bom"]["fields"]
        self.assertEqual([footprint["ref"] for footprint in footprints], ["R1", "U1"])
        self.assertEqual(footprints[0]["layer"], "B")
        self.assertEqual(footprints[1]["center"], [12.5, 8.0])
        self.assertEqual(fields["0"][1], "10k")
        self.assertEqual(fields["1"][2], "LQFP-48")

    def test_unparseable_easyeda_pro_epcb_still_waits_for_external_converter(self):
        payload = make_zip(
            {
                "project/main.epcb": b'{"type":"DOCTYPE_PCB"}\n{"type":"FOOTPRINT"}\n{"type":"PAD"}\n',
            }
        )

        result = process_epro_upload(payload, "demo.epro", "")

        self.assertEqual(result.status, "pending_external_converter")
        self.assertIn("main.epcb", result.diagnostics.get("easyeda_pro_pcb_members", [])[0])
        self.assertFalse(result.generated_files)

    def test_rejects_non_zip_epro_payload(self):
        result = inspect_epro_archive(b"not a zip")

        self.assertEqual(result.status, "unsupported")
        self.assertFalse(result.generated_files)

    def test_external_converter_template_error_is_reported(self):
        result = run_external_converter(b"not a zip", "demo.epro", "{missing_placeholder}")

        self.assertEqual(result.status, "external_converter_config_error")
        self.assertIn("command_template", result.diagnostics)

    def test_store_pcb_uploads_attaches_epro_and_derived_epcb_json(self):
        script = r"""
import io
import json
import zipfile

import app

epcb = b"\n".join([
    b'{"type":"DOCTYPE_PCB","title":"Demo Board"}',
    b'{"type":"COMPONENT","ref":"U1","value":"MCU","footprint":"LQFP-48","x":12.5,"y":8.0,"rotation":90,"layer":"top"}',
])
buffer = io.BytesIO()
with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
    zf.writestr("project/main.epcb", epcb)

stored = app.store_pcb_uploads(
    "bom-epro-test",
    [{"filename": "demo.epro", "content": buffer.getvalue(), "content_type": "application/octet-stream"}],
    {"username": "tester"},
)
print(json.dumps({
    "count": len(stored),
    "extensions": [item["extension"] for item in stored],
    "statuses": [item.get("processing_status") for item in stored],
    "derived": [bool(item.get("derived_from_pcb_id")) for item in stored],
    "sources": [item.get("conversion_source", "") for item in stored],
    "exists": [(app.DATA_DIR / item["relative_path"]).exists() for item in stored],
}, ensure_ascii=False))
"""
        with tempfile.TemporaryDirectory() as tmp:
            env = {
                **os.environ,
                "WAREHOUSE_DATA_DIR": tmp,
                "WAREHOUSE_DATA_ENCRYPTION": "0",
                "WAREHOUSE_OPEN_BROWSER": "0",
            }
            completed = subprocess.run(
                [sys.executable, "-c", script],
                cwd=os.getcwd(),
                env=env,
                capture_output=True,
                text=True,
                timeout=30,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
        self.assertEqual(payload["count"], 3)
        self.assertEqual(payload["extensions"], [".epro", ".json", ".html"])
        self.assertEqual(payload["statuses"], ["converted", "converted", "converted"])
        self.assertEqual(payload["derived"], [False, True, True])
        self.assertEqual(payload["sources"], ["", "internal_epcb_parser", "internal_epcb_parser"])
        self.assertEqual(payload["exists"], [True, True, True])


if __name__ == "__main__":
    unittest.main()
