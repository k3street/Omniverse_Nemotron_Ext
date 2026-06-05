"""SQLite FTS5 vector store for RAG document chunks.

The database is written to ``workspace/rag_index.db`` so it survives
service restarts and can be committed to version control for offline use.
BM25 ranking is provided natively by SQLite's FTS5 ``rank`` virtual column.
"""
import sqlite3
import os
import uuid
import datetime
from typing import List, Dict, Any

# Save the index permanently to workspace for portability
WORKSPACE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))), "workspace")
DB_PATH = os.path.join(WORKSPACE_DIR, "rag_index.db")


class FTSStore:
    """FTS5-backed document chunk store with version-scoped BM25 search."""
    def __init__(self):
        """Open (or create) the SQLite database and initialise the FTS5 virtual table."""
        os.makedirs(WORKSPACE_DIR, exist_ok=True)
        self.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_db()

    def _init_db(self):
        """Create the ``document_index`` FTS5 virtual table if absent."""
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
        """Insert one document chunk into the FTS5 index.

        Args:
            source_id (str): Parent source identifier.
            content (str): Text content to index.
            section_path (str): Heading extracted from the document.
            url (str): Canonical URL of the source document.
            version_scope (str): Isaac Sim version, e.g. ``"5.1.0"``.
            trust_tier (int): Numeric trust level (1 = authoritative).
        """
        c = self.conn.cursor()
        c.execute('''
            INSERT INTO document_index (content, source_id, section_path, url, version_scope, trust_tier)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (content, source_id, section_path, url, version_scope, trust_tier))
        self.conn.commit()

    def search(self, query: str, limit: int = 5, version_scope: str = None) -> List[Dict[str, Any]]:
        """Full-text BM25 search over indexed chunks.

        Multi-word queries are transformed to AND logic so all terms must
        appear, improving precision at some recall cost. Non-alphanumeric
        tokens are stripped before query construction to avoid FTS5 syntax
        errors.

        Args:
            query (str): Free-text search string.
            limit (int, optional): Maximum results to return. Defaults to 5.
            version_scope (str, optional): When given, restrict results to
                chunks whose ``version_scope`` matches exactly or equals
                ``"all"``.

        Returns:
            list[dict]: Matched rows as dicts including a ``rank`` key.
            Empty list on no match or invalid FTS query.
        """
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
