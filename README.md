# 🎵 Song Remixer

A simple web app that automatically syncs any acapella to any beat — matching **BPM**, **musical key**, and **downbeat alignment**.

## Features

- **Auto-detect BPM** for both beat and acapella
- **Auto-detect musical key** using chromagram analysis + Krumhansl-Schmuckler key profiles
- **Estimate downbeats** to align phrase starts
- **Background progress tracking** for long-running Auto Mix and stem split jobs
- **Mix style presets**: `balanced`, `club`, `vocal-focus`, and `demo-loud`
- **Advanced Mix controls** — 7-band EQ, per-track gain, and compressor threshold/ratio/attack/release
- **Visual alignment timeline** with stacked waveforms, beat grid, and draggable vocal offset handle
- **Analysis confidence indicators** for BPM, key, and downbeat with manual override fields
- **A/B preview variants** to compare different mix renderings without re-downloading
- **Studio stem splitting** with fine-tuned Hybrid Transformer Demucs (`htdemucs_ft`)
- **Stem routing mixer** — route individual stems to beat or vocal slots with mute/solo per stem
- **Non-blocking job tray** — keep browsing and reviewing while Auto Mix and stem jobs run
- **Cached server-generated waveform thumbnails** for stem preview cards and recent export previews
- **Per-stem previews** plus a prepared acapella preview after stem splitting
- **One-click stem vocal handoff** into the acapella slot, with optional immediate Auto Mix
- **Persistent recent export history** across server restarts while exports remain inside the retention window
- **Restart-safe analyze state** so manual BPM/key review can continue after a backend restart
- **Time-stretch** acapella to match beat tempo (via sox)
- **Pitch-shift** acapella to match beat key (via sox)
- **Grid alignment** with manual nudge control (±2 beats, expanded to ±8 beats in the timeline)
- **Mix and export** as WAV (via ffmpeg)

## Requirements

- Python 3.10+
- ffmpeg (already installed via Chocolatey)
- sox (already installed via WinGet)
- Demucs model dependencies installed from `backend/requirements.txt`

## Setup

```bash
cd backend
pip install -r requirements.txt
```

For browser-driven UI tests:

```bash
cd backend
pip install -r requirements-dev.txt
python -m playwright install chromium
```

## Run

```bash
cd backend
uvicorn main:app --reload
```

Then open your browser to **<http://localhost:8000>**.

## How to Use

1. Drag or select your **beat** (instrumental) and **acapella** (vocals).
2. Pick a **Mix style** before you render. `Balanced` is safest, `Club` pushes the beat, `Vocal Focus` carves more space for the vocal, and `Demo Loud` is the most aggressive master.
3. Click **Auto Mix for Me** to queue a one-click render with live progress, automatic sync, and preview playback before download.
4. If you want manual control, click **Advanced Analyze**, review the analysis, then use the nudge control before **Sync & Mix**. If the backend restarts, the latest saved analysis restores automatically so you can keep going without re-uploading.
5. To split a full song into stems, use **Studio Stem Splitter** and preview the returned stems or prepared acapella before downloading.
6. After splitting, you can load the joined vocals into the acapella slot automatically and optionally launch **Load vocals + Auto Mix** if a beat is already selected.
7. **Download** your remixed WAV, prepared acapella, or stem ZIP.

## Architecture

- **Backend**: FastAPI (Python) — analysis with `librosa`, processing with `sox` + `ffmpeg`
  - `schemas.py` — Pydantic models for API payloads and normalized mix settings
  - `jobs.py` — `InProcessJobStore` for job creation, snapshots, completion, and history
  - `artifacts.py` — `InProcessArtifactStore` for registration, TTL, and safe cleanup
  - `analysis_sessions.py` — `InProcessAnalysisSessionStore` for latest-analysis persistence
  - `audio.py` — Audio analysis, stem splitting, alignment, and ffmpeg filter generation
- **Frontend**: Vanilla JS ES modules + HTML/CSS (no build step)
  - `js/api.js` — Fetch wrappers, error parsing, and job polling
  - `js/ui.js` — Generic UI helpers (hidden states, errors, formatting)
  - `js/state.js` — Shared frontend state and selectors
  - `js/remix.js` — Beat/vocal inputs, analysis rendering, Advanced Mix, timeline, A/B previews
  - `js/stems.js` — Stem splitting, stem rows, routing, and stem preview behavior
  - `js/file-browser.js` — Folder picker, tree rendering, file preview, and slot loading
- **Desktop path**: The same backend can later be wrapped with Tauri or PyInstaller + webview

## Notes

- Downbeat detection uses an onset-strength heuristic and is not perfect. The nudge slider and timeline drag handle are there for manual correction.
- Key detection works best on clean, full-length audio. Very short clips or noisy recordings may be less accurate.
- Confidence indicators help you judge when to trust auto-detected values and when to use the override fields.
- Manual analyze and Sync & Mix actions still return directly, while longer Auto Mix and stem split jobs now run in the background with status polling so the page stays responsive while rendering.
- The stem splitter uses fine-tuned HT Demucs for 4 premium stems: `drums`, `bass`, `other`, and `vocals`.
- The first stem split can take longer because Demucs may need to download model weights.
- Generated exports, cached waveform thumbnails, analyzed uploads, and recent job history are cleaned up automatically after a retention window to prevent the upload directory from growing forever.
- All advanced mix settings (EQ, gain, compressor) are clamped to safe ranges by Pydantic validators before reaching the audio pipeline.

## Advanced Workflows

- Advanced Mix now sends EQ, gain, and compressor settings into the render pipeline.
- Advanced Analyze shows confidence indicators and a visual timeline for manual alignment.
- Stem Splitter can route individual stems into the beat or vocal slots before remixing.
- Long-running Auto Mix and stem jobs appear in the job tray so the UI remains usable while work runs.

## Testing

Run the backend suite:

```bash
python -m unittest discover -s backend -p 'test_*.py'
```

Run the browser harness for the stem prompt UI flow:

```bash
python -m unittest backend.test_browser
```

The browser harness covers both latest-analysis restoration and the stem prompt flow by stubbing `/analysis/latest`, `/split-stems/jobs`, generated waveform thumbnails, and the prepared acapella download.

Run the real stem split integration check manually:

```powershell
$env:SONG_REMIXER_RUN_REAL_STEM_SPLIT = "1"
$env:SONG_REMIXER_STEM_DEVICE = "cpu"
python -m unittest backend.test_integration.EndToEndApiTests.test_real_split_stems_fixture_when_enabled
```

GitHub Actions runs the main suite on every push and pull request, and a separate nightly workflow exercises the real Demucs splitter on the short fixture.
