"""Run generated TypeScript in a Deno process with no permissions.

Each run is a new process. Limits: wall time, CPU time, V8 heap, and resident memory.
Add a container or a microVM before any shared deployment.
"""
import json
import os
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from living_reports.engine import Query
from living_reports.metrics.runner import Fact, Result
from living_reports.sandbox.gateway import HANDLERS, SandboxContext, dispatch

PRELUDE = Path(__file__).parent / "prelude.ts"
MAX_OUTPUT = 64_000
MAX_LINE = 1_000_000


class SandboxResult(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    stdout: str = ""
    error: str | None = None
    tool_calls: int = 0
    duration_ms: int = 0
    killed_reason: str | None = None
    queries: list[tuple[Query, Result]] = []
    facts: list[Fact] = []
    sections: list[dict[str, Any]] = []


def deno_path() -> str | None:
    found = shutil.which("deno")
    if found:
        return found
    fallback = Path.home() / ".deno" / "bin" / "deno"
    return str(fallback) if fallback.exists() else None


def _rss_mb(pid: int) -> float:
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def _command(memory_mb: int, timeout_s: int) -> list[str]:
    deno = [
        deno_path(), "run", "--no-prompt", "--no-remote", "--no-npm", "--no-config",
        f"--v8-flags=--max-old-space-size={memory_mb}", str(PRELUDE),
    ]
    limit = shutil.which("prlimit")
    if limit:
        return [limit, f"--cpu={timeout_s + 1}", "--core=0", *deno]
    return deno


def run_code(code: str, ctx: SandboxContext, timeout_s: int = 10, memory_mb: int = 256) -> SandboxResult:
    started = time.monotonic()
    result = SandboxResult()
    nonce = secrets.token_hex(16)
    with tempfile.TemporaryDirectory(prefix="lr-sandbox-") as home:
        # A clean environment: no keys from the host reach this process.
        env = {"PATH": "/usr/bin:/bin", "HOME": home, "NO_COLOR": "1", "DENO_NO_UPDATE_CHECK": "1"}
        process = subprocess.Popen(
            _command(memory_mb, timeout_s),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            cwd=home,
            start_new_session=True,
            text=True,
        )
        reason: list[str] = []

        def stop(why: str) -> None:
            reason.append(why)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

        finished = threading.Event()

        def watch() -> None:
            while not finished.wait(0.05):
                if time.monotonic() - started > timeout_s:
                    return stop("timeout")
                if _rss_mb(process.pid) > memory_mb + 90:  # the runtime itself uses about 60 MB
                    return stop("memory")

        threading.Thread(target=watch, daemon=True).start()
        process.stdin.write(
            json.dumps({"code": code, "tools": sorted(HANDLERS), "nonce": nonce}) + "\n"
        )
        process.stdin.flush()
        size = 0
        try:
            while line := process.stdout.readline(MAX_LINE + 1):
                if len(line) > MAX_LINE:
                    stop("output")
                    break
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    continue
                kind = message.get("type") if isinstance(message, dict) else None
                if kind == "log":
                    text = str(message.get("text", ""))
                    size += len(text) + 1
                    if size > MAX_OUTPUT:
                        stop("output")
                        break
                    result.stdout += text + "\n"
                elif kind == "call" and message.get("nonce") == nonce:
                    # The nonce marks calls that came through the prelude.
                    result.tool_calls += 1
                    reply = dispatch(str(message.get("tool")), message.get("args"), ctx)
                    process.stdin.write(json.dumps(reply, default=str) + "\n")
                    process.stdin.flush()
                elif kind == "error":
                    result.error = str(message.get("text"))
                elif kind == "done":
                    break
        except (BrokenPipeError, ValueError):
            pass
        finished.set()
        try:
            code_exit = process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            stop("timeout")
            code_exit = process.wait()
    result.killed_reason = reason[0] if reason else None
    if result.killed_reason is None and code_exit not in (0, None):
        result.killed_reason = "oom" if code_exit != 0 else None
    result.duration_ms = int((time.monotonic() - started) * 1000)
    result.queries, result.facts, result.sections = ctx.queries, ctx.facts, ctx.sections
    return result
