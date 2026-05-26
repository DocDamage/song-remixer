"""
FastAPI backend for Song Remixer.
"""

from contextlib import asynccontextmanager
import json
import os
import shutil
import threading
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

import librosa
import numpy as np
from fastapi import Body, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from pydantic import ValidationError
from schemas import AdvancedMixSettings, ManualMixSettings
from jobs import AUTO_MIX_JOB_KIND, STEM_SPLIT_JOB_KIND, InProcessJobStore, utc_now_iso
from artifacts import InProcessArtifactStore
from analysis_sessions import InProcessAnalysisSessionStore
from audio import (
    DEFAULT_MIX_STYLE,
    align_and_mix,
    auto_mix_tracks,
    convert_to_wav,
    detect_bpm,
    detect_downbeat,
    detect_key,
    estimate_bpm_confidence,
    estimate_key_confidence,
    join_vocal_stems,
    process_acapella,
    semitone_shift,
    split_stems_with_demucs,
    STEM_SPLITTER_MODEL,
)

BASE_DIR = Path(__file__).parent
UPLOAD_DIR = BASE_DIR / "uploads"
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR.mkdir(exist_ok=True)
UPLOAD_ROOT = UPLOAD_DIR.resolve()
REQUIRED_AUDIO_TOOLS = ("ffmpeg", "sox")
ARTIFACT_TTL_SECONDS = int(os.environ.get("SONG_REMIXER_ARTIFACT_TTL_SECONDS", str(4 * 60 * 60)))
STATE_FILE_NAME = ".runtime_state.json"
ANALYZE_UPLOAD_KIND = "analyze-upload"
WAVEFORM_THUMBNAIL_KIND = "waveform-thumbnail"
WAVEFORM_BAR_COUNT = 64
WAVEFORM_WIDTH = 320
WAVEFORM_HEIGHT = 72
WAVEFORM_SAMPLE_RATE = 8000
STATE_LOCK = threading.RLock()

JOB_STORE = InProcessJobStore(ARTIFACT_TTL_SECONDS)
JOB_STORE.lock = STATE_LOCK

ARTIFACT_STORE = InProcessArtifactStore(ARTIFACT_TTL_SECONDS)
ARTIFACT_STORE.lock = STATE_LOCK

ANALYSIS_SESSION_STORE = InProcessAnalysisSessionStore(ARTIFACT_TTL_SECONDS)
ANALYSIS_SESSION_STORE.lock = STATE_LOCK

_utc_now_iso = utc_now_iso


def _download_url_for(file_name: str) -> str:
    return f"/download/{file_name}"


def _waveform_url_for(file_name: str) -> str:
    return f"/waveform/{file_name}"


def _status_url_for(job_id: str) -> str:
    return f"/jobs/{job_id}"


def _history_url() -> str:
    return "/history"


def _analysis_owner_for(beat_file_name: str, acapella_file_name: str) -> str:
    return f"analysis::{beat_file_name}::{acapella_file_name}"


def _runtime_state_path() -> Path:
    return UPLOAD_DIR / STATE_FILE_NAME


def _waveform_cache_path_for(file_name: str) -> Path:
    return UPLOAD_DIR / f"{file_name}.waveform.svg"


def _default_manual_mix_settings() -> dict[str, Any]:
    return {
        "mix_style": DEFAULT_MIX_STYLE,
        "nudge_beats": 0.0,
    }


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


def _normalize_manual_mix_settings(settings: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    if not isinstance(settings, dict):
        return ManualMixSettings().model_dump()
    return ManualMixSettings(**settings).model_dump()


def _normalize_tempo_ratio(beat_bpm: float, acapella_bpm: float) -> float:
    if beat_bpm <= 0 or acapella_bpm <= 0:
        return 1.0

    raw_ratio = beat_bpm / acapella_bpm
    candidates = [raw_ratio / 2.0, raw_ratio, raw_ratio * 2.0]
    plausible_candidates = [ratio for ratio in candidates if 0.75 <= ratio <= 1.5]
    if not plausible_candidates:
        return raw_ratio
    return min(plausible_candidates, key=lambda ratio: abs(1.0 - ratio))


def _clone_analysis_response(analysis: dict[str, Any], *, restored: bool) -> dict[str, Any]:
    suggested = dict(analysis["suggested"])
    suggested["tempo_ratio"] = _normalize_tempo_ratio(
        float(analysis["beat"].get("bpm", 0.0)),
        float(analysis["acapella"].get("bpm", 0.0)),
    )
    return {
        "beat": dict(analysis["beat"]),
        "acapella": dict(analysis["acapella"]),
        "suggested": suggested,
        "manual_mix": _normalize_manual_mix_settings(analysis.get("manual_mix")),
        "restored": restored,
    }


def _serialize_persisted_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "input_files": job["input_files"],
        "result": job.get("result"),
        "error": job.get("error"),
        "expires_at": job.get("expires_at"),
    }


def _result_artifact_names(result: Optional[dict[str, Any]]) -> set[str]:
    if not isinstance(result, dict):
        return set()

    artifact_names: set[str] = set()
    for key in ("output_file", "acapella_file"):
        file_name = result.get(key)
        if isinstance(file_name, str) and file_name:
            artifact_names.add(file_name)

    stem_downloads = result.get("stem_downloads")
    if isinstance(stem_downloads, list):
        for stem_download in stem_downloads:
            if not isinstance(stem_download, dict):
                continue
            file_name = stem_download.get("output_file")
            if isinstance(file_name, str) and file_name:
                artifact_names.add(file_name)

    return artifact_names


def _persist_runtime_state() -> None:
    state_file = _runtime_state_path()
    temp_file = state_file.with_name(f"{state_file.name}.tmp")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    with STATE_LOCK:
        with JOB_STORE.lock:
            payload = {
                "version": 1,
                "jobs": [
                    _serialize_persisted_job(job)
                    for job in JOB_STORE.jobs.values()
                    if job["status"] in {"completed", "failed"} and job.get("expires_at") is not None
                ],
                "history_job_ids": [entry["job_id"] for entry in JOB_STORE.history],
            "artifacts": [
                {
                    "file_name": file_name,
                    "owner_id": metadata["owner_id"],
                    "artifact_kind": metadata["artifact_kind"],
                    "expires_at": metadata["expires_at"],
                }
                for file_name, metadata in sorted(ARTIFACT_STORE.all_items())
                if metadata["path"].exists()
            ],
            "analysis_sessions": [
                {
                    "owner_id": owner_id,
                    "analysis": session["analysis"],
                    "created_at": session["created_at"],
                    "updated_at": session["updated_at"],
                    "expires_at": session["expires_at"],
                }
                for owner_id, session in sorted(ANALYSIS_SESSION_STORE.all_items())
                if session["analysis"]["beat"]["file_id"] in ARTIFACT_STORE
                and session["analysis"]["acapella"]["file_id"] in ARTIFACT_STORE
            ],
        }

    try:
        temp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        temp_file.replace(state_file)
    except OSError:
        _remove_files(temp_file)


def _restore_runtime_state() -> None:
    state_file = _runtime_state_path()
    if not state_file.exists():
        return

    try:
        payload = json.loads(state_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return

    now = time.time()
    restored_artifacts: dict[str, dict[str, Any]] = {}
    for artifact_payload in payload.get("artifacts", []):
        if not isinstance(artifact_payload, dict):
            continue

        file_name = artifact_payload.get("file_name")
        expires_at = artifact_payload.get("expires_at")
        if not isinstance(file_name, str) or not file_name or not isinstance(expires_at, (int, float)):
            continue
        if expires_at <= now:
            continue

        artifact_path = UPLOAD_DIR / file_name
        if not artifact_path.exists():
            continue

        restored_artifacts[file_name] = {
            "path": artifact_path,
            "owner_id": artifact_payload.get("owner_id"),
            "artifact_kind": artifact_payload.get("artifact_kind"),
            "expires_at": float(expires_at),
        }

    restored_jobs: dict[str, dict[str, Any]] = {}
    for job_payload in payload.get("jobs", []):
        if not isinstance(job_payload, dict):
            continue

        job_id = job_payload.get("job_id")
        status = job_payload.get("status")
        expires_at = job_payload.get("expires_at")
        if not isinstance(job_id, str) or not job_id:
            continue
        if status not in {"completed", "failed"}:
            continue
        if not isinstance(expires_at, (int, float)) or expires_at <= now:
            continue

        result = job_payload.get("result")
        if not _result_artifact_names(result).issubset(restored_artifacts):
            continue

        restored_jobs[job_id] = {
            "job_id": job_id,
            "kind": job_payload.get("kind") or AUTO_MIX_JOB_KIND,
            "status": status,
            "progress": int(job_payload.get("progress", 100)),
            "message": job_payload.get("message") or "Recovered export",
            "created_at": job_payload.get("created_at") or _utc_now_iso(),
            "updated_at": job_payload.get("updated_at") or _utc_now_iso(),
            "input_files": job_payload.get("input_files") if isinstance(job_payload.get("input_files"), dict) else {},
            "result": result if isinstance(result, dict) else None,
            "error": job_payload.get("error") if isinstance(job_payload.get("error"), str) else None,
            "expires_at": float(expires_at),
        }

    restored_analysis_sessions: dict[str, dict[str, Any]] = {}
    for session_payload in payload.get("analysis_sessions", []):
        if not isinstance(session_payload, dict):
            continue

        owner_id = session_payload.get("owner_id")
        analysis = session_payload.get("analysis")
        expires_at = session_payload.get("expires_at")
        if not isinstance(owner_id, str) or not owner_id:
            continue
        if not isinstance(analysis, dict) or not isinstance(expires_at, (int, float)):
            continue
        if expires_at <= now:
            continue

        beat = analysis.get("beat")
        acapella = analysis.get("acapella")
        suggested = analysis.get("suggested")
        if not isinstance(beat, dict) or not isinstance(acapella, dict) or not isinstance(suggested, dict):
            continue

        beat_file_id = beat.get("file_id")
        acapella_file_id = acapella.get("file_id")
        if beat_file_id not in restored_artifacts or acapella_file_id not in restored_artifacts:
            continue

        restored_analysis_sessions[owner_id] = {
            "owner_id": owner_id,
            "analysis": _clone_analysis_response(analysis, restored=True),
            "created_at": session_payload.get("created_at") or _utc_now_iso(),
            "updated_at": session_payload.get("updated_at") or _utc_now_iso(),
            "expires_at": float(expires_at),
        }

    with STATE_LOCK:
        ARTIFACT_STORE.clear()
        with ARTIFACT_STORE.lock:
            ARTIFACT_STORE.artifacts.update(restored_artifacts)
        with JOB_STORE.lock:
            JOB_STORE.jobs.clear()
            JOB_STORE.jobs.update(restored_jobs)
            JOB_STORE.history.clear()
            for job_id in payload.get("history_job_ids", []):
                job = restored_jobs.get(job_id)
                if job is not None:
                    JOB_STORE.history.append(_serialize_job(job))
        ANALYSIS_SESSION_STORE.reset()
        with ANALYSIS_SESSION_STORE.lock:
            ANALYSIS_SESSION_STORE.sessions.update(restored_analysis_sessions)


def _cleanup_orphan_uploads() -> None:
    tracked_paths = {_runtime_state_path().resolve(strict=False)}
    with STATE_LOCK:
        tracked_paths.update(metadata["path"].resolve(strict=False) for metadata in ARTIFACT_STORE.values())

    for entry in UPLOAD_DIR.iterdir():
        resolved_entry = entry.resolve(strict=False)
        if resolved_entry in tracked_paths:
            continue

        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            pass


def _reset_runtime_state(clear_persisted_state: bool = False) -> None:
    with STATE_LOCK:
        with JOB_STORE.lock:
            JOB_STORE.jobs.clear()
            JOB_STORE.history.clear()
        ARTIFACT_STORE.clear()
        ANALYSIS_SESSION_STORE.reset()

    if clear_persisted_state:
        _remove_files(_runtime_state_path())


def _cleanup_expired_artifacts() -> None:
    now = time.time()
    expired_files: list[Path] = []
    expired_jobs: list[str] = []
    expired_analysis_sessions: list[str] = []
    state_changed = False

    with STATE_LOCK:
        expired_files = ARTIFACT_STORE.remove_expired(now)
        if expired_files:
            state_changed = True

        with JOB_STORE.lock:
            for job_id, job in list(JOB_STORE.jobs.items()):
                expires_at = job.get("expires_at")
                if expires_at is not None and expires_at <= now:
                    expired_jobs.append(job_id)
                    JOB_STORE.jobs.pop(job_id, None)
                    state_changed = True

            if expired_jobs:
                retained_history = [entry for entry in JOB_STORE.history if entry["job_id"] not in expired_jobs]
                JOB_STORE.history.clear()
                JOB_STORE.history.extend(retained_history)

        valid_artifact_names = {name for name, _ in ARTIFACT_STORE.all_items()}
        expired_analysis_sessions = ANALYSIS_SESSION_STORE.remove_expired(now, valid_artifact_names)
        if expired_analysis_sessions:
            state_changed = True

    for path in expired_files:
        _remove_files(path)

    if state_changed:
        _persist_runtime_state()


def _register_artifact(path: Path, owner_id: str, artifact_kind: str) -> None:
    ARTIFACT_STORE.register(path, owner_id, artifact_kind)
    _persist_runtime_state()


def _artifact_metadata(file_name: str) -> Optional[dict[str, Any]]:
    return ARTIFACT_STORE.metadata(file_name)


def _touch_owner_state(owner_id: str) -> None:
    next_expiry = time.time() + ARTIFACT_TTL_SECONDS
    timestamp = _utc_now_iso()
    state_changed = False

    with STATE_LOCK:
        state_changed = ARTIFACT_STORE.touch_owner(owner_id) or state_changed

        job = JOB_STORE.jobs.get(owner_id)
        if job is not None:
            job["expires_at"] = next_expiry
            state_changed = True

        try:
            session = ANALYSIS_SESSION_STORE.snapshot(owner_id)
        except KeyError:
            session = None
        if session is not None:
            with ANALYSIS_SESSION_STORE.lock:
                ANALYSIS_SESSION_STORE.sessions[owner_id]["expires_at"] = next_expiry
                ANALYSIS_SESSION_STORE.sessions[owner_id]["updated_at"] = timestamp
            state_changed = True

    if state_changed:
        _persist_runtime_state()


def _touch_artifact(file_name: str) -> None:
    metadata = _artifact_metadata(file_name)
    if metadata is None:
        return
    _touch_owner_state(metadata["owner_id"])


def _store_analysis_session(analysis: dict[str, Any]) -> None:
    owner_id = _analysis_owner_for(analysis["beat"]["file_id"], analysis["acapella"]["file_id"])
    ANALYSIS_SESSION_STORE.store(owner_id, _clone_analysis_response(analysis, restored=False))
    _persist_runtime_state()


def _update_analysis_session_settings(
    beat_file_id: str,
    acapella_file_id: str,
    mix_style: str,
    nudge_beats: float,
) -> dict[str, Any]:
    owner_id = _analysis_owner_for(beat_file_id, acapella_file_id)
    manual_mix = _normalize_manual_mix_settings(
        {
            "mix_style": mix_style,
            "nudge_beats": nudge_beats,
        }
    )

    try:
        ANALYSIS_SESSION_STORE.update_settings(owner_id, manual_mix)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Saved analysis session not found") from exc

    ARTIFACT_STORE.touch_owner(owner_id)

    _persist_runtime_state()
    return manual_mix


def _latest_analysis_owner_id() -> str:
    _cleanup_expired_artifacts()
    try:
        return ANALYSIS_SESSION_STORE.latest_owner_id()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="No saved analysis session found") from exc


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


def _latest_analysis_snapshot() -> dict[str, Any]:
    owner_id = _latest_analysis_owner_id()
    try:
        session = ANALYSIS_SESSION_STORE.snapshot(owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="No saved analysis session found") from exc

    payload = _clone_analysis_response(session["analysis"], restored=True)
    payload["restored_at"] = session["updated_at"]

    _touch_owner_state(owner_id)
    return payload


def _clear_analysis_session(owner_id: str) -> None:
    try:
        ANALYSIS_SESSION_STORE.clear(owner_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="No saved analysis session found") from exc

    removed_paths = ARTIFACT_STORE.remove_by_owner(owner_id)

    for path in removed_paths:
        _remove_files(path)

    _persist_runtime_state()


def _build_waveform_thumbnail_svg(samples: Any) -> str:
    total_samples = len(samples)
    peaks: list[float] = []
    samples_per_bar = max(1, total_samples // WAVEFORM_BAR_COUNT) if total_samples else 1

    for bar_index in range(WAVEFORM_BAR_COUNT):
        start = bar_index * samples_per_bar
        end = min(total_samples, start + samples_per_bar)
        if start >= total_samples:
            peaks.append(0.0)
            continue

        peak = 0.0
        for sample in samples[start:end]:
            peak = max(peak, abs(float(sample)))
        peaks.append(min(1.0, peak))

    bar_width = WAVEFORM_WIDTH / WAVEFORM_BAR_COUNT
    midpoint = WAVEFORM_HEIGHT / 2
    bars = []
    for bar_index, peak in enumerate(peaks):
        amplitude = max(3.5, peak * (WAVEFORM_HEIGHT * 0.38))
        x = bar_index * bar_width + 0.75
        y = midpoint - amplitude
        width = max(1.5, bar_width - 1.5)
        height = amplitude * 2
        bars.append(
            f'<rect x="{x:.2f}" y="{y:.2f}" width="{width:.2f}" height="{height:.2f}" rx="1.5" fill="url(#waveform-bars)" />'
        )

    return "".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WAVEFORM_WIDTH}" height="{WAVEFORM_HEIGHT}" viewBox="0 0 {WAVEFORM_WIDTH} {WAVEFORM_HEIGHT}" preserveAspectRatio="none">',
            "<defs>",
            '<linearGradient id="waveform-bg" x1="0%" y1="0%" x2="100%" y2="100%">',
            '<stop offset="0%" stop-color="#121b30" />',
            '<stop offset="100%" stop-color="#0b111f" />',
            "</linearGradient>",
            '<linearGradient id="waveform-bars" x1="0%" y1="0%" x2="100%" y2="0%">',
            '<stop offset="0%" stop-color="#ff8b7a" />',
            '<stop offset="100%" stop-color="#78ece3" />',
            "</linearGradient>",
            "</defs>",
            f'<rect width="{WAVEFORM_WIDTH}" height="{WAVEFORM_HEIGHT}" rx="10" fill="url(#waveform-bg)" />',
            f'<line x1="0" y1="{midpoint:.2f}" x2="{WAVEFORM_WIDTH}" y2="{midpoint:.2f}" stroke="rgba(143,229,221,0.25)" stroke-width="1" />',
            *bars,
            "</svg>",
        ]
    )


def _ensure_waveform_thumbnail(file_name: str) -> Path:
    source_path = _resolve_upload_path(file_name)
    if not source_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if source_path.suffix.lower() == ".zip":
        raise HTTPException(status_code=400, detail="Waveform previews are only available for audio files")

    thumbnail_path = _waveform_cache_path_for(file_name)
    source_metadata = _artifact_metadata(file_name)
    owner_id = source_metadata["owner_id"] if source_metadata else source_path.stem
    artifact_kind = (
        f"{source_metadata['artifact_kind']}-{WAVEFORM_THUMBNAIL_KIND}"
        if source_metadata and isinstance(source_metadata.get("artifact_kind"), str)
        else WAVEFORM_THUMBNAIL_KIND
    )

    if thumbnail_path.exists():
        if _artifact_metadata(thumbnail_path.name) is None:
            _register_artifact(thumbnail_path, owner_id, artifact_kind)
        return thumbnail_path

    temp_thumbnail_path = thumbnail_path.with_name(f"{thumbnail_path.name}.tmp")
    try:
        samples, _sample_rate = librosa.load(str(source_path), sr=WAVEFORM_SAMPLE_RATE, mono=True)
        thumbnail_svg = _build_waveform_thumbnail_svg(samples)
        temp_thumbnail_path.write_text(thumbnail_svg, encoding="utf-8")
        temp_thumbnail_path.replace(thumbnail_path)
    except HTTPException:
        _remove_files(temp_thumbnail_path)
        raise
    except RuntimeError as exc:
        _remove_files(temp_thumbnail_path)
        raise HTTPException(status_code=422, detail=f"Waveform preview failed: {_with_actionable_hint(str(exc))}") from exc
    except Exception as exc:
        _remove_files(temp_thumbnail_path)
        raise HTTPException(status_code=422, detail=f"Waveform preview failed: {_with_actionable_hint(str(exc))}") from exc

    _register_artifact(thumbnail_path, owner_id, artifact_kind)
    return thumbnail_path


def _serialize_job(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job["job_id"],
        "kind": job["kind"],
        "status": job["status"],
        "progress": job["progress"],
        "message": job["message"],
        "created_at": job["created_at"],
        "updated_at": job["updated_at"],
        "input_files": job["input_files"],
        "result": job.get("result"),
        "error": job.get("error"),
        "status_url": _status_url_for(job["job_id"]),
        "history_url": _history_url(),
    }


def _create_job(kind: str, input_files: dict[str, Any]) -> dict[str, Any]:
    _cleanup_expired_artifacts()
    job_id = str(uuid.uuid4())
    return JOB_STORE.create(job_id, kind, input_files)


def _get_job(job_id: str) -> dict[str, Any]:
    try:
        return JOB_STORE.snapshot(job_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


def _list_history() -> list[dict[str, Any]]:
    _cleanup_expired_artifacts()
    return JOB_STORE.list_history()


def _update_job(job_id: str, *, status: Optional[str] = None, progress: Optional[int] = None, message: Optional[str] = None) -> None:
    changes: dict[str, Any] = {}
    if status is not None:
        changes["status"] = status
    if progress is not None:
        changes["progress"] = progress
    if message is not None:
        changes["message"] = message
    if changes:
        try:
            JOB_STORE.update(job_id, **changes)
        except KeyError:
            return


def _complete_job(job_id: str, result: dict[str, Any]) -> None:
    try:
        JOB_STORE.complete(job_id, result)
    except KeyError:
        return
    _persist_runtime_state()


def _fail_job(job_id: str, error_message: str) -> None:
    try:
        JOB_STORE.fail(job_id, error_message)
    except KeyError:
        return
    _persist_runtime_state()


def _start_job_thread(job_id: str, worker: Callable[[str], dict[str, Any]]) -> None:
    thread = threading.Thread(target=_run_job_worker, args=(job_id, worker), daemon=True)
    thread.start()


def _run_job_worker(job_id: str, worker: Callable[[str], dict[str, Any]]) -> None:
    try:
        result = worker(job_id)
    except HTTPException as exc:
        _fail_job(job_id, str(exc.detail))
    except RuntimeError as exc:
        _fail_job(job_id, _with_actionable_hint(str(exc)))
    except Exception as exc:
        _fail_job(job_id, _with_actionable_hint(f"Unexpected error: {exc}"))
    else:
        _complete_job(job_id, result)


def _cleanup_upload_dir() -> None:
    for entry in UPLOAD_DIR.iterdir():
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
        except OSError:
            pass


def _remove_files(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass


def _ensure_runtime_dependencies() -> None:
    missing_tools = [tool for tool in REQUIRED_AUDIO_TOOLS if shutil.which(tool) is None]
    if missing_tools:
        missing = ", ".join(missing_tools)
        raise RuntimeError(f"Missing required audio tools: {missing}. Install them and restart the server.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    UPLOAD_DIR.mkdir(exist_ok=True)
    _reset_runtime_state()
    _restore_runtime_state()
    _cleanup_expired_artifacts()
    _cleanup_orphan_uploads()
    _ensure_runtime_dependencies()
    yield

app = FastAPI(title="Song Remixer", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


def _safe_suffix(filename: Optional[str]) -> str:
    if not filename:
        return ""

    suffix = Path(filename).suffix.lower()
    if not suffix:
        return ""

    if len(suffix) > 10 or not all(ch.isalnum() or ch == "." for ch in suffix):
        return ""

    return suffix


def _build_raw_upload_path(file_id: str, filename: Optional[str], label: str) -> Path:
    suffix = _safe_suffix(filename)
    return UPLOAD_DIR / f"{file_id}_{label}_upload{suffix}"


def _resolve_upload_path(filename: str) -> Path:
    candidate = (UPLOAD_DIR / filename).resolve(strict=False)
    if candidate.parent != UPLOAD_ROOT:
        raise HTTPException(status_code=400, detail="Invalid file reference")
    return candidate


def _validate_process_inputs(bpm: float, tempo_ratio: float) -> None:
    if bpm <= 0:
        raise HTTPException(status_code=400, detail="Beat BPM must be greater than zero")

    if tempo_ratio <= 0:
        raise HTTPException(status_code=400, detail="Tempo ratio must be greater than zero")


def _with_actionable_hint(detail: str) -> str:
    lower_detail = detail.lower()
    if "missing required audio tools" in lower_detail:
        return f"{detail} Tip: install ffmpeg and sox, then restart the server."
    if "demucs is not installed" in lower_detail:
        return f"{detail} Tip: install backend/requirements.txt and restart the server."
    if "no vocal stems were produced" in lower_detail:
        return f"{detail} Tip: try a full song mix with clearly audible vocals."
    if "invalid data found" in lower_detail or "could not read the file" in lower_detail:
        return f"{detail} Tip: re-export the audio as WAV or MP3 and try again."
    if "cuda" in lower_detail and "available" in lower_detail:
        return f"{detail} Tip: set SONG_REMIXER_STEM_DEVICE=cpu if you want to force CPU splitting."
    return detail


async def _save_upload_file(upload: UploadFile, file_id: str, label: str) -> Path:
    raw_path = _build_raw_upload_path(file_id, upload.filename, label)

    try:
        with open(raw_path, "wb") as file_handle:
            shutil.copyfileobj(upload.file, file_handle)
        return raw_path
    except Exception:
        _remove_files(raw_path)
        raise
    finally:
        await upload.close()


async def _store_upload_as_wav(upload: UploadFile, file_id: str, label: str) -> Path:
    raw_path = await _save_upload_file(upload, file_id, label)
    wav_path = UPLOAD_DIR / f"{file_id}_{label}.wav"

    try:
        convert_to_wav(str(raw_path), str(wav_path))
        return wav_path
    except Exception:
        _remove_files(raw_path, wav_path)
        raise
    finally:
        _remove_files(raw_path)


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


def _build_analysis_response(
    beat_wav: Path,
    acapella_wav: Path,
    *,
    beat_source_name: Optional[str] = None,
    acapella_source_name: Optional[str] = None,
) -> dict:
    beat_analysis = _analyze_audio_file(beat_wav)
    acapella_analysis = _analyze_audio_file(acapella_wav)

    pitch_shift = semitone_shift(acapella_analysis["semitone"], beat_analysis["semitone"])
    tempo_ratio = _normalize_tempo_ratio(beat_analysis["bpm"], acapella_analysis["bpm"])

    return {
        "beat": {
            "file_id": beat_wav.name,
            "source_name": beat_source_name or beat_wav.name,
            **beat_analysis,
        },
        "acapella": {
            "file_id": acapella_wav.name,
            "source_name": acapella_source_name or acapella_wav.name,
            **acapella_analysis,
        },
        "suggested": {
            "tempo_ratio": tempo_ratio,
            "pitch_shift": pitch_shift,
        },
        "manual_mix": _default_manual_mix_settings(),
        "restored": False,
    }


def _wav_path_for_raw_upload(raw_path: Path) -> Path:
    stem_name = raw_path.stem
    if stem_name.endswith("_upload"):
        stem_name = stem_name[: -len("_upload")]
    return raw_path.with_name(f"{stem_name}.wav")


def _move_stem_outputs(stem_files: dict[str, Path], artifact_owner: str) -> dict[str, Path]:
    persistent_stems: dict[str, Path] = {}
    for stem_name, stem_path in sorted(stem_files.items()):
        target_path = UPLOAD_DIR / f"{artifact_owner}_stem_{stem_name}.wav"
        shutil.copyfile(stem_path, target_path)
        persistent_stems[stem_name] = target_path
    return persistent_stems


def _build_mix_result(output_path: Path, beat_file_name: str, acapella_file_name: str, mix_style: str) -> dict[str, Any]:
    return {
        "output_file": output_path.name,
        "download_url": _download_url_for(output_path.name),
        "preview_url": _download_url_for(output_path.name),
        "thumbnail_url": _waveform_url_for(output_path.name),
        "preview_variants": {
            "final": {
                "label": "Final Mix",
                "preview_url": _download_url_for(output_path.name),
                "thumbnail_url": _waveform_url_for(output_path.name),
            }
        },
        "beat_file_name": beat_file_name,
        "acapella_file_name": acapella_file_name,
        "mix_style": mix_style,
        "status_line": (
            f"{beat_file_name} and {acapella_file_name} were aligned, polished, and exported automatically. "
            "Your remix is ready to download."
        ),
    }


def _build_stem_result(
    archive_path: Path,
    acapella_path: Path,
    stem_files: dict[str, Path],
    source_track_name: str,
) -> dict[str, Any]:
    stem_names = sorted(stem_files)
    stem_downloads = [
        {
            "name": stem_name,
            "output_file": stem_path.name,
            "download_url": _download_url_for(stem_path.name),
            "preview_url": _download_url_for(stem_path.name),
            "thumbnail_url": _waveform_url_for(stem_path.name),
            "file_name": f"{stem_name}.wav",
        }
        for stem_name, stem_path in sorted(stem_files.items())
    ]
    return {
        "output_file": archive_path.name,
        "download_url": _download_url_for(archive_path.name),
        "stems": stem_names,
        "stem_downloads": stem_downloads,
        "model": STEM_SPLITTER_MODEL,
        "acapella_file": acapella_path.name,
        "acapella_download_url": _download_url_for(acapella_path.name),
        "acapella_preview_url": _download_url_for(acapella_path.name),
        "acapella_thumbnail_url": _waveform_url_for(acapella_path.name),
        "acapella_file_name": "separated-acapella.wav",
        "source_track_name": source_track_name,
        "status_line": (
            f"{source_track_name} was separated into {', '.join(stem_names[:-1])}, and {stem_names[-1]} "
            "using fine-tuned HT Demucs. Your 24-bit WAV stem bundle is ready to download."
            if len(stem_names) > 2
            else f"{source_track_name} was separated into {', '.join(stem_names)}."
        ),
    }


def _run_auto_mix_pipeline(
    raw_beat_path: Path,
    raw_acapella_path: Path,
    beat_file_name: str,
    acapella_file_name: str,
    mix_style: str = DEFAULT_MIX_STYLE,
    artifact_owner: Optional[str] = None,
    job_id: Optional[str] = None,
) -> dict[str, Any]:
    beat_wav = _wav_path_for_raw_upload(raw_beat_path)
    acapella_wav = _wav_path_for_raw_upload(raw_acapella_path)
    process_id = str(uuid.uuid4())
    processed_path = UPLOAD_DIR / f"{process_id}_processed.wav"
    output_path = UPLOAD_DIR / f"{process_id}_mixed.wav"

    try:
        if job_id:
            _update_job(job_id, status="running", progress=10, message="Converting uploaded tracks...")
        convert_to_wav(str(raw_beat_path), str(beat_wav))
        convert_to_wav(str(raw_acapella_path), str(acapella_wav))
        _remove_files(raw_beat_path, raw_acapella_path)

        if job_id:
            _update_job(job_id, progress=35, message="Analyzing beat and vocal timing...")
        analysis = _build_analysis_response(beat_wav, acapella_wav)
        _validate_process_inputs(analysis["beat"]["bpm"], analysis["suggested"]["tempo_ratio"])

        if job_id:
            _update_job(job_id, progress=60, message="Preparing the vocal track...")
        process_acapella(
            str(acapella_wav),
            str(processed_path),
            analysis["suggested"]["tempo_ratio"],
            analysis["suggested"]["pitch_shift"],
        )

        adjusted_acap_downbeat = analysis["acapella"]["downbeat"] / analysis["suggested"]["tempo_ratio"]
        if job_id:
            _update_job(job_id, progress=85, message="Rendering the finished remix...")
        auto_mix_tracks(
            str(beat_wav),
            str(processed_path),
            str(output_path),
            analysis["beat"]["downbeat"],
            adjusted_acap_downbeat,
            0.0,
            analysis["beat"]["bpm"],
            mix_style,
        )
    finally:
        _remove_files(raw_beat_path, raw_acapella_path, beat_wav, acapella_wav, processed_path)

    _register_artifact(output_path, artifact_owner or process_id, AUTO_MIX_JOB_KIND)
    return _build_mix_result(output_path, beat_file_name, acapella_file_name, mix_style)


def _run_stem_split_pipeline(
    raw_track_path: Path,
    source_track_name: str,
    artifact_owner: str,
    job_id: Optional[str] = None,
) -> dict[str, Any]:
    stem_output_root = UPLOAD_DIR / f"{artifact_owner}_stem_work"
    archive_path = UPLOAD_DIR / f"{artifact_owner}_stems.zip"
    acapella_path = UPLOAD_DIR / f"{artifact_owner}_acapella.wav"
    persistent_stems: dict[str, Path] = {}

    try:
        if job_id:
            _update_job(job_id, status="running", progress=12, message="Separating stems with HT Demucs...")
        stem_files = split_stems_with_demucs(str(raw_track_path), str(stem_output_root))

        if job_id:
            _update_job(job_id, progress=72, message="Preparing individual stem previews...")
        persistent_stems = _move_stem_outputs(stem_files, artifact_owner)

        if job_id:
            _update_job(job_id, progress=86, message="Building the acapella handoff and stem ZIP...")
        join_vocal_stems(persistent_stems, str(acapella_path))
        _create_stem_archive(persistent_stems, archive_path)
    finally:
        _remove_files(raw_track_path)
        if stem_output_root.exists():
            shutil.rmtree(stem_output_root, ignore_errors=True)

    _register_artifact(archive_path, artifact_owner, STEM_SPLIT_JOB_KIND)
    _register_artifact(acapella_path, artifact_owner, STEM_SPLIT_JOB_KIND)
    for stem_path in persistent_stems.values():
        _register_artifact(stem_path, artifact_owner, STEM_SPLIT_JOB_KIND)
    return _build_stem_result(archive_path, acapella_path, persistent_stems, source_track_name)


def _create_stem_archive(stem_files: dict[str, Path], archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for stem_name, stem_path in stem_files.items():
            archive.write(stem_path, arcname=f"{stem_name}{stem_path.suffix}")


def _download_metadata(file_path: Path) -> tuple[str, str]:
    file_name = file_path.name
    if file_path.suffix.lower() == ".zip":
        return "application/zip", "stems.zip"
    if file_name.endswith("_acapella.wav"):
        return "audio/wav", "separated-acapella.wav"
    if "_stem_" in file_name:
        stem_name = file_name.split("_stem_", maxsplit=1)[1].rsplit(".", maxsplit=1)[0]
        return "audio/wav", f"{stem_name}.wav"
    if file_name.endswith("_mixed.wav"):
        return "audio/wav", "remixed.wav"
    return "audio/wav", "remixed.wav"


@app.get("/")
async def root():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.post("/analyze")
async def analyze(
    beat: UploadFile = File(...),
    acapella: UploadFile = File(...),
):
    """Upload and analyze both tracks. Returns BPM, key, and downbeat for each."""
    beat_id = str(uuid.uuid4())
    acapella_id = str(uuid.uuid4())
    beat_source_name = beat.filename or "beat"
    acapella_source_name = acapella.filename or "acapella"
    beat_wav = UPLOAD_DIR / f"{beat_id}_beat.wav"
    acapella_wav = UPLOAD_DIR / f"{acapella_id}_acapella.wav"

    try:
        beat_wav = await _store_upload_as_wav(beat, beat_id, "beat")
        acapella_wav = await _store_upload_as_wav(acapella, acapella_id, "acapella")
        analysis_response = _build_analysis_response(
            beat_wav,
            acapella_wav,
            beat_source_name=beat_source_name,
            acapella_source_name=acapella_source_name,
        )
        artifact_owner = _analysis_owner_for(beat_wav.name, acapella_wav.name)
        _register_artifact(beat_wav, artifact_owner, ANALYZE_UPLOAD_KIND)
        _register_artifact(acapella_wav, artifact_owner, ANALYZE_UPLOAD_KIND)
        _store_analysis_session(analysis_response)
        return analysis_response
    except RuntimeError as exc:
        _remove_files(beat_wav, acapella_wav)
        raise HTTPException(status_code=422, detail=f"Audio analysis failed: {exc}") from exc
    except Exception:
        _remove_files(beat_wav, acapella_wav)
        raise


@app.post("/process")
async def process(
    beat_file_id: str = Form(...),
    acapella_file_id: str = Form(...),
    bpm: float = Form(...),
    pitch_shift: float = Form(...),
    tempo_ratio: float = Form(...),
    beat_downbeat: float = Form(...),
    acapella_downbeat: float = Form(...),
    nudge_beats: float = Form(0.0),
    mix_style: str = Form(DEFAULT_MIX_STYLE),
    advanced_mix: Optional[str] = Form(None),
):
    """
    Apply time-stretch, pitch-shift, alignment, and mixing.
    Returns a download URL for the remixed WAV.
    """
    beat_path = _resolve_upload_path(beat_file_id)
    acapella_path = _resolve_upload_path(acapella_file_id)
    _validate_process_inputs(bpm, tempo_ratio)

    if not beat_path.exists() or not acapella_path.exists():
        raise HTTPException(status_code=404, detail="Uploaded files not found. Please re-upload.")

    _touch_artifact(beat_file_id)
    _touch_artifact(acapella_file_id)
    try:
        _update_analysis_session_settings(beat_file_id, acapella_file_id, mix_style, nudge_beats)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise

    advanced_mix_settings = _parse_advanced_mix_settings(advanced_mix)

    process_id = str(uuid.uuid4())
    processed_path = UPLOAD_DIR / f"{process_id}_processed.wav"
    output_path = UPLOAD_DIR / f"{process_id}_mixed.wav"

    try:
        # 1. Stretch + pitch-shift acapella
        process_acapella(
            str(acapella_path),
            str(processed_path),
            tempo_ratio,
            pitch_shift,
        )

        # 2. Adjust downbeat for tempo change (stretching changes time positions)
        adjusted_acap_downbeat = acapella_downbeat / tempo_ratio

        # 3. Align and mix
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
    except RuntimeError as exc:
        _remove_files(processed_path, output_path)
        raise HTTPException(status_code=422, detail=f"Audio processing failed: {exc}") from exc
    except Exception:
        _remove_files(processed_path, output_path)
        raise
    finally:
        _remove_files(processed_path)

    _register_artifact(output_path, process_id, "manual-mix")
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


@app.post("/auto-mix")
async def auto_mix(
    beat: UploadFile = File(...),
    acapella: UploadFile = File(...),
    mix_style: str = Form(DEFAULT_MIX_STYLE),
):
    """Upload both tracks and return a finished one-click mix with automatic track treatment."""
    beat_id = str(uuid.uuid4())
    acapella_id = str(uuid.uuid4())
    beat_file_name = beat.filename or "beat"
    acapella_file_name = acapella.filename or "acapella"
    raw_beat_path = UPLOAD_DIR / f"{beat_id}_beat_upload{_safe_suffix(beat.filename)}"
    raw_acapella_path = UPLOAD_DIR / f"{acapella_id}_acapella_upload{_safe_suffix(acapella.filename)}"

    try:
        raw_beat_path = await _save_upload_file(beat, beat_id, "beat")
        raw_acapella_path = await _save_upload_file(acapella, acapella_id, "acapella")
        return _run_auto_mix_pipeline(
            raw_beat_path,
            raw_acapella_path,
            beat_file_name,
            acapella_file_name,
            mix_style,
        )
    except RuntimeError as exc:
        _remove_files(raw_beat_path, raw_acapella_path)
        raise HTTPException(status_code=422, detail=f"Auto mix failed: {_with_actionable_hint(str(exc))}") from exc
    except HTTPException:
        _remove_files(raw_beat_path, raw_acapella_path)
        raise
    except Exception:
        _remove_files(raw_beat_path, raw_acapella_path)
        raise


@app.post("/auto-mix/jobs")
async def create_auto_mix_job(
    beat: UploadFile = File(...),
    acapella: UploadFile = File(...),
    mix_style: str = Form(DEFAULT_MIX_STYLE),
):
    beat_id = str(uuid.uuid4())
    acapella_id = str(uuid.uuid4())
    beat_file_name = beat.filename or "beat"
    acapella_file_name = acapella.filename or "acapella"
    raw_beat_path = UPLOAD_DIR / f"{beat_id}_beat_upload{_safe_suffix(beat.filename)}"
    raw_acapella_path = UPLOAD_DIR / f"{acapella_id}_acapella_upload{_safe_suffix(acapella.filename)}"

    try:
        raw_beat_path = await _save_upload_file(beat, beat_id, "beat")
        raw_acapella_path = await _save_upload_file(acapella, acapella_id, "acapella")
    except Exception:
        _remove_files(raw_beat_path, raw_acapella_path)
        raise

    job = _create_job(
        AUTO_MIX_JOB_KIND,
        {
            "beat": beat_file_name,
            "acapella": acapella_file_name,
        },
    )
    _start_job_thread(
        job["job_id"],
        lambda job_id: _run_auto_mix_pipeline(
            raw_beat_path,
            raw_acapella_path,
            beat_file_name,
            acapella_file_name,
            mix_style,
            artifact_owner=job_id,
            job_id=job_id,
        ),
    )
    return JSONResponse(status_code=202, content=_get_job(job["job_id"]))


@app.post("/split-stems")
async def split_stems(track: UploadFile = File(...)):
    """Upload a full mix and return a zip of high-quality separated stems."""
    track_id = str(uuid.uuid4())
    source_track_name = track.filename or "track"
    raw_track_path = UPLOAD_DIR / f"{track_id}_track_upload{_safe_suffix(track.filename)}"

    try:
        raw_track_path = await _save_upload_file(track, track_id, "track")
        return _run_stem_split_pipeline(
            raw_track_path,
            source_track_name,
            artifact_owner=str(uuid.uuid4()),
        )
    except RuntimeError as exc:
        _remove_files(raw_track_path)
        raise HTTPException(status_code=422, detail=f"Stem split failed: {_with_actionable_hint(str(exc))}") from exc
    except Exception:
        _remove_files(raw_track_path)
        raise


@app.post("/split-stems/jobs")
async def create_stem_split_job(track: UploadFile = File(...)):
    track_id = str(uuid.uuid4())
    source_track_name = track.filename or "track"
    raw_track_path = UPLOAD_DIR / f"{track_id}_track_upload{_safe_suffix(track.filename)}"

    try:
        raw_track_path = await _save_upload_file(track, track_id, "track")
    except Exception:
        _remove_files(raw_track_path)
        raise

    job = _create_job(STEM_SPLIT_JOB_KIND, {"track": source_track_name})
    _start_job_thread(
        job["job_id"],
        lambda job_id: _run_stem_split_pipeline(
            raw_track_path,
            source_track_name,
            artifact_owner=job_id,
            job_id=job_id,
        ),
    )
    return JSONResponse(status_code=202, content=_get_job(job["job_id"]))


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    return _get_job(job_id)


@app.get("/history")
async def history():
    return {"items": _list_history()}


@app.get("/analysis/latest")
async def latest_analysis():
    return _latest_analysis_snapshot()


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


@app.put("/analysis/settings")
async def update_analysis_settings(payload: dict[str, Any] = Body(...)):
    beat_file_id = payload.get("beat_file_id")
    acapella_file_id = payload.get("acapella_file_id")
    if not isinstance(beat_file_id, str) or not beat_file_id:
        raise HTTPException(status_code=400, detail="beat_file_id is required")
    if not isinstance(acapella_file_id, str) or not acapella_file_id:
        raise HTTPException(status_code=400, detail="acapella_file_id is required")

    manual_mix = _update_analysis_session_settings(
        beat_file_id,
        acapella_file_id,
        payload.get("mix_style", DEFAULT_MIX_STYLE),
        payload.get("nudge_beats", 0.0),
    )
    return {"manual_mix": manual_mix}


@app.delete("/analysis/latest")
async def clear_latest_analysis():
    owner_id = _latest_analysis_owner_id()
    _clear_analysis_session(owner_id)
    return Response(status_code=204)


@app.get("/waveform/{filename}")
async def waveform(filename: str):
    _cleanup_expired_artifacts()
    thumbnail_path = _ensure_waveform_thumbnail(filename)
    _touch_artifact(filename)
    return FileResponse(str(thumbnail_path), media_type="image/svg+xml")


@app.get("/download/{filename}")
async def download(filename: str):
    _cleanup_expired_artifacts()
    file_path = _resolve_upload_path(filename)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    _touch_artifact(filename)
    media_type, download_name = _download_metadata(file_path)
    return FileResponse(str(file_path), media_type=media_type, filename=download_name)
