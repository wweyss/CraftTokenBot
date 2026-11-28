"""SQLite storage. Existing users and balances are preserved."""
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path


class Database:
    def __init__(self, path: str | Path):
        self.path = str(path)

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as db, db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA foreign_keys = ON")
            yield db

    def init(self):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY, username TEXT,
                    tokens INTEGER DEFAULT 100
                );
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id),
                    amount INTEGER NOT NULL, reason TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS dialogs (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS preferences (
                    user_id INTEGER PRIMARY KEY REFERENCES users(user_id),
                    mode TEXT NOT NULL DEFAULT 'fast', dialog_id INTEGER REFERENCES dialogs(id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY, dialog_id INTEGER NOT NULL REFERENCES dialogs(id),
                    role TEXT NOT NULL, content TEXT NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS ai_attempts (
                    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(user_id),
                    at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS image_trial (
                    id INTEGER PRIMARY KEY CHECK(id=1),
                    user_id INTEGER NOT NULL REFERENCES users(user_id), at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ai_attempt_time ON ai_attempts(at);
                CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id, id);
                CREATE INDEX IF NOT EXISTS dialog_user ON dialogs(user_id, id);
                CREATE INDEX IF NOT EXISTS message_dialog ON messages(dialog_id, id);
            """)
            if "ai_enabled" not in {r["name"] for r in db.execute("PRAGMA table_info(preferences)")}:
                db.execute("ALTER TABLE preferences ADD COLUMN ai_enabled INTEGER NOT NULL DEFAULT 0")
            if "model_slot" not in {r["name"] for r in db.execute("PRAGMA table_info(preferences)")}:
                db.execute("ALTER TABLE preferences ADD COLUMN model_slot TEXT NOT NULL DEFAULT 'primary'")
            if "kind" not in {r["name"] for r in db.execute("PRAGMA table_info(messages)")}:
                db.execute("ALTER TABLE messages ADD COLUMN kind TEXT NOT NULL DEFAULT 'demo'")

    def ensure_user(self, user_id: int, username: str | None) -> int:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            inserted = db.execute(
                "INSERT OR IGNORE INTO users(user_id, username, tokens) VALUES (?, ?, 100)",
                (user_id, username),
            ).rowcount
            if inserted:
                db.execute("INSERT INTO ledger(user_id, amount, reason) VALUES (?, 100, 'Стартовый бонус')", (user_id,))
            else:
                db.execute("UPDATE users SET username=? WHERE user_id=?", (username, user_id))
            db.execute("INSERT OR IGNORE INTO preferences(user_id) VALUES (?)", (user_id,))
            return db.execute("SELECT tokens FROM users WHERE user_id=?", (user_id,)).fetchone()[0]

    def change_balance(self, user_id: int, amount: int, reason: str) -> bool:
        if amount == 0:
            return False
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            updated = db.execute(
                "UPDATE users SET tokens=tokens+? WHERE user_id=? AND tokens+? >= 0",
                (amount, user_id, amount),
            ).rowcount
            if updated:
                db.execute("INSERT INTO ledger(user_id, amount, reason) VALUES (?, ?, ?)", (user_id, amount, reason))
            return bool(updated)

    def ledger(self, user_id: int):
        with self.connect() as db:
            return db.execute("SELECT amount, reason, created_at FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)).fetchall()

    def mode(self, user_id: int):
        with self.connect() as db:
            return db.execute("SELECT mode FROM preferences WHERE user_id=?", (user_id,)).fetchone()[0]

    def set_mode(self, user_id: int, mode: str):
        if mode not in ("fast", "creative"):
            raise ValueError("Unknown mode")
        with self.connect() as db:
            db.execute("UPDATE preferences SET mode=? WHERE user_id=?", (mode, user_id))

    def new_dialog(self, user_id: int):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            dialog = db.execute("INSERT INTO dialogs(user_id) VALUES (?)", (user_id,)).lastrowid
            db.execute("UPDATE preferences SET dialog_id=? WHERE user_id=?", (dialog, user_id))
            return dialog

    def save_exchange(self, user_id: int, prompt: str, answer: str, *, kind: str = "demo"):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            dialog = db.execute("SELECT dialog_id FROM preferences WHERE user_id=?", (user_id,)).fetchone()[0]
            if dialog is None:
                dialog = db.execute("INSERT INTO dialogs(user_id) VALUES (?)", (user_id,)).lastrowid
                db.execute("UPDATE preferences SET dialog_id=? WHERE user_id=?", (dialog, user_id))
            db.executemany("INSERT INTO messages(dialog_id, role, content, kind) VALUES (?, ?, ?, ?)", [
                (dialog, "user", prompt, kind), (dialog, "assistant", answer, kind),
            ])

    def recent_prompts(self, user_id: int):
        with self.connect() as db:
            return db.execute("""
                SELECT m.content, m.created_at FROM messages m
                JOIN dialogs d ON d.id=m.dialog_id
                WHERE d.user_id=? AND m.role='user'
                ORDER BY m.id DESC LIMIT 10
            """, (user_id,)).fetchall()

    def clear_history(self, user_id: int):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE preferences SET dialog_id=NULL WHERE user_id=?", (user_id,))
            db.execute("DELETE FROM messages WHERE dialog_id IN (SELECT id FROM dialogs WHERE user_id=?)", (user_id,))
            db.execute("DELETE FROM dialogs WHERE user_id=?", (user_id,))

    def ai_enabled(self, user_id: int) -> bool:
        with self.connect() as db:
            return bool(db.execute("SELECT ai_enabled FROM preferences WHERE user_id=?", (user_id,)).fetchone()[0])

    def set_ai_enabled(self, user_id: int, enabled: bool):
        with self.connect() as db:
            db.execute("UPDATE preferences SET ai_enabled=? WHERE user_id=?", (int(enabled), user_id))

    def model_slot(self, user_id: int) -> str:
        with self.connect() as db:
            return db.execute("SELECT model_slot FROM preferences WHERE user_id=?", (user_id,)).fetchone()[0]

    def select_model(self, user_id: int, slot: str) -> bool:
        """A different provider needs fresh consent and an isolated new dialog."""
        if slot not in ("primary", "gpt"):
            raise ValueError("Unknown model slot")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT model_slot FROM preferences WHERE user_id=?", (user_id,)).fetchone()[0] == slot:
                return False
            dialog = db.execute("INSERT INTO dialogs(user_id) VALUES (?)", (user_id,)).lastrowid
            db.execute("UPDATE preferences SET model_slot=?, dialog_id=?, ai_enabled=0 WHERE user_id=?", (slot, dialog, user_id))
            return True

    def ai_context(self, user_id: int):
        with self.connect() as db:
            rows = db.execute("""
                SELECT role, content FROM messages
                WHERE dialog_id=(SELECT dialog_id FROM preferences WHERE user_id=?)
                  AND kind='ai'
                ORDER BY id DESC LIMIT 6
            """, (user_id,)).fetchall()
        return [{"role": row["role"], "content": row["content"][:3000]} for row in reversed(rows)]

    def reserve_ai_attempt(self, user_id: int, *, total_limit: int = 40, personal_limit: int = 10) -> bool:
        """Count attempts, including failures; rolling 24-hour and minute limits."""
        now = int(time.time())
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM ai_attempts WHERE at<=?", (now-86400,))
            total, personal, minute = db.execute("""
                SELECT COUNT(*), COUNT(CASE WHEN user_id=? THEN 1 END),
                       COUNT(CASE WHEN at>? THEN 1 END) FROM ai_attempts
            """, (user_id, now-60)).fetchone()
            if total >= total_limit or personal >= personal_limit or minute >= 15:
                return False
            db.execute("INSERT INTO ai_attempts(user_id, at) VALUES (?, ?)", (user_id, now))
            return True

    def image_trial_used(self) -> bool:
        with self.connect() as db:
            return db.execute("SELECT 1 FROM image_trial WHERE id=1").fetchone() is not None

    def reserve_image_trial(self, user_id: int) -> bool:
        """One paid image attempt per test database, including failures."""
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            return bool(db.execute("INSERT OR IGNORE INTO image_trial(id, user_id, at) VALUES (1, ?, ?)", (user_id, int(time.time()))).rowcount)
