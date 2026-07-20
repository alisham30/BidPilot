"""Single LLM entry point.

Every Claude call in BidPilot goes through `extract()`: schema-enforced
structured output (server-side JSON-schema constraint), validated by Pydantic,
with retry-on-invalid. No free-text parsing of LLM responses anywhere.

Tests mock at this boundary (`app.llm.extract` / `app.llm.embed`).
"""
from __future__ import annotations

import logging
from typing import Type, TypeVar

import anthropic
from pydantic import BaseModel

from .config import settings, sources

log = logging.getLogger("bidpilot.llm")

T = TypeVar("T", bound=BaseModel)

_client: anthropic.Anthropic | None = None
_openai_client = None


class LLMError(RuntimeError):
    """Raised when the LLM cannot produce a valid structured response."""


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(
            api_key=settings.anthropic_api_key or None,
            timeout=float(sources.llm.timeout_seconds),
        )
    return _client


def _clip(text: str) -> str:
    limit = sources.llm.max_input_chars
    if len(text) <= limit:
        return text
    log.warning("input clipped from %d to %d chars", len(text), limit)
    return text[:limit] + "\n\n[... document truncated at configured max_input_chars ...]"


def _get_openai():
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI
        _openai_client = OpenAI(api_key=settings.openai_api_key or None,
                                timeout=float(sources.llm.timeout_seconds))
    return _openai_client


def _extract_openai(schema: Type[T], system: str, content: str, max_tokens: int) -> T:
    """OpenAI structured-output path (schema-enforced, same contract)."""
    client = _get_openai()
    resp = client.beta.chat.completions.parse(
        model=sources.llm.openai_model,
        max_tokens=max_tokens,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": content}],
        response_format=schema,
    )
    choice = resp.choices[0]
    if choice.message.refusal:
        raise LLMError(f"model refused request ({schema.__name__})")
    if choice.finish_reason == "length":
        raise LLMError(f"output truncated at max_tokens ({schema.__name__})")
    if choice.message.parsed is None:
        raise LLMError(f"no parseable output for {schema.__name__}")
    return choice.message.parsed


def extract(schema: Type[T], system: str, user_content: str, max_tokens: int = 16000) -> T:
    """One structured LLM call. Returns a validated instance of `schema`.

    Retries (max_retries from config) on invalid/unparseable output, then
    raises LLMError — callers turn that into an escalation, never fake data.
    """
    content = _clip(user_content)
    last_err: Exception | None = None

    if sources.llm.provider == "openai":
        for attempt in range(sources.llm.max_retries + 1):
            try:
                return _extract_openai(schema, system, content, max_tokens)
            except LLMError as e:
                last_err = e
                log.warning("invalid structured output on attempt %d: %s", attempt + 1, e)
            except Exception as e:
                last_err = e
                log.warning("openai error on attempt %d: %s", attempt + 1, e)
        raise LLMError(f"failed after {sources.llm.max_retries + 1} attempts: {last_err}")

    client = _get_client()
    for attempt in range(sources.llm.max_retries + 1):
        try:
            response = client.messages.parse(
                model=sources.llm.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
                output_format=schema,
            )
            if response.stop_reason == "refusal":
                raise LLMError(f"model refused request ({schema.__name__})")
            if response.stop_reason == "max_tokens":
                raise LLMError(f"output truncated at max_tokens ({schema.__name__})")
            parsed = response.parsed_output
            if parsed is None:
                raise LLMError(f"no parseable output for {schema.__name__}")
            return parsed
        except (anthropic.RateLimitError, anthropic.InternalServerError) as e:
            last_err = e  # SDK already backs off; one more outer retry
            log.warning("retryable API error on attempt %d: %s", attempt + 1, e)
        except LLMError as e:
            last_err = e
            log.warning("invalid structured output on attempt %d: %s", attempt + 1, e)
        except anthropic.APIStatusError as e:
            raise LLMError(f"API error {e.status_code}: {e.message}") from e
        except anthropic.APIConnectionError as e:
            last_err = e
            log.warning("connection error on attempt %d: %s", attempt + 1, e)

    raise LLMError(f"failed after {sources.llm.max_retries + 1} attempts: {last_err}")


def embed(texts: list[str]) -> list[list[float]]:
    """Embed texts for pgvector shortlisting (OpenAI text-embedding-3-small)."""
    resp = _get_openai().embeddings.create(model=sources.llm.embedding_model, input=texts)
    return [d.embedding for d in resp.data]
