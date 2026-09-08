"""Spoken summary generator for completed top-level turns.

When enabled on a project, generates a short, speech-friendly restatement of the
assistant's response upon terminal completion of a top-level session and attaches
it to MessageData.content as a second block:
    {"type": "spoken_summary", "text": "<= 3 sentences", "lang": "pt-BR"}
The original response (output_text) is never shortened, altered, or replaced.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from omnigent.model_fallbacks import (
    SPOKEN_SUMMARY_GEMINI_DEFAULT_MODEL,
    SPOKEN_SUMMARY_OPENAI_DEFAULT_MODEL,
)

if TYPE_CHECKING:
    from omnigent.entities import Conversation
    from omnigent.stores.conversation_store import ConversationStore

_logger = logging.getLogger(__name__)

#: Character length threshold below which the assistant text is already short
#: enough to be spoken directly; the rewrite is skipped. Matches ADR-0018.
SPOKEN_SUMMARY_THRESHOLD_CHARS: int = 320

#: Hard timeout (seconds) for the spoken summary rewrite call.
SPOKEN_SUMMARY_TIMEOUT_S: float = 10.0

# Common stopwords for fast, zero-dependency BCP-47 language detection
_PORTUGUESE_STOPWORDS = frozenset(
    {
        "de",
        "a",
        "o",
        "que",
        "e",
        "do",
        "da",
        "em",
        "um",
        "para",
        "é",
        "com",
        "não",
        "uma",
        "os",
        "no",
        "se",
        "na",
        "por",
        "mais",
        "as",
        "dos",
        "como",
        "mas",
        "foi",
        "ao",
        "ele",
        "das",
        "tem",
        "à",
        "seu",
        "sua",
        "ou",
        "ser",
        "quando",
        "muito",
        "está",
        "também",
        "pelo",
        "pela",
        "até",
        "isso",
        "ela",
        "entre",
        "depois",
        "sem",
        "mesmo",
        "aos",
        "ter",
        "seus",
        "quem",
        "nas",
        "me",
        "esse",
        "eles",
        "estão",
        "você",
        "tinha",
        "foram",
        "essa",
        "num",
        "nem",
        "suas",
        "meu",
        "minha",
        "têm",
        "numa",
        "pelos",
        "elas",
        "havia",
        "seja",
        "qual",
        "será",
        "nós",
        "tenho",
        "lhe",
        "deles",
        "este",
        "esta",
        "estou",
        "estamos",
        "fui",
        "fomos",
        "consegui",
        "arquivo",
        "tarefa",
        "executado",
        "resposta",
        "sucesso",
        "concluído",
        "ajudar",
        "código",
    }
)

_SPANISH_STOPWORDS = frozenset(
    {
        "el",
        "la",
        "de",
        "que",
        "y",
        "a",
        "en",
        "un",
        "ser",
        "se",
        "no",
        "haber",
        "por",
        "con",
        "su",
        "para",
        "como",
        "estar",
        "tener",
        "le",
        "lo",
        "todo",
        "pero",
        "más",
        "hacer",
        "o",
        "poder",
        "este",
        "ya",
        "otro",
        "ese",
        "si",
        "me",
        "primer",
        "porque",
        "dar",
        "cuando",
        "él",
        "muy",
        "sin",
        "vez",
        "mucho",
        "saber",
        "qué",
        "sobre",
        "mi",
        "alguno",
        "mismo",
        "yo",
        "también",
    }
)

_FRENCH_STOPWORDS = frozenset(
    {
        "le",
        "la",
        "de",
        "et",
        "un",
        "une",
        "est",
        "il",
        "que",
        "dans",
        "pour",
        "pas",
        "sur",
        "qui",
        "avec",
        "ce",
        "les",
        "des",
        "en",
        "du",
        "au",
        "sont",
        "ne",
        "par",
        "se",
        "plus",
        "nous",
        "vous",
        "cette",
        "comme",
        "mais",
    }
)

_GERMAN_STOPWORDS = frozenset(
    {
        "der",
        "die",
        "das",
        "und",
        "in",
        "den",
        "von",
        "zu",
        "mit",
        "sich",
        "des",
        "auf",
        "für",
        "ist",
        "im",
        "dem",
        "nicht",
        "ein",
        "eine",
        "als",
        "auch",
        "es",
        "an",
        "werden",
        "aus",
        "er",
        "hat",
        "dass",
        "sie",
        "nach",
        "wird",
    }
)

_ENGLISH_STOPWORDS = frozenset(
    {
        "the",
        "be",
        "to",
        "of",
        "and",
        "a",
        "in",
        "that",
        "have",
        "i",
        "it",
        "for",
        "not",
        "on",
        "with",
        "he",
        "as",
        "you",
        "do",
        "at",
        "this",
        "but",
        "his",
        "by",
        "from",
        "they",
        "we",
        "say",
        "her",
        "she",
        "or",
        "an",
        "will",
        "my",
        "one",
        "all",
        "would",
        "there",
        "their",
        "what",
        "so",
        "up",
        "out",
        "if",
        "about",
        "who",
        "get",
        "which",
        "go",
        "me",
        "when",
        "make",
        "can",
        "like",
        "time",
        "no",
        "just",
        "know",
        "take",
        "into",
        "your",
        "good",
        "some",
        "could",
        "them",
        "see",
        "other",
        "than",
        "then",
        "now",
        "look",
        "only",
        "come",
        "its",
        "over",
        "think",
        "also",
        "back",
        "after",
        "use",
        "how",
        "our",
        "work",
        "well",
        "completed",
        "successfully",
        "finished",
    }
)


def strip_markdown_for_speech(text: str) -> str:
    """Strip markdown formatting, code blocks, URLs, and noisy markup before rewrite.

    :param text: Raw assistant text.
    :returns: Plain prose suitable for the ear.
    """
    if not text:
        return ""
    # Strip fenced code blocks
    cleaned = re.sub(r"```[\s\S]*?```", " ", text)
    # Strip inline backticks
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    # Strip markdown images
    cleaned = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", cleaned)
    # Strip markdown links, keeping anchor text
    cleaned = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", cleaned)
    # Strip URLs
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    # Strip markdown headers, blockquotes, bullets from line beginnings
    cleaned = re.sub(r"^\s*[#>*\-]+\s*", "", cleaned, flags=re.MULTILINE)
    # Strip bold / italics markers
    cleaned = re.sub(r"(\*\*|__)(.*?)\1", r"\2", cleaned)
    cleaned = re.sub(r"(\*|_)(.*?)\1", r"\2", cleaned)
    # Strip strikethrough
    cleaned = re.sub(r"~~(.*?)~~", r"\1", cleaned)
    # Strip markdown table rows / delimiter rows
    cleaned = re.sub(r"^\s*\|.*\|\s*$", "", cleaned, flags=re.MULTILINE)
    # Collapse whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def detect_bcp47_language(text: str) -> str:
    """Infer BCP-47 language tag from text via stopword frequency. Defaults to 'en-US'.

    :param text: Text to analyze.
    :returns: Standard BCP-47 tag, e.g. ``"pt-BR"`` or ``"en-US"``.
    """
    if not text:
        return "en-US"
    words = re.findall(r"\b\w+\b", text.lower())
    if not words:
        return "en-US"

    scores = {
        "pt-BR": sum(1 for w in words if w in _PORTUGUESE_STOPWORDS),
        "es-ES": sum(1 for w in words if w in _SPANISH_STOPWORDS),
        "fr-FR": sum(1 for w in words if w in _FRENCH_STOPWORDS),
        "de-DE": sum(1 for w in words if w in _GERMAN_STOPWORDS),
        "en-US": sum(1 for w in words if w in _ENGLISH_STOPWORDS),
    }
    best_lang, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score == 0:
        return "en-US"
    return best_lang


def clamp_sentences(text: str, max_sentences: int = 3) -> str:
    """Ensure text contains at most *max_sentences* sentences.

    :param text: The raw spoken text.
    :param max_sentences: Maximum sentences to retain (default 3).
    :returns: Clamped text.
    """
    cleaned = text.strip().strip("\"'")
    if not cleaned:
        return ""
    # Split on sentence terminals followed by whitespace
    parts = re.split(r"(?<=[.!?])\s+", cleaned)
    if len(parts) <= max_sentences:
        return cleaned
    return " ".join(parts[:max_sentences])


def build_spoken_summary_prompt(cleaned_text: str, language: str = "auto") -> str:
    """Construct the rewriter instructions following ADR-0018 guidelines.

    :param cleaned_text: Sanitized response text.
    :param language: Target language setting ("auto" or a BCP-47 tag).
    :returns: Formatted prompt string.
    """
    lang_instruction = (
        "Same language they were answered in."
        if not language or language == "auto"
        else (
            f"The summary MUST be in {language} regardless of the language of the "
            "original response."
        )
    )
    instruction = (
        "Rewrite this assistant reply as something spoken aloud to the person who asked. "
        f"{lang_instruction} "
        "At most three short sentences. Plain spoken prose only. "
        "Drop code blocks, file paths, bullet lists, tables, and long numbers -- "
        "say what happened and the outcome instead. "
        "Written to be heard, not read. "
        "Never add information, never comment on the answer's quality, never say you are "
        "summarising. "
        "Reply with the spoken text only."
    )
    return f"{instruction}\n\n---\n{cleaned_text}"


def resolve_spoken_summary_settings(
    conv: Conversation | None,
    conversation_store: ConversationStore | None,
    *,
    override_enabled: bool | None = None,
    override_language: str | None = None,
) -> tuple[bool, str]:
    """Resolve whether spoken summary is enabled and the target language.

    :param conv: The session conversation entity, or None.
    :param conversation_store: The conversation store, or None.
    :param override_enabled: Caller explicit enabled override.
    :param override_language: Caller explicit language override.
    :returns: Tuple of (enabled: bool, language: str).
    """
    if override_enabled is not None:
        return override_enabled, override_language or "auto"

    # 1. Check conversation labels if explicitly set
    if conv and conv.labels:
        if "spoken_summary_enabled" in conv.labels:
            raw_val = conv.labels["spoken_summary_enabled"].strip().lower()
            enabled = raw_val in ("true", "1", "yes", "on")
            lang = conv.labels.get("spoken_summary_language", "auto").strip() or "auto"
            return enabled, lang

    # 2. Check project config
    if conv and conv.project_id and conversation_store is not None:
        try:
            if hasattr(conversation_store, "get_project_config"):
                p_cfg = conversation_store.get_project_config(conv.project_id)
            else:
                p_cfg = {}
            if p_cfg:
                spoken_cfg = p_cfg.get("spoken_summary")
                if isinstance(spoken_cfg, dict):
                    enabled = bool(spoken_cfg.get("enabled", False))
                    lang = str(
                        spoken_cfg.get("language")
                        or spoken_cfg.get("lang")
                        or p_cfg.get("spoken_summary_language")
                        or p_cfg.get("language")
                        or "auto"
                    ).strip()
                    return enabled, lang or "auto"
                if isinstance(spoken_cfg, bool):
                    enabled = spoken_cfg
                    lang = str(
                        p_cfg.get("spoken_summary_language") or p_cfg.get("language") or "auto"
                    ).strip()
                    return enabled, lang or "auto"
                if "spoken_summary_enabled" in p_cfg:
                    enabled = bool(p_cfg.get("spoken_summary_enabled", False))
                    lang = str(
                        p_cfg.get("spoken_summary_language") or p_cfg.get("language") or "auto"
                    ).strip()
                    return enabled, lang or "auto"
        except Exception:  # noqa: BLE001
            _logger.debug("Failed to read project config for project_id=%s", conv.project_id)

    # 3. Global env fallback (default: False, "auto")
    env_enabled = os.environ.get("OMNIGENT_SPOKEN_SUMMARY_ENABLED", "").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )
    env_lang = os.environ.get("OMNIGENT_SPOKEN_SUMMARY_LANGUAGE", "auto").strip() or "auto"
    return env_enabled, env_lang


def resolve_spoken_summary_model(model_override: str | None = None) -> str:
    """Resolve the model id to use for the spoken summary rewrite.

    :param model_override: Optional explicit model id override.
    :returns: Resolved model string.
    """
    if model_override and model_override.strip():
        return model_override.strip()

    env_model = os.environ.get("OMNIGENT_SPOKEN_SUMMARY_MODEL", "").strip()
    if env_model:
        return env_model

    # Default to Gemini flash if Gemini credential is present, else OpenAI mini
    if (os.environ.get("GEMINI_API_KEY") or "").strip() or (
        os.environ.get("GOOGLE_API_KEY") or ""
    ).strip():
        return f"gemini/{SPOKEN_SUMMARY_GEMINI_DEFAULT_MODEL}"

    return SPOKEN_SUMMARY_OPENAI_DEFAULT_MODEL


def resolve_spoken_summary_connection(model: str) -> dict[str, str] | None:
    """Resolve connection parameters (API keys, base URL) for the given model.

    :param model: The model identifier string.
    :returns: Connection parameters dictionary, or None.
    """
    # 1. Environment variable credentials
    if model.startswith(("gemini/", "google/")):
        api_key = (
            os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        ).strip()
        if api_key:
            return {"api_key": api_key}
    elif model.startswith("anthropic/"):
        api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if api_key:
            return {"api_key": api_key}
    else:
        api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
        base_url = (os.environ.get("OPENAI_BASE_URL") or "").strip()
        conn: dict[str, str] = {}
        if api_key:
            conn["api_key"] = api_key
        if base_url:
            conn["base_url"] = base_url
        if conn:
            return conn

    # 2. Check local provider configs (~/.omnigent/config.yaml)
    try:
        from omnigent.onboarding.detected import effective_config_with_detected
        from omnigent.onboarding.provider_config import load_config, load_providers

        config = load_config()
        providers = load_providers(effective_config_with_detected(config))
        is_anthropic = model.startswith(("anthropic/", "claude"))
        is_gemini = model.startswith(("gemini/", "google"))
        family_name = "gemini" if is_gemini else ("anthropic" if is_anthropic else "openai")

        for entry in providers.values():
            fam = entry.family(family_name)
            if fam and fam.api_key:
                conn = {"api_key": fam.api_key}
                if fam.base_url:
                    conn["base_url"] = fam.base_url
                return conn
    except Exception:  # noqa: BLE001
        _logger.debug("Failed to resolve provider config for spoken summary model %r", model)

    return None


def should_generate_spoken_summary(
    conv: Conversation | None,
    text: str,
    *,
    is_terminal_completion: bool,
    deny_reason: str | None,
    enabled: bool,
) -> bool:
    """Strictly verify whether all four conditions for spoken summary generation hold.

    1. The setting is enabled.
    2. The session is top-level (parent_conversation_id is null and kind != 'sub_agent').
    3. The turn reached terminal response.completed (not failed, not cancelled, no deny).
    4. The assistant text is non-empty and longer than SPOKEN_SUMMARY_THRESHOLD_CHARS (320).

    :param conv: The conversation entity.
    :param text: The final assistant output text.
    :param is_terminal_completion: Whether the turn completed with response.completed.
    :param deny_reason: Deny reason if policy denied the turn.
    :param enabled: Whether spoken summary is enabled for this session.
    :returns: True only when all four conditions are satisfied.
    """
    if not enabled:
        return False

    if not is_terminal_completion or deny_reason is not None:
        return False

    if not text or len(text.strip()) <= SPOKEN_SUMMARY_THRESHOLD_CHARS:
        return False

    if conv is None:
        return False

    # Top-level session check: parent must be null, not sub-agent
    if (
        conv.parent_conversation_id is not None
        or getattr(conv, "parent_session_id", None) is not None
    ):
        return False
    if getattr(conv, "kind", "default") == "sub_agent":
        return False

    return True


async def generate_spoken_summary(
    text: str,
    *,
    language: str = "auto",
    model_override: str | None = None,
    llm_client: Any | None = None,
    timeout_s: float = SPOKEN_SUMMARY_TIMEOUT_S,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Perform the spoken rewrite LLM call and return (spoken_summary_part, usage_delta).

    Never raises: on any failure, error, timeout, or empty model response, logs at
    info/debug level and returns (None, None).

    :param text: Raw assistant text.
    :param language: Language setting ("auto" or BCP-47 tag).
    :param model_override: Optional model override.
    :param llm_client: Optional pre-configured LLM client instance (for testing).
    :param timeout_s: Hard timeout in seconds.
    :returns: (spoken_summary_content_part, usage_delta) or (None, None).
    """
    cleaned_text = strip_markdown_for_speech(text)
    if not cleaned_text:
        return None, None

    prompt = build_spoken_summary_prompt(cleaned_text, language=language)
    model = resolve_spoken_summary_model(model_override)
    connection = resolve_spoken_summary_connection(model)

    try:
        if llm_client is None:
            from omnigent.llms import Client

            client = Client()
        else:
            client = llm_client

        async def _call() -> Any:
            return await client.responses.create(
                model=model,
                input=[{"role": "user", "content": prompt}],
                tools=[],
                connection_params=connection,
                timeout=int(timeout_s),
            )

        resp = await asyncio.wait_for(_call(), timeout=timeout_s)
    except Exception as exc:  # noqa: BLE001
        _logger.info("Spoken summary generation failed or timed out: %s; skipping summary", exc)
        return None, None

    try:
        # Extract text from response
        if isinstance(resp, str):
            raw_summary = resp
        elif hasattr(resp, "text") and isinstance(resp.text, str) and resp.text:
            raw_summary = resp.text
        elif hasattr(resp, "output"):
            from omnigent.llms.summarize import extract_summary_text

            raw_summary = extract_summary_text(resp)
        else:
            raw_summary = getattr(resp, "text", "") or ""

        # Post-clean summary
        summary_text = clamp_sentences(raw_summary, max_sentences=3)
        if not summary_text:
            return None, None

        # Determine language tag
        if language and language != "auto":
            lang_tag = language
        else:
            lang_tag = detect_bcp47_language(summary_text)

        spoken_summary_part = {
            "type": "spoken_summary",
            "text": summary_text,
            "lang": lang_tag,
        }

        # Build usage delta for attribution
        usage_delta: dict[str, Any] | None = None
        usage_obj = getattr(resp, "usage", None)
        if usage_obj is not None:
            in_tok = int(getattr(usage_obj, "input_tokens", 0) or 0)
            out_tok = int(getattr(usage_obj, "output_tokens", 0) or 0)
            tot_tok = int(getattr(usage_obj, "total_tokens", 0) or (in_tok + out_tok))
            if in_tok or out_tok or tot_tok:
                usage_delta = {
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "total_tokens": tot_tok,
                    "by_model": {
                        model: {
                            "input_tokens": in_tok,
                            "output_tokens": out_tok,
                            "total_tokens": tot_tok,
                        }
                    },
                }

        return spoken_summary_part, usage_delta
    except Exception as exc:  # noqa: BLE001
        _logger.info("Error formatting spoken summary: %s; skipping summary", exc)
        return None, None
