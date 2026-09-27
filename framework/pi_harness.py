"""Pi as the agent harness, over its RPC mode.

One long-lived `pi --mode rpc` child holds the conversation, the same way the run held
one ClaudeSDKClient. Commands go in as JSON lines; events come back as JSON lines.

What the runner needs from a harness is small: send a prompt, and be told what
happened while it ran -- text, which tool is being called, when the turn is over.
That is what this exposes, in terms that say nothing about pi, so a second backend can
be written against it without the runner changing.
"""

import json
import os
import queue
import subprocess
import threading


class Turn:
    """What one prompt produced: the text, the tools it called, and how it ended."""

    def __init__(self):
        self.text = []
        self.tool_calls = []
        self.usage = None
        self.stop_reason = None

    @property
    def output(self):
        return "".join(self.text).strip()


class PiHarness:
    def __init__(self, cwd, extension=None, model=None, tools=None,
                 system_prompt_file=None, session_dir=None, on_event=None, log=print,
                 extra_args=()):
        self.cwd = cwd
        self.extension = extension
        self.extra_args = list(extra_args)
        self.model = model
        self.tools = tools
        self.system_prompt_file = system_prompt_file
        self.session_dir = session_dir
        self.on_event = on_event          # called with every event, for phase display
        self.log = log
        self.proc = None
        self.events = queue.Queue()
        self._responses = {}
        self._next_id = 0
        self._session_id = None

    # --- lifecycle ---------------------------------------------------------------

    def start(self):
        cmd = ["pi", "--mode", "rpc"]
        if self.extension:
            cmd += ["--extension", self.extension]
        if self.model:
            cmd += ["--model", self.model]
        if self.tools:
            cmd += ["--tools", ",".join(self.tools)]
        if self.session_dir:
            cmd += ["--session-dir", self.session_dir]
        else:
            cmd += ["--no-session"]
        cmd += self.extra_args
        self.proc = subprocess.Popen(cmd, cwd=self.cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        return self

    def close(self):
        if not self.proc:
            return
        # Closing stdin is how pi is asked to shut down in an orderly way.
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.kill()

    # --- wire ----------------------------------------------------------------------

    def _read_stdout(self):
        """Split on LF only. A generic line reader would also break on U+2028/U+2029,
        which are valid inside JSON strings."""
        for raw in self.proc.stdout:
            raw = raw.rstrip(b"\r\n")
            if not raw:
                continue
            try:
                record = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if record.get("type") == "response":
                self._responses.setdefault(record.get("id"), queue.Queue()).put(record)
            else:
                self.events.put(record)
        self.events.put({"type": "_closed"})

    def _read_stderr(self):
        for raw in self.proc.stderr:
            text = raw.decode("utf8", "replace").rstrip()
            if text:
                self.log(f"[pi] {text}")

    def _command(self, type_, **fields):
        self._next_id += 1
        cid = f"c{self._next_id}"
        box = self._responses.setdefault(cid, queue.Queue())
        self.proc.stdin.write(json.dumps({"id": cid, "type": type_, **fields}).encode() + b"\n")
        self.proc.stdin.flush()
        reply = box.get()
        self._responses.pop(cid, None)
        if not reply.get("success"):
            raise RuntimeError(f"pi {type_} failed: {reply.get('error')}")
        return reply.get("data")

    # --- what the runner uses -------------------------------------------------------

    def state(self):
        return self._command("get_state")

    def session_stats(self):
        return self._command("get_session_stats")

    def abort(self):
        return self._command("abort")

    def prompt(self, text):
        """Send one prompt and drain until pi says it has no more automatic work.

        `agent_end` is not the end: a retry, a compaction or recovery can follow it.
        `agent_settled` is. Waiting on the wrong one ends a turn early and the next
        prompt lands in the middle of the last one.
        """
        turn = Turn()
        self._command("prompt", message=text)
        while True:
            event = self.events.get()
            kind = event.get("type")
            if self.on_event:
                self.on_event(event, turn)
            if kind == "_closed":
                turn.stop_reason = "pi exited"
                return turn
            if kind == "message_update":
                inner = event.get("assistantMessageEvent") or {}
                if inner.get("type") == "text_delta":
                    turn.text.append(inner.get("delta", ""))
                if event.get("usage"):
                    turn.usage = event["usage"]
            elif kind == "tool_execution_start":
                turn.tool_calls.append(event.get("toolName"))
            elif kind == "message_end":
                message = event.get("message") or {}
                if message.get("role") == "assistant" and message.get("stopReason"):
                    turn.stop_reason = message["stopReason"]
            elif kind == "agent_settled":
                return turn


# pi-mcp-adapter (npm) is the MCP client: the maintained one, and the one that keeps
# tool definitions out of the context window until they are wanted.
MCP_EXTENSION = "npm:pi-mcp-adapter"


def mcp_tool_name(server, tool):
    """What a direct-registered MCP tool is called: <server>_<tool>. The agent must be
    told the name it is actually offered, not the bare one."""
    return f"{server}_{tool}"


def write_mcp_config(path, servers):
    """This run's MCP config, passed to pi with --mcp-config.

    An explicitly selected config file is used as given. A project-level .mcp.json is
    not: the adapter skips unapproved project servers in headless sessions, and an
    unattended run has nobody to approve them.

    Two choices are made for every server here rather than left to the defaults:

      lifecycle eager -- the default is lazy, which starts the server on its first
        tool call. The runner reaches the same server over its control socket and
        would be waiting on a socket that only appears once the agent happens to
        submit something.
      directTools    -- the default routes everything through one proxy tool, which is
        the right trade for a dozen servers a session might not touch. These tools are
        the run: they are called every turn, and the campaign prompt names them.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    servers = {name: {"lifecycle": "eager", "directTools": True, **cfg}
               for name, cfg in servers.items()}
    with open(path, "w") as f:
        json.dump({"mcpServers": servers,
                   "settings": {"disableProxyTool": True}}, f, indent=2)
    return path
