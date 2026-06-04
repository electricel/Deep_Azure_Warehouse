import sqlite3
import unittest

import app
import warehouse_bom as bom_tools


class BomMatchingTests(unittest.TestCase):
    def test_same_value_different_package_stays_separate_and_unmatched(self):
        rows = [
            ["Designator", "Value", "Footprint", "Qty"],
            ["C1", "100nF", "C0603", "2"],
            ["C2", "100nF", "C0805", "3"],
        ]

        items = bom_tools.parse_bom_rows(rows)

        self.assertEqual(len(items), 2)
        by_package = {item["package"]: item for item in items}
        self.assertEqual(by_package["C0603"]["quantity"], 2)
        self.assertEqual(by_package["C0805"]["quantity"], 3)

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE inventory (
                category TEXT,
                name TEXT,
                quantity INTEGER,
                note TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO inventory (category, name, quantity, note) VALUES (?, ?, ?, ?)",
            ("电容", "100nF / C0603", 50, "封装:C0603"),
        )

        stock_0603 = app.stock_for_item(
            conn,
            by_package["C0603"]["category"],
            by_package["C0603"]["name"],
            by_package["C0603"]["aliases"],
            package=by_package["C0603"]["package"],
        )
        stock_0805 = app.stock_for_item(
            conn,
            by_package["C0805"]["category"],
            by_package["C0805"]["name"],
            by_package["C0805"]["aliases"],
            package=by_package["C0805"]["package"],
        )

        self.assertEqual(stock_0603, 50)
        self.assertEqual(stock_0805, 0)

    def test_package_code_alone_does_not_match_wrong_part(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            CREATE TABLE inventory (
                category TEXT,
                name TEXT,
                quantity INTEGER,
                note TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO inventory (category, name, quantity, note) VALUES (?, ?, ?, ?)",
            ("电阻", "10k / R0805", 25, "封装:R0805"),
        )

        stock = app.stock_for_item(
            conn,
            "电容",
            "100nF | 封装:C0805",
            ["100nF", "C0805"],
            package="C0805",
        )

        self.assertEqual(stock, 0)


class InventoryLocationSearchTests(unittest.TestCase):
    def assert_location_query_matches(self, query, locations, expected):
        plan = app.inventory_smart_search_plan({"q": [query]})
        self.assertTrue(plan["has_search"])
        self.assertTrue(plan["location_search"])
        self.assertEqual(plan["order_by"], "location")

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE inventory (location TEXT)")
        conn.executemany("INSERT INTO inventory (location) VALUES (?)", [(item,) for item in locations])

        where = " AND ".join(plan["filters"])
        rows = conn.execute(
            f"SELECT location FROM inventory WHERE {where} ORDER BY location COLLATE NOCASE",
            plan["params"],
        ).fetchall()

        self.assertEqual([row["location"] for row in rows], expected)

    def test_device_location_token_normalizes_to_canonical_cell(self):
        location = app.normalize_inventory_device_location_token("H01-7-4")

        self.assertIsNotNone(location)
        self.assertEqual(location["canonical"], "H1-07-04")
        self.assertEqual(location["level"], "cell")

    def test_device_box_location_search_does_not_match_other_boxes(self):
        self.assert_location_query_matches(
            "H1",
            ["H1", "H1-01-01", "H01-02-03", "H10-01-01", "H2-01-01"],
            ["H01-02-03", "H1", "H1-01-01"],
        )

    def test_device_strip_location_search_matches_only_that_strip(self):
        self.assert_location_query_matches(
            "H1-07",
            ["H1-07", "H1-07-01", "H1-07-04", "H1-08-01", "H10-07-01"],
            ["H1-07", "H1-07-01", "H1-07-04"],
        )

    def test_device_cell_location_search_matches_exact_cell_only(self):
        self.assert_location_query_matches(
            "H1-07-04",
            ["H1-07-04", "H1-7-4", "H01-07-04", "H1-07-03", "H1-07-04-extra"],
            ["H01-07-04", "H1-07-04", "H1-7-4"],
        )


class SecurityOriginTests(unittest.TestCase):
    def test_localhost_origin_with_configured_port_is_allowed(self):
        origins = app.configured_allowed_origins()

        self.assertIn(f"http://127.0.0.1:{app.PORT}", origins)
        self.assertIn(f"http://localhost:{app.PORT}", origins)


if __name__ == "__main__":
    unittest.main()
