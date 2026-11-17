"""Provider-agnostic LLM client.

It works against any of:

- Anthropic (``claude-...``)
- OpenAI / Azure OpenAI (``gpt-...``)
- Google Gemini (``gemini-...``)
- Ollama (``ollama/<model>``) — a local, no-cost server speaking the
  OpenAI chat API (``http://localhost:11434/v1``); used for demos / tests so
  metered cloud tokens are never spent, and by air-gapped deployments.

Configuration is read from environment variables and the model name.
The client exposes a single ``generate(prompt)`` API; provider selection
is hidden from callers.

Selection rules (highest priority first):
1. Explicit ``provider=`` arg.
2. ``LR_LLM_PROVIDER`` env var (``anthropic`` | ``openai`` | ``google`` | ``ollama``).
3. Model-name prefix:
   - ``claude-...`` → anthropic
   - ``gpt-...`` / ``o1-...`` / ``o3-...`` → openai
   - ``gemini-...`` → google
   - ``ollama/...`` → ollama (prefix stripped before the call)
4. First provider with a configured API key; then Ollama when
   ``OLLAMA_HOST`` / ``LR_OLLAMA_BASE_URL`` names a server.

Each provider's import is lazy so users only need the SDK they actually use.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Literal, Protocol

logger = logging.getLogger(__name__)

Provider = Literal["anthropic", "openai", "google", "ollama"]

OLLAMA_DEFAULT_BASE_URL = "http://localhost:11434/v1"
OLLAMA_DEFAULT_MODEL = "qwen2.5:7b-instruct"


class _Backend(Protocol):
    def generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 32000,
        seed: int | None = 42,
    ) -> str: ...


class LLMUnavailableError(RuntimeError):
    """Raised when no LLM provider can be resolved (no key + no SDK)."""


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    provider: Provider
    input_tokens: int | None = None
    output_tokens: int | None = None


def detect_provider(model: str | None = None) -> Provider | None:
    """Resolve provider from explicit env var or model-name prefix."""
    explicit = (os.environ.get("LR_LLM_PROVIDER") or "").strip().lower()
    if explicit in ("anthropic", "openai", "google", "ollama"):
        return explicit  # type: ignore[return-value]
    name = (model or "").lower()
    if name.startswith(("ollama/", "ollama:")):
        return "ollama"
    if name.startswith("claude"):
        return "anthropic"
    if name.startswith(("gpt", "o1-", "o3-", "o4-")):
        return "openai"
    if name.startswith("gemini"):
        return "google"
    return None


def first_configured_provider() -> Provider | None:
    """Pick the first provider with an API key set in the environment."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY"):
        return "google"
    if ollama_base_url(explicit_only=True):
        return "ollama"
    return None


def ollama_base_url(*, explicit_only: bool = False) -> str | None:
    """The Ollama OpenAI-compatible endpoint.

    ``LR_OLLAMA_BASE_URL`` wins; else ``OLLAMA_HOST`` (the
    variable the ``ollama`` CLI itself honors, e.g. ``http://gpu-box:11434``)
    with ``/v1`` appended. With ``explicit_only`` the localhost default is NOT
    assumed — auto-selection must not pick a server nobody configured."""
    explicit = (os.environ.get("LR_OLLAMA_BASE_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    host = (os.environ.get("OLLAMA_HOST") or "").strip()
    if host:
        if not host.startswith(("http://", "https://")):
            host = "http://" + host
        host = host.rstrip("/")
        return host if host.endswith("/v1") else host + "/v1"
    return None if explicit_only else OLLAMA_DEFAULT_BASE_URL


def generate(
    prompt: str,
    *,
    model: str | None = None,
    provider: Provider | None = None,
    temperature: float = 0.0,
    max_tokens: int = 32000,
    seed: int | None = 42,
    json_schema: dict | None = None,
) -> LLMResponse | None:
    """Run one completion against the resolved provider.

    Returns ``None`` if no provider is reachable (no API key configured *and*
    no env var override). The caller is expected to handle this — typically
    by emitting an "LLM unavailable" stub instead of raising.

    Determinism / coverage knobs (Task C):
      - ``temperature=0.0`` is the default for Architect / Analyst calls so
        repeated runs with identical prompts produce identical output. The
        previous default (Anthropic temp=1.0) caused suggestions to swing
        wildly between runs with little overlap.
      - ``max_tokens=8192`` lifts the 4096 ceiling that capped how many
        candidates a single brainstorm pass could articulate.
      - ``seed=42`` is honored by providers that support it (OpenAI);
        ignored by providers that don't (Anthropic determinism comes from
        temperature=0).
      - ``json_schema`` asks for structured output. The OpenAI and Ollama
        backends constrain decoding to the schema; the other backends ignore
        it, so callers must still validate the text.
    """
    chosen = provider or detect_provider(model) or first_configured_provider()
    if chosen is None:
        logger.info("No LLM provider configured; returning None for prompt-generate.")
        return None
    target_model = model or _default_model(chosen)
    try:
        backend = _make_backend(chosen)
    except LLMUnavailableError as e:
        logger.warning("LLM provider %s unavailable: %s", chosen, e)
        return None
    try:
        extra = {"json_schema": json_schema} if json_schema is not None else {}
        text = backend.generate(
            prompt,
            model=target_model,
            temperature=temperature,
            max_tokens=max_tokens,
            seed=seed,
            **extra,
        )
    except Exception as e:  # provider SDKs raise their own errors
        logger.warning("LLM call to %s failed: %s", chosen, e)
        return None
    usage = getattr(backend, "last_usage", None) or (None, None)
    return LLMResponse(
        text=text or "",
        model=target_model,
        provider=chosen,
        input_tokens=usage[0],
        output_tokens=usage[1],
    )


def _default_model(provider: Provider) -> str:
    return {
        "anthropic": os.environ.get("LR_LLM_MODEL") or "claude-opus-4-7",
        "openai": os.environ.get("LR_LLM_MODEL") or "gpt-4o",
        "google": os.environ.get("LR_LLM_MODEL") or "gemini-2.5-flash",
        "ollama": os.environ.get("LR_LLM_MODEL") or OLLAMA_DEFAULT_MODEL,
    }[provider]


def _make_backend(provider: Provider) -> _Backend:
    if provider == "anthropic":
        return _AnthropicBackend()
    if provider == "openai":
        return _OpenAIBackend()
    if provider == "google":
        return _GoogleBackend()
    if provider == "ollama":
        return _OllamaBackend()
    raise LLMUnavailableError(f"unknown provider: {provider!r}")


# ---------------------------------------------------------------------------
# Per-provider backends — each lazy-imports its own SDK
# ---------------------------------------------------------------------------


class _AnthropicBackend:
    def __init__(self) -> None:
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise LLMUnavailableError("ANTHROPIC_API_KEY not set")
        try:
            import anthropic  # type: ignore
        except ImportError as e:
            raise LLMUnavailableError("anthropic SDK not installed") from e
        self._client = anthropic.Anthropic(api_key=api_key)

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 32000,
        seed: int | None = 42,  # noqa: ARG002 — Anthropic has no seed param
        json_schema: dict | None = None,  # noqa: ARG002 — the caller validates
    ) -> str:
        msg = self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        usage = getattr(msg, "usage", None)
        if usage is not None:
            self.last_usage = (usage.input_tokens, usage.output_tokens)
        parts = []
        for block in msg.content:
            text = getattr(block, "text", None)
            if text:
                parts.append(text)
        return "".join(parts)


# OpenAI reasoning models: they take max_completion_tokens and reasoning_effort, not temperature.
_REASONING_PREFIXES = ("gpt-5", "o1", "o3", "o4")


class _OpenAIBackend:
    def __init__(self) -> None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise LLMUnavailableError("OPENAI_API_KEY not set")
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise LLMUnavailableError("openai SDK not installed") from e
        self._client = OpenAI(api_key=api_key)

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 32000,
        seed: int | None = 42,
        json_schema: dict | None = None,
    ) -> str:
        kwargs: dict = {"model": model, "messages": [{"role": "user", "content": prompt}]}
        if model.startswith("gpt-6"):
            # gpt-6 models reject max_tokens and "minimal". With "none" they do not reason, and
            # they take a temperature.
            kwargs |= {"max_completion_tokens": max_tokens, "reasoning_effort": "none",
                       "temperature": temperature}
        elif model.startswith(_REASONING_PREFIXES):
            # These models reject max_tokens and temperature. Their reasoning tokens count
            # against the cap, so minimal effort leaves the cap for the answer.
            kwargs |= {"max_completion_tokens": max_tokens, "reasoning_effort": "minimal"}
        else:
            kwargs |= {"temperature": temperature, "max_tokens": max_tokens}
        if seed is not None:
            kwargs["seed"] = seed
        if json_schema is not None:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": "output", "schema": json_schema},
            }
        resp = self._client.chat.completions.create(**kwargs)
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self.last_usage = (usage.prompt_tokens, usage.completion_tokens)
        return (resp.choices[0].message.content or "") if resp.choices else ""


class _OllamaBackend(_OpenAIBackend):
    """Ollama through its OpenAI-compatible ``/v1`` endpoint.

    Same wire format as OpenAI, so the OpenAI SDK is reused with a
    ``base_url`` and a placeholder key (Ollama ignores it). No cloud
    tokens are spent; the cost is local GPU time. ``seed`` and
    ``temperature`` are honored by Ollama's OpenAI shim."""

    def __init__(self) -> None:  # noqa: D107 — see class docstring
        try:
            from openai import OpenAI  # type: ignore
        except ImportError as e:
            raise LLMUnavailableError("openai SDK not installed (needed for the Ollama backend)") from e
        self._client = OpenAI(api_key=os.environ.get("OLLAMA_API_KEY") or "ollama", base_url=ollama_base_url())

    def generate(self, prompt: str, *, model: str, **kwargs) -> str:
        name = model
        for prefix in ("ollama/", "ollama:"):
            if name.lower().startswith(prefix):
                name = name[len(prefix):]
        return super().generate(prompt, model=name, **kwargs)


class _GoogleBackend:
    def __init__(self) -> None:
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            raise LLMUnavailableError("GOOGLE_API_KEY/GEMINI_API_KEY not set")
        try:
            from google import genai  # type: ignore
        except ImportError as e:
            raise LLMUnavailableError("google-genai SDK not installed") from e
        self._client = genai.Client(api_key=api_key)

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        temperature: float = 0.0,
        max_tokens: int = 32000,
        seed: int | None = 42,  # noqa: ARG002 — Gemini SDK doesn't expose seed
        json_schema: dict | None = None,
    ) -> str:
        # google-genai accepts generation_config-style kwargs via the
        # ``config`` parameter; on older SDK versions it's silently ignored,
        # which is acceptable — we still get deterministic-ish output
        # because the default Gemini sampler is low-temperature.
        try:
            from google.genai import types as _genai_types  # type: ignore

            schema = (
                {"response_mime_type": "application/json", "response_json_schema": json_schema}
                if json_schema is not None else {}
            )
            config = _genai_types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
                **schema,
            )
            resp = self._client.models.generate_content(
                model=model, contents=prompt, config=config
            )
        except Exception:
            # SDK shape mismatch — fall through to bare call so the path
            # still works on older google-genai installs.
            resp = self._client.models.generate_content(model=model, contents=prompt)
        usage = getattr(resp, "usage_metadata", None)
        if usage is not None:
            # Google bills thinking tokens at the output price.
            thoughts = getattr(usage, "thoughts_token_count", None) or 0
            self.last_usage = (usage.prompt_token_count, (usage.candidates_token_count or 0) + thoughts)
        return getattr(resp, "text", "") or ""
