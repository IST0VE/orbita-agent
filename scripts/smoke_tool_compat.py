"""Run a small, real LLM/tool/LLM graph using only a synthetic local file.

Uses the configured company endpoint and key. No publication or persistent memory.
Run explicitly: python scripts/smoke_tool_compat.py [--mode auto|native|prompt]
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent import config as cfg  # loads .env before the process-only overrides
from agent import roles
from agent.builder import build_graph
from agent.pipeline import Role


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=cfg.LLM_TOOL_MODES, default="auto")
    args = parser.parse_args()
    os.environ.update(
        {
            "LLM_TOOL_MODE": args.mode,
            "LLM_MAX_TOKENS": "1024",
            "LLM_TIMEOUT_S": "60",
            "LLM_MAX_RETRIES": "0",
            "PUBLISH_TARGET": "none",
            "MEMORY_ENABLED": "0",
            "KNOWLEDGE_ENABLED": "0",
            "PIPELINE_REQUIRE_APPROVAL": "0",
            "BUDGET_USD_PER_THREAD": "0",
        }
    )
    pipeline = replace(
        roles.PIPELINE,
        roles=(
            Role("probe", "1", "Tool compatibility check", "Read synthetic data", reads_files=True),
        ),
        prompt_for=lambda _: (
            "Read probe.md with read_task_file. Return only the verification code found inside. "
            "Do not guess the code. Do not explain your reasoning."
        ),
    )
    workspace = Path(__file__).resolve().parents[1]
    with TemporaryDirectory(prefix=".tool-compat-", dir=workspace) as directory:
        root = Path(directory).resolve()
        if not root.is_relative_to(workspace):
            raise RuntimeError("Temporary input escaped workspace")
        task = root / "probe"
        task.mkdir()
        code = "ORB-" + uuid4().hex
        (task / "probe.md").write_text("Verification code: " + code, encoding="utf-8")
        os.environ["AGENT_INPUT_DIR"] = str(root)
        state = (
            build_graph(pipeline=pipeline)
            .compile()
            .invoke(
                {"messages": [HumanMessage("Read probe.md and return its verification code.")]},
                {
                    "recursion_limit": 12,
                    "configurable": {"input_dir": "probe", "thread_id": "compat-smoke"},
                },
            )
        )
        reads = [m for m in state["messages"] if isinstance(m, ToolMessage)]
        answer = state.get("artifacts", {}).get("probe", "")
        passed = bool(reads) and code in answer and any(code in m.content for m in reads)
        print(
            json.dumps(
                {
                    "passed": passed,
                    "tool_results": len(reads),
                    "llm_calls": state.get("usage", {}).get("calls"),
                    "modes": [
                        m.response_metadata.get("tool_mode", "native")
                        for m in state["messages"]
                        if isinstance(m, AIMessage)
                    ],
                    "publication": state.get("publication", {}).get("status"),
                }
            )
        )
        if not passed:
            raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Endpoint URLs, server bodies and credentials do not belong in smoke output.
        print(
            json.dumps(
                {
                    "passed": False,
                    "error_type": type(exc).__name__,
                    "status_code": getattr(exc, "status_code", None),
                }
            )
        )
        raise SystemExit(1) from None
