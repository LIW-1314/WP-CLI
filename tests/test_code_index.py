from __future__ import annotations

from wpcli.rag import CodeIndex


def _index_with(tmp_path, files: dict[str, str]) -> CodeIndex:
    root = tmp_path / "repo"
    root.mkdir()
    for rel, content in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    index = CodeIndex(root, db_path=tmp_path / "idx.sqlite3")
    index.rebuild()
    return index


def test_search_returns_empty_before_index(tmp_path):
    index = CodeIndex(tmp_path, db_path=tmp_path / "idx.sqlite3")
    assert index.search("anything") == []


def test_bm25_ranks_higher_term_frequency_first(tmp_path):
    index = _index_with(
        tmp_path,
        {
            "a.py": "def foo():\n    return request\n",
            "b.py": "def bar():\n    return request request request\n",
        },
    )

    results = index.search("request", limit=10)

    assert results
    # b.py has a higher term frequency for "request", so BM25 ranks it first.
    assert results[0].path == "b.py"
    assert all("request" in result.snippet.lower() for result in results)


def test_search_is_token_based_not_substring(tmp_path):
    index = _index_with(tmp_path, {"a.py": "def parse_request():\n    return 1\n"})

    # "parse" is not a token of "parse_request", so it must not match.
    assert index.search("parse") == []
    # The full token still matches.
    assert index.search("parse_request")


def test_cjk_bigram_matching(tmp_path):
    index = _index_with(tmp_path, {"a.py": "# 处理请求并返回结果\nreturn 1\n"})

    results = index.search("请求")
    assert results
    assert "处理请求" in results[0].snippet

