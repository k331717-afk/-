"""Durable review ID index, separate from the customer-facing Notion schema."""
from __future__ import annotations

import base64
import json
import os
import io
import zipfile
import hashlib
import zlib
from pathlib import Path
from typing import Any

import requests
from cryptography.fernet import Fernet

STATE_ARTIFACT = "imweb-review-sync-state"


def encode_state(state: dict[str, Any]) -> str:
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return json.dumps({"format": "fernet-zlib-v1", "data": state_cipher().encrypt(zlib.compress(raw)).decode()})


def state_cipher() -> Fernet:
    secret = os.getenv("REVIEW_NOTION_TOKEN", "").strip()
    if not secret:
        raise RuntimeError("구매평 작업 기록을 암호화할 전용 토큰이 없습니다.")
    key = hashlib.sha256(b"concretebread-review-state-v1\0" + secret.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def decode_state(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if value.get("format") == "fernet-zlib-v1":
        return json.loads(zlib.decompress(state_cipher().decrypt(value["data"].encode())))
    if value.get("format") == "zlib-base64":
        return json.loads(zlib.decompress(base64.b64decode(value["data"])))
    return value


class ReviewState:
    def __init__(self, database_id: str) -> None:
        self.directory = Path(__file__).resolve().parent
        self.local_path = self.directory / "review_sync_state.json"
        self.repository = os.getenv("GITHUB_REPOSITORY", "")
        self.token = os.getenv("REVIEW_STATE_GITHUB_TOKEN", "")
        self.remote = bool(self.repository and self.token)
        if os.getenv("GITHUB_ACTIONS") == "true" and not self.remote:
            raise RuntimeError("구매평 작업 기록을 읽을 GitHub 토큰이 없습니다.")
        seed = self.directory / "review_index_seed.json"
        self.data = (decode_state(seed.read_text(encoding="utf-8")) if seed.exists() else
                     {"version": 1, "database_id": database_id, "reviews": {}, "pending": {}})
        if self.local_path.exists():
            self.data = decode_state(self.local_path.read_text(encoding="utf-8"))
        elif self.remote:
            response = self.request("GET", "/actions/artifacts", params={"name": STATE_ARTIFACT, "per_page": 100})
            response.raise_for_status()
            artifacts = [a for a in response.json().get("artifacts", []) if not a.get("expired") and a.get("name") == STATE_ARTIFACT]
            if artifacts:
                latest = max(artifacts, key=lambda a: a["id"])
                archive = self.request("GET", f"/actions/artifacts/{latest['id']}/zip")
                archive.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(archive.content)) as files:
                    names = [n for n in files.namelist() if n.split('/')[-1] == 'review_sync_state.json']
                    if len(names) != 1:
                        raise RuntimeError("구매평 작업 기록 파일을 확정할 수 없습니다.")
                    self.data = decode_state(files.read(names[0]).decode("utf-8"))
        if self.data.get("database_id") != database_id:
            raise RuntimeError("구매평 DB와 동기화 기록의 데이터베이스 ID가 다릅니다.")
        self.data.setdefault("pending", {})

    def request(self, method: str, path: str, **kwargs: Any) -> requests.Response:
        return requests.request(
            method, f"https://api.github.com/repos/{self.repository}{path}",
            headers={"Authorization": f"Bearer {self.token}",
                     "Accept": "application/vnd.github+json",
                     "X-GitHub-Api-Version": "2022-11-28"},
            timeout=60, **kwargs,
        )

    def save(self) -> None:
        content = encode_state(self.data)
        temporary = self.local_path.with_suffix(".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(self.local_path)
