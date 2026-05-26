import os
import shutil
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import librosa
import numpy as np
from fastapi.testclient import TestClient


BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402


class EndToEndApiTests(unittest.TestCase):
    def setUp(self):
        missing_tools = [tool for tool in ("ffmpeg", "sox") if shutil.which(tool) is None]
        if missing_tools:
            self.skipTest(f"Missing required audio tools: {', '.join(missing_tools)}")

        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.original_upload_dir = main.UPLOAD_DIR
        self.original_upload_root = main.UPLOAD_ROOT
        main.UPLOAD_DIR = Path(self.temp_dir.name) / "uploads"
        main.UPLOAD_ROOT = main.UPLOAD_DIR.resolve()

        self.addCleanup(self._restore_upload_paths)

    def _restore_upload_paths(self):
        main.UPLOAD_DIR = self.original_upload_dir
        main.UPLOAD_ROOT = self.original_upload_root

    def _write_click_track(self, path: Path, bpm: float, click_frequency: float, duration: float = 8.0) -> None:
        sample_rate = 22050
        beat_times = np.arange(0, duration, 60.0 / bpm)
        audio = librosa.clicks(
            times=beat_times,
            sr=sample_rate,
            click_freq=click_frequency,
            click_duration=0.08,
            length=int(duration * sample_rate),
        )
        audio = np.clip(audio, -1.0, 1.0)

        with wave.open(str(path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes((audio * 32767).astype(np.int16).tobytes())

    def test_analyze_process_and_download_generated_wav_tracks(self):
        inputs_dir = Path(self.temp_dir.name) / "inputs"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        beat_path = inputs_dir / "beat.wav"
        acapella_path = inputs_dir / "acapella.wav"
        self._write_click_track(beat_path, bpm=120.0, click_frequency=880.0)
        self._write_click_track(acapella_path, bpm=120.0, click_frequency=440.0)

        with TestClient(main.app) as client:
            analyze_response = client.post(
                "/analyze",
                files={
                    "beat": (beat_path.name, beat_path.read_bytes(), "audio/wav"),
                    "acapella": (acapella_path.name, acapella_path.read_bytes(), "audio/wav"),
                },
            )

            self.assertEqual(analyze_response.status_code, 200, analyze_response.text)
            analysis = analyze_response.json()
            self.assertGreater(analysis["beat"]["bpm"], 0)
            self.assertGreater(analysis["suggested"]["tempo_ratio"], 0)

            process_response = client.post(
                "/process",
                data={
                    "beat_file_id": analysis["beat"]["file_id"],
                    "acapella_file_id": analysis["acapella"]["file_id"],
                    "bpm": str(analysis["beat"]["bpm"]),
                    "pitch_shift": str(analysis["suggested"]["pitch_shift"]),
                    "tempo_ratio": str(analysis["suggested"]["tempo_ratio"]),
                    "beat_downbeat": str(analysis["beat"]["downbeat"]),
                    "acapella_downbeat": str(analysis["acapella"]["downbeat"]),
                    "nudge_beats": "0",
                },
            )

            self.assertEqual(process_response.status_code, 200, process_response.text)
            process_result = process_response.json()

            output_path = main.UPLOAD_DIR / process_result["output_file"]
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)

            download_response = client.get(process_result["download_url"])
            self.assertEqual(download_response.status_code, 200)
            self.assertEqual(download_response.headers["content-type"], "audio/wav")
            self.assertGreater(len(download_response.content), 0)

    def test_auto_mix_and_download_generated_wav_tracks(self):
        inputs_dir = Path(self.temp_dir.name) / "inputs-auto"
        inputs_dir.mkdir(parents=True, exist_ok=True)

        beat_path = inputs_dir / "beat.wav"
        acapella_path = inputs_dir / "acapella.wav"
        self._write_click_track(beat_path, bpm=124.0, click_frequency=880.0)
        self._write_click_track(acapella_path, bpm=118.0, click_frequency=440.0)

        with TestClient(main.app) as client:
            response = client.post(
                "/auto-mix",
                files={
                    "beat": (beat_path.name, beat_path.read_bytes(), "audio/wav"),
                    "acapella": (acapella_path.name, acapella_path.read_bytes(), "audio/wav"),
                },
            )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()

            output_path = main.UPLOAD_DIR / result["output_file"]
            self.assertTrue(output_path.exists())
            self.assertGreater(output_path.stat().st_size, 0)

            download_response = client.get(result["download_url"])
            self.assertEqual(download_response.status_code, 200)
            self.assertEqual(download_response.headers["content-type"], "audio/wav")
            self.assertGreater(len(download_response.content), 0)

    def test_real_split_stems_fixture_when_enabled(self):
        if os.environ.get("SONG_REMIXER_RUN_REAL_STEM_SPLIT") != "1":
            self.skipTest("Real stem split test is disabled")

        fixture_path = BACKEND_DIR.parent / "ui-fixtures" / "beat.wav"
        if not fixture_path.exists():
            self.skipTest("Missing UI fixture for real stem split test")

        with TestClient(main.app) as client:
            response = client.post(
                "/split-stems",
                files={
                    "track": (fixture_path.name, fixture_path.read_bytes(), "audio/wav"),
                },
            )

            self.assertEqual(response.status_code, 200, response.text)
            result = response.json()
            self.assertEqual(sorted(result["stems"]), ["bass", "drums", "other", "vocals"])
            self.assertGreaterEqual(len(result["stem_downloads"]), 4)

            zip_response = client.get(result["download_url"])
            self.assertEqual(zip_response.status_code, 200)
            self.assertEqual(zip_response.headers["content-type"], "application/zip")
            self.assertGreater(len(zip_response.content), 0)

            acapella_response = client.get(result["acapella_download_url"])
            self.assertEqual(acapella_response.status_code, 200)
            self.assertEqual(acapella_response.headers["content-type"], "audio/wav")
            self.assertGreater(len(acapella_response.content), 0)


if __name__ == "__main__":
    unittest.main()