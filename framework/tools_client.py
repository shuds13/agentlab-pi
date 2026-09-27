"""The tools module, seen from another process.

agent.py used to read run state straight off `tools` because the two shared a process.
Now the tools live in mcp_server.py, so the same questions go over its control socket.
The names are deliberately the ones agent.py already used -- submit_count(),
pending_count(), goal_is_met() -- so the runner reads the same either way.

Static facts (HAS_REMOTE, MAX_SUBMITS, ...) are fetched once at connect and exposed as
attributes, because they do not change while a run goes and a socket round trip per
read would be noise.
"""

import json
import os
import socket
import threading
import time


class ToolsClient:
    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()      # one request in flight per connection
        self._sock = None
        self._file = None

    # --- connection ------------------------------------------------------------

    def connect(self, timeout=60):
        """Wait for the server to come up, then read the run's fixed facts.

        The server is started by the MCP host, not by us, so at startup we are waiting
        for another process to be ready rather than for a service to respond."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                sock = socket.socket(socket.AF_UNIX)
                sock.connect(self.path)
                self._sock = sock
                self._file = sock.makefile("rwb")
                break
            except OSError as e:
                last = e
                time.sleep(0.2)
        else:
            raise RuntimeError(f"tool server socket {self.path} never appeared ({last})")

        cfg = self.call("config")
        self.HAS_REMOTE = cfg["has_remote"]
        self.HAS_LOCAL = cfg["has_local"]
        self.HAS_TRANSFER = cfg["has_transfer"]
        self.BATCH_MODE = cfg["batch_mode"]
        self.ENDPOINT_ID = cfg["endpoint_id"]
        self.TASK_DIR = cfg["task_dir"]
        self.WORK_DIR = cfg["work_dir"]
        self.TARGET = cfg["target"]
        self.SYSTEM = cfg["system"]
        self.MAX_CONCURRENT = cfg["max_concurrent"]
        self.LOCAL_MAX_CONCURRENT = cfg["local_max_concurrent"]
        self.MAX_SUBMITS = cfg["max_submits"]
        self.TOOL_NAMES = cfg["tool_names"]
        return self

    def close(self):
        for handle in (self._file, self._sock):
            try:
                if handle:
                    handle.close()
            except OSError:
                pass

    # --- requests ---------------------------------------------------------------

    def call(self, op, **params):
        with self._lock:
            self._file.write(json.dumps({"op": op, **params}).encode() + b"\n")
            self._file.flush()
            line = self._file.readline()
        if not line:
            raise RuntimeError("tool server closed the control socket")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise RuntimeError(f"control op {op} failed: {reply.get('error')}")
        return reply["data"]

    # --- the surface the runner uses ----------------------------------------------
    # A status poll is one round trip, so a turn reads it once and asks the snapshot
    # rather than making a call per field.

    def status(self):
        return self.call("status")

    def submit_count(self):
        return self.status()["submit_count"]

    def pending_count(self):
        return self.status()["pending_count"]

    def jobs_in_flight(self):
        return self.status()["jobs_in_flight"]

    def goal_is_met(self):
        return self.status()["goal_is_met"]

    def end_requested(self):
        return self.status()["end_requested"]

    def read_announcements(self):
        return self.status()["announcements"]

    def cycle_done_pending(self):
        """Reading clears it on the server, so this is never folded into status()."""
        return self.call("cycle_done_pending")["conclusion"]

    def request_stop(self):
        return self.call("request_stop")

    def backend_trouble(self):
        return self.call("backend_trouble")["problem"]

    def record_message(self, who, msg):
        return self.call("record_message", who=who, msg=msg)

    def wait_for_any(self, timeout=1800):
        """Blocks on the server until a job finishes or the timeout passes. The socket
        timeout is a little longer, so a slow reply is not mistaken for a dead server."""
        self._sock.settimeout(timeout + 30)
        try:
            return self.call("wait_for_any", timeout=timeout)["finished"]
        finally:
            self._sock.settimeout(None)


def default_socket_path(workspace_dir=None):
    workspace_dir = workspace_dir or os.environ.get("WORKSPACE_DIR", ".")
    return os.environ.get("TOOLS_CONTROL_SOCKET",
                          os.path.join(workspace_dir, "run", "tools.sock"))
