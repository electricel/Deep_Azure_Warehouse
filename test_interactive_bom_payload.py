import unittest

from warehouse_interactive_bom import build_interactive_bom_payload, component_refs


class InteractiveBomPayloadTest(unittest.TestCase):
    def test_component_refs_split_common_separators(self):
        self.assertEqual(component_refs({"designator": "R1, R2；R3、R4"}), ["R1", "R2", "R3", "R4"])

    def test_payload_contains_components_and_placeholder_board(self):
        payload = build_interactive_bom_payload(
            {
                "bom_id": "bom-test",
                "original_filename": "demo.xlsx",
                "component_rows": [
                    {
                        "designator": "U1",
                        "part_name": "MCU",
                        "value": "STM32",
                        "package": "LQFP-48",
                        "quantity": "2",
                        "lcsc_code": "C12345",
                    }
                ],
            }
        )

        self.assertEqual(payload["bom_id"], "bom-test")
        self.assertEqual(payload["title"], "demo.xlsx")
        self.assertTrue(payload["board"]["placeholder"])
        self.assertEqual(payload["meta"]["coordinate_source"], "placeholder")
        self.assertEqual(payload["components"][0]["refs"], ["U1"])
        self.assertEqual(payload["components"][0]["footprint"], "LQFP-48")
        self.assertEqual(payload["components"][0]["lcsc_code"], "C12345")
        self.assertIn("bbox", payload["components"][0])
        self.assertEqual(payload["config"]["bom_view"], "left-right")
        self.assertEqual(payload["pcbdata"]["metadata"]["title"], "demo.xlsx")
        self.assertEqual(payload["pcbdata"]["footprints"][0]["ref"], "U1")
        self.assertEqual(payload["pcbdata"]["bom"]["both"][0], [["U1", 0]])
        self.assertEqual(payload["pcbdata"]["bom"]["fields"]["0"][3], "C12345")


if __name__ == "__main__":
    unittest.main()
