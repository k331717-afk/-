"""Durable review ID index, separate from the customer-facing Notion schema."""
from __future__ import annotations

import base64
import json
import os
import zlib
from pathlib import Path
from typing import Any

import requests

STATE_BRANCH = "review-sync-state"
STATE_PATH = "imweb_daily_report/review_sync_state.json"


def encode_state(state: dict[str, Any]) -> str:
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return json.dumps({"format": "zlib-base64", "data": base64.b64encode(zlib.compress(raw)).decode()})


def decode_state(raw: str) -> dict[str, Any]:
    value = json.loads(raw)
    if value.get("format") == "zlib-base64":
        return json.loads(zlib.decompress(base64.b64decode(value["data"])))
    return value


class ReviewState:
    def __init__(self, database_id: str) -> None:
        self.directory = Path(__file__).resolve().parent
        self.local_path = self.directory / "review_sync_state.json"
        self.repository = os.getenv("GITHUB_REPOSITORY", "")
        self.token = os.getenv("REVIEW_STATE_GITHUB_TOKEN", "")
        self.sha: str | None = None
        self.remote = bool(self.repository and self.token)
        if os.getenv("GITHUB_ACTIONS") == "true" and not self.remote:
            raise RuntimeError("구매평 중복 방지 기록을 저장할 GitHub 토큰이 없습니다.")
        seed = self.directory / "review_index_seed.json"
        self.data = (decode_state(seed.read_text(encoding="utf-8")) if seed.exists() else
                     {"version": 1, "database_id": database_id, "reviews": {}, "pending": {}})
        if self.remote:
            response = self.request("GET", f"/contents/{STATE_PATH}", params={"ref": STATE_BRANCH})
            if response.status_code != 404:
                response.raise_for_status()
                payload = response.json()
                if not payload.get("content"):
                    raise RuntimeError("구매평 동기화 기록을 읽지 못했습니다. 중복 방지를 위해 중단합니다.")
                self.sha = payload["sha"]
                self.data = decode_state(base64.b64decode(payload["content"]).decode("utf-8"))
        elif self.local_path.exists():
            self.data = decode_state(self.local_path.read_text(encoding="utf-8"))
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
        if self.remote:
            branch = self.request("GET", f"/git/ref/heads/{STATE_BRANCH}")
            if branch.status_code == 404:
                commit = os.environ["GITHUB_SHA"]
                self.request("POST", "/git/refs", json={"ref": f"refs/heads/{STATE_BRANCH}", "sha": commit}).raise_for_status()
            else:
                branch.raise_for_status()
            payload: dict[str, Any] = {
                "message": "Save product review sync index",
                "branch": STATE_BRANCH,
                "content": base64.b64encode(content.encode("utf-8")).decode(),
            }
            if self.sha:
                payload["sha"] = self.sha
            result = self.request("PUT", f"/contents/{STATE_PATH}", json=payload)
            result.raise_for_status()
            self.sha = result.json()["content"]["sha"]
        else:
            temporary = self.local_path.with_suffix(".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(self.local_path)

