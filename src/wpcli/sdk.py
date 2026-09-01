from __future__ import annotations

from pathlib import Path

from wpcli.agent import QueryEngine
from wpcli.config import load_config
from wpcli.llm import create_llm_client
from wpcli.tools import ToolRegistry, get_builtin_tools


def create_default_engine(cwd: str | None = None) -> QueryEngine:
    root = str(Path(cwd or ".").resolve())
    config = load_config(project_root=root)
    client = create_llm_client(config.llm)
    registry = ToolRegistry()
    registry.register_all(get_builtin_tools())
    return QueryEngine(llm_client=client, tool_registry=registry, config=config, cwd=root)


__all__ = ["QueryEngine", "create_default_engine"]

