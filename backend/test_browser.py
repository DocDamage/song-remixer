import json
import shutil
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import sync_playwright
except ModuleNotFoundError:
    PlaywrightError = Exception
    sync_playwright = None


BACKEND_DIR = Path(__file__).resolve().parent
UI_FIXTURE_DIR = BACKEND_DIR.parent / "ui-fixtures"
MOCK_TIMELINE_RESPONSE = {
    "beat": {"file_id": "restored-beat.wav", "source_name": "beat.wav", "duration_sec": 4.0, "peaks": [0.1, 0.8, 0.4, 0.2]},
    "acapella": {"file_id": "restored-acapella.wav", "source_name": "vocals.wav", "duration_sec": 3.5, "peaks": [0.2, 0.7, 0.3, 0.1]},
    "grid": {"bpm": 128.0, "downbeat": 0.125, "beat_times": [0.125, 0.59375, 1.0625]},
    "suggested_offset_sec": -0.34375,
    "manual_mix": {"mix_style": "balanced", "nudge_beats": 0.0},
}
MOCK_ANALYSIS_RESPONSE = {
    "beat": {
        "file_id": "restored-beat.wav",
        "source_name": "beat.wav",
        "bpm": 128.0,
        "key": "C major",
        "semitone": 0,
        "downbeat": 0.125,
    },
    "acapella": {
        "file_id": "restored-acapella.wav",
        "source_name": "vocals.wav",
        "bpm": 96.0,
        "key": "A minor",
        "semitone": 9,
        "downbeat": 0.625,
    },
    "suggested": {
        "tempo_ratio": 128.0 / 96.0,
        "pitch_shift": -3,
    },
    "restored": True,
    "restored_at": "2026-05-26T00:00:02+00:00",
}
MOCK_SPLIT_RESULT = {
    "output_file": "mock-stems.zip",
    "download_url": "/download/mock-stems.zip",
    "stems": ["bass", "drums", "other", "vocals"],
    "stem_downloads": [
        {
            "name": "bass",
            "output_file": "mock-stem-bass.wav",
            "download_url": "/download/mock-stem-bass.wav",
            "preview_url": "/download/mock-stem-bass.wav",
            "thumbnail_url": "/waveform/mock-stem-bass.wav",
            "file_name": "bass.wav",
        },
        {
            "name": "drums",
            "output_file": "mock-stem-drums.wav",
            "download_url": "/download/mock-stem-drums.wav",
            "preview_url": "/download/mock-stem-drums.wav",
            "thumbnail_url": "/waveform/mock-stem-drums.wav",
            "file_name": "drums.wav",
        },
    ],
    "model": "htdemucs_ft",
    "acapella_file": "mock-acapella.wav",
    "acapella_download_url": "/download/mock-acapella.wav",
    "acapella_preview_url": "/download/mock-acapella.wav",
    "acapella_thumbnail_url": "/waveform/mock-acapella.wav",
    "acapella_file_name": "separated-acapella.wav",
    "status_line": "beat.wav was separated into bass, drums, other, and vocals using fine-tuned HT Demucs. Your 24-bit WAV stem bundle is ready to download.",
}
MOCK_SPLIT_JOB = {
    "job_id": "mock-split-job",
    "kind": "split-stems",
    "status": "completed",
    "progress": 100,
    "message": MOCK_SPLIT_RESULT["status_line"],
    "created_at": "2026-05-26T00:00:00+00:00",
    "updated_at": "2026-05-26T00:00:01+00:00",
    "input_files": {"track": "beat.wav"},
    "result": MOCK_SPLIT_RESULT,
    "error": None,
    "status_url": "/jobs/mock-split-job",
    "history_url": "/history",
}
MOCK_ACAPELLA_WAV = b"RIFFfakeWAVEfmt data"
MOCK_WAVEFORM_SVG = b"<svg xmlns='http://www.w3.org/2000/svg' width='320' height='72' viewBox='0 0 320 72'><rect width='320' height='72' rx='10' fill='#10192b'/><rect x='12' y='18' width='8' height='36' rx='2' fill='#ff8b7a'/><rect x='28' y='10' width='8' height='52' rx='2' fill='#78ece3'/></svg>"


class StemPromptBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sync_playwright is None:
            raise unittest.SkipTest(
                "Install browser test dependencies with `pip install -r backend/requirements-dev.txt`."
            )

        if not (UI_FIXTURE_DIR / "beat.wav").exists():
            raise unittest.SkipTest("Missing UI fixture: ui-fixtures/beat.wav")

        missing_tools = [tool for tool in ("ffmpeg", "sox") if shutil.which(tool) is None]
        if missing_tools:
            raise unittest.SkipTest(f"Missing required audio tools: {', '.join(missing_tools)}")

        cls.port = cls._pick_free_port()
        cls.base_url = f"http://127.0.0.1:{cls.port}"
        cls.server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(cls.port),
            ],
            cwd=BACKEND_DIR,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        try:
            cls._wait_for_server()
        except Exception:
            output = cls._read_server_output()
            cls._stop_server()
            raise

        try:
            cls.playwright = sync_playwright().start()
            cls.browser = cls.playwright.chromium.launch()
        except PlaywrightError as exc:
            cls._stop_server()
            raise unittest.SkipTest(
                "Install Playwright Chromium with `python -m playwright install chromium` to run browser tests."
            ) from exc

    @classmethod
    def tearDownClass(cls):
        browser = getattr(cls, "browser", None)
        if browser is not None:
            browser.close()

        playwright = getattr(cls, "playwright", None)
        if playwright is not None:
            playwright.stop()

        cls._stop_server()

    @classmethod
    def _pick_free_port(cls) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    @classmethod
    def _wait_for_server(cls) -> None:
        deadline = time.time() + 20
        last_error = None

        while time.time() < deadline:
            if cls.server.poll() is not None:
                raise RuntimeError(f"Test server exited early. Output:\n{cls._read_server_output()}")

            try:
                with urlopen(cls.base_url, timeout=1) as response:
                    if response.status == 200:
                        return
            except URLError as exc:
                last_error = exc
                time.sleep(0.1)

        raise RuntimeError(f"Timed out waiting for browser test server: {last_error}")

    @classmethod
    def _read_server_output(cls) -> str:
        server = getattr(cls, "server", None)
        if server is None or server.stdout is None:
            return ""
        return server.stdout.read()

    @classmethod
    def _stop_server(cls) -> None:
        server = getattr(cls, "server", None)
        if server is None or server.poll() is not None:
            return

        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    def setUp(self):
        self.context = self.browser.new_context()
        self.page = self.context.new_page()
        self.addCleanup(self.context.close)

    def test_restores_latest_analysis_on_page_load(self):
        self.page.route("**/analysis/latest", self._fulfill_latest_analysis)

        self.page.goto(self.base_url, wait_until="networkidle")
        self.page.locator("#analysis-results").wait_for(state="visible")

        self.assertEqual(self.page.locator("#beat-bpm").text_content(), "128.0")
        self.assertEqual(self.page.locator("#acap-bpm").text_content(), "96.0")
        self.assertEqual(self.page.locator("#tempo-ratio").text_content(), "1.333x")
        self.assertEqual(self.page.locator("#pitch-shift").text_content(), "-3 st")
        self.assertIn("beat.wav (restored on server)", self.page.locator('[data-file-meta-for="beat"]').text_content())
        self.assertIn(
            "vocals.wav (restored on server)",
            self.page.locator('[data-file-meta-for="acapella"]').text_content(),
        )
        self.assertFalse(self.page.locator("#process-btn").is_disabled())

    def test_mobile_layout_keeps_primary_actions_visible(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.base_url, wait_until="networkidle")

        self.assertTrue(self.page.locator("#auto-mix-btn").is_visible())
        self.assertTrue(self.page.locator("#advanced-mix-btn").is_visible())
        self.assertTrue(self.page.locator(".sidebar").is_visible())

    def test_job_tray_shows_completed_stem_job(self):
        self.page.route("**/split-stems/jobs", self._fulfill_split_stems)
        self.page.route("**/waveform/*", self._fulfill_mock_waveform)

        self.page.goto(self.base_url, wait_until="networkidle")
        self.page.locator(".tab-btn[data-tab='stems']").click()
        self.page.locator("#stem-track").set_input_files(str(UI_FIXTURE_DIR / "beat.wav"))
        self.page.locator("#split-stems-btn").click()
        self.page.locator("#job-tray").wait_for(state="visible")

        self.assertIn("completed", self.page.locator("#job-tray").text_content())

    def test_stem_route_buttons_load_stem_into_vocal_slot(self):
        self.page.route("**/split-stems/jobs", self._fulfill_split_stems)
        self.page.route("**/waveform/*", self._fulfill_mock_waveform)
        self.page.route("**/download/mock-stem-bass.wav", self._fulfill_mock_acapella)
        self.page.route("**/download/mock-stem-drums.wav", self._fulfill_mock_acapella)

        self.page.goto(self.base_url, wait_until="networkidle")
        self.page.locator(".tab-btn[data-tab='stems']").click()
        self.page.locator("#stem-track").set_input_files(str(UI_FIXTURE_DIR / "beat.wav"))
        self.page.locator("#split-stems-btn").click()
        self.page.locator(".stem-route-btn[data-slot='vocal']").first.click()

        self.page.wait_for_function(
            """
            () => (document.querySelector('#acapella-file-meta')?.textContent || '').includes('.wav')
            """
        )
        self.assertIn(".wav", self.page.locator("#acapella-file-meta").text_content())

    def test_stem_prompt_can_load_joined_vocals_into_acapella_slot(self):
        self.page.route("**/split-stems/jobs", self._fulfill_split_stems)
        self.page.route("**/waveform/*", self._fulfill_mock_waveform)
        self.page.route("**/download/mock-acapella.wav", self._fulfill_mock_acapella)
        self.page.route("**/download/mock-stem-*.wav", self._fulfill_mock_acapella)

        self.page.goto(self.base_url, wait_until="networkidle")
        self.page.locator(".tab-btn[data-tab='stems']").click()
        self.page.locator("#stem-track").set_input_files(str(UI_FIXTURE_DIR / "beat.wav"))
        self.page.locator("#split-stems-btn").click()

        self.page.locator("#stem-vocal-prompt").wait_for(state="visible")
        self.assertEqual(
            self.page.locator("#stem-vocal-prompt-text").text_content(),
            "Do you want the joined vocals loaded into the acapella spot automatically?",
        )

        self.page.locator("#use-stem-vocals-btn").click()
        self.page.wait_for_function(
            """
            () => {
                const fileMeta = document.querySelector('#acapella-file-meta')?.textContent || '';
                const promptText = document.querySelector('#stem-vocal-prompt-text')?.textContent || '';
                const stemSection = document.querySelector('#stem-download-section');
                const stemDownloadLink = document.querySelector('#stem-download-link')?.getAttribute('href');
                return fileMeta.includes('separated-acapella.wav')
                    && promptText.includes('loaded into the acapella spot automatically')
                    && stemDownloadLink === '/download/mock-stems.zip'
                    && stemSection
                    && !stemSection.classList.contains('hidden');
            }
            """
        )

        self.assertIn("separated-acapella.wav", self.page.locator("#acapella-file-meta").text_content())
        self.assertIn(
            "loaded into the acapella spot automatically",
            self.page.locator("#stem-vocal-prompt-text").text_content(),
        )
        self.assertIn(
            "beat.wav was separated into bass, drums, other, and vocals",
            self.page.locator("#stem-status-line").text_content(),
        )
        self.assertEqual(self.page.locator("#stem-preview-grid .stem-preview-card").count(), 2)
        self.assertEqual(self.page.locator("#stem-preview-grid .waveform-thumbnail").count(), 2)
        self.assertEqual(self.page.locator("#stem-acapella-preview-card .waveform-thumbnail").count(), 1)
        self.assertTrue(
            self.page.locator("#stem-download-section").evaluate("el => !el.classList.contains('hidden')")
        )

    def _fulfill_split_stems(self, route):
        route.fulfill(
            status=202,
            content_type="application/json",
            body=json.dumps(MOCK_SPLIT_JOB),
        )

    def test_timeline_renders_after_restored_analysis(self):
        self.page.route("**/analysis/latest", self._fulfill_latest_analysis)
        self.page.route("**/analysis/latest/timeline", self._fulfill_timeline)

        self.page.goto(self.base_url, wait_until="networkidle")
        self.page.locator("#timeline-panel").wait_for(state="visible")

        self.assertIn("Offset:", self.page.locator("#timeline-offset-label").text_content())
        self.assertGreater(
            self.page.locator("#beat-timeline-canvas").evaluate("canvas => canvas.width"),
            0,
        )

    def _fulfill_timeline(self, route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(MOCK_TIMELINE_RESPONSE),
        )

    def _fulfill_latest_analysis(self, route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(MOCK_ANALYSIS_RESPONSE),
        )

    def _fulfill_mock_acapella(self, route):
        route.fulfill(
            status=200,
            content_type="audio/wav",
            body=MOCK_ACAPELLA_WAV,
        )

    def _fulfill_mock_waveform(self, route):
        route.fulfill(
            status=200,
            content_type="image/svg+xml",
            body=MOCK_WAVEFORM_SVG,
        )


if __name__ == "__main__":
    unittest.main()