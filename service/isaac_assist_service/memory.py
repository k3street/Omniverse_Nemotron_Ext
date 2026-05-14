"""Persistent conversation-history store backed by SQLite.

Each message is a (session_id, role, content) row in
``conversation_logs``. The database file lives next to this module so
the path is deterministic regardless of the working directory.
"""
import sqlite3
import json
import os
from typing import List, Dict

class MemoryManager:
    """
    Handles persisting conversation history to a local SQLite database,
    providing Short-Term context across service restarts.
    """
    def __init__(self, db_path: str = "memory.db"):
        self.db_path = os.path.join(os.path.dirname(__file__), db_path)
        self._init_db()

    def _init_db(self):
        """Create the ``conversation_logs`` table if it does not already exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('''
                CREATE TABLE IF NOT EXISTS conversation_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')

    def add_message(self, session_id: str, role: str, content: str):
        """Append a single message to the conversation log.

        Args:
            session_id (str): Opaque session identifier.
            role (str): Message role, e.g. ``"user"`` or ``"assistant"``.
            content (str): Raw message text.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO conversation_logs (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content)
            )

    def get_context(self, session_id: str, limit: int = 20) -> List[Dict]:
        """Retrieve the most recent messages for a session in chronological order.

        Args:
            session_id (str): Session to query.
            limit (int, optional): Maximum number of messages to return. Defaults to 20.

        Returns:
            list[dict]: Messages as ``[{"role": ..., "content": ...}, ...]``,
            oldest first.
        """
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                "SELECT role, content FROM conversation_logs WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                (session_id, limit)
            )
            rows = cursor.fetchall()
            
        # Reverse to get chronological order
        return [{"role": row[0], "content": row[1]} for row in reversed(rows)]

    def clear_session(self, session_id: str):
        """Delete all conversation history for a given session."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM conversation_logs WHERE session_id = ?",
                (session_id,)
            )
