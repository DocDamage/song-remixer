from __future__ import annotations

import time
from typing import Any
import threading

from jobs import utc_now_iso


class InProcessAnalysisSessionStore:
    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self.lock = threading.RLock()
        self.sessions: dict[str, dict[str, Any]] = {}

    def store(self, owner_id: str, analysis: dict[str, Any]) -> None:
        timestamp = utc_now_iso()
        with self.lock:
            self.sessions[owner_id] = {
                "owner_id": owner_id,
                "analysis": dict(analysis),
                "created_at": timestamp,
                "updated_at": timestamp,
                "expires_at": time.time() + self.ttl_seconds,
            }

    def update_settings(self, owner_id: str, manual_mix: dict[str, Any]) -> None:
        timestamp = utc_now_iso()
        next_expiry = time.time() + self.ttl_seconds
        with self.lock:
            session = self.sessions.get(owner_id)
            if session is None:
                raise KeyError(owner_id)
            session["analysis"]["manual_mix"] = dict(manual_mix)
            session["updated_at"] = timestamp
            session["expires_at"] = next_expiry

    def latest_owner_id(self) -> str:
        with self.lock:
            if not self.sessions:
                raise KeyError("no sessions")
            latest = max(self.sessions.values(), key=lambda s: s["updated_at"])
            return latest["owner_id"]

    def snapshot(self, owner_id: str) -> dict[str, Any]:
        with self.lock:
            session = self.sessions.get(owner_id)
            if session is None:
                raise KeyError(owner_id)
            return dict(session)

    def clear(self, owner_id: str) -> None:
        with self.lock:
            if self.sessions.pop(owner_id, None) is None:
                raise KeyError(owner_id)

    def remove_expired(self, now: float, valid_artifact_names: set[str]) -> list[str]:
        expired: list[str] = []
        with self.lock:
            for owner_id, session in list(self.sessions.items()):
                analysis = session.get("analysis") if isinstance(session, dict) else None
                beat = analysis.get("beat") if isinstance(analysis, dict) else None
                acapella = analysis.get("acapella") if isinstance(analysis, dict) else None
                beat_file_id = beat.get("file_id") if isinstance(beat, dict) else None
                acapella_file_id = acapella.get("file_id") if isinstance(acapella, dict) else None
                if (
                    session.get("expires_at") is None
                    or session["expires_at"] <= now
                    or beat_file_id not in valid_artifact_names
                    or acapella_file_id not in valid_artifact_names
                ):
                    expired.append(owner_id)
                    self.sessions.pop(owner_id, None)
        return expired

    def all_items(self) -> list[tuple[str, dict[str, Any]]]:
        with self.lock:
            return list(self.sessions.items())

    def reset(self) -> None:
        with self.lock:
            self.sessions.clear()
