"""LLM access layer.

All agents talk to an `LLM` object, never to a vendor SDK. The base class adds the
response cache that makes repeat runs byte-identical: the cache key covers provider,
model, effort, prompt version, system prompt, user content and output schema.
"""
from __future__ import annotations

import json
import threading
from typing import Callable, Optional, Type, TypeVar

from pydantic import BaseModel, ValidationError

from .. import config, db
from ..textutil import sha

T = TypeVar("T", bound=BaseModel)


class LLMError(RuntimeError):
    """Any failure to get a usable answer (network, refusal, truncation, bad JSON)."""


class LLM:
    name = "base"
    available = True

    def __init__(self) -> None:
        self.usage = {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0}
        self._lock = threading.Lock()

    # -- to implement ---------------------------------------------------------
    def _structured(self, role: str, system: str, user: str, schema: Type[T]) -> tuple[T, int, int]:
        raise NotImplementedError

    def _chat(self, role: str, system: str, messages: list[dict],
              tools: Optional[list[dict]], run_tool: Optional[Callable[[str, dict], str]]) -> str:
        raise NotImplementedError

    def model(self, role: str) -> str:
        return config.model_for(role)

    # -- public ---------------------------------------------------------------
    def structured(self, role: str, system: str, user: str, schema: Type[T], use_cache: bool = True) -> T:
        key = sha(self.name, self.model(role), config.ROLE_EFFORT.get(role, ""), config.PROMPT_VERSION,
                  system, user, schema.model_json_schema())
        if use_cache:
            hit = db.cache_get(key)
            if hit is not None:
                try:
                    out = schema.model_validate_json(hit)
                    self._count(cache_hits=1)
                    return out
                except ValidationError:
                    pass  # schema changed since it was cached: recompute
        out, tin, tout = self._structured(role, system, user, schema)
        db.cache_put(key, role, self.model(role), out.model_dump_json(), tin, tout)
        self._count(calls=1, input_tokens=tin, output_tokens=tout)
        return out

    def chat(self, role: str, system: str, messages: list[dict],
             tools: Optional[list[dict]] = None,
             run_tool: Optional[Callable[[str, dict], str]] = None) -> str:
        self._count(calls=1)
        return self._chat(role, system, messages, tools, run_tool)

    def _count(self, **kw: int) -> None:
        with self._lock:
            for k, v in kw.items():
                self.usage[k] += v or 0


class OfflineLLM(LLM):
    """No provider configured. Agents detect `available == False` and use their
    deterministic heuristic path instead, so the product still works end to end."""
    name = "offline"
    available = False

    def model(self, role: str) -> str:
        return "none"

    def _structured(self, *a, **k):
        raise LLMError("No LLM provider configured (offline mode).")

    def _chat(self, *a, **k):
        raise LLMError("No LLM provider configured (offline mode).")


def get_llm(provider: Optional[str] = None) -> LLM:
    provider = provider or config.llm_provider()
    if provider == "anthropic":
        from .anthropic_client import AnthropicLLM
        return AnthropicLLM()
    if provider == "openai_compat":
        from .openai_compat import OpenAICompatLLM
        return OpenAICompatLLM()
    if provider == "offline":
        return OfflineLLM()
    raise ValueError(f"Unknown SM_LLM_PROVIDER: {provider!r}")


def dumps(obj) -> str:
    """Stable JSON for prompts (sorted keys => identical bytes => cache hits)."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, indent=1, default=str)
