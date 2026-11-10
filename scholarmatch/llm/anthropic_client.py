"""Claude provider (official Anthropic SDK)."""
from __future__ import annotations

from typing import Callable, Optional, Type

import anthropic
from pydantic import ValidationError

from .. import config
from . import LLM, LLMError, T

# Models that accept the server-side refusal fallback (`fallbacks="default"`).
_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}
_FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOOL_TURNS = 6


class AnthropicLLM(LLM):
    name = "anthropic"

    def __init__(self) -> None:
        super().__init__()
        # Credentials resolve from the environment (ANTHROPIC_API_KEY / auth profile).
        self.client = anthropic.Anthropic(max_retries=4, timeout=120.0)

    def _structured(self, role: str, system: str, user: str, schema: Type[T]) -> tuple[T, int, int]:
        model = self.model(role)
        kwargs = dict(
            model=model, max_tokens=8000, system=system,
            messages=[{"role": "user", "content": user}],
            output_format=schema,
            output_config={"effort": config.ROLE_EFFORT.get(role, "medium")},
        )
        try:
            if model in _FALLBACK_MODELS:
                resp = self.client.beta.messages.parse(betas=[_FALLBACK_BETA], fallbacks="default", **kwargs)
            else:
                resp = self.client.messages.parse(**kwargs)
        except anthropic.APIError as e:  # SDK already retried 429/5xx/connection errors
            raise LLMError(f"{type(e).__name__}: {e}") from e
        except ValidationError as e:     # output did not match the schema (e.g. truncated)
            raise LLMError(f"Structured output failed validation: {str(e)[:200]}") from e
        _check_stop(resp)
        if resp.parsed_output is None:
            raise LLMError("Model returned no parseable structured output.")
        return resp.parsed_output, resp.usage.input_tokens, resp.usage.output_tokens

    def _chat(self, role: str, system: str, messages: list[dict],
              tools: Optional[list[dict]], run_tool: Optional[Callable[[str, dict], str]]) -> str:
        msgs = list(messages)
        kwargs = dict(model=self.model(role), max_tokens=4000, system=system,
                      output_config={"effort": config.ROLE_EFFORT.get(role, "medium")})
        if tools:
            kwargs["tools"] = tools
        for _ in range(MAX_TOOL_TURNS):
            try:
                resp = self.client.messages.create(messages=msgs, **kwargs)
            except anthropic.APIError as e:
                raise LLMError(f"{type(e).__name__}: {e}") from e
            self._count(input_tokens=resp.usage.input_tokens, output_tokens=resp.usage.output_tokens)
            if resp.stop_reason == "tool_use" and run_tool:
                msgs.append({"role": "assistant", "content": resp.content})
                results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        try:
                            out, err = run_tool(b.name, dict(b.input)), False
                        except Exception as e:  # tool errors go back to the model, not the user
                            out, err = f"Tool error: {e}", True
                        results.append({"type": "tool_result", "tool_use_id": b.id,
                                        "content": out, "is_error": err})
                msgs.append({"role": "user", "content": results})
                continue
            _check_stop(resp)
            return "".join(b.text for b in resp.content if b.type == "text").strip()
        raise LLMError("Chat agent exceeded its tool-call budget.")


def _check_stop(resp) -> None:
    if resp.stop_reason == "refusal":
        raise LLMError("The model declined this request.")
    if resp.stop_reason == "max_tokens":
        raise LLMError("Model output was truncated (max_tokens).")
