from __future__ import annotations

import asyncio

from wpcli.config import load_config
from wpcli.tools import ToolRegistry, get_builtin_tools
from wpcli.tools.base import ToolContext
from wpcli.tools.builtins import save_memory, search_memory


def test_read_write_file_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    config.policy.hitl_mode = "never"
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    context = ToolContext(cwd=str(tmp_path), config=config)

    async def run():
        write = registry.get("write_file")
        read = registry.get("read_file")
        assert write and read
        write_result = await write.execute(
            {"path": "hello.txt", "content": "hello\nworld\n"},
            context,
        )
        read_result = await read.execute({"path": "hello.txt"}, context)
        return write_result, read_result

    write_result, read_result = asyncio.run(run())
    assert not write_result.is_error
    assert "1: hello" in read_result.content
    assert "2: world" in read_result.content


def test_builtin_tools_include_memory_recall_and_skill_sedimentation():
    names = {tool.name for tool in get_builtin_tools()}

    assert "search_memory" in names
    assert "save_skill" in names


def test_memory_tools_save_metadata_and_recall_relevant_items(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    config = load_config(project_root=tmp_path)
    context = ToolContext(cwd=str(tmp_path), config=config)

    saved = asyncio.run(
        save_memory(
            {
                "content": "用户偏好用 uv 执行 Python 测试",
                "kind": "preference",
                "importance": 0.9,
            },
            context,
        )
    )
    recalled = asyncio.run(search_memory({"query": "怎么执行测试"}, context))

    assert not saved.is_error
    assert not recalled.is_error
    assert "uv" in recalled.content
    assert "preference" in recalled.content

