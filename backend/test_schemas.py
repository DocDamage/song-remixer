import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import schemas  # noqa: E402


class MixSettingsSchemaTests(unittest.TestCase):
    def test_manual_mix_settings_clamps_nudge_and_defaults_style(self):
        settings = schemas.ManualMixSettings(mix_style="unknown", nudge_beats=99)

        self.assertEqual(settings.mix_style, "balanced")
        self.assertEqual(settings.nudge_beats, 2.0)

    def test_advanced_mix_settings_normalizes_eq_and_dynamics(self):
        settings = schemas.AdvancedMixSettings(
            eq_bands=[
                {"frequency_hz": 60, "gain_db": -100},
                {"frequency_hz": 3500, "gain_db": 50},
            ],
            vocal_gain_db=14,
            beat_gain_db=-14,
            compressor_threshold_db=-99,
            compressor_ratio=99,
            compressor_attack_ms=0,
            compressor_release_ms=9999,
        )

        self.assertEqual(settings.eq_bands[0].gain_db, -40.0)
        self.assertEqual(settings.eq_bands[1].gain_db, 20.0)
        self.assertEqual(settings.vocal_gain_db, 12.0)
        self.assertEqual(settings.beat_gain_db, -12.0)
        self.assertEqual(settings.compressor_threshold_db, -60.0)
        self.assertEqual(settings.compressor_ratio, 12.0)
        self.assertEqual(settings.compressor_attack_ms, 1.0)
        self.assertEqual(settings.compressor_release_ms, 1000.0)

    def test_advanced_mix_settings_accepts_empty_payload_as_neutral(self):
        settings = schemas.AdvancedMixSettings()

        self.assertEqual(settings.eq_bands, [])
        self.assertEqual(settings.vocal_gain_db, 0.0)
        self.assertEqual(settings.beat_gain_db, 0.0)
        self.assertEqual(settings.compressor_ratio, 3.0)


if __name__ == "__main__":
    unittest.main()
