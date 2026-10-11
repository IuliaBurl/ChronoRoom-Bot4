"""SQLite storage for ChronoRoom: profiles, group invitations and anonymous chats."""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import uuid

logger = logging.getLogger("chronoroom")

# Chat ids used to be "chat_<low user id>_<high user id>". Those ids travel inside the
# callback_data of the "Reply" button, i.e. they are visible to the recipient's client and
# revealed the sender's Telegram id. They are now random; old ones are migrated.
LEGACY_CHAT_ID = re.compile(r"^chat_(\d+)_(\d+)$")


class Database:
    HAS_BIO = "bio IS NOT NULL AND TRIM(bio) != ''"

    def __init__(self, path: str) -> None:
        self.path = path
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS profiles (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                bio TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS group_requests (
                request_id TEXT PRIMARY KEY,
                creator_id INTEGER NOT NULL,
                selected_ids TEXT NOT NULL,
                group_link TEXT,
                group_message TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS group_responses (
                request_id TEXT NOT NULL,
                user_id INTEGER NOT NULL,
                response TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (request_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS active_chats (
                chat_id TEXT PRIMARY KEY,
                user1_id INTEGER NOT NULL,
                user2_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_active_chats_pair
                ON active_chats (user1_id, user2_id);
            """
        )
        self._ensure_column("group_requests", "group_message", "TEXT")
        with self.conn:
            self.conn.execute(f"DELETE FROM profiles WHERE NOT ({self.HAS_BIO})")
            self.conn.execute(
                "UPDATE active_chats SET chat_id = 'c' || lower(hex(randomblob(8))) "
                "WHERE chat_id GLOB 'chat_[0-9]*_[0-9]*'"
            )
        logger.info("Database ready: %s", self.path)

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        existing = {row["name"] for row in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            self.conn.commit()

    # ------------------------------------------------------------------ profiles

    def upsert_profile(
        self, user_id: int, username: str | None, first_name: str | None, bio: str
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO profiles (user_id, username, first_name, bio)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    bio = excluded.bio
                """,
                (user_id, username, first_name, bio),
            )

    def get_profile(self, user_id: int) -> dict | None:
        row = self.conn.execute(
            f"SELECT * FROM profiles WHERE user_id = ? AND {self.HAS_BIO}", (user_id,)
        ).fetchone()
        return dict(row) if row else None

    def get_profile_ids(self, exclude_user_id: int) -> list[int]:
        rows = self.conn.execute(
            f"SELECT user_id FROM profiles WHERE user_id != ? AND {self.HAS_BIO}",
            (exclude_user_id,),
        ).fetchall()
        return [row["user_id"] for row in rows]

    def get_profiles_by_ids(self, user_ids: list[int]) -> list[dict]:
        if not user_ids:
            return []
        placeholders = ",".join("?" * len(user_ids))
        rows = self.conn.execute(
            f"SELECT * FROM profiles WHERE user_id IN ({placeholders}) AND {self.HAS_BIO}",
            user_ids,
        ).fetchall()
        by_id = {row["user_id"]: dict(row) for row in rows}
        return [by_id[user_id] for user_id in user_ids if user_id in by_id]

    def delete_profile(self, user_id: int) -> bool:
        """Removes the profile and everything that was created or answered by this user
        (chats, group invitations with their link and message, answers to invitations)."""
        with self.conn:
            cursor = self.conn.execute("DELETE FROM profiles WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "DELETE FROM active_chats WHERE user1_id = ? OR user2_id = ?",
                (user_id, user_id),
            )
            self.conn.execute(
                "DELETE FROM group_responses WHERE user_id = ? OR request_id IN "
                "(SELECT request_id FROM group_requests WHERE creator_id = ?)",
                (user_id, user_id),
            )
            self.conn.execute("DELETE FROM group_requests WHERE creator_id = ?", (user_id,))
        return cursor.rowcount > 0

    # ------------------------------------------------------------------ group invitations

    def create_group_request(self, creator_id: int, selected_ids: list[int]) -> str:
        request_id = f"group_{uuid.uuid4().hex[:16]}"
        with self.conn:
            self.conn.execute(
                "INSERT INTO group_requests (request_id, creator_id, selected_ids) VALUES (?, ?, ?)",
                (request_id, creator_id, json.dumps(selected_ids)),
            )
        return request_id

    def get_group_request(self, request_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM group_requests WHERE request_id = ?", (request_id,)
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        data["selected_ids"] = [int(user_id) for user_id in json.loads(data["selected_ids"])]
        return data

    def update_group_request(
        self, request_id: str, group_link: str, group_message: str | None
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE group_requests
                SET group_link = ?, group_message = ?, status = 'sent'
                WHERE request_id = ?
                """,
                (group_link, group_message, request_id),
            )

    def record_group_response(self, request_id: str, user_id: int, response: str) -> bool:
        """Stores the first answer of an invitee. Returns False if they already answered,
        so a double tap (or 'Join' after 'Decline') cannot notify the organizer twice."""
        with self.conn:
            cursor = self.conn.execute(
                "INSERT OR IGNORE INTO group_responses (request_id, user_id, response) VALUES (?, ?, ?)",
                (request_id, user_id, response),
            )
        return cursor.rowcount == 1

    # ------------------------------------------------------------------ anonymous chats

    def create_chat(self, user1_id: int, user2_id: int) -> str:
        """Returns the chat of the pair, creating it if needed. The id is random, never derived
        from user ids."""
        low, high = sorted((int(user1_id), int(user2_id)))
        with self.conn:
            row = self.conn.execute(
                "SELECT chat_id FROM active_chats WHERE user1_id = ? AND user2_id = ?",
                (low, high),
            ).fetchone()
            if row:
                return row["chat_id"]
            chat_id = f"c{uuid.uuid4().hex[:16]}"
            self.conn.execute(
                "INSERT INTO active_chats (chat_id, user1_id, user2_id) VALUES (?, ?, ?)",
                (chat_id, low, high),
            )
        return chat_id

    def get_chat(self, chat_id: str | None) -> dict | None:
        if not chat_id:
            return None
        row = self.conn.execute(
            "SELECT * FROM active_chats WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        if row is None:
            # "Reply" buttons in old messages still carry the legacy id: resolve it by pair
            legacy = LEGACY_CHAT_ID.match(chat_id)
            if legacy:
                low, high = sorted(int(part) for part in legacy.groups())
                row = self.conn.execute(
                    "SELECT * FROM active_chats WHERE user1_id = ? AND user2_id = ?",
                    (low, high),
                ).fetchone()
        return dict(row) if row else None

    def close(self) -> None:
        self.conn.close()
