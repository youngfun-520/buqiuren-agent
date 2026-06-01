import json
from typing import Any, Optional, Callable
from app.core.config import CONFIG

class LLMUnavailable(RuntimeError):
    pass


def parse_model_output(text: str) -> dict[str, str]:
    raw = text or ""
    start_tag, end_tag = "<think>", "</think>"
    start = raw.find(start_tag)
    end = raw.find(end_tag, start + len(start_tag)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        think_content = raw[start + len(start_tag):end].strip()
        final_content = (raw[:start] + raw[end + len(end_tag):]).strip()
    else:
        think_content = ""
        final_content = raw.strip()
    return {"think_content": think_content, "final_content": final_content, "raw_content": text or ""}


def _strip_markdown_fence(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.splitlines()
    if lines and lines[0].strip().startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()


def _extract_json_candidate(text: str) -> str:
    cleaned = _strip_markdown_fence(text)
    starts = [idx for idx in (cleaned.find("{"), cleaned.find("[")) if idx >= 0]
    if not starts:
        return cleaned
    start = min(starts)
    open_char = cleaned[start]
    close_char = "}" if open_char == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(cleaned)):
        ch = cleaned[idx]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == open_char:
            depth += 1
        elif ch == close_char:
            depth -= 1
            if depth == 0:
                return cleaned[start:idx + 1]
    return cleaned[start:]


def safe_json(text: str, default: Any | None = None) -> Any:
    default = {} if default is None else default
    try:
        cleaned = _extract_json_candidate(text)
        return json.loads(cleaned)
    except Exception as exc:
        if isinstance(default, dict):
            return {**default, "_parse_error": str(exc), "_raw_text_preview": (text or "")[:800]}
        return default

class MiniMaxLLM:
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not (CONFIG.minimax_api_key and CONFIG.minimax_base_url and CONFIG.minimax_model):
            raise LLMUnavailable("MiniMax 环境变量未配置完整")
        from langchain_openai import ChatOpenAI
        self._client = ChatOpenAI(
            model=CONFIG.minimax_model,
            api_key=CONFIG.minimax_api_key,
            base_url=CONFIG.minimax_base_url,
            temperature=0,
            timeout=60,
            max_retries=2,
        )
        return self._client

    def invoke_json(self, prompt: str, default: dict[str, Any]) -> dict[str, Any]:
        client = self._get_client()
        resp = client.invoke(prompt)
        parsed = parse_model_output(resp.content)
        return safe_json(parsed["final_content"], default)

    def stream思考(self, prompt: str, on_thinking: Optional[Callable] = None):
        """流式思考：LLM 生成思考步骤，每个步骤立即回调（callback 版）"""
        client = self._get_client()
        full_text = ""
        for chunk in client.stream([{"role": "user", "content": prompt}]):
            if chunk.content:
                full_text += chunk.content
                parsed = parse_model_output(full_text)
                thinking = parsed.get("think_content", "").strip()
                if thinking and on_thinking:
                    on_thinking(thinking)
        parsed = parse_model_output(full_text)
        return safe_json(parsed["final_content"], {})

    def stream思考_gen(self, prompt: str, on_thinking: Optional[Callable] = None):
        """流式思考生成器版本：逐步 yield 思考内容块（句子级别），最后 yield 最终结果"""
        client = self._get_client()
        full_text = ""
        last_yielded_len = 0
        import re
        for chunk in client.stream([{"role": "user", "content": prompt}]):
            if chunk.content:
                full_text += chunk.content
                parsed = parse_model_output(full_text)
                thinking = parsed.get("think_content", "").strip()
                if thinking and len(thinking) > last_yielded_len:
                    sentences = thinking[last_yielded_len:]
                    matches = list(re.finditer(r'[。！？；\n]', sentences))
                    if matches:
                        last_match = matches[-1]
                        chunk_to_yield = sentences[:last_match.end()]
                        last_yielded_len = len(thinking) - len(sentences) + last_match.end()
                        chunk_text = chunk_to_yield.strip()
                        yield {"thinking": chunk_text, "done": False}
                        if on_thinking:
                            on_thinking(chunk_text)
        parsed = parse_model_output(full_text)
        thinking = parsed.get("think_content", "").strip()
        remaining = thinking[last_yielded_len:] if last_yielded_len < len(thinking) else ""
        if remaining:
            yield {"thinking": remaining.strip(), "done": False}
        yield {"done": True, "result": safe_json(parsed["final_content"], {})}

llm = MiniMaxLLM()
