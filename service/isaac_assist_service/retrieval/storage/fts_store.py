import sqlite3
import os
import uuid
import datetime
from typing import List, Dict, Any

# Save the index permanently to workspace for portability
WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "workspace")
DB_PATH = os.path.join(WORKSPACE_DIR, "rag_index.db")

class FTSStore:
    def __init__(self):
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        c = self.conn.cursor()
        c.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS document_index USING fts5(
                content,
                source_id UNINDEXED,
                section_path UNINDEXED,
                url UNINDEXED,
                version_scope UNINDEXED,
                trust_tier UNINDEXED
            );
        ''')
        self.conn.commit()

    def insert_chunk(self, source_id: str, content: str, section_path: str, url: str, version_scope: str, trust_tier: int):
        c = self.conn.cursor()
        c.execute('''
            INSERT INTO document_index (content, source_id, section_path, url, version_scope, trust_tier)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (content, source_id, section_path, url, version_scope, trust_tier))
        self.conn.commit()

    def count_chunks(self, source_id: str = None) -> int:
        """Return the number of indexed chunks, optionally filtered by source_id."""
        c = self.conn.cursor()
        if source_id:
            # FTS5 tables don't support WHERE on UNINDEXED columns in normal COUNT,
            # but we can use a shadow-table trick with content= or just iterate.
            # Safest approach: use a metadata table or content scan.
            c.execute(
                "SELECT COUNT(*) FROM document_index WHERE source_id = ?",
                (source_id,),
            )
        else:
            c.execute("SELECT COUNT(*) FROM document_index")
        row = c.fetchone()
        return row[0] if row else 0

    def delete_source(self, source_id: str) -> int:
        """Delete all chunks for a source. Returns count deleted."""
        c = self.conn.cursor()
        c.execute("DELETE FROM document_index WHERE source_id = ?", (source_id,))
        deleted = c.rowcount
        self.conn.commit()
        return deleted

    def search(self, query: str, limit: int = 5, version_scope: str = None) -> List[Dict[str, Any]]:
        c = self.conn.cursor()
        # Simple BM25 ranking built into SQLite FTS5 extension.
        # We replace spaces with AND to enforce all words matching for higher quality MVP results.
        formatted_query = " AND ".join([word for word in query.split() if word.isalnum()])
        if not formatted_query:
            return []
            
        try:
            if version_scope:
                c.execute('''
                    SELECT *, rank
                    FROM document_index
                    WHERE document_index MATCH ?
                      AND version_scope IN (?, 'all')
                    ORDER BY rank
                    LIMIT ?
                ''', (formatted_query, version_scope, limit))
            else:
                c.execute('''
                    SELECT *, rank
                    FROM document_index
                    WHERE document_index MATCH ?
                    ORDER BY rank
                    LIMIT ?
                ''', (formatted_query, limit))
            
            return [dict(row) for row in c.fetchall()]
        except sqlite3.OperationalError:
            # Query might be invalid syntax for FTS
            return []
