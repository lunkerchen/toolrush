"""ToolRush explicit read batches for execute_code on macOS.

Enables 'from hermes_tools import parallel' inside execute_code.
Batches 1..16 read operations through one RPC, executed concurrently
across up to 4 worker threads. Maintains input order, authorization,
and tool budget.
"""

import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from agent.thread_scoped_output import thread_scoped_silence

READ_TOOLS = frozenset({"read_file", "search_files", "web_search", "web_extract"})
MAX_BATCH = 16
MAX_WORKERS = 4


def _error(msg: str) -> str:
    return json.dumps({"error": msg})


def execute_read_batch(
    args: Dict[str, Any],
    *,
    allowed_tools: frozenset,
    counter: List[int],
    budget: int,
    log: List[Dict[str, Any]],
    dispatch,
    stop_event=None,
) -> str:
    """Atomic batch validation and concurrent execution."""
    calls = args.get("calls") if isinstance(args, dict) else None
    if not isinstance(calls, list) or not (1 <= len(calls) <= MAX_BATCH):
        return _error(f"parallel requires 1..{MAX_BATCH} read calls")

    for call in calls:
        if not isinstance(call, dict):
            return _error("Each parallel call must be a dict with tool and args")
        name = call.get("tool")
        if not isinstance(name, str) or name not in READ_TOOLS or name not in allowed_tools:
            return _error(f"Tool {name!r} is not an enabled parallel read tool")
        if not isinstance(call.get("args"), dict):
            return _error("Each parallel call requires an args dict")

    if counter[0] + len(calls) > budget:
        return _error(f"Tool call limit reached ({budget}); entire batch refused")

    if stop_event and stop_event.is_set():
        return _error("Tool batch interrupted before dispatch")

    counter[0] += len(calls)

    def invoke(call: Dict[str, Any]):
        start = time.monotonic()
        if stop_event and stop_event.is_set():
            raw = _error("Tool batch interrupted")
        else:
            try:
                # Fast path: READ_TOOLS (read_file, search_files, web_search, web_extract) are pure reads.
                # Dispatched via registry directly avoids global pre/post hook locks and contextvars collisions across threads.
                from tools.registry import registry
                target_obj = getattr(dispatch, "__self__", None)
                task_id = getattr(target_obj, "task_id", "default") if target_obj is not None else "default"
                if target_obj is not None and hasattr(target_obj, "ctx"):
                    raw = target_obj.ctx.copy().run(
                        registry.dispatch, call["tool"], dict(call["args"]), task_id=task_id
                    )
                else:
                    with thread_scoped_silence():
                        raw = registry.dispatch(call["tool"], dict(call["args"]), task_id=task_id)
            except Exception as exc:
                raw = _error(str(exc))

        try:
            result = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError):
            result = raw

        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (ValueError, TypeError):
                pass

        record = {
            "tool": call["tool"],
            "args_preview": str(call["args"])[:80],
            "duration": round(time.monotonic() - start, 3),
            "batch": True,
        }
        return result, record

    if len(calls) == 1:
        completed = [invoke(calls[0])]
    else:
        with ThreadPoolExecutor(
            max_workers=min(MAX_WORKERS, len(calls)),
            thread_name_prefix="toolrush-mac-read",
        ) as pool:
            completed = list(pool.map(invoke, calls))

    for result, record in completed:
        log.append(record)

    return json.dumps([result for result, record in completed], ensure_ascii=False)
