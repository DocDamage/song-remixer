"""
Audio analysis and processing utilities for Song Remixer.
Uses librosa for analysis, sox for time-stretch/pitch-shift, ffmpeg for mixing.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Tuple
import importlib.util

import librosa
import numpy as np

# Krumhansl-Kessler key profiles (standard music-theory key-finding)
MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
STEM_SPLITTER_MODEL = "htdemucs_ft"
STEM_SPLITTER_SEGMENT_SECONDS = 7
DEMUCS_RUNNER_PATH = Path(__file__).with_name("demucs_runner.py")
DEFAULT_MIX_STYLE = "balanced"
MIX_STYLE_PRESETS = {
    "balanced": {
        "beat_gain_bias": 0.0,
        "vocal_gain_bias": 0.0,
        "vocal_presence_bias": 0.0,
        "duck_ratio_bias": 0.0,
        "duck_threshold_db_bias": 0.0,
        "duck_release_bias": 0.0,
        "target_lufs": -14.0,
        "target_lra": 9.0,
        "true_peak": -1.5,
        "limiter_ceiling": 0.95,
    },
    "club": {
        "beat_gain_bias": 1.6,
        "vocal_gain_bias": -0.6,
        "vocal_presence_bias": 0.3,
        "duck_ratio_bias": 0.6,
        "duck_threshold_db_bias": -1.0,
        "duck_release_bias": -20.0,
        "target_lufs": -12.5,
        "target_lra": 8.0,
        "true_peak": -1.2,
        "limiter_ceiling": 0.97,
    },
    "vocal-focus": {
        "beat_gain_bias": -1.4,
        "vocal_gain_bias": 1.6,
        "vocal_presence_bias": 1.2,
        "duck_ratio_bias": 1.0,
        "duck_threshold_db_bias": -1.4,
        "duck_release_bias": 35.0,
        "target_lufs": -15.0,
        "target_lra": 7.0,
        "true_peak": -1.5,
        "limiter_ceiling": 0.94,
    },
    "demo-loud": {
        "beat_gain_bias": 0.8,
        "vocal_gain_bias": 0.8,
        "vocal_presence_bias": 0.5,
        "duck_ratio_bias": 0.8,
        "duck_threshold_db_bias": -0.6,
        "duck_release_bias": -10.0,
        "target_lufs": -11.5,
        "target_lra": 6.5,
        "true_peak": -1.0,
        "limiter_ceiling": 0.98,
    },
}


def detect_bpm(y: np.ndarray, sr: int) -> Tuple[float, np.ndarray]:
    """Detect BPM and beat frame times (in seconds)."""
    tempo, beat_frames = librosa.beat.beat_track(y=y, sr=sr)
    beat_times = librosa.frames_to_time(beat_frames, sr=sr)
    if isinstance(tempo, np.ndarray):
        tempo = float(tempo[0])
    else:
        tempo = float(tempo)
    return tempo, beat_times


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


def detect_key(y: np.ndarray, sr: int) -> Tuple[str, int]:
    """Detect musical key using chromagram + Krumhansl-Schmuckler key profiles."""
    chroma = librosa.feature.chroma_stft(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1)

    if np.sum(chroma_mean) < 1e-6:
        return "Unknown", 0

    best_corr = -np.inf
    best_key = "C major"
    best_semitone = 0

    for semitone in range(12):
        rotated = np.roll(chroma_mean, -semitone)
        major_corr = np.corrcoef(rotated, MAJOR_PROFILE)[0, 1]
        minor_corr = np.corrcoef(rotated, MINOR_PROFILE)[0, 1]

        if major_corr > best_corr:
            best_corr = major_corr
            best_key = f"{NOTE_NAMES[semitone]} major"
            best_semitone = semitone

        if minor_corr > best_corr:
            best_corr = minor_corr
            best_key = f"{NOTE_NAMES[semitone]} minor"
            best_semitone = semitone

    return best_key, best_semitone


def detect_downbeat(y: np.ndarray, sr: int, beat_times: np.ndarray) -> float:
    """
    Estimate downbeat time.
    Uses a simple heuristic: among the first ~8 beats, pick the one with
    the strongest onset strength as the downbeat.
    """
    if len(beat_times) == 0:
        return 0.0

    onset_env = librosa.onset.onset_strength(y=y, sr=sr)
    times = librosa.frames_to_time(np.arange(len(onset_env)), sr=sr)

    n_beats = min(8, len(beat_times))
    onset_at_beats = []
    for i in range(n_beats):
        idx = np.argmin(np.abs(times - beat_times[i]))
        onset_at_beats.append(onset_env[idx])

    strongest_idx = int(np.argmax(onset_at_beats))
    return float(beat_times[strongest_idx])


def semitone_shift(source_semitone: int, target_semitone: int) -> int:
    """Minimal semitone shift from source key to target key."""
    diff = target_semitone - source_semitone
    while diff > 6:
        diff -= 12
    while diff < -6:
        diff += 12
    return diff


def _summarize_command_error(stderr: str) -> str:
    """Extract the most useful final error line from ffmpeg/sox output."""
    lines = [line.strip() for line in stderr.splitlines() if line.strip()]
    if not lines:
        return "Unknown command failure"

    error_markers = ("error", "failed", "invalid", "unable", "could not", "no such")
    for line in reversed(lines):
        lower_line = line.lower()
        if any(marker in lower_line for marker in error_markers):
            return line

    return lines[-1]


def _run_cmd(cmd: list) -> None:
    """Run a subprocess command and raise on failure."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tool_name = Path(cmd[0]).name
        detail = _summarize_command_error(result.stderr)
        raise RuntimeError(f"{tool_name} failed: {detail}")


def _detect_demucs_runtime() -> tuple[str, int]:
    """Prefer CUDA when available; otherwise fall back to CPU-safe defaults."""
    forced_device = os.environ.get("SONG_REMIXER_STEM_DEVICE")
    if forced_device in {"cpu", "cuda"}:
        return forced_device, 2 if forced_device == "cuda" else 1

    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            return "cuda", 2
    except Exception:
        pass

    return "cpu", 1


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, value))


def _db_to_linear(db_value: float) -> float:
    return float(10 ** (db_value / 20.0))


def _resolve_mix_style(mix_style: str | None) -> tuple[str, dict[str, float]]:
    normalized_style = (mix_style or DEFAULT_MIX_STYLE).strip().lower()
    if normalized_style not in MIX_STYLE_PRESETS:
        normalized_style = DEFAULT_MIX_STYLE
    return normalized_style, MIX_STYLE_PRESETS[normalized_style]


def _analyze_mix_profile(input_path: str) -> dict[str, float]:
    y, sr = librosa.load(input_path, sr=22050, mono=True)
    if y.size == 0 or float(np.max(np.abs(y))) < 1e-6:
        raise RuntimeError("Track is silent or unreadable")

    rms = float(np.sqrt(np.mean(np.square(y)) + 1e-12))
    peak = float(np.max(np.abs(y)) + 1e-12)
    rms_db = float(20 * np.log10(rms))
    peak_db = float(20 * np.log10(peak))

    return {
        "rms_db": rms_db,
        "peak_db": peak_db,
        "crest_db": max(0.0, peak_db - rms_db),
        "centroid_hz": float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))),
        "bandwidth_hz": float(np.mean(librosa.feature.spectral_bandwidth(y=y, sr=sr))),
        "zero_crossing_rate": float(np.mean(librosa.feature.zero_crossing_rate(y))),
    }


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


def _build_mix_filter(profile: dict[str, float], track_role: str, mix_style: str | None = None, advanced_mix=None) -> str:
    _, style = _resolve_mix_style(mix_style)

    if track_role == "beat":
        gain_db = _clamp(-18.5 - profile["rms_db"] + style["beat_gain_bias"], -7.5, 5.0)
        gain_db += _advanced_value(advanced_mix, "beat_gain_db", 0.0)
        high_cut_hz = _clamp(18000.0 - max(0.0, profile["centroid_hz"] - 3500.0) * 0.7, 14000.0, 18000.0)
        mud_cut_db = -_clamp((2600.0 - profile["centroid_hz"]) / 350.0, 0.0, 4.0)
        ratio = _clamp(1.8 + profile["crest_db"] / 9.0, 1.8, 3.2)
        threshold = _clamp(_db_to_linear(min(-12.0, profile["rms_db"] + 7.0)), 0.03, 0.25)
        makeup = _clamp(1.0 + max(0.0, -gain_db) / 10.0, 1.0, 1.5)

        return ",".join(
            [
                "highpass=f=30",
                f"lowpass=f={high_cut_hz:.0f}",
                f"equalizer=f=260:t=q:w=1.0:g={mud_cut_db:.1f}",
                f"acompressor=threshold={threshold:.3f}:ratio={ratio:.2f}:attack=15:release=180:makeup={makeup:.2f}",
                f"volume={gain_db:.1f}dB",
            ]
        ) + _build_advanced_eq_filter(advanced_mix)

    gain_db = _clamp(-17.0 - profile["rms_db"] + style["vocal_gain_bias"], -5.5, 8.0)
    gain_db += _advanced_value(advanced_mix, "vocal_gain_db", 0.0)
    highpass_hz = _clamp(90.0 + profile["zero_crossing_rate"] * 120.0, 90.0, 140.0)
    mud_cut_db = -_clamp((2400.0 - profile["centroid_hz"]) / 320.0, 0.0, 4.0)
    presence_boost_db = _clamp((3200.0 - profile["centroid_hz"]) / 360.0 + style["vocal_presence_bias"], 0.0, 5.5)
    air_trim_db = -_clamp((profile["centroid_hz"] - 4200.0) / 650.0, 0.0, 2.5)
    ratio = _clamp(2.8 + profile["crest_db"] / 6.5, 2.8, 4.8)
    threshold = _clamp(_db_to_linear(min(-14.0, profile["rms_db"] + 9.0)), 0.03, 0.22)
    makeup = _clamp(1.3 + max(0.0, gain_db) / 8.0, 1.3, 2.2)

    return ",".join(
        [
            f"highpass=f={highpass_hz:.0f}",
            f"equalizer=f=250:t=q:w=1.1:g={mud_cut_db:.1f}",
            f"equalizer=f=3500:t=q:w=1.1:g={presence_boost_db:.1f}",
            f"equalizer=f=6500:t=q:w=1.0:g={air_trim_db:.1f}",
            f"acompressor=threshold={threshold:.3f}:ratio={ratio:.2f}:attack=5:release=90:makeup={makeup:.2f}",
            f"volume={gain_db:.1f}dB",
        ]
    ) + _build_advanced_eq_filter(advanced_mix)


def _refine_offset_with_onsets(beat_path: str, acapella_path: str, estimated_offset_sec: float, bpm: float) -> float:
    try:
        sample_rate = 22050
        hop_length = 512
        beat_y, _ = librosa.load(beat_path, sr=sample_rate, mono=True, duration=18.0)
        acapella_y, _ = librosa.load(acapella_path, sr=sample_rate, mono=True, duration=18.0)
        if beat_y.size == 0 or acapella_y.size == 0:
            return estimated_offset_sec

        beat_onset = librosa.onset.onset_strength(y=beat_y, sr=sample_rate, hop_length=hop_length)
        acapella_onset = librosa.onset.onset_strength(y=acapella_y, sr=sample_rate, hop_length=hop_length)
        if len(beat_onset) < 8 or len(acapella_onset) < 8:
            return estimated_offset_sec

        estimated_lag = int(round(-estimated_offset_sec * sample_rate / hop_length))
        beat_duration = 60.0 / max(bpm, 1e-6)
        search_window_sec = _clamp(beat_duration * 1.25, 0.18, 0.9)
        search_frames = max(2, int(round(search_window_sec * sample_rate / hop_length)))
        max_compare_frames = int(round(12.0 * sample_rate / hop_length))

        best_lag = estimated_lag
        best_score = -np.inf
        for lag in range(estimated_lag - search_frames, estimated_lag + search_frames + 1):
            beat_start = max(0, lag)
            acapella_start = max(0, -lag)
            overlap = min(len(beat_onset) - beat_start, len(acapella_onset) - acapella_start, max_compare_frames)
            if overlap < 8:
                continue

            beat_segment = beat_onset[beat_start : beat_start + overlap]
            acapella_segment = acapella_onset[acapella_start : acapella_start + overlap]
            beat_segment = beat_segment - np.mean(beat_segment)
            acapella_segment = acapella_segment - np.mean(acapella_segment)

            denominator = float(np.linalg.norm(beat_segment) * np.linalg.norm(acapella_segment))
            if denominator <= 1e-8:
                continue

            score = float(np.dot(beat_segment, acapella_segment) / denominator)
            if score > best_score:
                best_score = score
                best_lag = lag

        return -best_lag * hop_length / sample_rate
    except Exception:
        return estimated_offset_sec


def _render_styled_mix(beat_path: str, aligned_path: str, output_path: str, mix_style: str | None = None, advanced_mix=None) -> str:
    style_name, style = _resolve_mix_style(mix_style)
    beat_profile = _analyze_mix_profile(beat_path)
    vocal_profile = _analyze_mix_profile(aligned_path)

    beat_filter = _build_mix_filter(beat_profile, "beat", style_name, advanced_mix)
    vocal_filter = _build_mix_filter(vocal_profile, "vocal", style_name, advanced_mix)
    duck_threshold_db = (
        _advanced_value(advanced_mix, "compressor_threshold_db", -18.0)
        + vocal_profile["crest_db"] / 3.0
        + style["duck_threshold_db_bias"]
    )
    duck_threshold = _clamp(_db_to_linear(duck_threshold_db), 0.10, 0.25)
    duck_ratio = _clamp(
        _advanced_value(advanced_mix, "compressor_ratio", 2.0)
        + vocal_profile["crest_db"] / 12.0
        + style["duck_ratio_bias"] * 0.4,
        1.2,
        4.0,
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

    filter_complex = (
        f"[0:a]{beat_filter}[beat];"
        f"[1:a]{vocal_filter},asplit=2[vocal_sidechain][vocal_mix];"
        f"[beat][vocal_sidechain]sidechaincompress=threshold={duck_threshold:.3f}:ratio={duck_ratio:.2f}:attack={duck_attack}:release={duck_release}[ducked];"
        f"[ducked][vocal_mix]amix=inputs=2:duration=longest:dropout_transition=3,"
        f"loudnorm=I={style['target_lufs']}:LRA={style['target_lra']}:TP={style['true_peak']},"
        f"alimiter=limit={style['limiter_ceiling']}[aout]"
    )

    _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            beat_path,
            "-i",
            aligned_path,
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            "-ar",
            "44100",
            "-acodec",
            "pcm_s16le",
            output_path,
        ]
    )
    return output_path


def _align_track(acapella_path: str, aligned_path: str, offset_sec: float) -> None:
    offset_ms = int(offset_sec * 1000)

    if offset_ms >= 0:
        _run_cmd(
            [
                "ffmpeg",
                "-y",
                "-i",
                acapella_path,
                "-af",
                f"adelay={offset_ms}|{offset_ms}",
                "-acodec",
                "pcm_s16le",
                aligned_path,
            ]
        )
        return

    trim_sec = abs(offset_sec)
    _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            acapella_path,
            "-ss",
            str(trim_sec),
            "-acodec",
            "pcm_s16le",
            aligned_path,
        ]
    )


def convert_to_wav(input_path: str, output_path: str) -> None:
    """Convert any audio file to 44.1kHz 16-bit WAV using ffmpeg."""
    _run_cmd(
        [
            "ffmpeg",
            "-y",
            "-i",
            input_path,
            "-acodec",
            "pcm_s16le",
            "-ar",
            "44100",
            output_path,
        ]
    )


def split_stems_with_demucs(input_path: str, output_root: str, model: str = STEM_SPLITTER_MODEL) -> dict[str, Path]:
    """Split a full mix into premium stems using fine-tuned Hybrid Transformer Demucs."""
    if importlib.util.find_spec("demucs") is None:
        raise RuntimeError("Demucs is not installed. Run `pip install -r requirements.txt` to enable studio stem splitting.")

    output_dir = Path(output_root)
    output_dir.mkdir(parents=True, exist_ok=True)

    device, shifts = _detect_demucs_runtime()
    _run_cmd(
        [
            sys.executable,
            str(DEMUCS_RUNNER_PATH),
            "--input",
            input_path,
            "--output-root",
            str(output_dir),
            "--model",
            model,
            "--device",
            device,
            "--overlap",
            "0.4",
            "--shifts",
            str(shifts),
            "--segment",
            str(STEM_SPLITTER_SEGMENT_SECONDS),
        ]
    )

    track_dir = output_dir / model / Path(input_path).stem
    stem_files = {stem_path.stem: stem_path for stem_path in sorted(track_dir.glob("*.wav"))}
    if not stem_files:
        raise RuntimeError("Demucs did not produce any stem files")

    return stem_files


def join_vocal_stems(stem_files: dict[str, Path], output_path: str) -> Path:
    """Combine all vocal-related stems into one acapella-ready WAV file."""
    vocal_stems = [stem_path for stem_name, stem_path in stem_files.items() if "vocal" in stem_name.lower()]
    if not vocal_stems:
        raise RuntimeError("No vocal stems were produced")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if len(vocal_stems) == 1:
        shutil.copyfile(vocal_stems[0], output)
        return output

    cmd = ["ffmpeg", "-y"]
    for stem_path in vocal_stems:
        cmd.extend(["-i", str(stem_path)])

    mix_inputs = "".join(f"[{index}:a]" for index in range(len(vocal_stems)))
    filter_complex = (
        f"{mix_inputs}amix=inputs={len(vocal_stems)}:duration=longest:dropout_transition=2,"
        "loudnorm=I=-18:LRA=7:TP=-1.5[aout]"
    )
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            "[aout]",
            "-acodec",
            "pcm_s24le",
            str(output),
        ]
    )
    _run_cmd(cmd)
    return output


def process_acapella(input_path: str, output_path: str, tempo_ratio: float, semitones: float) -> None:
    """Time-stretch and pitch-shift acapella using sox."""
    cents = int(semitones * 100)
    # Ensure input is WAV for sox compatibility
    wav_path = input_path
    cleanup = False
    if not input_path.lower().endswith(".wav"):
        wav_path = output_path.replace(".wav", "_input.wav")
        convert_to_wav(input_path, wav_path)
        cleanup = True

    _run_cmd(["sox", wav_path, output_path, "tempo", str(tempo_ratio), "pitch", str(cents)])

    if cleanup and os.path.exists(wav_path):
        os.remove(wav_path)


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
    """
    Align acapella to beat grid and mix.
    nudge_beats: manual offset in beats (positive = delay acapella).
    Returns path to mixed WAV file.
    """
    beat_duration = 60.0 / bpm
    estimated_offset_sec = beat_downbeat - acapella_downbeat + (nudge_beats * beat_duration)
    offset_sec = _refine_offset_with_onsets(beat_path, acapella_path, estimated_offset_sec, bpm)

    aligned_path = str(Path(output_path).with_suffix("")) + "_aligned.wav"
    _align_track(acapella_path, aligned_path, offset_sec)

    _render_styled_mix(beat_path, aligned_path, output_path, mix_style, advanced_mix)

    if os.path.exists(aligned_path):
        os.remove(aligned_path)

    return output_path


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
    """Align, polish each track independently, then produce a finished auto mix."""
    beat_duration = 60.0 / bpm
    estimated_offset_sec = beat_downbeat - acapella_downbeat + (nudge_beats * beat_duration)
    offset_sec = _refine_offset_with_onsets(beat_path, acapella_path, estimated_offset_sec, bpm)
    aligned_path = str(Path(output_path).with_suffix("")) + "_aligned.wav"

    _align_track(acapella_path, aligned_path, offset_sec)

    try:
        _render_styled_mix(beat_path, aligned_path, output_path, mix_style, advanced_mix)
    finally:
        if os.path.exists(aligned_path):
            os.remove(aligned_path)

    return output_path
