from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Optional
import threading


class InProcessArtifactStore:
    def __init__(self, ttl_seconds: int):
        self.ttl_seconds = ttl_seconds
        self.lock = threading.RLock()
        self.artifacts: dict[str, dict[str, Any]] = {}

    def register(self, path: Path, owner_id: str, artifact_kind: str) -> None:
        if not path.exists():
            return
        with self.lock:
            self.artifacts[path.name] = {
                "path": path,
                "owner_id": owner_id,
                "artifact_kind": artifact_kind,
                "expires_at": time.time() + self.ttl_seconds,
            }

    def metadata(self, file_name: str) -> Optional[dict[str, Any]]:
        with self.lock:
            meta = self.artifacts.get(file_name)
            if meta is None:
                return None
            return dict(meta)

    def touch_owner(self, owner_id: str) -> bool:
        next_expiry = time.time() + self.ttl_seconds
        changed = False
        with self.lock:
            for artifact in self.artifacts.values():
                if artifact["owner_id"] == owner_id:
                    artifact["expires_at"] = next_expiry
                    changed = True
        return changed

    def remove_by_owner(self, owner_id: str) -> list[Path]:
        removed: list[Path] = []
        with self.lock:
            for file_name, meta in list(self.artifacts.items()):
                if meta["owner_id"] == owner_id:
                    removed.append(meta["path"])
                    self.artifacts.pop(file_name, None)
        return removed

    def remove_expired(self, now: float) -> list[Path]:
        expired: list[Path] = []
        with self.lock:
            for file_name, meta in list(self.artifacts.items()):
                if meta["expires_at"] <= now:
                    expired.append(meta["path"])
                    self.artifacts.pop(file_name, None)
        return expired

    def all_items(self) -> list[tuple[str, dict[str, Any]]]:
        with self.lock:
            return list(self.artifacts.items())

    def values(self) -> list[dict[str, Any]]:
        with self.lock:
            return list(self.artifacts.values())

    def clear(self) -> None:
        with self.lock:
            self.artifacts.clear()

    def __contains__(self, file_name: str) -> bool:
        with self.lock:
            return file_name in self.artifacts
