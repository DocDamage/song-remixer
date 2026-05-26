# Song Remixer Product and Architecture Improvements Implementation Plan

> **Status: COMPLETE** — All 13 tasks implemented. See `README.md` for updated feature list and architecture overview.
>
> **Completed by:** Agent swarm (4 agents, 3 phases)
> - Phase 1: Backend core — typed schemas, advanced mix API/audio pipeline, timeline API, A/B preview, confidence/overrides
> - Phase 2: Frontend features + backend extraction — timeline UI, stem routing mixer, job tray, visual polish, job/artifact/session stores
> - Phase 3: Frontend module split + final verification — ES modules, README update, full test suite
>
> **Test results:** 55 backend tests pass (1 skipped), 6 browser tests pass.
>
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Turn Song Remixer from a working prototype into a clearer remix workstation with real advanced controls, timeline-based alignment, stronger stem routing, safer backend contracts, and a codebase that can keep growing without becoming brittle.

**Architecture:** Keep the current FastAPI plus vanilla HTML/CSS/JS stack, but introduce typed backend schemas, pure audio-setting normalization helpers, focused frontend modules, and progressively enhanced UI components. Ship each improvement behind existing endpoints or narrowly scoped endpoint additions so current Auto Mix, Advanced Mix, and Stem Splitter flows continue working throughout the migration.

**Tech Stack:** Python 3.10+, FastAPI, Pydantic, librosa, ffmpeg, sox, Demucs, vanilla JavaScript ES modules, HTML, CSS, unittest, FastAPI TestClient, Playwright.

---

## Scope Check

This plan covers several independent subsystems:

- Backend API contracts and validation.
- Advanced Mix audio behavior.
- Timeline, waveform, and manual alignment UI.
- A/B preview UX.
- Analysis confidence and manual overrides.
- Stem routing mixer.
- Nonblocking job tray.
- Code decomposition and visual polish.

Treat this file as a master implementation roadmap. Each task below is designed to produce working, testable software on its own. If execution time is limited, ship tasks in order through Task 5 first; that gives the biggest immediate upgrade because the existing Advanced Mix modal becomes real.

## Current App Facts

- Entry page: `backend/static/index.html`.
- Main frontend behavior: `backend/static/app.js` is now a small ES-module orchestrator; domain behavior lives in `backend/static/js/*.js`.
- Main styling: `backend/static/style.css` at about 1,400 lines.
- Backend routes and runtime state: `backend/main.py` at about 1,400 lines.
- Audio processing: `backend/audio.py`.
- Existing backend test style: `unittest` with `unittest.mock.patch` in `backend/test_main.py` and `backend/test_audio.py`.
- Existing browser test style: Playwright launched through `backend/test_browser.py`.
- Existing commands:

```powershell
python -m unittest discover -s backend -p 'test_*.py'
python -m unittest backend.test_browser
```

## Target File Structure

Create or modify these files. Do the split gradually; do not rewrite the whole app at once.

- Create: `backend/schemas.py`
  - Owns Pydantic models for public API payloads and normalized mix settings.
- Create: `backend/jobs.py`
  - Owns in-process job creation, job snapshots, completion, failure, and history serialization.
- Create: `backend/artifacts.py`
  - Owns artifact registration, TTL refresh, download path validation, waveform cache naming, and cleanup.
- Create: `backend/analysis_sessions.py`
  - Owns latest-analysis persistence, manual mix settings persistence, and session clearing.
- Modify: `backend/main.py`
  - Becomes route wiring plus request/response orchestration. It should stop owning every state helper.
- Modify: `backend/audio.py`
  - Accept normalized advanced settings and timeline/override inputs while keeping current default mix styles.
- Create: `backend/static/js/state.js`
  - Owns shared frontend state and selectors.
- Create: `backend/static/js/api.js`
  - Owns `fetch` calls, `getErrorMessage`, and job polling.
- Create: `backend/static/js/ui.js`
  - Owns generic UI helpers: hidden states, processing, errors, buttons, tabs, modal open/close.
- Create: `backend/static/js/remix.js`
  - Owns beat/vocal inputs, analysis rendering, Advanced Mix payloads, timeline behavior, and A/B previews.
- Create: `backend/static/js/stems.js`
  - Owns stem splitting, stem rows, routing, and stem preview behavior.
- Create: `backend/static/js/file-browser.js`
  - Owns folder picker, tree rendering, file preview, and loading selected files into slots.
- Modify: `backend/static/app.js`
  - Temporarily becomes a bootstrapper that imports modules. Delete it after the module split is complete if `index.html` uses module scripts directly.
- Modify: `backend/static/index.html`
  - Remove inline styles, add timeline/job tray markup, use `type="module"` for JS.
- Modify: `backend/static/style.css`
  - Split later only if desired. First add organized sections for timeline, job tray, stem routing mixer, and accessibility.
- Create: `backend/test_schemas.py`
  - Tests API model validation and normalization.
- Create: `backend/test_jobs.py`
  - Tests job state behavior after extraction.
- Create: `backend/test_artifacts.py`
  - Tests path safety, TTL refresh, cleanup, and waveform cache behavior after extraction.
- Modify: `backend/test_audio.py`
  - Add tests for advanced mix filter generation and manual overrides.
- Modify: `backend/test_main.py`
  - Add endpoint contract tests for new payloads and keep existing route tests passing.
- Modify: `backend/test_browser.py`
  - Add browser coverage for Advanced Mix payloads, timeline nudge, stem routing, and job tray.

---

## Task 1: Add Typed Schemas Without Changing Existing Behavior

**Files:**
- Create: `backend/schemas.py`
- Create: `backend/test_schemas.py`
- Modify: `backend/main.py`

**Purpose:** Give the backend a single source of truth for API shapes before adding more features.

**Edge Cases Covered:**
- Unknown mix style falls back to `balanced`.
- Nudge clamps to the existing `-2.0..2.0` beat range.
- EQ band gains clamp to a safe `-40.0..20.0` dB range.
- Compressor/release values never become zero or negative.
- Missing advanced settings normalize to neutral settings.

- [x] **Step 1: Add schema tests first**

Create `backend/test_schemas.py`:

```python
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
```

- [x] **Step 2: Run the schema tests and verify failure**

Run:

```powershell
python -m unittest backend.test_schemas
```

Expected result: failure with `ModuleNotFoundError: No module named 'schemas'`.

- [x] **Step 3: Add `backend/schemas.py`**

```python
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


VALID_MIX_STYLES = {"balanced", "club", "vocal-focus", "demo-loud"}


def clamp_float(value: Any, minimum: float, maximum: float, fallback: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = fallback
    return max(minimum, min(maximum, numeric))


class ManualMixSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    mix_style: str = "balanced"
    nudge_beats: float = 0.0

    @field_validator("mix_style", mode="before")
    @classmethod
    def normalize_mix_style(cls, value: Any) -> str:
        normalized = str(value or "balanced").strip().lower()
        return normalized if normalized in VALID_MIX_STYLES else "balanced"

    @field_validator("nudge_beats", mode="before")
    @classmethod
    def normalize_nudge_beats(cls, value: Any) -> float:
        return clamp_float(value, -2.0, 2.0, 0.0)


class EqBandSetting(BaseModel):
    model_config = ConfigDict(extra="ignore")

    frequency_hz: float = Field(default=1000.0)
    gain_db: float = Field(default=0.0)
    q: float = Field(default=1.0)

    @field_validator("frequency_hz", mode="before")
    @classmethod
    def normalize_frequency(cls, value: Any) -> float:
        return clamp_float(value, 20.0, 20000.0, 1000.0)

    @field_validator("gain_db", mode="before")
    @classmethod
    def normalize_gain(cls, value: Any) -> float:
        return clamp_float(value, -40.0, 20.0, 0.0)

    @field_validator("q", mode="before")
    @classmethod
    def normalize_q(cls, value: Any) -> float:
        return clamp_float(value, 0.1, 12.0, 1.0)


class AdvancedMixSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    eq_bands: list[EqBandSetting] = Field(default_factory=list)
    vocal_gain_db: float = 0.0
    beat_gain_db: float = 0.0
    compressor_threshold_db: float = -28.0
    compressor_ratio: float = 3.0
    compressor_attack_ms: float = 12.0
    compressor_release_ms: float = 180.0

    @field_validator("eq_bands", mode="before")
    @classmethod
    def limit_eq_band_count(cls, value: Any) -> Any:
        if not isinstance(value, list):
            return []
        return value[:12]

    @field_validator("vocal_gain_db", "beat_gain_db", mode="before")
    @classmethod
    def normalize_track_gain(cls, value: Any) -> float:
        return clamp_float(value, -12.0, 12.0, 0.0)

    @field_validator("compressor_threshold_db", mode="before")
    @classmethod
    def normalize_threshold(cls, value: Any) -> float:
        return clamp_float(value, -60.0, 0.0, -28.0)

    @field_validator("compressor_ratio", mode="before")
    @classmethod
    def normalize_ratio(cls, value: Any) -> float:
        return clamp_float(value, 1.0, 12.0, 3.0)

    @field_validator("compressor_attack_ms", mode="before")
    @classmethod
    def normalize_attack(cls, value: Any) -> float:
        return clamp_float(value, 1.0, 250.0, 12.0)

    @field_validator("compressor_release_ms", mode="before")
    @classmethod
    def normalize_release(cls, value: Any) -> float:
        return clamp_float(value, 20.0, 1000.0, 180.0)


class TimelineOverrideSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    bpm: float | None = None
    pitch_shift: float | None = None
    beat_downbeat: float | None = None
    acapella_downbeat: float | None = None
    nudge_beats: float = 0.0

    @field_validator("bpm", mode="before")
    @classmethod
    def normalize_bpm(cls, value: Any) -> float | None:
        if value is None or value == "":
            return None
        return clamp_float(value, 40.0, 240.0, 120.0)

    @field_validator("pitch_shift", mode="before")
    @classmethod
    def normalize_pitch_shift(cls, value: Any) -> float | None:
        if value is None or value == "":
            return None
        return clamp_float(value, -12.0, 12.0, 0.0)

    @field_validator("beat_downbeat", "acapella_downbeat", mode="before")
    @classmethod
    def normalize_downbeat(cls, value: Any) -> float | None:
        if value is None or value == "":
            return None
        return clamp_float(value, 0.0, 60.0, 0.0)

    @field_validator("nudge_beats", mode="before")
    @classmethod
    def normalize_nudge(cls, value: Any) -> float:
        return clamp_float(value, -8.0, 8.0, 0.0)
```

- [x] **Step 4: Run the schema tests and verify pass**

Run:

```powershell
python -m unittest backend.test_schemas
```

Expected result: `OK`.

- [x] **Step 5: Replace `_normalize_manual_mix_settings` internals in `backend/main.py`**

Add import:

```python
from schemas import ManualMixSettings
```

Replace `_normalize_manual_mix_settings` body:

```python
def _normalize_manual_mix_settings(settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if not isinstance(settings, dict):
        return ManualMixSettings().model_dump()
    return ManualMixSettings(**settings).model_dump()
```

- [x] **Step 6: Run the full backend suite**

Run:

```powershell
python -m unittest discover -s backend -p 'test_*.py'
```

Expected result: existing tests plus `backend.test_schemas` pass.

Commit:

```powershell
git add backend/schemas.py backend/test_schemas.py backend/main.py
git commit -m "refactor: add typed remix settings schemas"
```

If this directory is still not a Git repository, skip the commit command and record the changed files in the final handoff.

---

## Task 2: Wire Advanced Mix Controls Into the API Contract

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/test_main.py`
- Modify: `backend/static/index.html`
- Modify: `backend/static/app.js`

**Purpose:** Make the existing Advanced Mix modal produce a real `advanced_mix` JSON payload.

**Edge Cases Covered:**
- Modal sliders can be moved before analysis.
- Invalid JSON in `advanced_mix` returns a 400.
- Missing `advanced_mix` keeps old behavior.
- Extreme slider values are clamped by `schemas.AdvancedMixSettings`.

- [x] **Step 1: Add endpoint contract tests**

Add to `ApiContractTests` in `backend/test_main.py`:

```python
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
```

Add `import json` near the top of `backend/test_main.py` if it is not already present.

- [x] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest backend.test_main.ApiContractTests.test_process_accepts_advanced_mix_payload backend.test_main.ApiContractTests.test_process_rejects_invalid_advanced_mix_json
```

Expected result: first test fails because `advanced_mix` is not accepted by `/process`.

- [x] **Step 3: Add parsing helper in `backend/main.py`**

Add import:

```python
from pydantic import ValidationError
from schemas import AdvancedMixSettings, ManualMixSettings
```

Add helper:

```python
def _parse_advanced_mix_settings(raw_payload: Optional[str]) -> Optional[AdvancedMixSettings]:
    if raw_payload is None or raw_payload == "":
        return None
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="advanced_mix must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="advanced_mix must be a JSON object")
    try:
        return AdvancedMixSettings(**payload)
    except ValidationError as exc:
        raise HTTPException(status_code=400, detail=f"advanced_mix validation failed: {exc}") from exc
```

Modify `/process` signature:

```python
    mix_style: str = Form(DEFAULT_MIX_STYLE),
    advanced_mix: Optional[str] = Form(None),
):
```

Inside `/process`, before calling `align_and_mix`:

```python
    advanced_mix_settings = _parse_advanced_mix_settings(advanced_mix)
```

Pass it to `align_and_mix`:

```python
        align_and_mix(
            str(beat_path),
            str(processed_path),
            str(output_path),
            beat_downbeat,
            adjusted_acap_downbeat,
            nudge_beats,
            bpm,
            mix_style,
            advanced_mix_settings,
        )
```

- [x] **Step 4: Update `backend/audio.py` function signatures**

```python
def align_and_mix(
    beat_path: str,
    acapella_path: str,
    output_path: str,
    beat_downbeat: float,
    acapella_downbeat: float,
    nudge_beats: float,
    bpm: float,
    mix_style: str = DEFAULT_MIX_STYLE,
    advanced_mix=None,
) -> str:
```

```python
def auto_mix_tracks(
    beat_path: str,
    acapella_path: str,
    output_path: str,
    beat_downbeat: float,
    acapella_downbeat: float,
    nudge_beats: float,
    bpm: float,
    mix_style: str = DEFAULT_MIX_STYLE,
    advanced_mix=None,
) -> str:
```

Pass `advanced_mix` into `_render_styled_mix`:

```python
    _render_styled_mix(beat_path, aligned_path, output_path, mix_style, advanced_mix)
```

Update `_render_styled_mix` signature:

```python
def _render_styled_mix(
    beat_path: str,
    aligned_path: str,
    output_path: str,
    mix_style: str | None = None,
    advanced_mix=None,
) -> str:
```

At this stage `_render_styled_mix` can ignore `advanced_mix`; Task 3 makes it audible.

- [x] **Step 5: Add data attributes to modal controls**

Modify `backend/static/index.html` Advanced Mix controls so each slider has a stable setting name:

```html
<input type="range" min="-40" max="20" value="0" orient="vertical" data-advanced-eq-frequency="60" data-advanced-eq-q="1">
<input type="range" min="-40" max="20" value="0" orient="vertical" data-advanced-eq-frequency="250" data-advanced-eq-q="1">
<input type="range" min="-40" max="20" value="0" orient="vertical" data-advanced-eq-frequency="500" data-advanced-eq-q="1">
<input type="range" min="-40" max="20" value="0" orient="vertical" data-advanced-eq-frequency="1000" data-advanced-eq-q="1">
<input type="range" min="-40" max="20" value="0" orient="vertical" data-advanced-eq-frequency="4000" data-advanced-eq-q="1">
<input type="range" min="-40" max="20" value="0" orient="vertical" data-advanced-eq-frequency="8000" data-advanced-eq-q="1">
<input type="range" min="-40" max="20" value="0" orient="vertical" data-advanced-eq-frequency="16000" data-advanced-eq-q="1">
```

For the lower controls, use explicit setting keys:

```html
<input type="range" min="20" max="20000" value="1000" class="knob" data-advanced-control="focus_frequency_hz">
<input type="range" min="0.1" max="10" step="0.1" value="1" class="knob" data-advanced-control="focus_q">
<input type="range" min="-12" max="12" value="0" class="knob" data-advanced-control="vocal_gain_db">
<input type="range" min="-12" max="12" value="0" class="knob" data-advanced-control="beat_gain_db">
<input type="range" min="-60" max="0" value="-28" class="knob" data-advanced-control="compressor_threshold_db">
<input type="range" min="1" max="250" value="12" class="knob" data-advanced-control="compressor_attack_ms">
<input type="range" min="20" max="1000" value="180" class="knob" data-advanced-control="compressor_release_ms">
```

- [x] **Step 6: Build frontend payload**

Add to `backend/static/app.js`:

```javascript
function getAdvancedMixPayload() {
    const eqBands = Array.from(document.querySelectorAll('[data-advanced-eq-frequency]'))
        .map((slider) => ({
            frequency_hz: Number(slider.dataset.advancedEqFrequency),
            gain_db: Number(slider.value),
            q: Number(slider.dataset.advancedEqQ || 1)
        }))
        .filter((band) => Number.isFinite(band.gain_db) && band.gain_db !== 0);

    const payload = { eq_bands: eqBands };
    for (const control of document.querySelectorAll('[data-advanced-control]')) {
        const key = control.dataset.advancedControl;
        const value = Number(control.value);
        if (key && Number.isFinite(value)) {
            payload[key] = value;
        }
    }
    if (payload.focus_frequency_hz && payload.focus_q) {
        payload.eq_bands.push({
            frequency_hz: payload.focus_frequency_hz,
            gain_db: payload.vocal_gain_db || 0,
            q: payload.focus_q
        });
    }
    delete payload.focus_frequency_hz;
    delete payload.focus_q;
    return payload;
}
```

In `runSyncAndMix`, before `fetch('/process')`:

```javascript
    formData.append('advanced_mix', JSON.stringify(getAdvancedMixPayload()));
```

- [x] **Step 7: Run targeted tests**

Run:

```powershell
python -m unittest backend.test_main.ApiContractTests.test_process_accepts_advanced_mix_payload backend.test_main.ApiContractTests.test_process_rejects_invalid_advanced_mix_json
```

Expected result: `OK`.

Run:

```powershell
python -m unittest discover -s backend -p 'test_*.py'
```

Expected result: all backend tests pass.

Commit:

```powershell
git add backend/main.py backend/audio.py backend/static/index.html backend/static/app.js backend/test_main.py
git commit -m "feat: submit advanced mix settings"
```

---

## Task 3: Make Advanced Mix Settings Audible in ffmpeg

**Files:**
- Modify: `backend/audio.py`
- Modify: `backend/test_audio.py`

**Purpose:** Convert Advanced Mix settings into real filter changes while preserving current mix presets.

**Edge Cases Covered:**
- Empty advanced settings produce the current filter chain.
- Zero-gain EQ bands are skipped.
- Very large slider values arrive already clamped by schemas, but audio helpers also guard against bad objects.
- User gain changes combine with preset gain bias without clipping the filter string into invalid ffmpeg syntax.

- [x] **Step 1: Add audio filter tests**

Add to `backend/test_audio.py`:

```python
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
```

- [x] **Step 2: Run tests and verify failure**

Run:

```powershell
python -m unittest backend.test_audio.AdvancedMixFilterTests
```

Expected result: failure because `_build_advanced_eq_filter` does not exist and `_build_mix_filter` does not accept advanced settings.

- [x] **Step 3: Add safe advanced helper functions**

Add to `backend/audio.py`:

```python
def _advanced_value(settings, key: str, default: float = 0.0) -> float:
    try:
        return float(getattr(settings, key, default))
    except (TypeError, ValueError):
        return default


def _build_advanced_eq_filter(settings) -> str:
    if settings is None:
        return ""

    filters: list[str] = []
    for band in getattr(settings, "eq_bands", []) or []:
        try:
            gain_db = float(getattr(band, "gain_db", 0.0))
            frequency_hz = float(getattr(band, "frequency_hz", 1000.0))
            q = float(getattr(band, "q", 1.0))
        except (TypeError, ValueError):
            continue

        if abs(gain_db) < 0.05:
            continue

        frequency_hz = _clamp(frequency_hz, 20.0, 20000.0)
        gain_db = _clamp(gain_db, -40.0, 20.0)
        q = _clamp(q, 0.1, 12.0)
        filters.append(f"equalizer=f={frequency_hz:.0f}:t=q:w={q:.2f}:g={gain_db:.1f}")

    return "," + ",".join(filters) if filters else ""
```

Modify `_build_mix_filter` signature:

```python
def _build_mix_filter(profile: dict[str, float], track_role: str, mix_style: str | None = None, advanced_mix=None) -> str:
```

In beat branch, after calculating `gain_db`:

```python
        gain_db += _advanced_value(advanced_mix, "beat_gain_db", 0.0)
```

In vocal branch, after calculating `gain_db`:

```python
    gain_db += _advanced_value(advanced_mix, "vocal_gain_db", 0.0)
```

At the end of both beat and vocal filter lists, add:

```python
                f"volume={gain_db:.1f}dB",
            ]
        ) + _build_advanced_eq_filter(advanced_mix)
```

For vocal branch:

```python
            f"volume={gain_db:.1f}dB",
        ]
    ) + _build_advanced_eq_filter(advanced_mix)
```

- [x] **Step 4: Add compressor setting influence**

Modify `_render_styled_mix`:

```python
    beat_filter = _build_mix_filter(beat_profile, "beat", style_name, advanced_mix)
    vocal_filter = _build_mix_filter(vocal_profile, "vocal", style_name, advanced_mix)
    duck_threshold_db = (
        _advanced_value(advanced_mix, "compressor_threshold_db", -28.0)
        + vocal_profile["crest_db"] / 2.0
        + style["duck_threshold_db_bias"]
    )
    duck_threshold = _clamp(_db_to_linear(duck_threshold_db), 0.008, 0.08)
    duck_ratio = _clamp(
        _advanced_value(advanced_mix, "compressor_ratio", 4.5)
        + vocal_profile["crest_db"] / 5.0
        + style["duck_ratio_bias"],
        1.0,
        14.0,
    )
    duck_attack = int(_clamp(_advanced_value(advanced_mix, "compressor_attack_ms", 12.0), 1.0, 250.0))
    duck_release = int(
        _clamp(
            _advanced_value(advanced_mix, "compressor_release_ms", 180.0)
            + vocal_profile["bandwidth_hz"] / 25.0
            + style["duck_release_bias"],
            20.0,
            1200.0,
        )
    )
```

Update `sidechaincompress`:

```python
        f"[beat][vocal]sidechaincompress=threshold={duck_threshold:.3f}:ratio={duck_ratio:.2f}:attack={duck_attack}:release={duck_release}[ducked];"
```

- [x] **Step 5: Run audio tests**

Run:

```powershell
python -m unittest backend.test_audio
```

Expected result: `OK`.

- [x] **Step 6: Run full backend suite**

Run:

```powershell
python -m unittest discover -s backend -p 'test_*.py'
```

Expected result: all tests pass.

Commit:

```powershell
git add backend/audio.py backend/test_audio.py
git commit -m "feat: apply advanced mix controls to render pipeline"
```

---

## Task 4: Add a Timeline Data API for Waveform Peaks and Beat Grid

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/audio.py`
- Modify: `backend/test_main.py`

**Purpose:** Provide structured data for a stacked waveform timeline: peaks, duration, BPM, downbeats, beat markers, and suggested offset.

**Edge Cases Covered:**
- Missing analysis session returns 404.
- Expired artifacts return 404.
- Very short audio still returns at least one peak.
- BPM outside valid bounds returns a safe empty beat grid.
- Beat grid is capped so huge files do not return massive JSON.

- [x] **Step 1: Add test for timeline endpoint**

Add to `backend/test_main.py`:

```python
    def test_timeline_endpoint_returns_peaks_and_beat_markers(self):
        beat_path = main.UPLOAD_DIR / "beat.wav"
        acap_path = main.UPLOAD_DIR / "acap.wav"
        beat_path.write_bytes(b"beat")
        acap_path.write_bytes(b"acap")
        main._register_artifact(beat_path, "analysis::beat.wav::acap.wav", main.ANALYZE_UPLOAD_KIND)
        main._register_artifact(acap_path, "analysis::beat.wav::acap.wav", main.ANALYZE_UPLOAD_KIND)
        main.ANALYSIS_SESSIONS["analysis::beat.wav::acap.wav"] = {
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

        with patch.object(main, "_ensure_runtime_dependencies", return_value=None), patch.object(
            main, "_build_waveform_peaks", side_effect=[
                {"duration_sec": 2.0, "peaks": [0.1, 0.5, 0.2]},
                {"duration_sec": 1.5, "peaks": [0.2, 0.6, 0.3]},
            ]
        ):
            with TestClient(main.app) as client:
                response = client.get("/analysis/latest/timeline")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["beat"]["peaks"], [0.1, 0.5, 0.2])
        self.assertEqual(payload["acapella"]["duration_sec"], 1.5)
        self.assertEqual(payload["grid"]["bpm"], 120.0)
        self.assertEqual(payload["grid"]["beat_times"][0], 0.5)
        self.assertAlmostEqual(payload["suggested_offset_sec"], 0.5 - (0.25 / 1.2))
```

Add `import time` at the top if needed.

- [x] **Step 2: Add waveform peak helper**

Add to `backend/main.py`:

```python
def _build_waveform_peaks(file_path: Path, peak_count: int = 512) -> dict[str, Any]:
    y, sr = librosa.load(str(file_path), sr=WAVEFORM_SAMPLE_RATE, mono=True)
    if y.size == 0:
        return {"duration_sec": 0.0, "peaks": [0.0]}

    samples_per_peak = max(1, int(len(y) / peak_count))
    peaks: list[float] = []
    for start in range(0, len(y), samples_per_peak):
        window = y[start : start + samples_per_peak]
        if window.size == 0:
            continue
        peaks.append(float(min(1.0, max(0.0, abs(window).max()))))

    return {
        "duration_sec": float(len(y) / sr),
        "peaks": peaks[:peak_count],
    }
```

Add beat grid helper:

```python
def _build_beat_grid(bpm: float, downbeat: float, duration_sec: float, max_markers: int = 512) -> list[float]:
    if bpm <= 0 or duration_sec <= 0:
        return []
    beat_duration = 60.0 / bpm
    if beat_duration <= 0:
        return []
    markers: list[float] = []
    current = max(0.0, downbeat)
    while current <= duration_sec and len(markers) < max_markers:
        markers.append(round(current, 6))
        current += beat_duration
    return markers
```

- [x] **Step 3: Add route**

Add to `backend/main.py`:

```python
@app.get("/analysis/latest/timeline")
async def latest_analysis_timeline():
    analysis = _latest_analysis_snapshot()
    beat_file_id = analysis["beat"]["file_id"]
    acapella_file_id = analysis["acapella"]["file_id"]
    beat_path = _resolve_upload_path(beat_file_id)
    acapella_path = _resolve_upload_path(acapella_file_id)
    if not beat_path.exists() or not acapella_path.exists():
        raise HTTPException(status_code=404, detail="Analysis audio files are no longer available")

    beat_waveform = _build_waveform_peaks(beat_path)
    acapella_waveform = _build_waveform_peaks(acapella_path)
    duration_sec = max(beat_waveform["duration_sec"], acapella_waveform["duration_sec"])
    bpm = float(analysis["beat"]["bpm"])
    beat_downbeat = float(analysis["beat"]["downbeat"])
    tempo_ratio = float(analysis["suggested"]["tempo_ratio"])
    acapella_downbeat = float(analysis["acapella"]["downbeat"])
    suggested_offset_sec = beat_downbeat - (acapella_downbeat / tempo_ratio)

    return {
        "beat": {
            "file_id": beat_file_id,
            "source_name": analysis["beat"]["source_name"],
            **beat_waveform,
        },
        "acapella": {
            "file_id": acapella_file_id,
            "source_name": analysis["acapella"]["source_name"],
            **acapella_waveform,
        },
        "grid": {
            "bpm": bpm,
            "downbeat": beat_downbeat,
            "beat_times": _build_beat_grid(bpm, beat_downbeat, duration_sec),
        },
        "suggested_offset_sec": suggested_offset_sec,
        "manual_mix": analysis.get("manual_mix", _default_manual_mix_settings()),
    }
```

- [x] **Step 4: Run targeted test**

Run:

```powershell
python -m unittest backend.test_main.ApiContractTests.test_timeline_endpoint_returns_peaks_and_beat_markers
```

Expected result: `OK`.

Commit:

```powershell
git add backend/main.py backend/test_main.py
git commit -m "feat: expose timeline waveform data"
```

---

## Task 5: Build the Stacked Waveform Timeline UI

**Files:**
- Modify: `backend/static/index.html`
- Modify: `backend/static/style.css`
- Modify: `backend/static/app.js`
- Modify: `backend/test_browser.py`

**Purpose:** Replace abstract alignment controls with a visual timeline that shows beat/vocal waveforms, beat markers, detected downbeats, and draggable vocal offset.

**Edge Cases Covered:**
- Timeline hidden until analysis exists.
- Timeline fetch failure leaves existing nudge slider usable.
- Dragging clamps to `-8..8` beats visually, while existing `/process` route can still clamp to its supported range until Task 9 expands manual controls.
- Mobile layout stacks tracks and preserves readable labels.

- [x] **Step 1: Add timeline markup under `analysis-results`**

Add after the suggestions grid in `backend/static/index.html`:

```html
<div id="timeline-panel" class="timeline-panel hidden">
    <div class="timeline-header">
        <h3>Timeline</h3>
        <span id="timeline-offset-label" class="timeline-offset-label">Offset: 0.000s</span>
    </div>
    <div id="timeline-ruler" class="timeline-ruler" aria-hidden="true"></div>
    <div class="timeline-track" data-timeline-track="beat">
        <div class="timeline-track-label">Beat</div>
        <canvas id="beat-timeline-canvas" class="timeline-canvas" width="900" height="88"></canvas>
    </div>
    <div class="timeline-track" data-timeline-track="acapella">
        <div class="timeline-track-label">Vocals</div>
        <canvas id="acapella-timeline-canvas" class="timeline-canvas" width="900" height="88"></canvas>
        <button id="timeline-vocal-handle" class="timeline-vocal-handle" type="button" aria-label="Drag vocal alignment"></button>
    </div>
</div>
```

- [x] **Step 2: Add timeline styles**

Add to `backend/static/style.css`:

```css
.timeline-panel {
    margin: 1rem 0;
    padding: 1rem;
    background: #111;
    border: 1px solid #222;
    border-radius: 6px;
}

.timeline-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1rem;
    margin-bottom: 0.75rem;
}

.timeline-header h3 {
    font-size: 0.9rem;
    font-weight: 700;
    text-transform: uppercase;
}

.timeline-offset-label {
    color: #aaa;
    font-size: 0.82rem;
    font-variant-numeric: tabular-nums;
}

.timeline-ruler {
    position: relative;
    height: 20px;
    border-bottom: 1px solid #222;
    margin-bottom: 0.35rem;
}

.timeline-track {
    position: relative;
    display: grid;
    grid-template-columns: 76px minmax(0, 1fr);
    gap: 0.75rem;
    align-items: center;
    min-height: 88px;
}

.timeline-track + .timeline-track {
    margin-top: 0.5rem;
}

.timeline-track-label {
    color: #888;
    font-size: 0.75rem;
    font-weight: 700;
    text-transform: uppercase;
}

.timeline-canvas {
    width: 100%;
    height: 88px;
    background: #050505;
    border: 1px solid #222;
    border-radius: 6px;
}

.timeline-vocal-handle {
    position: absolute;
    top: 12px;
    left: 76px;
    width: 10px;
    height: 64px;
    border: 1px solid #fff;
    border-radius: 3px;
    background: rgba(255, 255, 255, 0.8);
    cursor: ew-resize;
}

.timeline-vocal-handle:focus-visible {
    outline: 2px solid #fff;
    outline-offset: 3px;
}

@media (max-width: 768px) {
    .timeline-track {
        grid-template-columns: 1fr;
        gap: 0.35rem;
    }

    .timeline-vocal-handle {
        left: 0;
    }
}
```

- [x] **Step 3: Add timeline state and render helpers**

Add to `backend/static/app.js`:

```javascript
const timelinePanelEl = document.getElementById('timeline-panel');
const timelineOffsetLabelEl = document.getElementById('timeline-offset-label');
const beatTimelineCanvas = document.getElementById('beat-timeline-canvas');
const acapellaTimelineCanvas = document.getElementById('acapella-timeline-canvas');
const timelineVocalHandle = document.getElementById('timeline-vocal-handle');

let timelineData = null;
let timelineOffsetSec = 0;

function drawWaveformCanvas(canvas, peaks, options = {}) {
    if (!canvas || !Array.isArray(peaks)) return;
    const rect = canvas.getBoundingClientRect();
    const width = Math.max(1, Math.round(rect.width * window.devicePixelRatio));
    const height = Math.max(1, Math.round(rect.height * window.devicePixelRatio));
    if (canvas.width !== width) canvas.width = width;
    if (canvas.height !== height) canvas.height = height;

    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, width, height);
    ctx.fillStyle = '#050505';
    ctx.fillRect(0, 0, width, height);

    const midpoint = height / 2;
    const barWidth = width / Math.max(1, peaks.length);
    ctx.fillStyle = options.color || '#ffffff';
    peaks.forEach((peak, index) => {
        const amplitude = Math.max(2, Number(peak || 0) * height * 0.42);
        ctx.fillRect(index * barWidth, midpoint - amplitude, Math.max(1, barWidth - 1), amplitude * 2);
    });

    if (Array.isArray(options.beatTimes) && Number.isFinite(options.durationSec) && options.durationSec > 0) {
        ctx.strokeStyle = 'rgba(255,255,255,0.18)';
        ctx.lineWidth = Math.max(1, window.devicePixelRatio);
        for (const beatTime of options.beatTimes) {
            const x = (beatTime / options.durationSec) * width;
            ctx.beginPath();
            ctx.moveTo(x, 0);
            ctx.lineTo(x, height);
            ctx.stroke();
        }
    }
}

function updateTimelineHandle() {
    if (!timelineData || !timelineVocalHandle) return;
    const beatDuration = 60 / Math.max(1, timelineData.grid.bpm);
    const nudgeBeats = Number(nudgeInput.value || 0);
    timelineOffsetSec = timelineData.suggested_offset_sec + (nudgeBeats * beatDuration);
    timelineOffsetLabelEl.textContent = `Offset: ${timelineOffsetSec.toFixed(3)}s`;

    const canvasRect = acapellaTimelineCanvas.getBoundingClientRect();
    const durationSec = Math.max(timelineData.beat.duration_sec, timelineData.acapella.duration_sec, 1);
    const x = Math.max(0, Math.min(canvasRect.width, (Math.max(0, timelineOffsetSec) / durationSec) * canvasRect.width));
    timelineVocalHandle.style.transform = `translateX(${x}px)`;
}

function renderTimeline(data) {
    timelineData = data;
    timelinePanelEl.classList.remove('hidden');
    const durationSec = Math.max(data.beat.duration_sec, data.acapella.duration_sec, 1);
    drawWaveformCanvas(beatTimelineCanvas, data.beat.peaks, {
        color: '#f4f4f4',
        beatTimes: data.grid.beat_times,
        durationSec
    });
    drawWaveformCanvas(acapellaTimelineCanvas, data.acapella.peaks, {
        color: '#a8f0ff',
        beatTimes: data.grid.beat_times,
        durationSec
    });
    updateTimelineHandle();
}

async function refreshTimeline() {
    if (!analysisData) return;
    try {
        const response = await fetch('/analysis/latest/timeline');
        if (!response.ok) return;
        renderTimeline(await response.json());
    } catch (_error) {
        timelinePanelEl.classList.add('hidden');
    }
}
```

Call `refreshTimeline()` at the end of `applyAnalysisState`:

```javascript
    refreshTimeline();
```

Call `updateTimelineHandle()` inside the existing `nudgeInput` listener:

```javascript
    updateTimelineHandle();
```

- [x] **Step 4: Add drag behavior**

Add to `backend/static/app.js`:

```javascript
function setNudgeFromTimelinePointer(clientX) {
    if (!timelineData) return;
    const rect = acapellaTimelineCanvas.getBoundingClientRect();
    const clampedX = Math.max(0, Math.min(rect.width, clientX - rect.left));
    const durationSec = Math.max(timelineData.beat.duration_sec, timelineData.acapella.duration_sec, 1);
    const selectedOffsetSec = (clampedX / Math.max(1, rect.width)) * durationSec;
    const beatDuration = 60 / Math.max(1, timelineData.grid.bpm);
    const nudgeBeats = (selectedOffsetSec - timelineData.suggested_offset_sec) / beatDuration;
    const clampedNudge = Math.max(Number(nudgeInput.min), Math.min(Number(nudgeInput.max), nudgeBeats));
    nudgeInput.value = clampedNudge.toFixed(2);
    nudgeVal.textContent = nudgeInput.value;
    updateTimelineHandle();
    persistAnalysisSettings();
}

if (timelineVocalHandle) {
    timelineVocalHandle.addEventListener('pointerdown', (event) => {
        event.preventDefault();
        timelineVocalHandle.setPointerCapture(event.pointerId);
    });
    timelineVocalHandle.addEventListener('pointermove', (event) => {
        if (!timelineVocalHandle.hasPointerCapture(event.pointerId)) return;
        setNudgeFromTimelinePointer(event.clientX);
    });
    timelineVocalHandle.addEventListener('pointerup', (event) => {
        if (timelineVocalHandle.hasPointerCapture(event.pointerId)) {
            timelineVocalHandle.releasePointerCapture(event.pointerId);
        }
    });
}

window.addEventListener('resize', () => {
    if (timelineData) renderTimeline(timelineData);
});
```

- [x] **Step 5: Add browser test**

Add to `backend/test_browser.py`:

```python
MOCK_TIMELINE_RESPONSE = {
    "beat": {"file_id": "restored-beat.wav", "source_name": "beat.wav", "duration_sec": 4.0, "peaks": [0.1, 0.8, 0.4, 0.2]},
    "acapella": {"file_id": "restored-acapella.wav", "source_name": "vocals.wav", "duration_sec": 3.5, "peaks": [0.2, 0.7, 0.3, 0.1]},
    "grid": {"bpm": 128.0, "downbeat": 0.125, "beat_times": [0.125, 0.59375, 1.0625]},
    "suggested_offset_sec": -0.34375,
    "manual_mix": {"mix_style": "balanced", "nudge_beats": 0.0},
}
```

Add test:

```python
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
```

Add route fulfill helper:

```python
    def _fulfill_timeline(self, route):
        route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(MOCK_TIMELINE_RESPONSE),
        )
```

- [x] **Step 6: Run browser test**

Run:

```powershell
python -m unittest backend.test_browser.StemPromptBrowserTests.test_timeline_renders_after_restored_analysis
```

Expected result: `OK` or skipped if Playwright dependencies are unavailable.

Commit:

```powershell
git add backend/static/index.html backend/static/style.css backend/static/app.js backend/test_browser.py
git commit -m "feat: add visual alignment timeline"
```

---

## Task 6: Add A/B Preview States

**Files:**
- Modify: `backend/main.py`
- Modify: `backend/audio.py`
- Modify: `backend/static/index.html`
- Modify: `backend/static/app.js`
- Modify: `backend/static/style.css`
- Modify: `backend/test_main.py`
- Modify: `backend/test_browser.py`

**Purpose:** Let users compare generated results against rough/reference versions without downloading every render.

**Edge Cases Covered:**
- Preview render missing because TTL expired shows a clear error.
- User switches preview source while audio is playing; current audio pauses cleanly.
- Auto Mix response and manual `/process` response both supply comparable preview URLs.
- Existing history cards continue rendering.

- [x] **Step 1: Add backend result fields**

In `_build_mix_result`, add a `preview_variants` object:

```python
        "preview_variants": {
            "final": {
                "label": "Final Mix",
                "preview_url": _download_url_for(output_path.name),
                "thumbnail_url": _waveform_url_for(output_path.name),
            }
        },
```

In `/process`, return the same shape:

```python
    return {
        "output_file": output_path.name,
        "download_url": f"/download/{output_path.name}",
        "preview_url": f"/download/{output_path.name}",
        "thumbnail_url": _waveform_url_for(output_path.name),
        "preview_variants": {
            "final": {
                "label": "Final Mix",
                "preview_url": f"/download/{output_path.name}",
                "thumbnail_url": _waveform_url_for(output_path.name),
            }
        },
    }
```

- [x] **Step 2: Add preview selector markup**

Add inside `#download-preview-card`:

```html
<div id="preview-variant-controls" class="preview-variant-controls" role="group" aria-label="Preview variant"></div>
```

- [x] **Step 3: Add frontend renderer**

Add to `backend/static/app.js`:

```javascript
function renderPreviewVariantControls(result) {
    const container = document.getElementById('preview-variant-controls');
    if (!container) return;
    container.innerHTML = '';
    const variants = result.preview_variants || {
        final: {
            label: 'Final Mix',
            preview_url: result.preview_url || result.download_url,
            thumbnail_url: result.thumbnail_url
        }
    };
    for (const [key, variant] of Object.entries(variants)) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'preview-variant-btn';
        button.dataset.variantKey = key;
        button.textContent = variant.label || formatTitleCase(key);
        button.addEventListener('click', () => {
            downloadPreviewEl.pause();
            setAudioPreview(downloadPreviewCardEl, downloadPreviewEl, variant.preview_url, variant.thumbnail_url);
            container.querySelectorAll('.preview-variant-btn').forEach((btn) => btn.classList.remove('active'));
            button.classList.add('active');
        });
        container.appendChild(button);
    }
    const firstButton = container.querySelector('.preview-variant-btn');
    if (firstButton) firstButton.classList.add('active');
}
```

Call after both manual and auto mix results:

```javascript
        renderPreviewVariantControls(result);
```

- [x] **Step 4: Add styles**

```css
.preview-variant-controls {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
    margin: 0.5rem 0;
}

.preview-variant-btn {
    padding: 0.35rem 0.65rem;
    border: 1px solid #333;
    border-radius: 4px;
    background: #181818;
    color: #aaa;
    font-size: 0.8rem;
    font-weight: 700;
    cursor: pointer;
}

.preview-variant-btn.active,
.preview-variant-btn:hover {
    border-color: #fff;
    color: #fff;
}
```

- [x] **Step 5: Add test for preview variants**

Add assertion to existing mix endpoint tests:

```python
self.assertEqual(response.json()["preview_variants"]["final"]["label"], "Final Mix")
self.assertEqual(response.json()["preview_variants"]["final"]["preview_url"], "/download/auto-id_mixed.wav")
```

For `/process` test:

```python
self.assertEqual(response.json()["preview_variants"]["final"]["thumbnail_url"], "/waveform/mix-id_mixed.wav")
```

- [x] **Step 6: Run tests**

Run:

```powershell
python -m unittest backend.test_main
python -m unittest backend.test_browser
```

Expected result: backend tests pass; browser tests pass or skip if dependencies are unavailable.

Commit:

```powershell
git add backend/main.py backend/audio.py backend/static/index.html backend/static/app.js backend/static/style.css backend/test_main.py backend/test_browser.py
git commit -m "feat: add preview variant controls"
```

---

## Task 7: Add Analysis Confidence and Manual Overrides

**Files:**
- Modify: `backend/audio.py`
- Modify: `backend/main.py`
- Modify: `backend/schemas.py`
- Modify: `backend/static/index.html`
- Modify: `backend/static/app.js`
- Modify: `backend/test_audio.py`
- Modify: `backend/test_main.py`

**Purpose:** Admit uncertainty in BPM/key/downbeat detection and give users controlled override fields.

**Edge Cases Covered:**
- Silent audio returns low confidence and friendly error paths.
- Short clips return lower confidence instead of pretending certainty.
- Override BPM clamps to `40..240`.
- Override pitch clamps to `-12..12`.
- Override downbeat clamps to `0..60` seconds.

- [x] **Step 1: Add confidence helper tests**

Add to `backend/test_audio.py`:

```python
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
```

- [x] **Step 2: Add audio confidence functions**

Add to `backend/audio.py`:

```python
def estimate_bpm_confidence(beat_times: np.ndarray, bpm: float) -> float:
    if beat_times is None or len(beat_times) < 4 or bpm <= 0:
        return 0.0
    intervals = np.diff(beat_times)
    if intervals.size == 0:
        return 0.0
    expected = 60.0 / bpm
    if expected <= 0:
        return 0.0
    jitter = float(np.std(intervals) / expected)
    return float(_clamp(1.0 - jitter * 2.5, 0.0, 1.0))


def estimate_key_confidence(chroma_mean: np.ndarray) -> float:
    if chroma_mean is None or chroma_mean.size != 12 or float(np.sum(chroma_mean)) < 1e-6:
        return 0.0

    scores: list[float] = []
    for semitone in range(12):
        rotated = np.roll(chroma_mean, -semitone)
        scores.append(float(np.corrcoef(rotated, MAJOR_PROFILE)[0, 1]))
        scores.append(float(np.corrcoef(rotated, MINOR_PROFILE)[0, 1]))

    scores = [score for score in scores if np.isfinite(score)]
    if len(scores) < 2:
        return 0.0
    scores.sort(reverse=True)
    margin = scores[0] - scores[1]
    return float(_clamp(margin * 2.0, 0.0, 1.0))
```

- [x] **Step 3: Return confidence in analysis response**

Modify `detect_key` to calculate `chroma_mean` as it already does. Add a new function if easier:

```python
def analyze_key_with_confidence(y: np.ndarray, sr: int) -> tuple[str, int, float]:
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)
    key, semitone = detect_key(y, sr)
    return key, semitone, estimate_key_confidence(chroma_mean)
```

In `backend/main.py`, import:

```python
from audio import estimate_bpm_confidence, estimate_key_confidence
```

Then update `_analyze_audio_file`:

```python
def _analyze_audio_file(file_path: Path) -> dict:
    y, sr = librosa.load(str(file_path), sr=22050)
    bpm, beat_times = detect_bpm(y, sr)
    key, semitone = detect_key(y, sr)
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)
    downbeat = detect_downbeat(y, sr, beat_times)
    return {
        "bpm": bpm,
        "key": key,
        "semitone": semitone,
        "downbeat": downbeat,
        "confidence": {
            "bpm": estimate_bpm_confidence(beat_times, bpm),
            "key": estimate_key_confidence(chroma_mean),
            "downbeat": 0.6 if len(beat_times) >= 4 else 0.2,
        },
    }
```

- [x] **Step 4: Add manual override fields to UI**

Add to `backend/static/index.html` inside `.adjustments`:

```html
<div class="manual-overrides">
    <label>
        BPM
        <input id="override-bpm" type="number" min="40" max="240" step="0.01">
    </label>
    <label>
        Pitch shift
        <input id="override-pitch-shift" type="number" min="-12" max="12" step="0.01">
    </label>
    <label>
        Beat downbeat
        <input id="override-beat-downbeat" type="number" min="0" max="60" step="0.001">
    </label>
    <label>
        Vocal downbeat
        <input id="override-acapella-downbeat" type="number" min="0" max="60" step="0.001">
    </label>
</div>
```

Add JS refs:

```javascript
const overrideBpmInput = document.getElementById('override-bpm');
const overridePitchShiftInput = document.getElementById('override-pitch-shift');
const overrideBeatDownbeatInput = document.getElementById('override-beat-downbeat');
const overrideAcapellaDownbeatInput = document.getElementById('override-acapella-downbeat');
```

Populate in `renderAnalysis`:

```javascript
    overrideBpmInput.value = formatNumber(data.beat.bpm, 2);
    overridePitchShiftInput.value = Number.isFinite(data.suggested.pitch_shift) ? data.suggested.pitch_shift : 0;
    overrideBeatDownbeatInput.value = formatNumber(data.beat.downbeat, 3);
    overrideAcapellaDownbeatInput.value = formatNumber(data.acapella.downbeat, 3);
```

Use override values in `runSyncAndMix`:

```javascript
    const bpm = Number(overrideBpmInput.value || analysisData.beat.bpm);
    const pitchShift = Number(overridePitchShiftInput.value || analysisData.suggested.pitch_shift);
    const beatDownbeat = Number(overrideBeatDownbeatInput.value || analysisData.beat.downbeat);
    const acapellaDownbeat = Number(overrideAcapellaDownbeatInput.value || analysisData.acapella.downbeat);

    formData.append('bpm', bpm);
    formData.append('pitch_shift', pitchShift);
    formData.append('tempo_ratio', analysisData.suggested.tempo_ratio);
    formData.append('beat_downbeat', beatDownbeat);
    formData.append('acapella_downbeat', acapellaDownbeat);
```

- [x] **Step 5: Add confidence display**

Add helper:

```javascript
function formatConfidence(value) {
    if (!Number.isFinite(value)) return 'unknown';
    if (value >= 0.75) return 'high';
    if (value >= 0.45) return 'medium';
    return 'low';
}
```

Add to result cards:

```javascript
document.getElementById('beat-confidence').textContent = formatConfidence(data.beat.confidence?.bpm);
document.getElementById('acap-confidence').textContent = formatConfidence(data.acapella.confidence?.bpm);
```

Add HTML lines:

```html
<p>Confidence: <span id="beat-confidence">--</span></p>
<p>Confidence: <span id="acap-confidence">--</span></p>
```

- [x] **Step 6: Run tests**

Run:

```powershell
python -m unittest backend.test_audio.AnalysisConfidenceTests
python -m unittest backend.test_main
```

Expected result: `OK`.

Commit:

```powershell
git add backend/audio.py backend/main.py backend/static/index.html backend/static/app.js backend/test_audio.py backend/test_main.py
git commit -m "feat: add analysis confidence and overrides"
```

---

## Task 8: Upgrade Stem Splitter Into a Routing Mixer

**Files:**
- Modify: `backend/static/index.html`
- Modify: `backend/static/style.css`
- Modify: `backend/static/app.js`
- Modify: `backend/test_browser.py`

**Purpose:** Turn post-split stem cards into a usable routing surface with preview, mute/solo, send-to-beat, send-to-vocal, and clear selected routing.

**Edge Cases Covered:**
- Only one stem can be routed to beat at a time.
- Only one stem can be routed to vocal at a time.
- Muting a preview pauses its audio and keeps route state unchanged.
- If stem download fetch fails, checkbox reverts.
- Routing a stem preserves the stem section after switching tabs.

- [x] **Step 1: Replace checkboxes with explicit route buttons**

In `renderStemRows`, build controls like this:

```javascript
function createStemRouteButton(stem, slot) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'stem-route-btn';
    button.dataset.slot = slot;
    button.dataset.stemName = stem.name;
    button.textContent = slot === 'beat' ? 'Beat' : 'Vocal';
    button.addEventListener('click', async () => {
        await routeStemToSlot(stem, slot, button);
    });
    return button;
}

async function routeStemToSlot(stem, slot, button) {
    button.disabled = true;
    try {
        await loadStemToSlot(stem.name, stem.download_url, slot === 'beat' ? 'beat' : 'acapella');
        document.querySelectorAll(`.stem-route-btn[data-slot="${slot}"]`).forEach((btn) => {
            btn.classList.toggle('active', btn === button);
        });
    } catch (err) {
        showError(`Error loading ${formatTitleCase(stem.name)} to ${slot}: ${err.message}`);
    } finally {
        button.disabled = false;
    }
}
```

- [x] **Step 2: Add mute/solo preview controls**

Inside each stem row:

```javascript
const audio = document.createElement('audio');
audio.className = 'stem-row-audio';
audio.controls = true;
audio.preload = 'none';
audio.src = stem.preview_url || stem.download_url;

const muteButton = document.createElement('button');
muteButton.type = 'button';
muteButton.className = 'stem-monitor-btn';
muteButton.textContent = 'Mute';
muteButton.addEventListener('click', () => {
    audio.muted = !audio.muted;
    muteButton.classList.toggle('active', audio.muted);
});

const soloButton = document.createElement('button');
soloButton.type = 'button';
soloButton.className = 'stem-monitor-btn';
soloButton.textContent = 'Solo';
soloButton.addEventListener('click', () => {
    document.querySelectorAll('.stem-row-audio').forEach((otherAudio) => {
        otherAudio.muted = otherAudio !== audio;
    });
    document.querySelectorAll('.stem-monitor-btn').forEach((btn) => btn.classList.remove('active'));
    soloButton.classList.add('active');
});
```

- [x] **Step 3: Add browser assertion**

Add to `backend/test_browser.py`:

```python
    def test_stem_route_buttons_load_stem_into_vocal_slot(self):
        self.page.route("**/split-stems/jobs", self._fulfill_split_stems)
        self.page.route("**/waveform/*", self._fulfill_mock_waveform)
        self.page.route("**/download/mock-stem-bass.wav", self._fulfill_mock_acapella)
        self.page.route("**/download/mock-stem-drums.wav", self._fulfill_mock_acapella)

        self.page.goto(self.base_url, wait_until="networkidle")
        self.page.locator("#stem-track").set_input_files(str(UI_FIXTURE_DIR / "beat.wav"))
        self.page.locator("#split-stems-btn").click()
        self.page.locator(".stem-route-btn[data-slot='vocal']").first.click()

        self.page.wait_for_function(
            """
            () => (document.querySelector('#acapella-file-meta')?.textContent || '').includes('.wav')
            """
        )
        self.assertIn(".wav", self.page.locator("#acapella-file-meta").text_content())
```

- [x] **Step 4: Run browser tests**

Run:

```powershell
python -m unittest backend.test_browser
```

Expected result: pass or dependency skip.

Commit:

```powershell
git add backend/static/index.html backend/static/style.css backend/static/app.js backend/test_browser.py
git commit -m "feat: add stem routing mixer controls"
```

---

## Task 9: Replace Blocking Processing Overlay With a Job Tray

**Files:**
- Modify: `backend/static/index.html`
- Modify: `backend/static/style.css`
- Modify: `backend/static/app.js`
- Modify: `backend/test_browser.py`

**Purpose:** Let users keep browsing files and reviewing exports while long jobs run.

**Edge Cases Covered:**
- Multiple jobs can appear in the tray.
- Failed jobs show their error and leave inputs enabled.
- Completed jobs remain actionable until TTL expiry.
- Overlay still appears only for immediate non-job operations if needed.

- [x] **Step 1: Add job tray markup**

Add near the bottom of `.app`:

```html
<section id="job-tray" class="job-tray hidden" aria-label="Running jobs">
    <div class="job-tray-header">
        <h2>Jobs</h2>
        <button id="job-tray-clear-btn" class="btn-secondary" type="button">Clear done</button>
    </div>
    <div id="job-tray-list" class="job-tray-list"></div>
</section>
```

- [x] **Step 2: Add tray styles**

```css
.job-tray {
    position: fixed;
    right: 1rem;
    bottom: 1rem;
    width: min(360px, calc(100vw - 2rem));
    max-height: 50vh;
    overflow: auto;
    background: #0a0a0a;
    border: 1px solid #333;
    border-radius: 8px;
    padding: 0.75rem;
    z-index: 120;
    box-shadow: 0 18px 40px rgba(0,0,0,0.45);
}

.job-tray-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 0.75rem;
    margin-bottom: 0.75rem;
}

.job-tray-header h2 {
    font-size: 0.85rem;
    text-transform: uppercase;
}

.job-tray-card {
    padding: 0.75rem;
    border: 1px solid #222;
    border-radius: 6px;
    background: #111;
}

.job-tray-card + .job-tray-card {
    margin-top: 0.5rem;
}

.job-tray-title {
    display: flex;
    justify-content: space-between;
    gap: 0.75rem;
    color: #fff;
    font-size: 0.85rem;
    font-weight: 700;
}
```

- [x] **Step 3: Add job tray state**

```javascript
const jobTrayEl = document.getElementById('job-tray');
const jobTrayListEl = document.getElementById('job-tray-list');
const jobTrayClearBtn = document.getElementById('job-tray-clear-btn');
const activeJobs = new Map();

function upsertJobTrayCard(job, label) {
    activeJobs.set(job.job_id, { job, label });
    renderJobTray();
}

function renderJobTray() {
    jobTrayListEl.innerHTML = '';
    const entries = Array.from(activeJobs.values());
    jobTrayEl.classList.toggle('hidden', entries.length === 0);
    for (const entry of entries) {
        const card = document.createElement('div');
        card.className = 'job-tray-card';
        card.dataset.jobId = entry.job.job_id;
        card.innerHTML = `
            <div class="job-tray-title">
                <span>${escapeHtml(entry.label)}</span>
                <span>${escapeHtml(entry.job.status)}</span>
            </div>
            <div class="progress-track">
                <div class="progress-bar" style="width:${Math.max(0, Math.min(100, entry.job.progress || 0))}%"></div>
            </div>
            <p class="helper-text">${escapeHtml(entry.job.message || '')}</p>
        `;
        jobTrayListEl.appendChild(card);
    }
}

if (jobTrayClearBtn) {
    jobTrayClearBtn.addEventListener('click', () => {
        for (const [jobId, entry] of activeJobs.entries()) {
            if (entry.job.status === 'completed' || entry.job.status === 'failed') {
                activeJobs.delete(jobId);
            }
        }
        renderJobTray();
    });
}
```

- [x] **Step 4: Update `watchJob` to use tray**

```javascript
async function watchJob(initialJob, fallbackLabel) {
    let job = initialJob;
    upsertJobTrayCard(job, fallbackLabel);
    while (job.status === 'queued' || job.status === 'running') {
        upsertJobTrayCard(job, fallbackLabel);
        await wait(900);
        const response = await fetch(job.status_url);
        if (!response.ok) throw new Error(await getErrorMessage(response, `${fallbackLabel} status check failed`));
        job = await response.json();
    }
    upsertJobTrayCard(job, fallbackLabel);
    if (job.status === 'completed') {
        return job;
    }
    throw new Error(job.error || job.message || `${fallbackLabel} failed`);
}
```

Remove `showProcessing` calls from `startAutoMixJob` and `startStemSplitJob`; keep button disabled states while each specific job is being created.

- [x] **Step 5: Add browser test**

```python
    def test_job_tray_shows_completed_stem_job(self):
        self.page.route("**/split-stems/jobs", self._fulfill_split_stems)
        self.page.route("**/waveform/*", self._fulfill_mock_waveform)

        self.page.goto(self.base_url, wait_until="networkidle")
        self.page.locator("#stem-track").set_input_files(str(UI_FIXTURE_DIR / "beat.wav"))
        self.page.locator("#split-stems-btn").click()
        self.page.locator("#job-tray").wait_for(state="visible")

        self.assertIn("completed", self.page.locator("#job-tray").text_content())
```

- [x] **Step 6: Run browser tests**

Run:

```powershell
python -m unittest backend.test_browser
```

Expected result: pass or dependency skip.

Commit:

```powershell
git add backend/static/index.html backend/static/style.css backend/static/app.js backend/test_browser.py
git commit -m "feat: add nonblocking job tray"
```

---

## Task 10: Extract Backend State Helpers

**Files:**
- Create: `backend/jobs.py`
- Create: `backend/artifacts.py`
- Create: `backend/analysis_sessions.py`
- Create: `backend/test_jobs.py`
- Create: `backend/test_artifacts.py`
- Modify: `backend/main.py`

**Purpose:** Reduce `backend/main.py` from a mixed state/store/routes file into route orchestration.

**Edge Cases Covered:**
- Runtime state restores exactly as before.
- Artifact path safety remains identical.
- Job history order remains newest-first.
- Cleanup never deletes files outside `UPLOAD_DIR`.

- [x] **Step 1: Extract jobs first**

Move job-specific constants and functions from `main.py` into `backend/jobs.py`:

```python
from __future__ import annotations

from collections import deque
from datetime import datetime, timezone
import threading
import time
from typing import Any, Callable


AUTO_MIX_JOB_KIND = "auto-mix"
STEM_SPLIT_JOB_KIND = "split-stems"
JOB_HISTORY_LIMIT = 8


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InProcessJobStore:
    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self.lock = threading.RLock()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.history: deque[dict[str, Any]] = deque(maxlen=JOB_HISTORY_LIMIT)

    def create(self, job_id: str, kind: str, input_files: dict[str, str]) -> dict[str, Any]:
        job = {
            "job_id": job_id,
            "kind": kind,
            "status": "queued",
            "progress": 0,
            "message": "Queued",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "input_files": dict(input_files),
            "result": None,
            "error": None,
            "expires_at": None,
            "status_url": f"/jobs/{job_id}",
            "history_url": "/history",
        }
        with self.lock:
            self.jobs[job_id] = job
        return self.snapshot(job_id)

    def snapshot(self, job_id: str) -> dict[str, Any]:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None:
                raise KeyError(job_id)
            return dict(job)

    def update(self, job_id: str, **changes: Any) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job.update(changes)
            job["updated_at"] = utc_now_iso()

    def complete(self, job_id: str, result: dict[str, Any]) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "completed"
            job["progress"] = 100
            job["message"] = result.get("status_line") or "Job complete"
            job["result"] = result
            job["error"] = None
            job["updated_at"] = utc_now_iso()
            job["expires_at"] = time.time() + self.ttl_seconds
            self.history.appendleft(dict(job))

    def fail(self, job_id: str, error_message: str) -> None:
        with self.lock:
            job = self.jobs[job_id]
            job["status"] = "failed"
            job["progress"] = min(int(job.get("progress", 0)), 99)
            job["message"] = error_message
            job["error"] = error_message
            job["updated_at"] = utc_now_iso()
            job["expires_at"] = time.time() + self.ttl_seconds
            self.history.appendleft(dict(job))

    def list_history(self) -> list[dict[str, Any]]:
        with self.lock:
            return [dict(entry) for entry in self.history]
```

- [x] **Step 2: Add job store tests**

Create `backend/test_jobs.py`:

```python
import sys
import unittest
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

from jobs import InProcessJobStore  # noqa: E402


class InProcessJobStoreTests(unittest.TestCase):
    def test_completed_jobs_enter_history_newest_first(self):
        store = InProcessJobStore(ttl_seconds=60)
        store.create("one", "auto-mix", {"beat": "a.wav"})
        store.create("two", "split-stems", {"track": "b.wav"})

        store.complete("one", {"status_line": "first done"})
        store.complete("two", {"status_line": "second done"})

        history = store.list_history()
        self.assertEqual([entry["job_id"] for entry in history], ["two", "one"])
        self.assertEqual(history[0]["status"], "completed")

    def test_failed_job_keeps_error_message(self):
        store = InProcessJobStore(ttl_seconds=60)
        store.create("job", "auto-mix", {})

        store.fail("job", "render failed")

        snapshot = store.snapshot("job")
        self.assertEqual(snapshot["status"], "failed")
        self.assertEqual(snapshot["error"], "render failed")
```

- [x] **Step 3: Run extraction tests**

Run:

```powershell
python -m unittest backend.test_jobs
```

Expected result: `OK`.

- [x] **Step 4: Wire `main.py` to job store**

In `main.py`, instantiate:

```python
from jobs import AUTO_MIX_JOB_KIND, STEM_SPLIT_JOB_KIND, InProcessJobStore

JOB_STORE = InProcessJobStore(ARTIFACT_TTL_SECONDS)
```

Then replace `_create_job`, `_get_job`, `_update_job`, `_complete_job`, `_fail_job`, and `_list_history` internals by delegating to `JOB_STORE`. Keep public helper names temporarily so existing tests keep passing:

```python
def _create_job(kind: str, input_files: dict[str, str]) -> dict[str, Any]:
    job_id = str(uuid.uuid4())
    return JOB_STORE.create(job_id, kind, input_files)


def _get_job(job_id: str) -> dict[str, Any]:
    try:
        return JOB_STORE.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
```

Use the same pattern for update, complete, fail, and list history.

- [x] **Step 5: Run full suite**

Run:

```powershell
python -m unittest discover -s backend -p 'test_*.py'
```

Expected result: all tests pass.

Commit:

```powershell
git add backend/jobs.py backend/test_jobs.py backend/main.py
git commit -m "refactor: extract job store"
```

Repeat the same pattern for artifacts and analysis sessions after jobs are stable. Use existing tests in `backend/test_main.py` as the behavior lock, then move code in small batches.

---

## Task 11: Split Frontend JavaScript Into Focused Modules

**Files:**
- Create: `backend/static/js/state.js`
- Create: `backend/static/js/api.js`
- Create: `backend/static/js/ui.js`
- Create: `backend/static/js/remix.js`
- Create: `backend/static/js/stems.js`
- Create: `backend/static/js/file-browser.js`
- Modify: `backend/static/app.js`
- Modify: `backend/static/index.html`
- Modify: `backend/test_browser.py`

**Purpose:** Stop `app.js` from absorbing every new feature.

**Edge Cases Covered:**
- Module loading works from FastAPI static files.
- Existing page initialization order is preserved.
- Browser tests still pass after script type changes.
- Shared state changes are explicit through exported functions.

- [x] **Step 1: Convert script tag**

In `backend/static/index.html`:

```html
<script type="module" src="/static/app.js"></script>
```

- [x] **Step 2: Create API module**

Create `backend/static/js/api.js`:

```javascript
export async function getErrorMessage(response, fallbackMessage) {
    const responseText = await response.text();
    if (!responseText) return fallbackMessage;
    try {
        const payload = JSON.parse(responseText);
        if (typeof payload.detail === 'string') return payload.detail;
        if (Array.isArray(payload.detail)) return payload.detail.map((item) => item.msg || JSON.stringify(item)).join(', ');
        if (typeof payload.message === 'string') return payload.message;
    } catch (_error) {
        return responseText;
    }
    return fallbackMessage;
}

export async function postForm(url, formData, fallbackMessage) {
    const response = await fetch(url, { method: 'POST', body: formData });
    if (!response.ok) throw new Error(await getErrorMessage(response, fallbackMessage));
    return response.json();
}

export async function fetchJson(url, fallbackMessage) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(await getErrorMessage(response, fallbackMessage));
    return response.json();
}

export async function wait(ms) {
    return new Promise((resolve) => window.setTimeout(resolve, ms));
}
```

- [x] **Step 3: Create UI module**

Create `backend/static/js/ui.js`:

```javascript
export function showError(errorEl, message) {
    errorEl.textContent = message;
    errorEl.classList.remove('hidden');
}

export function hideError(errorEl) {
    errorEl.classList.add('hidden');
    errorEl.textContent = '';
}

export function setHidden(element, hidden) {
    if (!element) return;
    element.classList.toggle('hidden', hidden);
}

export function formatTitleCase(value) {
    return value.split('-').map((part) => part.charAt(0).toUpperCase() + part.slice(1)).join(' ');
}

export function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}
```

- [x] **Step 4: Move code in one domain at a time**

Start with file browser because it has the fewest backend dependencies:

```javascript
// backend/static/js/file-browser.js
export function initFileBrowser(options) {
    const {
        browseFolderBtn,
        folderFallbackInput,
        fileBrowserSearch,
        fileTreeEl,
        sidebarPlayerAudio,
        sidebarPlayerName,
        showError,
        loadFileToSlot
    } = options;

    const state = {
        roots: [],
        selectedNode: null,
        previewObjectUrl: null,
        searchQuery: ''
    };

    browseFolderBtn.addEventListener('click', async () => {
        if ('showDirectoryPicker' in window) {
            try {
                const handle = await window.showDirectoryPicker();
                state.roots.push({
                    name: handle.name,
                    kind: 'directory',
                    children: null,
                    expanded: true,
                    handle,
                    file: null,
                    path: handle.name,
                    source: 'picker'
                });
                renderBrowser();
            } catch (err) {
                if (err.name !== 'AbortError') showError('Could not open folder: ' + err.message);
            }
        } else {
            folderFallbackInput.click();
        }
    });

    function renderBrowser() {
        fileTreeEl.innerHTML = '';
        if (state.roots.length === 0) {
            const empty = document.createElement('div');
            empty.className = 'file-tree-empty';
            empty.textContent = 'Click "+ Folder" to browse local files';
            fileTreeEl.appendChild(empty);
        }
    }

    fileBrowserSearch.addEventListener('input', (event) => {
        state.searchQuery = event.target.value.trim();
        renderBrowser();
    });

    renderBrowser();
}
```

Continue moving one domain per commit: API helpers, UI helpers, stems, remix, bootstrap.

- [x] **Step 5: Keep `app.js` as bootstrap**

Final `backend/static/app.js` should look like this:

```javascript
import { initFileBrowser } from './js/file-browser.js';
import { initRemix } from './js/remix.js';
import { initStems } from './js/stems.js';
import { showError } from './js/ui.js';

const errorEl = document.getElementById('error');

const shared = {
    showError: (message) => showError(errorEl, message)
};

initFileBrowser(shared);
initRemix(shared);
initStems(shared);
```

The actual exported `init*` functions will need richer option objects than this tiny example; keep dependencies explicit and avoid hidden globals in new modules.

- [x] **Step 6: Run browser tests after each module move**

Run:

```powershell
python -m unittest backend.test_browser
```

Expected result: pass or dependency skip after each small move.

Commit after each domain move:

```powershell
git add backend/static/app.js backend/static/js backend/static/index.html backend/test_browser.py
git commit -m "refactor: extract frontend file browser module"
```

---

## Task 12: Visual Polish and Accessibility Pass

**Files:**
- Modify: `backend/static/index.html`
- Modify: `backend/static/style.css`
- Modify: `backend/test_browser.py`

**Purpose:** Make the app feel more like a focused studio tool and less like a prototype while keeping the current dark identity.

**Edge Cases Covered:**
- Keyboard-only users can operate tabs, modal close, file inputs, job tray, route buttons, and timeline handle.
- Mobile layout does not overlap controls.
- Inline styles are removed.
- Text labels remain readable over the background image.

- [x] **Step 1: Remove inline styles**

Replace:

```html
<input type="file" id="folder-fallback-input" webkitdirectory directory style="display:none">
```

With:

```html
<input type="file" id="folder-fallback-input" class="visually-hidden-input" webkitdirectory directory>
```

Replace restored banner inline styles with classes:

```html
<div id="restored-session-banner" class="status-card restored-session-banner hidden">
    <span class="status-badge">Restored session</span>
    <button id="clear-restored-session-btn" class="btn-secondary compact-btn" type="button">Clear</button>
    <span class="subtle-inline-copy">Previous analysis and settings were restored after server restart.</span>
</div>
```

Add CSS:

```css
.visually-hidden-input {
    display: none;
}

.restored-session-banner {
    margin-top: 1.5rem;
}

.compact-btn {
    padding: 0.2rem 0.7rem;
    font-size: 0.9em;
}

.subtle-inline-copy {
    color: #a0a0a0;
    font-size: 0.92em;
}
```

- [x] **Step 2: Improve focus states**

Add:

```css
button:focus-visible,
a:focus-visible,
input:focus-visible,
select:focus-visible {
    outline: 2px solid #fff;
    outline-offset: 3px;
}

.tab-btn:focus-visible {
    border-bottom-color: #fff;
}
```

- [x] **Step 3: Add modal keyboard behavior**

Add to modal JS:

```javascript
document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !advancedMixModal.classList.contains('hidden')) {
        closeModal();
    }
});
```

- [x] **Step 4: Add browser smoke test for mobile layout**

Add to `backend/test_browser.py`:

```python
    def test_mobile_layout_keeps_primary_actions_visible(self):
        self.page.set_viewport_size({"width": 390, "height": 844})
        self.page.goto(self.base_url, wait_until="networkidle")

        self.assertTrue(self.page.locator("#auto-mix-btn").is_visible())
        self.assertTrue(self.page.locator("#advanced-mix-btn").is_visible())
        self.assertTrue(self.page.locator(".sidebar").is_visible())
```

- [x] **Step 5: Run browser tests**

Run:

```powershell
python -m unittest backend.test_browser
```

Expected result: pass or dependency skip.

Commit:

```powershell
git add backend/static/index.html backend/static/style.css backend/static/app.js backend/test_browser.py
git commit -m "style: polish studio UI accessibility"
```

---

## Task 13: Final Verification Matrix

**Files:**
- Modify: `README.md`

**Purpose:** Capture the new workflows and verify that core paths still work.

- [x] **Step 1: Run backend tests**

Run:

```powershell
python -m unittest discover -s backend -p 'test_*.py'
```

Expected result: all tests pass.

- [x] **Step 2: Run browser tests**

Run:

```powershell
python -m unittest backend.test_browser
```

Expected result: tests pass or skip with a clear dependency message.

- [x] **Step 3: Run the app manually**

Run:

```powershell
cd backend
uvicorn main:app --reload
```

Open:

```text
http://localhost:8000
```

Manual smoke checklist:

- Load `ui-fixtures/beat.wav` into Instrumental.
- Load `ui-fixtures/acapella.wav` into Vocals.
- Run Advanced Mix.
- Move at least one EQ slider and one compressor control.
- Verify the request includes `advanced_mix`.
- Verify preview audio appears after render.
- Run Advanced Analyze.
- Verify timeline appears.
- Drag the vocal timeline handle and verify nudge value changes.
- Switch to Stem Splitter.
- Load a full mix.
- Run stem split with mocked or real Demucs environment.
- Route one stem to Beat and one stem to Vocal.
- Return to Remix and verify loaded file names.
- Confirm recent export history still renders after restart within TTL.

- [x] **Step 4: Update README workflow notes**

Add a short section:

```markdown
## Advanced Workflows

- Advanced Mix now sends EQ, gain, and compressor settings into the render pipeline.
- Advanced Analyze shows confidence indicators and a visual timeline for manual alignment.
- Stem Splitter can route individual stems into the beat or vocal slots before remixing.
- Long-running Auto Mix and stem jobs appear in the job tray so the UI remains usable while work runs.
```

Commit:

```powershell
git add README.md
git commit -m "docs: document advanced remix workflows"
```

---

## Recommended Execution Order

1. Task 1: schemas.
2. Task 2: Advanced Mix API payload.
3. Task 3: audible Advanced Mix pipeline.
4. Task 4: timeline data API.
5. Task 5: timeline UI.
6. Task 6: A/B preview states.
7. Task 7: confidence and manual overrides.
8. Task 8: stem routing mixer.
9. Task 9: job tray.
10. Task 10: backend extraction.
11. Task 11: frontend module split.
12. Task 12: visual polish and accessibility.
13. Task 13: final verification and docs.

This order keeps user-visible value arriving early while postponing larger refactors until tests protect the new behavior.

## Risk Notes

- The Advanced Mix modal currently has UI controls whose semantics are ambiguous. The plan maps them conservatively to EQ, gain, and compressor behavior. If a control feels musically wrong after listening tests, rename the label rather than preserving confusing behavior.
- The timeline endpoint loads audio through librosa. Use peak count caps and request it only after analysis to avoid slowing initial page load.
- Demucs stem splitting is expensive. Keep job tray changes UI-only first; do not introduce a persistent external queue until in-process behavior is stable.
- Browser File System Access APIs differ across browsers. Keep the existing `webkitdirectory` fallback.
- The frontend module split should happen after feature work, because moving globals first makes behavior changes harder to review.

## Completion Definition

The improvement program is complete when:

- Advanced Mix settings affect the exported WAV.
- Users can visually inspect and adjust alignment.
- Users can compare previews before downloading.
- Analysis uncertainty is visible and editable.
- Stem outputs can be routed into remix slots from the UI.
- Long-running jobs no longer block the whole app.
- `backend/main.py` and `backend/static/app.js` have clear module boundaries or are small bootstrapping files.
- Backend and browser tests cover each new workflow.
- README explains the new workflows.
