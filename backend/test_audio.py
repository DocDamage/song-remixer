import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import numpy as np
import soundfile as sf


BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import audio  # noqa: E402


class CommandErrorSummaryTests(unittest.TestCase):
    def test_summarize_command_error_prefers_specific_error_line(self):
        stderr = """
        ffmpeg banner
        stream info
        Error opening input files: Invalid data found when processing input
        """

        self.assertEqual(
            audio._summarize_command_error(stderr),
            "Error opening input files: Invalid data found when processing input",
        )

    def test_summarize_command_error_falls_back_to_last_line(self):
        stderr = "warning line\nlast useful line"

        self.assertEqual(audio._summarize_command_error(stderr), "last useful line")

    def test_split_stems_uses_integer_segment_value_for_demucs_cli(self):
        captured_cmd = {}

        with TemporaryDirectory() as tmp_dir:
            output_root = Path(tmp_dir) / "stems"
            input_path = str(Path(tmp_dir) / "mix.wav")
            Path(input_path).touch()

            def fake_run_cmd(cmd):
                captured_cmd["cmd"] = cmd
                track_dir = output_root / audio.STEM_SPLITTER_MODEL / Path(input_path).stem
                track_dir.mkdir(parents=True, exist_ok=True)
                (track_dir / "vocals.wav").touch()

            with patch("audio.importlib.util.find_spec", return_value=object()), patch(
                "audio._detect_demucs_runtime", return_value=("cpu", 1)
            ), patch("audio._run_cmd", side_effect=fake_run_cmd):
                stem_files = audio.split_stems_with_demucs(input_path, str(output_root))

        segment_index = captured_cmd["cmd"].index("--segment") + 1
        self.assertEqual(Path(captured_cmd["cmd"][1]).name, "demucs_runner.py")
        self.assertEqual(captured_cmd["cmd"][segment_index], str(audio.STEM_SPLITTER_SEGMENT_SECONDS))
        self.assertIn("vocals", stem_files)

    def test_resolve_mix_style_falls_back_to_default(self):
        style_name, style = audio._resolve_mix_style("not-a-style")

        self.assertEqual(style_name, audio.DEFAULT_MIX_STYLE)
        self.assertEqual(style, audio.MIX_STYLE_PRESETS[audio.DEFAULT_MIX_STYLE])

    def test_refine_offset_with_onsets_tracks_click_alignment(self):
        sample_rate = 22050
        beat = np.zeros(sample_rate * 4, dtype=np.float32)
        vocal = np.zeros(sample_rate * 4, dtype=np.float32)
        beat_clicks = [0.5, 1.0, 1.5, 2.0]
        vocal_clicks = [0.72, 1.22, 1.72, 2.22]

        for click_time in beat_clicks:
            beat[int(click_time * sample_rate)] = 1.0
        for click_time in vocal_clicks:
            vocal[int(click_time * sample_rate)] = 1.0

        with TemporaryDirectory() as tmp_dir:
            beat_path = Path(tmp_dir) / "beat.wav"
            vocal_path = Path(tmp_dir) / "vocal.wav"
            sf.write(beat_path, beat, sample_rate)
            sf.write(vocal_path, vocal, sample_rate)

            refined = audio._refine_offset_with_onsets(str(beat_path), str(vocal_path), -0.08, 120.0)

        self.assertAlmostEqual(refined, -0.22, delta=0.06)


class AdvancedMixFilterTests(unittest.TestCase):
    def test_build_advanced_eq_filter_skips_zero_gain_bands(self):
        class Band:
            frequency_hz = 3500
            gain_db = 0
            q = 1.2

        class Settings:
            eq_bands = [Band()]

        self.assertEqual(audio._build_advanced_eq_filter(Settings()), "")

    def test_build_advanced_eq_filter_escapes_numeric_values(self):
        class Band:
            frequency_hz = 3500
            gain_db = 4
            q = 1.2

        class Settings:
            eq_bands = [Band()]

        self.assertEqual(
            audio._build_advanced_eq_filter(Settings()),
            ",equalizer=f=3500:t=q:w=1.20:g=4.0",
        )

    def test_advanced_mix_adjusts_track_gain_filters(self):
        class Settings:
            vocal_gain_db = 2.5
            beat_gain_db = -1.5
            eq_bands = []

        profile = {
            "rms_db": -20.0,
            "peak_db": -3.0,
            "crest_db": 17.0,
            "centroid_hz": 3200.0,
            "bandwidth_hz": 1800.0,
            "zero_crossing_rate": 0.05,
        }

        beat_filter = audio._build_mix_filter(profile, "beat", "balanced", Settings())
        vocal_filter = audio._build_mix_filter(profile, "vocal", "balanced", Settings())

        self.assertIn("volume=0.0dB", beat_filter)
        self.assertIn("volume=5.5dB", vocal_filter)


class AnalysisConfidenceTests(unittest.TestCase):
    def test_estimate_bpm_confidence_is_low_for_empty_beats(self):
        self.assertEqual(audio.estimate_bpm_confidence(np.array([]), 120.0), 0.0)

    def test_estimate_bpm_confidence_increases_for_consistent_beats(self):
        beat_times = np.array([0.0, 0.5, 1.0, 1.5, 2.0])

        confidence = audio.estimate_bpm_confidence(beat_times, 120.0)

        self.assertGreater(confidence, 0.8)

    def test_estimate_key_confidence_returns_normalized_range(self):
        chroma_mean = np.array([1.0, 0.1, 0.8, 0.2, 0.7, 0.3, 0.6, 0.2, 0.4, 0.2, 0.3, 0.1])

        confidence = audio.estimate_key_confidence(chroma_mean)

        self.assertGreaterEqual(confidence, 0.0)
        self.assertLessEqual(confidence, 1.0)


if __name__ == "__main__":
    unittest.main()