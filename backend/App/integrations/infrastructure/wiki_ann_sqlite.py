from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any

from backend.App.integrations.infrastructure.wiki_ann_index import (
    WikiDocument,
    _cosine,
    _doc_vector,
    _idf_table,
    _scaled,
    _tokenize,
    collect_documents,
)

logger = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS wiki_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    relative_path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    body_hash TEXT NOT NULL,
    vector_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS wiki_documents_path_idx
    ON wiki_documents(relative_path);
"""


def _connect(target_path: Path) -> sqlite3.Connection:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(target_path))
    connection.executescript(_SCHEMA)
    return connection


def _store_idf(connection: sqlite3.Connection, idf: dict[str, float]) -> None:
    connection.execute(
        "INSERT OR REPLACE INTO wiki_meta(key, value) VALUES (?, ?)",
        ("idf_json", json.dumps(idf, ensure_ascii=False)),
    )


def _load_idf(connection: sqlite3.Connection) -> dict[str, float]:
    cursor = connection.execute(
        "SELECT value FROM wiki_meta WHERE key = ?", ("idf_json",),
    )
    row = cursor.fetchone()
    if not row:
        return {}
    try:
        decoded = json.loads(row[0])
    except json.JSONDecodeError:
        return {}
    if not isinstance(decoded, dict):
        return {}
    return {str(term): float(weight) for term, weight in decoded.items()}


def _hash_body(body: str) -> str:
    import hashlib

    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()


def build_sqlite_index(wiki_root: Path, target_path: Path) -> Path:
    documents = collect_documents(wiki_root)
    token_lists = [_tokenize(doc.body) for doc in documents]
    idf = _idf_table(token_lists)
    connection = _connect(target_path)
    try:
        connection.execute("DELETE FROM wiki_documents")
        _store_idf(connection, idf)
        for doc, tokens in zip(documents, token_lists):
            vector = _scaled(_doc_vector(tokens), idf)
            connection.execute(
                """
                INSERT INTO wiki_documents
                    (relative_path, title, body, body_hash, vector_json, updated_at)
                VALUES (?, ?, ?, ?, ?, strftime('%s','now'))
                """,
                (
                    doc.relative_path,
                    doc.title,
                    doc.body,
                    _hash_body(doc.body),
                    json.dumps(vector, ensure_ascii=False),
                ),
            )
        connection.commit()
    finally:
        connection.close()
    return target_path


def update_sqlite_index(wiki_root: Path, target_path: Path) -> dict[str, int]:
    documents = collect_documents(wiki_root)
    token_lists = [_tokenize(doc.body) for doc in documents]
    idf = _idf_table(token_lists)
    connection = _connect(target_path)
    added = 0
    updated = 0
    removed = 0
    try:
        existing_rows = connection.execute(
            "SELECT relative_path, body_hash FROM wiki_documents",
        ).fetchall()
        existing_map = {row[0]: row[1] for row in existing_rows}
        seen_paths: set[str] = set()
        for doc, tokens in zip(documents, token_lists):
            seen_paths.add(doc.relative_path)
            body_hash = _hash_body(doc.body)
            vector = _scaled(_doc_vector(tokens), idf)
            vector_json = json.dumps(vector, ensure_ascii=False)
            previous_hash = existing_map.get(doc.relative_path)
            if previous_hash == body_hash:
                connection.execute(
                    "UPDATE wiki_documents SET vector_json = ? WHERE relative_path = ?",
                    (vector_json, doc.relative_path),
                )
                continue
            if previous_hash is None:
                connection.execute(
                    """
                    INSERT INTO wiki_documents
                        (relative_path, title, body, body_hash, vector_json, updated_at)
                    VALUES (?, ?, ?, ?, ?, strftime('%s','now'))
                    """,
                    (
                        doc.relative_path,
                        doc.title,
                        doc.body,
                        body_hash,
                        vector_json,
                    ),
                )
                added += 1
            else:
                connection.execute(
                    """
                    UPDATE wiki_documents
                    SET title = ?, body = ?, body_hash = ?, vector_json = ?,
                        updated_at = strftime('%s','now')
                    WHERE relative_path = ?
                    """,
                    (
                        doc.title,
                        doc.body,
                        body_hash,
                        vector_json,
                        doc.relative_path,
                    ),
                )
                updated += 1
        for relative_path in existing_map:
            if relative_path in seen_paths:
                continue
            connection.execute(
                "DELETE FROM wiki_documents WHERE relative_path = ?",
                (relative_path,),
            )
            removed += 1
        _store_idf(connection, idf)
        connection.commit()
    finally:
        connection.close()
    return {"added": added, "updated": updated, "removed": removed}


def search_sqlite(
    target_path: Path,
    query: str,
    *,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    if not target_path.is_file():
        return []
    connection = _connect(target_path)
    try:
        idf = _load_idf(connection)
        query_vector = _scaled(_doc_vector(_tokenize(query)), idf)
        if not query_vector:
            return []
        rows = connection.execute(
            "SELECT relative_path, title, body, vector_json FROM wiki_documents",
        ).fetchall()
    finally:
        connection.close()
    scored: list[tuple[float, dict[str, Any]]] = []
    for relative_path, title, body, vector_json in rows:
        try:
            vector = json.loads(vector_json)
        except json.JSONDecodeError:
            continue
        if not isinstance(vector, dict):
            continue
        cleaned: dict[str, float] = {}
        for term, weight in vector.items():
            try:
                cleaned[str(term)] = float(weight)
            except (TypeError, ValueError):
                continue
        score = _cosine(query_vector, cleaned)
        if score <= 0.0:
            continue
        scored.append(
            (
                score,
                {
                    "relative_path": relative_path,
                    "title": title,
                    "body": body,
                },
            )
        )
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {**document, "score": score}
        for score, document in scored[:top_k]
    ]


def index_stats(target_path: Path) -> dict[str, int]:
    if not target_path.is_file():
        return {"documents": 0, "terms": 0}
    connection = _connect(target_path)
    try:
        documents_count = connection.execute(
            "SELECT COUNT(*) FROM wiki_documents",
        ).fetchone()[0]
        idf = _load_idf(connection)
    finally:
        connection.close()
    return {"documents": int(documents_count or 0), "terms": len(idf)}


def documents_from_sqlite(target_path: Path) -> list[WikiDocument]:
    if not target_path.is_file():
        return []
    connection = _connect(target_path)
    try:
        rows = connection.execute(
            "SELECT relative_path, title, body FROM wiki_documents ORDER BY relative_path"
        ).fetchall()
    finally:
        connection.close()
    return [
        WikiDocument(relative_path=row[0], title=row[1], body=row[2])
        for row in rows
    ]


__all__ = (
    "build_sqlite_index",
    "update_sqlite_index",
    "search_sqlite",
    "index_stats",
    "documents_from_sqlite",
)
