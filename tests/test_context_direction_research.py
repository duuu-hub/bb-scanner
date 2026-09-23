import unittest

from context_direction_research import alignment, sign_state, tercile


class ContextDirectionResearchTests(unittest.TestCase):
    def test_terciles(self):
        self.assertEqual(tercile(10), "LOW")
        self.assertEqual(tercile(50), "MID")
        self.assertEqual(tercile(90), "HIGH")

    def test_sign_state(self):
        self.assertEqual(sign_state(1), "EXPANDING")
        self.assertEqual(sign_state(-1), "CONTRACTING")
        self.assertEqual(sign_state(0), "FLAT")

    def test_alignment(self):
        self.assertEqual(alignment(1, 2), "BOTH_UP")
        self.assertEqual(alignment(-1, -2), "BOTH_DOWN")
        self.assertEqual(alignment(1, -2), "MIXED")


if __name__ == "__main__":
    unittest.main()
