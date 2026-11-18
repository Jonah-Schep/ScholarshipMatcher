"""Optional provider for any OpenAI-compatible endpoint (e.g. a university-hosted
gateway such as VT ARC, for data that must stay on institution-approved systems).

This file intentionally does not use the Anthropic SDK; the two providers are
interchangeable behind `scholarmatch.llm.LLM`.
"""
from __future__ import annotations

import json
import os
import time
from typing import Callable, Optional, Type

from pydantic import ValidationError

from . import LLM, LLMError, T


BACKOFF_SECONDS = (5.0, 10.0, 20.0)


def _retryable() -> tuple[type[BaseException], ...]:
    """Answers worth waiting out: 429 (too many requests in flight) and 5xx ("backend unavailable")."""
    from openai import InternalServerError, RateLimitError
    return (RateLimitError, InternalServerError)


def call_with_backoff(call, retryable: tuple[type[BaseException], ...], pauses=BACKOFF_SECONDS, sleep=time.sleep):
    """Run `call`, retrying `retryable` errors after each pause in `pauses`; other errors (timeouts) are raised."""
    for pause in pauses:
        try:
            return call()
        except retryable:
            sleep(pause)
    return call()


class OpenAICompatLLM(LLM):
    name = "openai_compat"

    def __init__(self) -> None:
        super().__init__()
        from openai import OpenAI

        self._model = os.environ.get("SM_OPENAI_MODEL", "")
        if not self._model:
            raise RuntimeError("Set SM_OPENAI_MODEL for the openai_compat provider.")
        # No SDK retries (WP8): on a timeout the server is usually still working, so a retry only adds a second
        # request in flight (VT ARC allows 10 per user, then 429). `_create` retries busy / rate-limit answers
        # itself, with longer pauses; a timeout fails the item, which then falls back to the rule-based score.
        self.client = OpenAI(base_url=os.environ.get("SM_OPENAI_BASE_URL") or None,
                             api_key=os.environ.get("SM_OPENAI_API_KEY") or "none",
                             max_retries=0, timeout=float(os.environ.get("SM_OPENAI_TIMEOUT", 300)))

    def _create(self, **kw):
        return call_with_backoff(lambda: self.client.chat.completions.create(**kw), _retryable())

    def model(self, role: str) -> str:
        return os.environ.get(f"SM_MODEL_{role.upper()}") or self._model

    def _structured(self, role: str, system: str, user: str, schema: Type[T]) -> tuple[T, int, int]:
        js = schema.model_json_schema()
        sys_prompt = (f"{system}\n\nRespond with ONE JSON object only, matching this JSON Schema:\n"
                      f"{json.dumps(js, sort_keys=True)}")
        last_err = ""
        for _attempt in range(2):
            try:
                resp = self._create(
                    model=self.model(role), temperature=0, seed=7,
                    response_format={"type": "json_object"},
                    messages=[{"role": "system", "content": sys_prompt},
                              {"role": "user", "content": user + last_err}])
            except Exception as e:
                raise LLMError(f"{role}: {type(e).__name__}: {e}") from e
            text = resp.choices[0].message.content or ""
            try:
                out = schema.model_validate_json(text[text.find("{"): text.rfind("}") + 1])
                u = resp.usage
                return out, getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0)
            except ValidationError as e:
                last_err = f"\n\nYour previous answer was invalid: {str(e)[:500]}\nReturn corrected JSON."
        raise LLMError(f"{role}: model did not return schema-valid JSON after one repair attempt.")

    def _chat(self, role: str, system: str, messages: list[dict],
              tools: Optional[list[dict]], run_tool: Optional[Callable[[str, dict], str]]) -> str:
        # No tool loop on this provider: the chat agent injects its context up front instead.
        try:
            resp = self._create(
                model=self.model(role), temperature=0, seed=7,
                messages=[{"role": "system", "content": system}, *messages])
        except Exception as e:
            raise LLMError(f"{type(e).__name__}: {e}") from e
        return (resp.choices[0].message.content or "").strip()
