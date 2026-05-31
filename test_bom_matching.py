import sqlite3
import unittest

import app


class BomMatchingTests(unittest.TestCase):
    def test_same_value_different_package_stays_separate_and_unmatched(self):
        rows = [
            ["Designator", "Value", "Footprint", "Qty"],
            ["C1", "100nF", "C0603", "2"],
            ["C2", "100nF", "C0805", "3"],
        ]

        items = app.parse_bom_rows(rows)

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


class SecurityOriginTests(unittest.TestCase):
    def test_localhost_origin_with_configured_port_is_allowed(self):
        origins = app.configured_allowed_origins()

        self.assertIn(f"http://127.0.0.1:{app.PORT}", origins)
        self.assertIn(f"http://localhost:{app.PORT}", origins)


if __name__ == "__main__":
    unittest.main()
