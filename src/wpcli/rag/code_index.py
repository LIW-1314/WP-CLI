from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

TEXT_SUFFIXES = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".java",
    ".kt",
    ".go",
    ".rs",
    ".md",
    ".toml",
    ".yaml",
    ".yml",
    ".json",
    ".xml",
    ".html",
    ".css",
    ".scss",
    ".sql",
    ".sh",
}

SKIP_DIRS = {".git", ".venv", "node_modules", "dist", "build", "target", "__pycache__"}

# BM25 free parameters (standard Okapi defaults).
_BM25_K1 = 1.5
_BM25_B = 0.75

# Tokenization: ASCII words (identifiers, numbers) plus CJK runs split into
# bigrams. Each non-empty source line is one "document".
_WORD_RE = re.compile(r"[a-z0-9_]+")
_CJK_RE = re.compile(r"[㐀-䶿一-鿿]+")


@dataclass(slots=True)
class CodeSearchResult:
    path: str
    line: int
    snippet: str


def _tokenize(text: str) -> list[str]:
    """Split a line into searchable tokens (with multiplicity, for term frequency)."""
    normalized = text.lower()
    tokens: list[str] = []
    for match in _WORD_RE.finditer(normalized):
        tokens.append(match.group(0))
    for match in _CJK_RE.finditer(normalized):
        run = match.group(0)
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[index : index + 2] for index in range(len(run) - 1))
    return tokens


def _idf(document_frequency: int, doc_count: int) -> float:
    """Inverse document frequency with the standard +0.5 smoothing."""
    return math.log(1.0 + (doc_count - document_frequency + 0.5) / (document_frequency + 0.5))


class CodeIndex:
    def __init__(self, root: str | Path, db_path: str | Path | None = None):
        self.root = Path(root).resolve()
        self.db_path = (
            Path(db_path).expanduser() if db_path else self.root / ".wpcli" / "code_index.sqlite3"
        )
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def rebuild(self, path: str | Path | None = None) -> int:
        """Index files under *path* (default: the whole root), returning the doc count."""
        base = self._resolve(path or self.root)
        files = [base] if base.is_file() else list(self._iter_files(base))
        df: dict[str, int] = {}
        doc_count = 0
        total_length = 0
        with self._connect() as conn:
            conn.execute("delete from code_chunks where root = ?", (str(self.root),))
            for file_path in files:
                rel = str(file_path.relative_to(self.root))
                try:
                    lines = file_path.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue
                for line_number, line in enumerate(lines, start=1):
                    stripped = line.strip()
                    if not stripped:
                        continue
                    conn.execute(
                        """
                        insert into code_chunks(root, path, line, content)
                        values (?, ?, ?, ?)
                        """,
                        (str(self.root), rel, line_number, stripped),
                    )
                    tokens = _tokenize(stripped)
                    doc_count += 1
                    total_length += len(tokens)
                    for term in set(tokens):
                        df[term] = df.get(term, 0) + 1

            conn.execute("delete from code_terms where root = ?", (str(self.root),))
            conn.execute("delete from code_stats where root = ?", (str(self.root),))
            if df:
                conn.executemany(
                    "insert into code_terms(root, term, df) values (?, ?, ?)",
                    [(str(self.root), term, count) for term, count in df.items()],
                )
            conn.execute(
                "insert into code_stats(root, doc_count, total_length) values (?, ?, ?)",
                (str(self.root), doc_count, total_length),
            )
            return doc_count

    def search(self, query: str, limit: int = 20) -> list[CodeSearchResult]:
        """Rank source lines by BM25 relevance against *query*.

        Scoring is token-based: a query token matches a document only when the
        tokenizer produces the exact same token for both. Substring-only matches
        (e.g. ``parse`` against ``parse_request``) are excluded; use the ``grep``
        tool for substring/regex search.
        """
        tokens = _tokenize(query)
        if not tokens:
            return []
        query_terms = list(dict.fromkeys(tokens))

        doc_count, total_length = self._load_stats()
        if doc_count == 0:
            return []
        average_length = total_length / doc_count

        df_map = self._load_df(query_terms)
        idf = {term: _idf(df_map.get(term, 0), doc_count) for term in query_terms}

        scored: list[tuple[float, str, int, str]] = []
        for path, line, content in self._fetch_candidates(query_terms):
            doc_tokens = _tokenize(content)
            if not doc_tokens:
                continue
            term_frequency = Counter(doc_tokens)
            doc_length = len(doc_tokens)
            score = 0.0
            for term in query_terms:
                frequency = term_frequency.get(term, 0)
                if not frequency:
                    continue
                denominator = frequency + _BM25_K1 * (
                    1.0 - _BM25_B + _BM25_B * doc_length / average_length
                )
                score += idf[term] * frequency * (_BM25_K1 + 1.0) / denominator
            if score > 0.0:
                scored.append((score, path, line, content))

        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        return [
            CodeSearchResult(path, int(line), content)
            for _score, path, line, content in scored[:limit]
        ]

    def _fetch_candidates(self, terms: list[str]) -> list[tuple[str, int, str]]:
        """Fetch rows containing any query token as a substring.

        This is a superset of true token matches; rows that only match by
        substring are discarded by the zero-score filter in ``search``.
        """
        clauses = " or ".join("lower(content) like ?" for _ in terms)
        params: list[str] = [str(self.root), *[f"%{term}%" for term in terms]]
        with self._connect() as conn:
            return conn.execute(
                f"""
                select path, line, content
                from code_chunks
                where root = ? and ({clauses})
                """,
                params,
            ).fetchall()

    def _load_stats(self) -> tuple[int, int]:
        with self._connect() as conn:
            row = conn.execute(
                "select doc_count, total_length from code_stats where root = ?",
                (str(self.root),),
            ).fetchone()
        if not row:
            return 0, 0
        return int(row[0]), int(row[1])

    def _load_df(self, terms: list[str]) -> dict[str, int]:
        if not terms:
            return {}
        placeholders = ", ".join("?" for _ in terms)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                select term, df from code_terms
                where root = ? and term in ({placeholders})
                """,
                [str(self.root), *terms],
            ).fetchall()
        return {term: int(df) for term, df in rows}

    def _iter_files(self, base: Path):
        for path in base.rglob("*"):
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if path.is_file() and path.suffix.lower() in TEXT_SUFFIXES:
                yield path

    def _resolve(self, value: str | Path) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve()
        resolved.relative_to(self.root)
        return resolved

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                create table if not exists code_chunks (
                    id integer primary key autoincrement,
                    root text not null,
                    path text not null,
                    line integer not null,
                    content text not null
                )
                """
            )
            conn.execute(
                "create index if not exists idx_code_chunks_root_path on code_chunks(root, path)"
            )
            conn.execute(
                """
                create index if not exists idx_code_chunks_root_content
                on code_chunks(root, content)
                """
            )
            conn.execute(
                """
                create table if not exists code_terms (
                    root text not null,
                    term text not null,
                    df integer not null
                )
                """
            )
            conn.execute(
                "create index if not exists idx_code_terms_root_term on code_terms(root, term)"
            )
            conn.execute(
                """
                create table if not exists code_stats (
                    root text primary key,
                    doc_count integer not null,
                    total_length integer not null
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

