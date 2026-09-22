"""Durable record of what has been posted.

The state file is the memory of the account: it prevents duplicate posts for a
slot, keeps scene and pillar rotation from repeating itself, and enforces the
daily post cap. It is a plain JSON file so it can be committed back to the repo
by a scheduled job and read by a human.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


@dataclass
class PostRecord:
    date: str
    slot: int
    pillar: str
    scene: str
    theme: str
    caption: str
    media_id: str = ""
    permalink: str = ""
    media_url: str = ""
    published_at: str = ""
    dry_run: bool = False

    @property
    def key(self) -> str:
        return f"{self.date}#{self.slot}"


@dataclass
class State:
    path: Path
    version: int = SCHEMA_VERSION
    posts: list[PostRecord] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> "State":
        target = Path(path)
        if not target.is_file():
            return cls(path=target)
        try:
            raw = json.loads(target.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{target}: state file is not valid JSON: {exc}") from exc

        version = int(raw.get("version", SCHEMA_VERSION))
        if version > SCHEMA_VERSION:
            raise ValueError(
                f"{target}: state file version {version} is newer than this code "
                f"understands ({SCHEMA_VERSION}); upgrade the package"
            )
        posts = [PostRecord(**item) for item in raw.get("posts", [])]
        return cls(path=target, version=version, posts=posts)

    def save(self) -> None:
        """Write atomically so an interrupted run cannot truncate the history."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
            "posts": [asdict(p) for p in self.posts],
        }
        blob = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
        handle, tmp_name = tempfile.mkstemp(
            dir=str(self.path.parent), prefix=".state-", suffix=".json"
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    # -- queries ---------------------------------------------------------

    def has_slot(self, date: str, slot: int) -> bool:
        return any(p.date == date and p.slot == slot for p in self.posts)

    def posts_on(self, date: str) -> list[PostRecord]:
        return [p for p in self.posts if p.date == date]

    def recent(self, count: int) -> list[PostRecord]:
        """The most recent `count` posts, oldest first."""
        if count <= 0:
            return []
        return sorted(self.posts, key=lambda p: (p.date, p.slot))[-count:]

    def recent_pillars(self, count: int) -> list[str]:
        return [p.pillar for p in self.recent(count)]

    def recent_scenes(self, count: int) -> list[str]:
        return [p.scene for p in self.recent(count)]

    def record(self, post: PostRecord) -> None:
        self.posts = [p for p in self.posts if p.key != post.key]
        self.posts.append(post)
        self.posts.sort(key=lambda p: (p.date, p.slot))

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "posts": [asdict(p) for p in self.posts]}
