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
    final_tempo_ratio: float = 1.0

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

    @field_validator("final_tempo_ratio", mode="before")
    @classmethod
    def normalize_final_tempo_ratio(cls, value: Any) -> float:
        return clamp_float(value, 0.75, 1.5, 1.0)


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
