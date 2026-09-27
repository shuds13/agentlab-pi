#!/usr/bin/env python3
"""AgentLab's tools as a standalone MCP server.

Run as:  python3 framework/mcp_server.py
Needs the same environment a run does: LAB_DIR, CAMPAIGN, WORKSPACE_DIR, SYSTEM, ...

Two surfaces on one process, because they are two audiences for one piece of state:

  stdio          MCP (JSON-RPC 2.0, LF-framed) -- what the MODEL calls. Any MCP host
                 speaks it: Claude Code natively, pi through its MCP extension.
  control socket A line of JSON per request -- what the HARNESS asks. agent.py needs
                 to know how many jobs are in flight, whether the agent called
                 goal_met, when to wind down. That is not model-facing and must not
                 be offered as a tool.

The job state -- executors, futures, claims, budgets -- lives here, in one process,
and neither surface owns it. That is the point of the split: the harness became
replaceable without the tools moving.
"""

import asyncio
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mcp_tool  # noqa: E402
import tools  # noqa: E402

PROTOCOL_VERSION = "2025-06-18"
CONTROL_SOCKET = os.environ.get(
    "TOOLS_CONTROL_SOCKET",
    os.path.join(os.environ.get("WORKSPACE_DIR", "."), "run", "tools.sock"))


def log(msg):
    """Diagnostics go to stderr. stdout is the protocol and nothing else."""
    print(f"[tools] {msg}", file=sys.stderr, flush=True)


# --- MCP over stdio -------------------------------------------------------------

async def call_tool(name, arguments):
    """Run one tool. Its result is already MCP-shaped; a crash becomes an error result
    rather than taking the server down mid-run."""
    entry = mcp_tool.REGISTRY.get(name)
    if entry is None:
        return {"content": [{"type": "text", "text": f"no such tool: {name}"}],
                "isError": True}
    try:
        result = await entry.handler(arguments or {})
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return {"content": [{"type": "text", "text": f"{name} failed: {e}"}],
                "isError": True}
    # The tools were written against a decorator that spelled it `is_error`.
    out = {"content": result.get("content", [])}
    if result.get("is_error") or result.get("isError"):
        out["isError"] = True
    return out


async def handle_mcp(request):
    method = request.get("method")
    if method == "initialize":
        return {"protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": tools.SERVER_NAME, "version": "1.0.0"}}
    if method == "tools/list":
        return {"tools": [t.spec() for t in tools.offered_tools()]}
    if method == "tools/call":
        params = request.get("params") or {}
        return await call_tool(params.get("name"), params.get("arguments"))
    if method == "ping":
        return {}
    return None


async def serve_stdio():
    """Read LF-framed JSON-RPC from stdin until it closes, which is the host asking
    this process to shut down."""
    loop = asyncio.get_running_loop()
    reader = asyncio.StreamReader()
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(reader),
                                 sys.stdin)
    while True:
        line = await reader.readline()
        if not line:
            return                      # stdin closed: orderly shutdown
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError as e:
            log(f"unparseable record: {e}")
            continue
        # A notification has no id and takes no reply.
        if request.get("id") is None:
            continue
        result = await handle_mcp(request)
        response = {"jsonrpc": "2.0", "id": request["id"]}
        if result is None:
            response["error"] = {"code": -32601,
                                 "message": f"no method {request.get('method')}"}
        else:
            response["result"] = result
        sys.stdout.write(json.dumps(response, default=str) + "\n")
        sys.stdout.flush()


# --- control surface ------------------------------------------------------------
# Everything agent.py used to read straight off the tools module when the two shared
# a process. Static facts are settled at startup; the rest is asked for as it changes.

def config():
    """What does not change while the run goes."""
    return {
        "has_remote": tools.HAS_REMOTE,
        "has_local": tools.HAS_LOCAL,
        "has_transfer": tools.HAS_TRANSFER,
        "batch_mode": tools.BATCH_MODE,
        "endpoint_id": tools.ENDPOINT_ID,
        "task_dir": tools.TASK_DIR,
        "work_dir": tools.WORK_DIR,
        "target": tools.TARGET,
        "system": tools.SYSTEM,
        "max_concurrent": tools.MAX_CONCURRENT,
        "local_max_concurrent": tools.LOCAL_MAX_CONCURRENT,
        "max_submits": tools.MAX_SUBMITS,
        "tool_names": [t.name for t in tools.offered_tools()],
    }


def status():
    """What the harness polls between turns."""
    return {
        "submit_count": tools.submit_count(),
        "local_submit_count": tools.local_submit_count(),
        "jobs_in_flight": tools.jobs_in_flight(),
        "pending_count": tools.pending_count(),
        "goal_is_met": tools.goal_is_met(),
        "end_requested": tools.end_requested(),
        "problem_since": tools.problem_since(),
        "announcements": tools.read_announcements(),
    }


async def handle_control(request):
    op = request.get("op")
    if op == "config":
        return config()
    if op == "status":
        return status()
    if op == "cycle_done_pending":
        # Reading clears it, so it is its own call: a status poll must not consume it.
        return {"conclusion": tools.cycle_done_pending()}
    if op == "request_stop":
        tools.request_stop()
        return {"stopping": True}
    if op == "backend_trouble":
        return {"problem": tools.backend_trouble()}
    if op == "record_message":
        tools.record_message(request.get("who", ""), request.get("msg", ""))
        return {"recorded": True}
    if op == "wait_for_any":
        # Blocking, so it runs off the event loop and the MCP surface stays answerable
        # while the harness waits.
        finished = await asyncio.to_thread(tools.wait_for_any,
                                           request.get("timeout", 1800))
        return {"finished": finished}
    if op == "shutdown":
        tools.shutdown_executor()
        return {"shutdown": True}
    raise ValueError(f"no control op {op!r}")


async def control_connection(reader, writer):
    try:
        while True:
            line = await reader.readline()
            if not line:
                return
            try:
                result = await handle_control(json.loads(line))
                response = {"ok": True, "data": result}
            except Exception as e:
                traceback.print_exc(file=sys.stderr)
                response = {"ok": False, "error": str(e)}
            writer.write(json.dumps(response, default=str).encode() + b"\n")
            await writer.drain()
    finally:
        writer.close()


async def serve_control():
    path = CONTROL_SOCKET
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # A socket left by a process that did not exit cleanly would otherwise block bind.
    if os.path.exists(path):
        os.unlink(path)
    server = await asyncio.start_unix_server(control_connection, path)
    log(f"control socket {path}")
    return server


async def main():
    control = await serve_control()
    log(f"serving {len(tools.offered_tools())} tools for campaign "
        f"{os.environ.get('CAMPAIGN', '?')} on {tools.SYSTEM}")
    try:
        await serve_stdio()
    finally:
        log("shutting down")
        control.close()
        tools.shutdown_executor()
        if os.path.exists(CONTROL_SOCKET):
            os.unlink(CONTROL_SOCKET)


if __name__ == "__main__":
    asyncio.run(main())
