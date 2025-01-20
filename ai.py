"""
ai.py — Multi-provider AI reply generation
Фаза 4: динамические промпты с плейсхолдерами TOPIC / TARGET_REFERENCE.
Никаких хардкодных имён — всё передаётся через параметры.
"""

from __future__ import annotations

import asyncio
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)
from typing import Literal, Optional

from config import get_settings, logger

Provider = Literal["openai", "gemini", "perplexity", "groq"]

AI_SKIP = "AI_SKIP"
REPLY_SKIP = AI_SKIP  # back-compat

_ENGLISH_ONLY_PREFIX = (
    "CRITICAL INSTRUCTION: You MUST respond in English ONLY. "
    "NEVER use Russian, Turkish, Arabic, Chinese, or any other language. "
    "English ONLY — this is a hard requirement, no exceptions.\n\n"
)


def _is_english_reply(text: str) -> bool:
    cyrillic_count = sum(1 for c in text if "\u0400" <= c <= "\u04ff")
    if cyrillic_count > 2:
        return False
    ascii_count = sum(1 for c in text if ord(c) < 128)
    return ascii_count / max(len(text), 1) >= 0.85


# ─────────────────────────────────────────────────────────────────────────────
# Фаза 4: Шаблонизированные промпты (никаких хардкодных имён)
# ─────────────────────────────────────────────────────────────────────────────

# CRITIC_PROMPT — технически острый, тематический ответ
# TOPIC и TARGET_REFERENCE передаются динамически через generate_reply()
CRITIC_PROMPT_TEMPLATE = (
    "You are a Senior Backend Engineer / {PERSONA}. "
    "Analyze the tweet about {TOPIC}.\n\n"
    "Your task:\n"
    "1. Identify the low-level root cause (C-level, memory, runtime, or protocol layer if applicable).\n"
    "2. Give ONE sharp, technically precise take — no hedging.\n"
    "3. If relevant, recommend the author explore: {TARGET_REFERENCE}.\n\n"
    "Tone: technically impeccable, dry, authoritative. No emojis. No links. No hashtags.\n\n"
    "Rules:\n"
    "- English only\n"
    "- ABSOLUTE MAXIMUM 2 sentences. Count them. Stop after 2.\n"
    "- Target 100-180 chars. Hard ceiling 280 chars.\n"
    "- Specific is better than vague — levels, syscalls, data structures > generic wisdom\n"
    "- Do NOT start with @mentions\n"
    "- Do NOT use 'great point', 'I agree', 'absolutely'\n\n"
    "Good examples:\n"
    "  'GIL contention is the tell — switch to multiprocessing or use asyncio correctly'\n"
    "  'classic thundering herd on your connection pool, add jitter to reconnect backoff'\n"
    "  'that malloc pattern will fragment your heap badly under load, look at jemalloc'\n\n"
    "Bad examples (never):\n"
    "  'Great insight! The dynamics you described are indeed fascinating...'\n"
    "  'As a senior engineer I can confirm that best practices are key #engineering'"
)

# Дефолтный промпт для торгового аккаунта Маркус
DEFAULT_SYSTEM_PROMPT = (
    "You are Marcus, an independent trader based in NYC. "
    "10+ years trading ES futures, 0DTE SPX options and BTC. "
    "You swing trade macro setups, fade retail crowding, and follow options flow closely. "
    "Skeptical of the Fed, think most retail traders overtrade. "
    "You read Zerohedge, follow @spotgamma, @SqueezeMetrics, @MacroAlf, @GameofTrades_. "
    "Outside markets: into combat sports, stoic philosophy, occasional whiskey takes.\n\n"
    "Your voice on X: dry, confident, occasionally sarcastic. "
    "You drop specific levels, flow data, or a contrarian angle — then shut up. "
    "No cheerleading, no hedging everything with 'but DYOR'. "
    "You sound like someone who's been burned enough times to stop being cocky, "
    "but still has conviction.\n\n"
    "When you see a post — reply with ONE sharp take. "
    "Something you'd actually type between watching the tape.\n\n"
    "Rules:\n"
    "- English only\n"
    "- ABSOLUTE MAXIMUM 2 sentences. Count them. Stop after sentence 2.\n"
    "- Target 100-160 chars. Hard ceiling 240 chars.\n"
    "- No hashtags, no emojis, no 'great point', no 'I agree', no 'absolutely'\n"
    "- Casual but sharp — like texting a trading buddy, not writing a report\n"
    "- Specific is better than vague — levels, indicators, flow > generic wisdom\n\n"
    "Good examples:\n"
    "  'VIX term structure still inverted, that's the tell nobody's watching'\n"
    "  'gamma flip at 5200 — above that dealers are forced buyers all day'\n"
    "  'retail piling in while GEX went negative yesterday, not a great combo'\n"
    "  'BTC dominance breaking out usually means alts get wrecked first'\n"
    "  'been wrong before but this smells like a stop hunt before the real move'\n\n"
    "Bad examples (never do this):\n"
    "  'Great insight! The market dynamics you described are indeed fascinating...'\n"
    "  'I completely agree with your analysis of the current macroeconomic situation.'\n"
    "  'As a professional trader I can confirm that risk management is key #trading'"
)

COMMENT_SYSTEM_PROMPT = (
    "You are Marcus, an independent trader based in NYC. "
    "10+ years trading ES futures, 0DTE SPX options and BTC. "
    "You swing trade macro setups, fade retail crowding, and follow options flow closely. "
    "Skeptical of the Fed, think most retail traders overtrade. "
    "Outside markets: into combat sports, stoic philosophy, occasional whiskey takes.\n\n"
    "Your voice on X: casual, warm, occasionally dry. Like texting a buddy.\n\n"
    "Someone left a comment. "
    "Reply to THEIR comment naturally — agree, add color, push back lightly, or just acknowledge.\n\n"
    "Rules:\n"
    "- English only\n"
    "- ABSOLUTE MAXIMUM 2 sentences. Count them. Stop after sentence 2.\n"
    "- Target 100-160 chars. Hard ceiling 240 chars.\n"
    "- No hashtags, no emojis, no 'great point', no 'absolutely'\n"
    "- NEVER start with @mentions — X adds them automatically\n"
    "- Reply to the COMMENT, not to the original post author\n"
    "- Sound human — like a real reply, not a report\n"
    "- Specific > generic, but casual is fine here\n\n"
    "Good examples:\n"
    "  'yeah Abu Dhabi has been on a mission lately, Hormuz bypass is no joke'\n"
    "  'exactly, once GEX flips negative that's when it gets messy'\n"
    "  'fair point, ADNOC has been quietly building serious infrastructure'\n"
    "  'retail always piles in at the worst time lol'\n\n"
    "Bad examples (never do this):\n"
    "  'Great insight! The dynamics you described are indeed fascinating...'\n"
    "  'As a professional trader I can confirm that risk management is key #trading'"
)


def build_critic_prompt(
    topic: str,
    target_reference: str = "",
    persona: str = "Tech Lead",
) -> str:
    """
    Фаза 4: строит CRITIC_PROMPT из шаблона с динамическими переменными.

    Args:
        topic: Тема поста (например "Python GIL", "Rust memory model", "DNS latency")
        target_reference: Ссылка/ресурс для рекомендации автору твита.
                          Пример: "CPython internals docs", "статью Brendan Gregg по perf"
                          Если пустая строка — рекомендация не включается.
        persona: Роль/персонаж AI. Например "Backend Engineer", "SRE", "Systems Programmer"

    Returns:
        Готовый системный промпт для generate_reply()

    Пример вызова:
        prompt = build_critic_prompt(
            topic="Python async/await performance",
            target_reference="the asyncio internals talk from PyCon 2024",
            persona="Backend Engineer specializing in high-throughput systems",
        )
        reply, provider = await generate_reply(post_text, system_prompt=prompt)
    """
    return CRITIC_PROMPT_TEMPLATE.format(
        TOPIC=topic,
        PERSONA=persona,
        TARGET_REFERENCE=target_reference or "relevant docs / reference implementation",
    )


# ─────────────────────────────────────────────────────────────────────────────
# AI clients (lazy init, reset on settings reload)
# ─────────────────────────────────────────────────────────────────────────────

_openai_client = None
_gemini_model_cache: dict[str, object] = {}
_perplexity_client = None
_groq_client = None


def reset_ai_clients() -> None:
    global _openai_client, _gemini_model_cache, _perplexity_client, _groq_client
    _openai_client = None
    _gemini_model_cache = {}
    _perplexity_client = None
    _groq_client = None


def _get_openai_client():
    global _openai_client
    if _openai_client is None:
        from openai import AsyncOpenAI

        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OpenAI API key not configured. Go to 'API Keys' tab.")
        _openai_client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _openai_client


def _get_perplexity_client():
    global _perplexity_client
    if _perplexity_client is None:
        from openai import AsyncOpenAI

        settings = get_settings()
        if not settings.perplexity_api_key:
            raise ValueError("Perplexity API key not configured. Go to 'API Keys' tab.")
        _perplexity_client = AsyncOpenAI(
            api_key=settings.perplexity_api_key,
            base_url="https://api.perplexity.ai",
        )
    return _perplexity_client


def _get_groq_client():
    global _groq_client
    if _groq_client is None:
        from openai import AsyncOpenAI

        settings = get_settings()
        if not settings.groq_api_key:
            raise ValueError("Groq API key not configured. Go to 'API Keys' tab.")
        _groq_client = AsyncOpenAI(
            api_key=settings.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _groq_client


def _get_gemini_model(system_prompt: str):
    import google.generativeai as genai

    settings = get_settings()
    if not settings.gemini_api_key:
        raise ValueError("Gemini API key not configured. Go to 'API Keys' tab.")
    cache_key = (settings.gemini_api_key[:16] + system_prompt)[:80]
    if cache_key not in _gemini_model_cache:
        genai.configure(api_key=settings.gemini_api_key)
        for model_name in [
            "gemini-2.0-flash",
            "gemini-1.5-flash-latest",
            "gemini-1.5-flash",
        ]:
            try:
                m = genai.GenerativeModel(
                    model_name=model_name, system_instruction=system_prompt
                )
                _gemini_model_cache[cache_key] = m
                _gemini_model_cache[cache_key + ":name"] = model_name
                logger.debug(f"[AI:gemini] Using model: {model_name}")
                break
            except Exception as e:
                logger.debug(f"[AI:gemini] Model {model_name} unavailable: {e}")
        if cache_key not in _gemini_model_cache:
            raise ValueError(
                "No working Gemini model found. Check your API key and quota."
            )
    return _gemini_model_cache[cache_key]


def _build_prompt(post_text: str, comment_text: Optional[str]) -> str:
    if comment_text:
        return (
            f"[Context - original post]:\n{post_text}\n\n"
            f"[Reply to THIS comment]:\n{comment_text}\n\n"
            f"Write a short reply to the comment above. "
            f"Do NOT start with @mentions. Do NOT address the original post author."
        )
    return f"Post:\n{post_text}\n\nWrite your reply:"


async def _generate_openai(
    post_text: str, comment_text: Optional[str], system_prompt: str
) -> str:
    client = _get_openai_client()
    response = await client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_prompt(post_text, comment_text)},
        ],
        max_tokens=80,
        temperature=0.85,
        top_p=0.9,
        frequency_penalty=0.3,
        presence_penalty=0.2,
    )
    return response.choices[0].message.content.strip()


async def _generate_gemini(
    post_text: str, comment_text: Optional[str], system_prompt: str
) -> str:
    import google.generativeai as genai

    model = _get_gemini_model(system_prompt)
    loop = asyncio.get_running_loop()
    response = await loop.run_in_executor(
        None,
        lambda: model.generate_content(
            _build_prompt(post_text, comment_text),
            generation_config={
                "max_output_tokens": 80,
                "temperature": 0.85,
                "top_p": 0.9,
            },
        ),
    )
    return response.text.strip()


async def _generate_perplexity(
    post_text: str, comment_text: Optional[str], system_prompt: str
) -> str:
    client = _get_perplexity_client()
    response = await client.chat.completions.create(
        model="sonar",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_prompt(post_text, comment_text)},
        ],
        max_tokens=80,
        temperature=0.85,
        top_p=0.9,
    )
    return response.choices[0].message.content.strip()


async def _generate_groq(
    post_text: str, comment_text: Optional[str], system_prompt: str
) -> str:
    client = _get_groq_client()
    response = await client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": _build_prompt(post_text, comment_text)},
        ],
        max_tokens=80,
        temperature=0.85,
        top_p=0.9,
    )
    return response.choices[0].message.content.strip()


async def generate_reply(
    post_text: str,
    comment_text: Optional[str] = None,
    provider: Optional[Provider] = None,
    system_prompt: Optional[str] = None,
    # Фаза 4: параметры для динамического CRITIC_PROMPT
    topic: Optional[str] = None,
    target_reference: Optional[str] = None,
    persona: Optional[str] = None,
) -> tuple[str, Provider]:
    """
    Generate a reply using the selected provider.

    Фаза 4: если переданы topic/target_reference/persona — использует CRITIC_PROMPT_TEMPLATE
    вместо дефолтного промпта Маркуса.

    Args:
        post_text:        Текст твита для ответа
        comment_text:     Текст комментария (если отвечаем на коммент, а не на пост)
        provider:         AI провайдер (openai/gemini/perplexity/groq)
        system_prompt:    Явный системный промпт (приоритет над topic/persona)
        topic:            Тема для CRITIC_PROMPT (Фаза 4)
        target_reference: Ссылка/ресурс для рекомендации (Фаза 4)
        persona:          Роль AI (Фаза 4)

    Returns:
        (reply_text, provider_used)
    """
    settings = get_settings()

    if provider is None:
        provider = settings.default_ai_provider  # type: ignore

    # ── Выбор промпта (приоритет: explicit > critic > comment > default) ──
    if system_prompt is None:
        if topic:
            # Фаза 4: динамический CRITIC_PROMPT с плейсхолдерами
            system_prompt = build_critic_prompt(
                topic=topic,
                target_reference=target_reference or "",
                persona=persona or "Senior Backend Engineer",
            )
            logger.debug(
                f"[AI] Using CRITIC_PROMPT | topic={topic!r} | ref={target_reference!r}"
            )
        elif comment_text:
            system_prompt = COMMENT_SYSTEM_PROMPT
        else:
            system_prompt = DEFAULT_SYSTEM_PROMPT

    # Всегда добавляем English-only prefix
    if not system_prompt.startswith("CRITICAL INSTRUCTION"):
        system_prompt = _ENGLISH_ONLY_PREFIX + system_prompt

    configured: dict[str, bool] = {
        "openai": bool(settings.openai_api_key),
        "gemini": bool(settings.gemini_api_key),
        "perplexity": bool(settings.perplexity_api_key),
        "groq": bool(settings.groq_api_key),
    }

    logger.debug(
        f"[AI] Provider: {provider} | Keys: {[p for p, ok in configured.items() if ok]}"
    )

    if not configured.get(provider, False):
        raise RuntimeError(
            f"API key for '{provider}' is not configured.\n"
            f"Go to 'API Keys' tab in the GUI, add the key and click Save."
        )

    async def _try_generate(prov: str) -> str:
        if prov == "openai":
            return await _generate_openai(post_text, comment_text, system_prompt)
        elif prov == "gemini":
            return await _generate_gemini(post_text, comment_text, system_prompt)
        elif prov == "perplexity":
            return await _generate_perplexity(post_text, comment_text, system_prompt)
        else:
            return await _generate_groq(post_text, comment_text, system_prompt)

    try:
        text = await _try_generate(provider)
        text = text.strip("\"'").strip()

        # English-only guard
        if not text.upper().startswith("SKIP") and not _is_english_reply(text):
            logger.warning(f"[AI:{provider}] Non-English reply — retrying")
            text = await _try_generate(provider)
            text = text.strip("\"'").strip()
            if not _is_english_reply(text):
                logger.error(f"[AI:{provider}] Still non-English — skipping")
                return AI_SKIP, provider

        # Off-topic guard
        if text.upper().startswith("SKIP"):
            logger.info(f"[AI:{provider}] Off-topic — SKIP")
            return AI_SKIP, provider

        if len(text) > 280:
            chunk = text[:277]
            cut = max(chunk.rfind("."), chunk.rfind("!"), chunk.rfind("?"))
            text = chunk[: cut + 1].strip() if cut > 60 else chunk.strip()
            logger.warning(f"[AI:{provider}] Truncated to {len(text)} chars")

        logger.success(f"[AI:{provider}] Generated reply ({len(text)} chars)")
        return text, provider

    except Exception as e:
        err_str = str(e)
        logger.error("[AI:{}] Failed: {}", provider, err_str[:300])
        raise RuntimeError(f"[AI:{provider}] {err_str}") from e
