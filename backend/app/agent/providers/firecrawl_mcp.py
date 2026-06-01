import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import threading
from typing import Any
from app.core.config import CONFIG

class SearchProviderError(RuntimeError):
    pass


def _resolve_command(command: str) -> str:
    command = (command or "npx").strip()
    if os.path.isabs(command) and os.path.exists(command):
        return command
    resolved = shutil.which(command)
    if resolved:
        return resolved
    if command == "npx":
        for candidate in ("/usr/bin/npx", "/usr/local/bin/npx", os.path.expanduser("~/.nvm/versions/node/v24.16.0/bin/npx"), "/mnt/c/Program Files/nodejs/npx.cmd"):
            if os.path.exists(candidate):
                return candidate
    raise SearchProviderError(f"MCP 命令不存在：{command}。请安装 Node.js/npm，或设置 FIRECRAWL_MCP_COMMAND。")

class MCPStdioClient:
    def __init__(self, command: str, args: list[str], env: dict[str, str | None] | None = None, timeout: int = 60):
        self.command = _resolve_command(command)
        self.args = args
        self.env = env or {}
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self._next_id = 1
        self._pending: dict[int, queue.Queue] = {}
        self._pending_lock = threading.Lock()
        self._events: list[dict[str, Any]] = []
        self._stderr_lines: list[str] = []
        self._initialized = False

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        merged_env = os.environ.copy()
        merged_env.update({k: v for k, v in self.env.items() if v is not None})
        self.proc = subprocess.Popen(
            [self.command] + list(self.args),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1, env=merged_env,
        )
        threading.Thread(target=self._read_stdout_loop, daemon=True).start()
        threading.Thread(target=self._read_stderr_loop, daemon=True).start()
        self.initialize()

    def _read_stdout_loop(self):
        while self.proc and self.proc.stdout:
            line = self.proc.stdout.readline()
            if not line: break
            line = line.strip()
            if not line: continue
            try: msg = json.loads(line)
            except Exception:
                self._events.append({"raw_stdout": line}); continue
            msg_id = msg.get("id")
            if msg_id is not None:
                with self._pending_lock: q = self._pending.get(msg_id)
                if q: q.put(msg)
                else: self._events.append(msg)
            else:
                self._events.append(msg)

    def _read_stderr_loop(self):
        while self.proc and self.proc.stderr:
            line = self.proc.stderr.readline()
            if not line: break
            line = line.strip()
            if line: self._stderr_lines.append(line)

    def _send(self, msg: dict[str, Any]) -> None:
        if not self.proc or self.proc.poll() is not None:
            raise SearchProviderError(f"MCP 进程未运行或已退出。stderr={self._stderr_lines[-8:]}")
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def request(self, method: str, params: dict[str, Any] | None = None, timeout: int | None = None) -> dict[str, Any]:
        if self.proc is None: self.start()
        with self._pending_lock:
            msg_id = self._next_id; self._next_id += 1
            q: queue.Queue = queue.Queue(maxsize=1)
            self._pending[msg_id] = q
        self._send({"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params or {}})
        try:
            resp = q.get(timeout=timeout or self.timeout)
        except queue.Empty as exc:
            raise SearchProviderError(f"MCP 请求超时: method={method}; stderr={self._stderr_lines[-8:]}") from exc
        finally:
            with self._pending_lock: self._pending.pop(msg_id, None)
        if "error" in resp:
            raise SearchProviderError(f"MCP JSON-RPC 错误: {resp['error']}")
        return resp

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def initialize(self) -> dict[str, Any] | None:
        if self._initialized: return None
        with self._pending_lock:
            msg_id = self._next_id; self._next_id += 1
            q: queue.Queue = queue.Queue(maxsize=1)
            self._pending[msg_id] = q
        self._send({"jsonrpc":"2.0","id":msg_id,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"buqiuren-fastapi","version":"1.0"}}})
        try:
            resp = q.get(timeout=self.timeout)
        except queue.Empty as exc:
            raise SearchProviderError(f"MCP initialize 超时。stderr={self._stderr_lines[-10:]}") from exc
        finally:
            with self._pending_lock: self._pending.pop(msg_id, None)
        if "error" in resp: raise SearchProviderError(f"MCP initialize 失败: {resp['error']}")
        self.notify("notifications/initialized", {})
        self._initialized = True
        return resp

    def list_tools(self) -> list[dict[str, Any]]:
        return self.request("tools/list").get("result", {}).get("tools", [])

    def call_tool(self, name: str, arguments: dict[str, Any], timeout: int | None = None) -> dict[str, Any]:
        resp = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        result = resp.get("result", {})
        if result.get("isError"):
            raise SearchProviderError(f"MCP 工具 {name} 执行失败: {self.extract_content_text(result)}")
        return result

    @staticmethod
    def extract_content_text(result: dict[str, Any]) -> str:
        parts = []
        for block in result.get("content") or []:
            if isinstance(block, dict):
                if isinstance(block.get("text"), str): parts.append(block["text"])
                elif block.get("type") == "json" and "json" in block: parts.append(json.dumps(block["json"], ensure_ascii=False))
            elif isinstance(block, str): parts.append(block)
        return "\n".join(parts).strip()

    def close(self) -> None:
        if self.proc and self.proc.poll() is None: self.proc.terminate()

class FirecrawlMCPProvider:
    def __init__(self):
        mode = (CONFIG.search_provider or "firecrawl_mcp").lower()
        if mode not in {"firecrawl_mcp", "firecrawl", "mcp"}:
            raise SearchProviderError(f"当前工程为 MCP-only，不支持 SEARCH_PROVIDER={CONFIG.search_provider!r}")
        self.client = MCPStdioClient(
            command=CONFIG.firecrawl_mcp_command,
            args=shlex.split(CONFIG.firecrawl_mcp_args or "-y firecrawl-mcp"),
            env={"FIRECRAWL_API_KEY": CONFIG.firecrawl_api_key, "FIRECRAWL_API_URL": CONFIG.firecrawl_api_url},
            timeout=CONFIG.firecrawl_mcp_timeout_seconds,
        )

    @staticmethod
    def _extract_json_from_text(text: str) -> Any:
        try: return json.loads((text or "").strip())
        except Exception: pass
        m = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text or "", flags=re.S)
        if m:
            try: return json.loads(m.group(1))
            except Exception: pass
        for pattern in (r"(\{.*\})", r"(\[.*\])"):
            m = re.search(pattern, text or "", flags=re.S)
            if m:
                try: return json.loads(m.group(1))
                except Exception: pass
        return None

    @staticmethod
    def _unwrap_items(data: Any) -> list:
        if isinstance(data, list): return data
        if not isinstance(data, dict): return []
        if isinstance(data.get("web"), list): return data["web"]
        value = data.get("data")
        if isinstance(value, dict):
            for k in ("web", "results", "items", "data"):
                if isinstance(value.get(k), list): return value[k]
        for k in ("results", "items"):
            if isinstance(data.get(k), list): return data[k]
        return []

    @staticmethod
    def _first_text(*values: Any) -> str:
        for v in values:
            if isinstance(v, str) and v.strip(): return v.strip()
        return ""

    def search(self, query: str, limit: int = 8) -> list[dict[str, Any]]:
        result = self.client.call_tool("firecrawl_search", {"query": query, "limit": max(1, min(int(limit or 8), 20)), "sources": [{"type": "web"}]}, timeout=CONFIG.firecrawl_mcp_timeout_seconds)
        text = MCPStdioClient.extract_content_text(result)
        items = self._unwrap_items(self._extract_json_from_text(text))
        normalized = []
        for item in items:
            if not isinstance(item, dict): continue
            meta = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            url = self._first_text(item.get("url"), item.get("link"), meta.get("sourceURL"), meta.get("url"))
            if not url: continue
            normalized.append({"title": self._first_text(item.get("title"), meta.get("title")), "url": url, "snippet": self._first_text(item.get("description"), item.get("snippet"), meta.get("description"), item.get("markdown"))[:500], "provider": "firecrawl_mcp"})
        return normalized[:limit]

    def scrape(self, url: str) -> dict[str, Any]:
        result = self.client.call_tool("firecrawl_scrape", {"url": url, "formats": ["markdown", "html"], "onlyMainContent": True, "waitFor": 3000}, timeout=CONFIG.firecrawl_mcp_timeout_seconds)
        text = MCPStdioClient.extract_content_text(result)
        parsed = self._extract_json_from_text(text)
        page = parsed.get("data", parsed) if isinstance(parsed, dict) else {}
        meta = page.get("metadata") if isinstance(page.get("metadata"), dict) else {}
        return {
            "url": url,
            "title": self._first_text(page.get("title"), meta.get("title")),
            "markdown": self._first_text(page.get("markdown"), page.get("content")),
            "html": self._first_text(page.get("html"), page.get("rawHtml")),
            "text": self._first_text(page.get("markdown"), page.get("content"), page.get("html"), text),
            "content_type": page.get("contentType") or meta.get("contentType") or "",
            "status_code": page.get("statusCode") or meta.get("statusCode"),
            "metadata": meta,
        }

_provider: FirecrawlMCPProvider | None = None

def get_search_provider() -> FirecrawlMCPProvider:
    global _provider
    if _provider is None: _provider = FirecrawlMCPProvider()
    return _provider
