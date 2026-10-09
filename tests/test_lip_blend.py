import unittest

import numpy as np

from avatars.lip_blend import blend_mouth_region


class LipBlendTestCase(unittest.TestCase):
    def setUp(self):
        self.original = np.full((100, 80, 3), 20, dtype=np.uint8)
        self.generated = np.full((100, 80, 3), 220, dtype=np.uint8)

    def test_zero_strength_returns_original(self):
        result = blend_mouth_region(self.original, self.generated, 0.0)
        np.testing.assert_array_equal(result, self.original)

    def test_upper_face_is_preserved(self):
        result = blend_mouth_region(self.original, self.generated, 1.0)
        np.testing.assert_array_equal(result[:46], self.original[:46])

    def test_lower_center_uses_requested_strength(self):
        result = blend_mouth_region(self.original, self.generated, 0.5)
        self.assertEqual(int(result[70, 40, 0]), 120)

    def test_strength_is_clamped(self):
        high = blend_mouth_region(self.original, self.generated, 3.0)
        low = blend_mouth_region(self.original, self.generated, -2.0)
        self.assertEqual(int(high[70, 40, 0]), 220)
        np.testing.assert_array_equal(low, self.original)

    def test_shape_mismatch_is_rejected(self):
        with self.assertRaises(ValueError):
            blend_mouth_region(self.original, self.generated[:90], 0.5)


if __name__ == "__main__":
    unittest.main()
