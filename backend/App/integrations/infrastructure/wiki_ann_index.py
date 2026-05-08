from __future__ import annotations

import json
import logging
import math
import re
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")


@dataclass(frozen=True)
class WikiDocument:
    relative_path: str
    title: str
    body: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_PATTERN.findall(text or "")]


def _doc_vector(tokens: list[str]) -> dict[str, float]:
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = float(len(tokens))
    return {term: count / total for term, count in counts.items()}


def _idf_table(docs: Iterable[list[str]]) -> dict[str, float]:
    document_count = 0
    df_counter: Counter[str] = Counter()
    for tokens in docs:
        document_count += 1
        seen = set(tokens)
        for term in seen:
            df_counter[term] += 1
    if document_count == 0:
        return {}
    return {
        term: math.log((1 + document_count) / (1 + freq)) + 1
        for term, freq in df_counter.items()
    }


def _scaled(vector: dict[str, float], idf: dict[str, float]) -> dict[str, float]:
    scaled: dict[str, float] = {}
    for term, weight in vector.items():
        scaled[term] = weight * idf.get(term, 1.0)
    return scaled


def _cosine(left: dict[str, float], right: dict[str, float]) -> float:
    if not left or not right:
        return 0.0
    dot = 0.0
    for term, weight in left.items():
        if term in right:
            dot += weight * right[term]
    norm_left = math.sqrt(sum(value * value for value in left.values()))
    norm_right = math.sqrt(sum(value * value for value in right.values()))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return dot / (norm_left * norm_right)


def collect_documents(wiki_root: Path) -> list[WikiDocument]:
    if not wiki_root.is_dir():
        return []
    documents: list[WikiDocument] = []
    for path in sorted(wiki_root.rglob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        relative = path.relative_to(wiki_root).as_posix()
        first_heading_match = re.search(r"^#\s+(.+?)$", text, re.MULTILINE)
        title = first_heading_match.group(1).strip() if first_heading_match else relative
        documents.append(WikiDocument(relative_path=relative, title=title, body=text))
    return documents


def build_index(wiki_root: Path) -> dict[str, Any]:
    documents = collect_documents(wiki_root)
    token_lists = [_tokenize(doc.body) for doc in documents]
    idf = _idf_table(token_lists)
    vectors: list[dict[str, float]] = [
        _scaled(_doc_vector(tokens), idf) for tokens in token_lists
    ]
    return {
        "documents": [doc.to_dict() for doc in documents],
        "vectors": vectors,
        "idf": idf,
    }


def save_index(wiki_root: Path, target_path: Path) -> Path:
    index = build_index(wiki_root)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    return target_path


def load_index(target_path: Path) -> dict[str, Any] | None:
    if not target_path.is_file():
        return None
    try:
        return json.loads(target_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.debug("wiki_ann_index: load failed: %s", exc)
        return None


def search(
    index: dict[str, Any],
    query: str,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    if not index:
        return []
    documents = index.get("documents") or []
    vectors = index.get("vectors") or []
    idf = index.get("idf") or {}
    if not documents or not vectors:
        return []
    query_vector = _scaled(_doc_vector(_tokenize(query)), idf)
    if not query_vector:
        return []
    scored: list[tuple[float, dict[str, Any]]] = []
    for doc, vector in zip(documents, vectors):
        score = _cosine(query_vector, vector)
        if score <= 0.0:
            continue
        scored.append((score, doc))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {**document, "score": score}
        for score, document in scored[:top_k]
    ]
