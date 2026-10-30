"""Offline tests: no network, dummy AES key. Run: python3 -m unittest discover -s tests"""
import os
import sys
import unittest
from datetime import datetime

os.environ["SOLAIRE_AES_KEY"] = "0123456789abcdef"
os.environ["SOLAIRE_AES_IV"] = "fedcba9876543210"
os.environ["SOLAIRE_CONFIG_DIR"] = "/nonexistent"
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import solaire_fetch as sf  # noqa: E402


class FetchTests(unittest.TestCase):
    def test_encrypt_roundtrip(self):
        text = '{"curDate":"2026-01-01","year":"2026"}'
        self.assertEqual(sf.decrypt(sf.encrypt(text)), text)

    def test_parse_share(self):
        code = "a" * 32
        self.assertEqual(sf.parse_share(f"https://example.com/#/public/share/data-screen/?shareCode={code}&domain=x"), code)
        self.assertEqual(sf.parse_share(code), code)
        self.assertIsNone(sf.parse_share("not a link!"))
        self.assertIsNone(sf.parse_share(""))

    def test_stop_analysis_counts_zero_runs_inside_the_producing_window(self):
        vals = [None, 0, 0, 100, 200, 0, 0, 300, 400, 0, 500, 0, 0]
        minutes, runs, lost = sf.stop_analysis(vals)
        self.assertEqual((minutes, runs), (15, 2))  # 3 interior zero slots, in 2 runs; edges are night
        self.assertGreater(lost, 0)

    def test_stop_analysis_no_production(self):
        self.assertEqual(sf.stop_analysis([0, 0, None]), (0, 0, 0.0))

    def test_clear_curve_shape(self):
        curve = sf.clear_curve(datetime(2026, 6, 21), 12.0, 77.0, 5.5, 3.3)
        self.assertEqual(len(curve), 288)
        self.assertEqual(curve[0], 0)  # midnight
        self.assertGreater(max(curve), 1500)
        self.assertLessEqual(max(curve), 3300)  # clipped at the array rating
        self.assertEqual(curve.index(max(curve)) // 12 in range(10, 15), True)  # peak around local noon


if __name__ == "__main__":
    unittest.main()
