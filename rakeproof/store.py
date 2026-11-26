"""Inspection cards retain the exact uploaded pixels and one adopted set of settings."""

import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.exceptions import NotFound

from image_lab.validation import Conflict, Invalid, integer

from .geometry import default_settings, validate_settings


class Cards:
    def __init__(self, data):
        root = Path(data)
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / "inspection.sqlite3"
        with self.connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS cards (
                    id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL CHECK (revision >= 1),
                    updated TEXT NOT NULL,
                    metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS photos (
                    card_id TEXT NOT NULL REFERENCES cards(id) ON DELETE CASCADE,
                    side TEXT NOT NULL CHECK (side IN ('left', 'right')),
                    content BLOB NOT NULL,
                    PRIMARY KEY (card_id, side)
                );
            """)

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _record(row):
        if row is None:
            raise NotFound("没有这张核验卡")
        return {"id": row["id"], "revision": row["revision"], "updated": row["updated"], **json.loads(row["metadata"])}

    @staticmethod
    def _check(key):
        if not re.fullmatch(r"[0-9a-f]{32}", key):
            raise NotFound("没有这张核验卡")

    def create(self, name, left, right, settings=None, synthetic=False):
        label = str(name or "").strip()[:80] or "未命名斜照小样"
        left_name, left_size, left_data = left
        right_name, right_size, right_data = right
        metadata = {
            "name": label, "left_name": left_name, "right_name": right_name,
            "left_size": left_size, "right_size": right_size,
            "settings": validate_settings(settings or default_settings(left_size), left_size, right_size),
            "synthetic": synthetic,
        }
        key = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT count(*) FROM cards").fetchone()[0] >= 80:
                raise Invalid("核验卡已达 80 张上限，请将新试样放在另一数据目录")
            db.execute("INSERT INTO cards VALUES (?, 1, ?, ?)", (key, now, json.dumps(metadata, ensure_ascii=False)))
            db.executemany("INSERT INTO photos VALUES (?, ?, ?)", ((key, "left", left_data), (key, "right", right_data)))
        return self.read(key)

    def list(self):
        with self.connection() as db:
            return [self._record(row) for row in db.execute("SELECT * FROM cards ORDER BY updated DESC, id ASC")]

    def read(self, key):
        self._check(key)
        with self.connection() as db:
            return self._record(db.execute("SELECT * FROM cards WHERE id=?", (key,)).fetchone())

    def photos(self, key, sides=("left", "right")):
        self._check(key)
        with self.connection() as db:
            photos = {}
            for side in sides:
                row = db.execute("SELECT content FROM photos WHERE card_id=? AND side=?", (key, side)).fetchone()
                if row is None:
                    raise NotFound("没有这张原照")
                photos[side] = row["content"]
            return photos

    def save(self, key, request):
        self._check(key)
        version = integer(request.get("revision"), "核验卡版本", 1)
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            card = self._record(db.execute("SELECT * FROM cards WHERE id=?", (key,)).fetchone())
            if version != card["revision"]:
                raise Conflict("这张卡已在另一窗口更新，请重新打开")
            changes = validate_settings(request, card["left_size"], card["right_size"])
            metadata = {field: value for field, value in card.items() if field not in {"id", "revision", "updated"}}
            metadata["settings"] = changes
            now = datetime.now(timezone.utc).isoformat()
            db.execute(
                "UPDATE cards SET revision=revision+1, updated=?, metadata=? WHERE id=?",
                (now, json.dumps(metadata, ensure_ascii=False), key),
            )
            return self._record(db.execute("SELECT * FROM cards WHERE id=?", (key,)).fetchone())
