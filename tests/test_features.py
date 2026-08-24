import unittest

import numpy as np

from csi_capture.core.features import extract_window_features, window_features_to_matrix


class FeatureExtractionTests(unittest.TestCase):
    def test_extract_window_features_shape(self):
        frames = []
        for idx in range(20):
            frames.append(
                {
                    "timestamp": 1_700_000_000_000 + idx * 100,
                    "csi": [1 + idx, 2 + idx, 3 + idx, 4 + idx, 5 + idx, 6 + idx],
                }
            )

        feats = extract_window_features(
            frames,
            run_id="run_001",
            label="class-a",
            window_ms=1000,
            overlap=0.5,
        )
        self.assertGreater(len(feats), 0)

        matrix = window_features_to_matrix(feats)
        self.assertEqual(matrix.ndim, 2)
        self.assertEqual(matrix.shape[1], 4)
        self.assertTrue(np.all(np.isfinite(matrix)))


if __name__ == "__main__":
    unittest.main()
