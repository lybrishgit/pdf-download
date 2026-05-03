"""記錄各期刊上次抓到哪一期，避免重複抓取。

State file 格式（JSON）：
{
  "journals": {
    "nejm": {
      "last_issue_id": "nejm:vol392:iss18",
      "last_publication_date": "2026-04-30",
      "last_fetched_at": "2026-05-03T14:21:00"
    },
    ...
  }
}
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional


class State:
    def __init__(self, path: Path):
        self.path = path
        self.data = self._load()

    def _load(self) -> dict:
        if not self.path.exists():
            return {"journals": {}}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {"journals": {}}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def last_issue_id(self, slug: str) -> Optional[str]:
        return self.data["journals"].get(slug, {}).get("last_issue_id")

    def is_already_fetched(self, slug: str, issue_id: str) -> bool:
        return self.last_issue_id(slug) == issue_id

    def record_fetch(self, slug: str, issue_id: str, publication_date: str) -> None:
        self.data["journals"][slug] = {
            "last_issue_id": issue_id,
            "last_publication_date": publication_date,
            "last_fetched_at": datetime.now().isoformat(timespec="seconds"),
        }
