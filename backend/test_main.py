import io
import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
from fastapi.testclient import TestClient

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import main  # noqa: E402


class UploadPathTests(unittest.TestCase):
    def test_build_raw_upload_path_uses_server_owned_name(self):
        path = main._build_raw_upload_path("abc123", "..\\..\\evil.mp3", "beat")

        self.assertEqual(path.parent.resolve(), main.UPLOAD_ROOT)
        self.assertEqual(path.name, "abc123_beat_upload.mp3")

    def test_resolve_upload_path_rejects_parent_directory_reference(self):
        with self.assertRaises(main.HTTPException) as context:
            main._resolve_upload_path("..\\secret.wav")

        self.assertEqual(context.exception.status_code, 400)

    def test_validate_process_inputs_rejects_zero_bpm(self):
        with self.assertRaises(main.HTTPException) as context:
            main._validate_process_inputs(0.0, 1.0)

        self.assertEqual(context.exception.status_code, 400)

    def test_validate_process_inputs_rejects_zero_tempo_ratio(self):
        with self.assertRaises(main.HTTPException) as context:
            main._validate_process_inputs(120.0, 0.0)

        self.assertEqual(context.exception.status_code, 400)

    def test_normalize_tempo_ratio_folds_doubled_acapella_bpm(self):
        ratio = main._normalize_tempo_ratio(89.1029, 184.5703)

        self.assertAlmostEqual(ratio, 0.9655, places=3)

    def test_normalize_tempo_ratio_folds_halved_acapella_bpm(self):
        ratio = main._normalize_tempo_ratio(184.5703, 89.1029)

        self.assertAlmostEqual(ratio, 1.0357, places=3)

    def test_clone_analysis_response_repairs_stale_half_speed_ratio(self):
        analysis = {
            "beat": {"file_id": "beat.wav", "bpm": 89.1029},
            "acapella": {"file_id": "acapella.wav", "bpm": 184.5703},
            "suggested": {"tempo_ratio": 0.4828, "pitch_shift": 0},
            "manual_mix": {"mix_style": "balanced", "nudge_beats": 0.0},
        }

        restored = main._clone_analysis_response(analysis, restored=True)

        self.assertAlmostEqual(restored["suggested"]["tempo_ratio"], 0.9655, places=3)

    def test_ensure_runtime_dependencies_reports_missing_tools(self):
        def fake_which(tool_name):
            if tool_name == "sox":
                return None
            return f"C:/tools/{tool_name}.exe"

        with patch.object(main.shutil, "which", side_effect=fake_which):
            with self.assertRaises(RuntimeError) as context:
                main._ensure_runtime_dependencies()

        self.assertIn("sox", str(context.exception))


class ApiContractTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

        self.original_upload_dir = main.UPLOAD_DIR
        self.original_upload_root = main.UPLOAD_ROOT
        main.UPLOAD_DIR = Path(self.temp_dir.name)
        main.UPLOAD_ROOT = main.UPLOAD_DIR.resolve()

        self.addCleanup(self._restore_upload_paths)

    def _restore_upload_paths(self):
        main.UPLOAD_DIR = self.original_upload_dir
        main.UPLOAD_ROOT = self.original_upload_root

    def test_analyze_returns_full_precision_values(self):
        convert_calls = []

        def fake_convert(input_path, output_path):
            convert_calls.append((Path(input_path).name, Path(output_path).name))
            Path(output_path).write_bytes(b"wav-data")

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["beat-id", "acap-id"]
        ), patch.object(main, "convert_to_wav", side_effect=fake_convert), patch.object(
            main.librosa, "load", side_effect=[(np.zeros(8), 22050), (np.zeros(8), 22050)]
        ), patch.object(
            main, "detect_bpm", side_effect=[(128.4567, np.array([0.0, 0.5])), (91.2345, np.array([0.25, 0.75]))]
        ), patch.object(
            main, "detect_key", side_effect=[("C major", 0), ("A minor", 9)]
        ), patch.object(main, "detect_downbeat", side_effect=[0.123456, 0.654321]):
            with TestClient(main.app) as client:
                response = client.post(
                    "/analyze",
                    files={
                        "beat": ("../../evil.mp3", io.BytesIO(b"beat"), "audio/mpeg"),
                        "acapella": ("vocals.wav", io.BytesIO(b"acapella"), "audio/wav"),
                    },
                )

        self.assertEqual(response.status_code, 200)
        data = response.json()

        self.assertEqual(data["beat"]["file_id"], "beat-id_beat.wav")
        self.assertEqual(data["acapella"]["file_id"], "acap-id_acapella.wav")
        self.assertAlmostEqual(data["beat"]["bpm"], 128.4567)
        self.assertAlmostEqual(data["acapella"]["downbeat"], 0.654321)
        self.assertAlmostEqual(data["suggested"]["tempo_ratio"], 128.4567 / 91.2345)
        self.assertEqual(data["beat"]["source_name"], "../../evil.mp3")
        self.assertEqual(data["acapella"]["source_name"], "vocals.wav")
        self.assertEqual(data["manual_mix"], {"mix_style": main.DEFAULT_MIX_STYLE, "nudge_beats": 0.0})
        self.assertFalse(data["restored"])
        self.assertEqual(convert_calls[0][0], "beat-id_beat_upload.mp3")
        self.assertEqual(convert_calls[1][0], "acap-id_acapella_upload.wav")

    def test_latest_analysis_endpoint_restores_snapshot_after_restart(self):
        def fake_convert(_input_path, output_path):
            Path(output_path).write_bytes(b"wav-data")

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["beat-id", "acap-id"]
        ), patch.object(main, "convert_to_wav", side_effect=fake_convert), patch.object(
            main.librosa, "load", side_effect=[(np.zeros(8), 22050), (np.zeros(8), 22050)]
        ), patch.object(
            main, "detect_bpm", side_effect=[(128.0, np.array([0.0, 0.5])), (96.0, np.array([0.25, 0.75]))]
        ), patch.object(
            main, "detect_key", side_effect=[("C major", 0), ("A minor", 9)]
        ), patch.object(main, "detect_downbeat", side_effect=[0.125, 0.625]):
            with TestClient(main.app) as client:
                analyze_response = client.post(
                    "/analyze",
                    files={
                        "beat": ("beat.wav", io.BytesIO(b"beat"), "audio/wav"),
                        "acapella": ("vocals.wav", io.BytesIO(b"acapella"), "audio/wav"),
                    },
                )

                analysis = analyze_response.json()
                update_response = client.put(
                    "/analysis/settings",
                    json={
                        "beat_file_id": analysis["beat"]["file_id"],
                        "acapella_file_id": analysis["acapella"]["file_id"],
                        "mix_style": "club",
                        "nudge_beats": 0.75,
                    },
                )

        self.assertEqual(update_response.status_code, 200)

        self.assertEqual(analyze_response.status_code, 200)
        main._reset_runtime_state()

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None):
            with TestClient(main.app) as client:
                restore_response = client.get("/analysis/latest")

        self.assertEqual(restore_response.status_code, 200)
        restored = restore_response.json()
        self.assertTrue(restored["restored"])
        self.assertEqual(restored["beat"]["file_id"], "beat-id_beat.wav")
        self.assertEqual(restored["beat"]["source_name"], "beat.wav")
        self.assertEqual(restored["acapella"]["file_id"], "acap-id_acapella.wav")
        self.assertEqual(restored["acapella"]["source_name"], "vocals.wav")
        self.assertEqual(restored["manual_mix"]["mix_style"], "club")
        self.assertAlmostEqual(restored["manual_mix"]["nudge_beats"], 0.75)
        self.assertIn("restored_at", restored)

    def test_analyze_uploads_survive_restart_for_manual_process(self):
        def fake_convert(_input_path, output_path):
            Path(output_path).write_bytes(b"wav-data")

        def fake_process_acapella(_input_path, output_path, _tempo_ratio, _pitch_shift):
            Path(output_path).write_bytes(b"processed")

        def fake_align_and_mix(
            _beat_path,
            _acapella_path,
            output_path,
            _beat_downbeat,
            _acapella_downbeat,
            _nudge_beats,
            _bpm,
            _mix_style,
            _advanced_mix=None,
        ):
            Path(output_path).write_bytes(b"mixed")
            return output_path

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["beat-id", "acap-id"]
        ), patch.object(main, "convert_to_wav", side_effect=fake_convert), patch.object(
            main.librosa, "load", side_effect=[(np.zeros(8), 22050), (np.zeros(8), 22050)]
        ), patch.object(
            main, "detect_bpm", side_effect=[(124.0, np.array([0.0, 0.5])), (118.0, np.array([0.25, 0.75]))]
        ), patch.object(
            main, "detect_key", side_effect=[("C major", 0), ("A minor", 9)]
        ), patch.object(main, "detect_downbeat", side_effect=[0.125, 0.625]):
            with TestClient(main.app) as client:
                analyze_response = client.post(
                    "/analyze",
                    files={
                        "beat": ("beat.wav", io.BytesIO(b"beat"), "audio/wav"),
                        "acapella": ("vocals.wav", io.BytesIO(b"acapella"), "audio/wav"),
                    },
                )

        self.assertEqual(analyze_response.status_code, 200)
        analysis = analyze_response.json()

        main._reset_runtime_state()

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", return_value="mix-id"
        ), patch.object(main, "process_acapella", side_effect=fake_process_acapella), patch.object(
            main, "align_and_mix", side_effect=fake_align_and_mix
        ):
            with TestClient(main.app) as client:
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
                        "nudge_beats": "0.75",
                        "mix_style": "club",
                    },
                )

        self.assertEqual(process_response.status_code, 200)
        self.assertEqual(process_response.json()["download_url"], "/download/mix-id_mixed.wav")
        self.assertEqual(process_response.json()["thumbnail_url"], "/waveform/mix-id_mixed.wav")

        main._reset_runtime_state()

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None):
            with TestClient(main.app) as client:
                restore_response = client.get("/analysis/latest")

        self.assertEqual(restore_response.status_code, 200)
        restored = restore_response.json()
        self.assertEqual(restored["manual_mix"]["mix_style"], "club")
        self.assertAlmostEqual(restored["manual_mix"]["nudge_beats"], 0.75)

    def test_delete_latest_analysis_clears_saved_session_and_uploads(self):
        def fake_convert(_input_path, output_path):
            Path(output_path).write_bytes(b"wav-data")

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["beat-id", "acap-id"]
        ), patch.object(main, "convert_to_wav", side_effect=fake_convert), patch.object(
            main.librosa, "load", side_effect=[(np.zeros(8), 22050), (np.zeros(8), 22050)]
        ), patch.object(
            main, "detect_bpm", side_effect=[(128.0, np.array([0.0, 0.5])), (96.0, np.array([0.25, 0.75]))]
        ), patch.object(
            main, "detect_key", side_effect=[("C major", 0), ("A minor", 9)]
        ), patch.object(main, "detect_downbeat", side_effect=[0.125, 0.625]):
            with TestClient(main.app) as client:
                analyze_response = client.post(
                    "/analyze",
                    files={
                        "beat": ("beat.wav", io.BytesIO(b"beat"), "audio/wav"),
                        "acapella": ("vocals.wav", io.BytesIO(b"acapella"), "audio/wav"),
                    },
                )

                analysis = analyze_response.json()
                delete_response = client.delete("/analysis/latest")
                missing_response = client.get("/analysis/latest")

        self.assertEqual(delete_response.status_code, 204)
        self.assertEqual(missing_response.status_code, 404)
        self.assertFalse((main.UPLOAD_DIR / analysis["beat"]["file_id"]).exists())
        self.assertFalse((main.UPLOAD_DIR / analysis["acapella"]["file_id"]).exists())

    def test_process_uses_adjusted_downbeat_and_returns_download_url(self):
        align_calls = []

        def fake_process_acapella(_input_path, output_path, _tempo_ratio, _pitch_shift):
            Path(output_path).write_bytes(b"processed")

        def fake_align_and_mix(
            beat_path,
            acapella_path,
            output_path,
            beat_downbeat,
            acapella_downbeat,
            nudge_beats,
            bpm,
            mix_style,
            advanced_mix=None,
        ):
            align_calls.append(
                {
                    "beat_path": Path(beat_path).name,
                    "acapella_path": Path(acapella_path).name,
                    "beat_downbeat": beat_downbeat,
                    "acapella_downbeat": acapella_downbeat,
                    "nudge_beats": nudge_beats,
                    "bpm": bpm,
                    "mix_style": mix_style,
                }
            )
            Path(output_path).write_bytes(b"mixed")
            return output_path

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", return_value="mix-id"
        ), patch.object(main, "process_acapella", side_effect=fake_process_acapella), patch.object(
            main, "align_and_mix", side_effect=fake_align_and_mix
        ):
            with TestClient(main.app) as client:
                (main.UPLOAD_DIR / "beat.wav").write_bytes(b"beat")
                (main.UPLOAD_DIR / "acap.wav").write_bytes(b"acap")

                response = client.post(
                    "/process",
                    data={
                        "beat_file_id": "beat.wav",
                        "acapella_file_id": "acap.wav",
                        "bpm": "127.75",
                        "pitch_shift": "-2",
                        "tempo_ratio": "1.3333",
                        "beat_downbeat": "2.345678",
                        "acapella_downbeat": "1.234567",
                        "nudge_beats": "0.25",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["download_url"], "/download/mix-id_mixed.wav")
        self.assertEqual(response.json()["thumbnail_url"], "/waveform/mix-id_mixed.wav")
        self.assertEqual(response.json()["preview_variants"]["final"]["label"], "Final Mix")
        self.assertEqual(response.json()["preview_variants"]["final"]["thumbnail_url"], "/waveform/mix-id_mixed.wav")
        self.assertEqual(align_calls[0]["beat_path"], "beat.wav")
        self.assertEqual(align_calls[0]["acapella_path"], "mix-id_processed.wav")
        self.assertAlmostEqual(align_calls[0]["beat_downbeat"], 2.345678)
        self.assertAlmostEqual(align_calls[0]["acapella_downbeat"], 1.234567 / 1.3333)
        self.assertAlmostEqual(align_calls[0]["nudge_beats"], 0.25)
        self.assertAlmostEqual(align_calls[0]["bpm"], 127.75)
        self.assertEqual(align_calls[0]["mix_style"], main.DEFAULT_MIX_STYLE)

    def test_process_returns_audio_processing_error_details(self):
        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main, "process_acapella", side_effect=RuntimeError("sox failed")
        ):
            with TestClient(main.app) as client:
                (main.UPLOAD_DIR / "beat.wav").write_bytes(b"beat")
                (main.UPLOAD_DIR / "acap.wav").write_bytes(b"acap")

                response = client.post(
                    "/process",
                    data={
                        "beat_file_id": "beat.wav",
                        "acapella_file_id": "acap.wav",
                        "bpm": "120",
                        "pitch_shift": "0",
                        "tempo_ratio": "1.2",
                        "beat_downbeat": "1.0",
                        "acapella_downbeat": "0.5",
                        "nudge_beats": "0",
                    },
                )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Audio processing failed", response.json()["detail"])

    def test_split_stems_returns_zip_download(self):
        def fake_split_stems(input_path, output_root):
            track_dir = Path(output_root) / "htdemucs_ft" / Path(input_path).stem
            track_dir.mkdir(parents=True, exist_ok=True)
            stem_paths = {}
            for stem_name in ["bass", "drums", "other", "vocals"]:
                stem_path = track_dir / f"{stem_name}.wav"
                stem_path.write_bytes(stem_name.encode("utf-8"))
                stem_paths[stem_name] = stem_path
            return stem_paths

        def fake_join_vocals(stem_files, output_path):
            Path(output_path).write_bytes(b"vocals")
            return Path(output_path)

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["track-id", "job-id"]
        ), patch.object(main, "split_stems_with_demucs", side_effect=fake_split_stems), patch.object(
            main, "join_vocal_stems", side_effect=fake_join_vocals
        ):
            with TestClient(main.app) as client:
                response = client.post(
                    "/split-stems",
                    files={
                        "track": ("song.wav", io.BytesIO(b"mix"), "audio/wav"),
                    },
                )

                self.assertEqual(response.status_code, 200)
                data = response.json()
                self.assertEqual(data["download_url"], "/download/job-id_stems.zip")
                self.assertEqual(data["stems"], ["bass", "drums", "other", "vocals"])
                self.assertEqual(data["acapella_download_url"], "/download/job-id_acapella.wav")
                self.assertEqual(data["acapella_file_name"], "separated-acapella.wav")

                download_response = client.get(data["download_url"])
                self.assertEqual(download_response.status_code, 200)
                self.assertEqual(download_response.headers["content-type"], "application/zip")
                self.assertGreater(len(download_response.content), 0)

                acapella_download = client.get(data["acapella_download_url"])
                self.assertEqual(acapella_download.status_code, 200)
                self.assertEqual(acapella_download.headers["content-type"], "audio/wav")
                self.assertGreater(len(acapella_download.content), 0)

    def test_split_stems_returns_error_details(self):
        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main, "split_stems_with_demucs", side_effect=RuntimeError("demucs missing")
        ):
            with TestClient(main.app) as client:
                response = client.post(
                    "/split-stems",
                    files={
                        "track": ("song.wav", io.BytesIO(b"mix"), "audio/wav"),
                    },
                )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Stem split failed", response.json()["detail"])

    def test_auto_mix_returns_download_url_and_runs_auto_mix_chain(self):
        auto_mix_calls = []

        def fake_convert(_input_path, output_path):
            Path(output_path).write_bytes(b"wav-data")

        def fake_process_acapella(_input_path, output_path, _tempo_ratio, _pitch_shift):
            Path(output_path).write_bytes(b"processed")

        def fake_auto_mix_tracks(
            beat_path,
            acapella_path,
            output_path,
            beat_downbeat,
            acapella_downbeat,
            nudge_beats,
            bpm,
            mix_style,
        ):
            auto_mix_calls.append(
                {
                    "beat_path": Path(beat_path).name,
                    "acapella_path": Path(acapella_path).name,
                    "beat_downbeat": beat_downbeat,
                    "acapella_downbeat": acapella_downbeat,
                    "nudge_beats": nudge_beats,
                    "bpm": bpm,
                    "mix_style": mix_style,
                }
            )
            Path(output_path).write_bytes(b"mixed")
            return output_path

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["beat-id", "acap-id", "auto-id"]
        ), patch.object(main, "convert_to_wav", side_effect=fake_convert), patch.object(
            main.librosa, "load", side_effect=[(np.zeros(8), 22050), (np.zeros(8), 22050)]
        ), patch.object(
            main, "detect_bpm", side_effect=[(124.5, np.array([0.0, 0.5])), (110.0, np.array([0.25, 0.75]))]
        ), patch.object(
            main, "detect_key", side_effect=[("C major", 0), ("A minor", 9)]
        ), patch.object(main, "detect_downbeat", side_effect=[0.125, 0.625]), patch.object(
            main, "process_acapella", side_effect=fake_process_acapella
        ), patch.object(main, "auto_mix_tracks", side_effect=fake_auto_mix_tracks):
            with TestClient(main.app) as client:
                response = client.post(
                    "/auto-mix",
                    files={
                        "beat": ("beat.wav", io.BytesIO(b"beat"), "audio/wav"),
                        "acapella": ("vocals.wav", io.BytesIO(b"acapella"), "audio/wav"),
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["download_url"], "/download/auto-id_mixed.wav")
        self.assertEqual(response.json()["thumbnail_url"], "/waveform/auto-id_mixed.wav")
        self.assertEqual(response.json()["preview_variants"]["final"]["label"], "Final Mix")
        self.assertEqual(response.json()["preview_variants"]["final"]["preview_url"], "/download/auto-id_mixed.wav")
        self.assertEqual(auto_mix_calls[0]["beat_path"], "beat-id_beat.wav")
        self.assertEqual(auto_mix_calls[0]["acapella_path"], "auto-id_processed.wav")
        self.assertAlmostEqual(auto_mix_calls[0]["beat_downbeat"], 0.125)
        self.assertAlmostEqual(auto_mix_calls[0]["acapella_downbeat"], 0.625 / (124.5 / 110.0))
        self.assertAlmostEqual(auto_mix_calls[0]["nudge_beats"], 0.0)
        self.assertAlmostEqual(auto_mix_calls[0]["bpm"], 124.5)
        self.assertEqual(auto_mix_calls[0]["mix_style"], main.DEFAULT_MIX_STYLE)

    def test_auto_mix_returns_audio_processing_error_details(self):
        def fake_convert(_input_path, output_path):
            Path(output_path).write_bytes(b"wav-data")

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["beat-id", "acap-id", "auto-id"]
        ), patch.object(main, "convert_to_wav", side_effect=fake_convert), patch.object(
            main.librosa, "load", side_effect=[(np.zeros(8), 22050), (np.zeros(8), 22050)]
        ), patch.object(
            main, "detect_bpm", side_effect=[(124.5, np.array([0.0, 0.5])), (110.0, np.array([0.25, 0.75]))]
        ), patch.object(
            main, "detect_key", side_effect=[("C major", 0), ("A minor", 9)]
        ), patch.object(main, "detect_downbeat", side_effect=[0.125, 0.625]), patch.object(
            main, "process_acapella", side_effect=RuntimeError("sox failed")
        ):
            with TestClient(main.app) as client:
                response = client.post(
                    "/auto-mix",
                    files={
                        "beat": ("beat.wav", io.BytesIO(b"beat"), "audio/wav"),
                        "acapella": ("vocals.wav", io.BytesIO(b"acapella"), "audio/wav"),
                    },
                )

        self.assertEqual(response.status_code, 422)
        self.assertIn("Auto mix failed", response.json()["detail"])

    def test_auto_mix_job_endpoint_returns_completed_job_snapshot(self):
        def fake_convert(_input_path, output_path):
            Path(output_path).write_bytes(b"wav-data")

        def fake_process_acapella(_input_path, output_path, _tempo_ratio, _pitch_shift):
            Path(output_path).write_bytes(b"processed")

        def fake_auto_mix_tracks(
            _beat_path,
            _acapella_path,
            output_path,
            _beat_downbeat,
            _acapella_downbeat,
            _nudge_beats,
            _bpm,
            _mix_style,
            _advanced_mix=None,
        ):
            Path(output_path).write_bytes(b"mixed")
            return output_path

        def run_job_immediately(job_id, worker):
            main._run_job_worker(job_id, worker)

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["beat-id", "acap-id", "job-id", "mix-id"]
        ), patch.object(main, "convert_to_wav", side_effect=fake_convert), patch.object(
            main.librosa, "load", side_effect=[(np.zeros(8), 22050), (np.zeros(8), 22050)]
        ), patch.object(
            main, "detect_bpm", side_effect=[(124.5, np.array([0.0, 0.5])), (110.0, np.array([0.25, 0.75]))]
        ), patch.object(
            main, "detect_key", side_effect=[("C major", 0), ("A minor", 9)]
        ), patch.object(main, "detect_downbeat", side_effect=[0.125, 0.625]), patch.object(
            main, "process_acapella", side_effect=fake_process_acapella
        ), patch.object(main, "auto_mix_tracks", side_effect=fake_auto_mix_tracks), patch.object(
            main, "_start_job_thread", side_effect=run_job_immediately
        ):
            with TestClient(main.app) as client:
                response = client.post(
                    "/auto-mix/jobs",
                    files={
                        "beat": ("beat.wav", io.BytesIO(b"beat"), "audio/wav"),
                        "acapella": ("vocals.wav", io.BytesIO(b"acapella"), "audio/wav"),
                    },
                )

                self.assertEqual(response.status_code, 202)
                payload = response.json()
                self.assertEqual(payload["job_id"], "job-id")
                self.assertEqual(payload["status"], "completed")
                self.assertEqual(payload["result"]["download_url"], "/download/mix-id_mixed.wav")
                self.assertEqual(payload["result"]["preview_url"], "/download/mix-id_mixed.wav")
                self.assertEqual(payload["result"]["thumbnail_url"], "/waveform/mix-id_mixed.wav")

                status_response = client.get(payload["status_url"])
                self.assertEqual(status_response.status_code, 200)
                self.assertEqual(status_response.json()["status"], "completed")

    def test_waveform_endpoint_returns_cached_thumbnail(self):
        output_path = main.UPLOAD_DIR / "rendered_mixed.wav"
        output_path.write_bytes(b"mixed")
        main._register_artifact(output_path, "mix-owner", "manual-mix")

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.librosa, "load", return_value=(np.linspace(-1.0, 1.0, 128, dtype=np.float32), 8000)
        ) as load_mock:
            with TestClient(main.app) as client:
                first_response = client.get("/waveform/rendered_mixed.wav")
                second_response = client.get("/waveform/rendered_mixed.wav")

        self.assertEqual(first_response.status_code, 200)
        self.assertIn("image/svg+xml", first_response.headers["content-type"])
        self.assertIn("<svg", first_response.text)
        self.assertEqual(second_response.status_code, 200)
        self.assertEqual(load_mock.call_count, 1)
        self.assertTrue((main.UPLOAD_DIR / "rendered_mixed.wav.waveform.svg").exists())

    def test_history_is_restored_from_persisted_state_after_restart(self):
        output_path = main.UPLOAD_DIR / "persisted_mixed.wav"
        output_path.write_bytes(b"mixed")

        job = main._create_job(main.AUTO_MIX_JOB_KIND, {"beat": "beat.wav", "acapella": "vocals.wav"})
        main._register_artifact(output_path, job["job_id"], main.AUTO_MIX_JOB_KIND)
        main._complete_job(
            job["job_id"],
            main._build_mix_result(output_path, "beat.wav", "vocals.wav", main.DEFAULT_MIX_STYLE),
        )

        main._reset_runtime_state()

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None):
            with TestClient(main.app) as client:
                history_response = client.get("/history")
                self.assertEqual(history_response.status_code, 200)

                history_items = history_response.json()["items"]
                self.assertEqual(len(history_items), 1)
                self.assertEqual(history_items[0]["job_id"], job["job_id"])
                self.assertEqual(history_items[0]["result"]["download_url"], "/download/persisted_mixed.wav")
                self.assertEqual(history_items[0]["result"]["thumbnail_url"], "/waveform/persisted_mixed.wav")

                status_response = client.get(f"/jobs/{job['job_id']}")
                self.assertEqual(status_response.status_code, 200)
                self.assertEqual(status_response.json()["status"], "completed")

                download_response = client.get("/download/persisted_mixed.wav")
                self.assertEqual(download_response.status_code, 200)
                self.assertEqual(download_response.headers["content-type"], "audio/wav")

    def test_split_stem_job_endpoint_updates_history_with_stem_downloads(self):
        def fake_split_stems(input_path, output_root):
            track_dir = Path(output_root) / "htdemucs_ft" / Path(input_path).stem
            track_dir.mkdir(parents=True, exist_ok=True)
            stem_paths = {}
            for stem_name in ["bass", "drums", "other", "vocals"]:
                stem_path = track_dir / f"{stem_name}.wav"
                stem_path.write_bytes(stem_name.encode("utf-8"))
                stem_paths[stem_name] = stem_path
            return stem_paths

        def fake_join_vocals(_stem_files, output_path):
            Path(output_path).write_bytes(b"vocals")
            return Path(output_path)

        def run_job_immediately(job_id, worker):
            main._run_job_worker(job_id, worker)

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", side_effect=["track-id", "job-id"]
        ), patch.object(main, "split_stems_with_demucs", side_effect=fake_split_stems), patch.object(
            main, "join_vocal_stems", side_effect=fake_join_vocals
        ), patch.object(main, "_start_job_thread", side_effect=run_job_immediately):
            with TestClient(main.app) as client:
                response = client.post(
                    "/split-stems/jobs",
                    files={
                        "track": ("song.wav", io.BytesIO(b"mix"), "audio/wav"),
                    },
                )

                self.assertEqual(response.status_code, 202)
                payload = response.json()
                self.assertEqual(payload["job_id"], "job-id")
                self.assertEqual(payload["status"], "completed")
                self.assertEqual(payload["result"]["download_url"], "/download/job-id_stems.zip")
                self.assertEqual(len(payload["result"]["stem_downloads"]), 4)
                self.assertEqual(payload["result"]["stem_downloads"][0]["preview_url"], "/download/job-id_stem_bass.wav")
                self.assertEqual(payload["result"]["stem_downloads"][0]["thumbnail_url"], "/waveform/job-id_stem_bass.wav")
                self.assertEqual(payload["result"]["acapella_thumbnail_url"], "/waveform/job-id_acapella.wav")

                history_response = client.get("/history")
                self.assertEqual(history_response.status_code, 200)
                history_items = history_response.json()["items"]
                self.assertEqual(history_items[0]["job_id"], "job-id")
                self.assertEqual(history_items[0]["kind"], main.STEM_SPLIT_JOB_KIND)
                self.assertEqual(history_items[0]["result"]["acapella_download_url"], "/download/job-id_acapella.wav")

    def test_process_accepts_advanced_mix_payload(self):
        captured = {}

        def fake_process_acapella(_input_path, output_path, _tempo_ratio, _pitch_shift):
            Path(output_path).write_bytes(b"processed")

        def fake_align_and_mix(
            _beat_path,
            _acapella_path,
            output_path,
            _beat_downbeat,
            _acapella_downbeat,
            _nudge_beats,
            _bpm,
            mix_style,
            advanced_mix=None,
        ):
            captured["mix_style"] = mix_style
            captured["advanced_mix"] = advanced_mix
            Path(output_path).write_bytes(b"mixed")
            return output_path

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main.uuid, "uuid4", return_value="mix-id"
        ), patch.object(main, "process_acapella", side_effect=fake_process_acapella), patch.object(
            main, "align_and_mix", side_effect=fake_align_and_mix
        ):
            with TestClient(main.app) as client:
                (main.UPLOAD_DIR / "beat.wav").write_bytes(b"beat")
                (main.UPLOAD_DIR / "acap.wav").write_bytes(b"acap")

                response = client.post(
                    "/process",
                    data={
                        "beat_file_id": "beat.wav",
                        "acapella_file_id": "acap.wav",
                        "bpm": "120",
                        "pitch_shift": "0",
                        "tempo_ratio": "1",
                        "beat_downbeat": "0",
                        "acapella_downbeat": "0",
                        "nudge_beats": "0",
                        "mix_style": "club",
                        "advanced_mix": json.dumps(
                            {
                                "eq_bands": [{"frequency_hz": 3500, "gain_db": 4, "q": 1.2}],
                                "vocal_gain_db": 2.5,
                                "beat_gain_db": -1,
                                "compressor_ratio": 4,
                            }
                        ),
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured["mix_style"], "club")
        self.assertEqual(captured["advanced_mix"].eq_bands[0].frequency_hz, 3500.0)
        self.assertEqual(captured["advanced_mix"].eq_bands[0].gain_db, 4.0)
        self.assertEqual(captured["advanced_mix"].vocal_gain_db, 2.5)

    def test_process_rejects_invalid_advanced_mix_json(self):
        with patch.object(main, "_ensure_runtime_dependencies", return_value=None):
            with TestClient(main.app) as client:
                (main.UPLOAD_DIR / "beat.wav").write_bytes(b"beat")
                (main.UPLOAD_DIR / "acap.wav").write_bytes(b"acap")

                response = client.post(
                    "/process",
                    data={
                        "beat_file_id": "beat.wav",
                        "acapella_file_id": "acap.wav",
                        "bpm": "120",
                        "pitch_shift": "0",
                        "tempo_ratio": "1",
                        "beat_downbeat": "0",
                        "acapella_downbeat": "0",
                        "advanced_mix": "{bad json",
                    },
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("advanced_mix must be valid JSON", response.json()["detail"])

    def test_timeline_endpoint_returns_peaks_and_beat_markers(self):
        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main, "_build_waveform_peaks", side_effect=[
                {"duration_sec": 2.0, "peaks": [0.1, 0.5, 0.2]},
                {"duration_sec": 1.5, "peaks": [0.2, 0.6, 0.3]},
            ]
        ):
            with TestClient(main.app) as client:
                beat_path = main.UPLOAD_DIR / "beat.wav"
                acap_path = main.UPLOAD_DIR / "acap.wav"
                beat_path.write_bytes(b"beat")
                acap_path.write_bytes(b"acap")
                main._register_artifact(beat_path, "analysis::beat.wav::acap.wav", main.ANALYZE_UPLOAD_KIND)
                main._register_artifact(acap_path, "analysis::beat.wav::acap.wav", main.ANALYZE_UPLOAD_KIND)
                main.ANALYSIS_SESSION_STORE.sessions["analysis::beat.wav::acap.wav"] = {
                    "owner_id": "analysis::beat.wav::acap.wav",
                    "analysis": {
                        "beat": {"file_id": "beat.wav", "source_name": "beat.wav", "bpm": 120.0, "key": "C major", "semitone": 0, "downbeat": 0.5},
                        "acapella": {"file_id": "acap.wav", "source_name": "acap.wav", "bpm": 100.0, "key": "D major", "semitone": 2, "downbeat": 0.25},
                        "suggested": {"tempo_ratio": 1.2, "pitch_shift": -2},
                        "manual_mix": {"mix_style": "balanced", "nudge_beats": 0.0},
                        "restored": False,
                    },
                    "created_at": main._utc_now_iso(),
                    "updated_at": main._utc_now_iso(),
                    "expires_at": time.time() + 60,
                }

                response = client.get("/analysis/latest/timeline")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["beat"]["peaks"], [0.1, 0.5, 0.2])
        self.assertEqual(payload["acapella"]["duration_sec"], 1.5)
        self.assertEqual(payload["grid"]["bpm"], 120.0)
        self.assertEqual(payload["grid"]["beat_times"][0], 0.5)
        self.assertAlmostEqual(payload["suggested_offset_sec"], 0.5 - (0.25 / 1.2))


if __name__ == "__main__":
    unittest.main()
