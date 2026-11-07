"""One client for any provider. The sorter speaks Anthropic's message format; for OpenAI-style providers
(OpenRouter, xAI/Grok, anything with /chat/completions) the request and reply are translated here.

Configure in catalog/llm-config.json (git-ignored) or environment variables:
    {"provider": "groq", "key": "gsk_...", "model": "openai/gpt-oss-120b"}
    {"provider": "deepseek", "key": "sk-...", "model": "deepseek-flash"}
    {"provider": "xai", "key": "xai-...", "model": "grok-4-fast"}
    {"provider": "openrouter", "key": "sk-or-...", "model": "anthropic/claude-haiku-5-5"}
    {"provider": "anthropic", "key": "sk-ant-...", "model": "claude-sonnet-5-5"}
    env: SEJ_PROVIDER, SEJ_API_KEY (or ANTHROPIC_API_KEY / XAI_API_KEY / OPENROUTER_API_KEY), SEJ_MODEL"""
import json
import os
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "..", "catalog", "llm-config.json")
BASES = {"xai": "https://api.x.ai/v1", "openrouter": "https://openrouter.ai/api/v1", "openai": "https://api.openai.com/v1", "groq": "https://api.groq.com/openai/v1", "deepseek": "https://api.deepseek.com/v1",
         "gemini": "https://generativelanguage.googleapis.com/v1beta/openai"}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "xai": "XAI_API_KEY", "openrouter": "OPENROUTER_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
           "gemini": "GEMINI_API_KEY"}
DEFAULT_MODEL = {"anthropic": "claude-sonnet-5-5", "xai": "grok-4-fast", "openrouter": "anthropic/claude-sonnet-5-5", "groq": "openai/gpt-oss-120b", "deepseek": "deepseek-flash"}


def load_config():
    cfg = {}
    if os.path.exists(CONFIG):
        with open(CONFIG) as fh:
            cfg = json.load(fh)
    prov = os.environ.get("SEJ_PROVIDER") or cfg.get("provider") or ("anthropic" if os.environ.get("ANTHROPIC_API_KEY") else None)
    if not prov:
        for p, env in KEY_ENV.items():
            if os.environ.get(env):
                prov = p
                break
    if not prov:
        raise SystemExit("No AI provider set: write catalog/llm-config.json or set SEJ_PROVIDER and SEJ_API_KEY")
    key = os.environ.get("SEJ_API_KEY") or cfg.get("key") or os.environ.get(KEY_ENV.get(prov, ""), "")
    model = os.environ.get("SEJ_MODEL") or cfg.get("model") or DEFAULT_MODEL.get(prov)
    if not key or not model:
        raise SystemExit(f"provider {prov}: missing key or model")
    return prov, key, model, cfg


def client(**over):
    prov, key, model, cfg = load_config()
    model = over.get("model") or model
    if prov == "anthropic":
        return AnthropicClient(key, model)
    c = OpenAIClient(cfg.get("base_url") or BASES[prov], key, model, prov)
    c.vision = cfg.get("vision", not ((prov == "groq" and "gpt-oss" in model) or (prov == "deepseek" and "flash" not in model)))
    c.lean = cfg.get("lean", prov == "groq")                     # free tiers: short prompts
    c.budget = int(cfg.get("max_request_tokens", 7000 if prov == "groq" else 0))
    return c


class _Base:
    def __init__(self, key, model):
        self.key, self.model = key, model
        self.usage = {"input": 0, "output": 0, "cache_read": 0, "calls": 0}

    def _post(self, url, body, headers):
        for attempt in range(8):
            r = requests.post(url, json=body, headers=headers, timeout=600)
            if r.status_code in (429, 500, 502, 503, 529) and attempt < 7:
                wait = r.headers.get("retry-after")
                time.sleep(min(90, float(wait) + 1 if wait and wait.replace(".", "").isdigit() else 10 * (attempt + 1)))
                continue
            if r.status_code >= 400:
                raise RuntimeError(f"{self.__class__.__name__} {r.status_code}: {r.text[:500]}")
            try:
                out = r.json()
            except ValueError:
                out = None
            if isinstance(out, dict):
                return out
            if attempt < 7:          # empty or broken body (seen from DeepSeek under load): ask again
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"{self.__class__.__name__}: unreadable reply {r.status_code} {r.text[:300]!r}")
        raise RuntimeError("AI provider unavailable")


class AnthropicClient(_Base):
    def __call__(self, body):
        out = self._post("https://api.anthropic.com/v1/messages", {"model": self.model, **body},
                         {"x-api-key": self.key, "anthropic-version": "2023-06-01"})
        u = out.get("usage", {})
        self.usage["input"] += u.get("input_tokens", 0)
        self.usage["output"] += u.get("output_tokens", 0)
        self.usage["cache_read"] += u.get("cache_read_input_tokens", 0)
        self.usage["calls"] += 1
        return out


class OpenAIClient(_Base):
    """Anthropic-format in, Anthropic-format out, OpenAI /chat/completions on the wire."""

    def __init__(self, base, key, model, provider):
        super().__init__(key, model)
        self.base, self.provider = base, provider
        self.vision, self.lean, self.budget = True, False, 0

    @staticmethod
    def _text(blocks):
        if isinstance(blocks, str):
            return blocks
        return "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")

    def translate(self, body):
        msgs = [{"role": "system", "content": self._text(body.get("system", ""))}]
        for m in body["messages"]:
            content = m["content"] if isinstance(m["content"], list) else [{"type": "text", "text": m["content"]}]
            if m["role"] == "assistant":
                calls = [{"id": b["id"], "type": "function", "function": {"name": b["name"], "arguments": json.dumps(b.get("input") or {})}}
                         for b in content if b.get("type") == "tool_use"]
                msg = {"role": "assistant", "content": self._text(content) or None}
                if calls:
                    msg["tool_calls"] = calls
                msgs.append(msg)
                continue
            parts, images = [], []
            for b in content:
                if b.get("type") == "tool_result":
                    inner = b.get("content") or []
                    msgs.append({"role": "tool", "tool_call_id": b["tool_use_id"], "content": self._text(inner) or "(no text)"})
                    for x in inner if isinstance(inner, list) else []:
                        if x.get("type") == "image":
                            images.append(x)
                elif b.get("type") == "text":
                    parts.append({"type": "text", "text": b["text"]})
                elif b.get("type") == "image":
                    images.append(b)
            if images and not self.vision:
                parts.append({"type": "text", "text": "(The page image was left out: this model can't read images. "
                                                      "Use the page text; for handwritten pages say so in flags.)"})
                images = []
            for x in images:   # tool messages can't carry images on most providers: send them as the user
                s = x["source"]
                parts.append({"type": "image_url", "image_url": {"url": f"data:{s['media_type']};base64,{s['data']}"}})
            if parts:
                if images and not any(p["type"] == "text" for p in parts):
                    parts.insert(0, {"type": "text", "text": "The page image(s) from the tool call above:"})
                content = parts if images else "\n\n".join(p["text"] for p in parts)
                msgs.append({"role": "user", "content": content})
        out = {"model": self.model, "messages": msgs, "max_tokens": body.get("max_tokens", 4000)}
        if body.get("tools"):
            out["tools"] = [{"type": "function", "function": {"name": t["name"], "description": t.get("description", ""),
                                                               "parameters": t["input_schema"]}} for t in body["tools"]]
        return out

    def trim(self, req):
        """Keep a request under the provider's per-request token budget: shorten the oldest tool results first."""
        if not self.budget:
            return req
        est = lambda: len(json.dumps(req, ensure_ascii=False)) // 4   # noqa: E731  (≈4 characters a token)
        msgs = req["messages"]
        last_assistant = max((i for i, m in enumerate(msgs) if m["role"] == "assistant"), default=len(msgs))
        for cut in (600, 200, 60):
            for m in msgs[1:last_assistant]:
                if est() <= self.budget:
                    return req
                if m["role"] == "tool" and isinstance(m.get("content"), str) and len(m["content"]) > cut:
                    m["content"] = m["content"][:cut] + " …(trimmed)"
        return req

    @staticmethod
    def _repair(err):
        i = err.find("{")
        try:
            gen = json.loads(json.loads(err[i:])["error"]["failed_generation"])
        except (ValueError, KeyError, TypeError):
            return None
        calls = gen if isinstance(gen, list) else [gen]
        tc = []
        for n, c in enumerate(calls):
            name = str(c.get("name", "")).split("/")[-1].split(".")[-1]
            if not name:
                return None
            tc.append({"id": f"repaired_{int(time.time() * 1000)}_{n}", "type": "function",
                       "function": {"name": name, "arguments": json.dumps(c.get("arguments") or c.get("parameters") or {})}})
        return {"choices": [{"message": {"content": None, "tool_calls": tc}}]}

    def __call__(self, body):
        headers = {"Authorization": f"Bearer {self.key}"}
        if self.provider == "openrouter":
            headers.update({"HTTP-Referer": "https://sadaqah-e-jariyah.pages.dev", "X-Title": "Sadaqah-e-Jariyah sorter"})
        req = self.trim(self.translate(body))
        for attempt in range(3):
            try:
                out = self._post(self.base + "/chat/completions", req, headers)
                break
            except RuntimeError as e:
                fixed = self._repair(str(e))
                if fixed is not None:     # the model named a tool "functions/x": take its call as meant
                    out = fixed
                    break
                if "tool_use_failed" not in str(e) or attempt == 2:
                    raise
        u = out.get("usage") or {}
        self.usage["input"] += u.get("prompt_tokens", 0)
        self.usage["output"] += u.get("completion_tokens", 0)
        self.usage["cache_read"] += (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        self.usage["calls"] += 1
        msg = (out.get("choices") or [{}])[0].get("message") or {}
        content = []
        if msg.get("content"):
            content.append({"type": "text", "text": msg["content"]})
        for c in msg.get("tool_calls") or []:
            try:
                args = json.loads(c["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_unparsed": c["function"].get("arguments")}
            content.append({"type": "tool_use", "id": c["id"], "name": c["function"]["name"], "input": args})
        return {"content": content, "stop_reason": "tool_use" if msg.get("tool_calls") else "end_turn"}
