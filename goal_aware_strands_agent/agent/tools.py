"""MCP stdio client (newline-delimited JSON-RPC) and deterministic mock browser."""
import json
import queue
import subprocess
import threading

class ToolError(RuntimeError): pass

class MCPStdio:
    def __init__(self, command, timeout=30):
        self.command, self.timeout = command, timeout
        self.proc = None
        self.pending = {}
        self.lock = threading.Lock()
        self.counter = 0
        self.closed = False

    def __enter__(self):
        self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", bufsize=1)
        threading.Thread(target=self._reader, daemon=True).start()
        self.request("initialize", {"protocolVersion":"2025-03-26", "capabilities":{},
                                    "clientInfo":{"name":"goal-aware-strands-agent", "version":"0.1.0"}})
        self.notify("notifications/initialized", {})
        return self

    def _reader(self):
        for line in self.proc.stdout:
            try: msg = json.loads(line)
            except json.JSONDecodeError: continue
            mid = msg.get("id")
            with self.lock: target = self.pending.get(mid)
            if target: target.put(msg)

    def notify(self, method, params):
        self._write({"jsonrpc":"2.0", "method":method, "params":params})

    def _write(self, data):
        if not self.proc or self.proc.poll() is not None: raise ToolError("MCP server exited")
        self.proc.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method, params):
        with self.lock:
            self.counter += 1
            mid = self.counter
            waiter = queue.Queue(maxsize=1)
            self.pending[mid] = waiter
        try:
            self._write({"jsonrpc":"2.0", "id":mid, "method":method, "params":params})
            try: response = waiter.get(timeout=self.timeout)
            except queue.Empty: raise ToolError(f"MCP request timed out: {method}")
            if "error" in response: raise ToolError(str(response["error"]))
            return response.get("result", {})
        finally:
            with self.lock: self.pending.pop(mid, None)

    def list_tools(self): return self.request("tools/list", {}).get("tools", [])
    def call(self, name, arguments): return self.request("tools/call", {"name":name,"arguments":arguments})
    def __exit__(self, *_):
        if self.proc:
            self.proc.terminate()
            try: self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired: self.proc.kill()
        self.closed = True

class MockBrowser:
    """Fully offline browser emulator for reproducible integration tests."""
    def __init__(self): self.page = "about:blank"; self.query = ""; self.downloads = False
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def list_tools(self):
        return [{"name":"browser_navigate", "inputSchema":{"type":"object", "properties":{"url":{"type":"string"}},"required":["url"],"additionalProperties":False}},
                {"name":"browser_snapshot", "inputSchema":{"type":"object", "properties":{},"additionalProperties":False}},
                {"name":"browser_click", "inputSchema":{"type":"object", "properties":{"element":{"type":"string"},"ref":{"type":"string"}},"required":["ref"],"additionalProperties":False}},
                {"name":"browser_type", "inputSchema":{"type":"object", "properties":{"element":{"type":"string"},"ref":{"type":"string"},"text":{"type":"string"}},"required":["ref","text"],"additionalProperties":False}}]
    def call(self, name, args):
        if name == "browser_navigate": self.page = args["url"]
        elif name == "browser_click":
            if args.get("ref") == "e13" and self.page.rstrip("/") == "https://www.python.org":
                self.page = "https://www.python.org/downloads/"; self.downloads = True
            else: return {"isError": True, "content":[{"type":"text","text":"Invalid click target"}]}
        elif name == "browser_type": self.query = args["text"]
        elif name != "browser_snapshot": return {"isError":True,"content":[{"type":"text","text":"Unknown tool"}]}
        if self.page.rstrip("/") == "https://www.python.org":
            view = "URL: https://www.python.org\n- link Downloads [ref=e13]\n- link Documentation [ref=e12]"
        elif self.downloads:
            view = "URL: https://www.python.org/downloads/\nDownloads page opened. Example version: Python 3.x (mock data, NOT a current version)."
        else: view = f"URL: {self.page}"
        return {"isError":False, "content":[{"type":"text", "text":view}]}
