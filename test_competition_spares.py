import unittest

import app


class CompetitionSparesTest(unittest.TestCase):
    def test_infers_mechanical_spares(self):
        self.assertEqual(app.infer_competition_material_type("气泵", "24V 真空泵", ""), "mechanical")
        self.assertEqual(app.infer_competition_material_type("其他", "M3 螺栓", "10mm"), "mechanical")
        self.assertEqual(app.infer_competition_material_type("碳板/板材", "3K 碳板", "2mm"), "mechanical")

    def test_infers_hardware_spares(self):
        self.assertEqual(app.infer_competition_material_type("硬件器件", "降压模块", "24V 转 5V"), "hardware")
        self.assertEqual(app.infer_competition_material_type("其他", "CAN 传感器线", "GH1.25"), "hardware")

    def test_normalizes_robot_class(self):
        self.assertEqual(app.normalize_competition_robot_class("", "机器人1 云台备件"), "robot_a")
        self.assertEqual(app.normalize_competition_robot_class("", "机器人2 底盘备件"), "robot_b")
        self.assertEqual(app.normalize_competition_robot_class("", "赛场维修工具"), "pit")
        self.assertEqual(app.normalize_competition_robot_class("", "通用备件"), "shared")

    def test_maps_image_recognition_item_to_competition_spare(self):
        item = app.competition_image_item_from_inventory_item(
            {
                "category": "其他",
                "name": "M3 螺栓",
                "value_spec": "10mm",
                "quantity": 20,
                "location": "H1-07-04",
            }
        )

        self.assertEqual(item["category"], "螺栓/螺母")
        self.assertEqual(item["material_type"], "mechanical")
        self.assertEqual(item["storage_location"], "H1-07-04")
        self.assertEqual(item["quantity"], 20)

    def test_storage_location_search_supports_box_strip_and_cell(self):
        box_clause, box_params = app.competition_material_storage_location_clause("H1")
        self.assertIn("LIKE", box_clause)
        self.assertIn("H1-%", box_params)
        self.assertIn("H01-%", box_params)

        strip_clause, strip_params = app.competition_material_storage_location_clause("H1-7")
        self.assertIn("LIKE", strip_clause)
        self.assertIn("H1-07-%", strip_params)
        self.assertIn("H01-07-%", strip_params)

        cell_clause, cell_params = app.competition_material_storage_location_clause("H1-7-4")
        self.assertNotIn("LIKE", cell_clause)
        self.assertIn("H1-07-04", cell_params)
        self.assertIn("H01-07-04", cell_params)


if __name__ == "__main__":
    unittest.main()
