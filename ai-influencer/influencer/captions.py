"""Caption writing.

Two writers are available. The Claude writer produces captions in the persona's
voice; the template writer is a dependency-free fallback so the pipeline still
runs (and still posts something readable) with no API key and no network.

Hashtags are not written by the model. They come from the planner, which keeps
them deterministic, auditable and inside Instagram's limits.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass

from .config import Config
from .planner import PostPlan
from . import safety

CAPTION_SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "alt_text": {"type": "string"},
    },
    "required": ["caption", "alt_text"],
    "additionalProperties": False,
}


class CaptionError(Exception):
    """Raised when a caption could not be produced."""


@dataclass(frozen=True)
class Caption:
    text: str
    alt_text: str
    writer: str


def _system_prompt(cfg: Config) -> str:
    persona = cfg.persona
    voice = persona.voice
    avoid = ", ".join(voice.avoid) if voice.avoid else "nothing in particular"
    return (
        f"You write Instagram captions as {persona.name} (@{persona.handle}), "
        f"a fictional character. Biography: {persona.bio or 'not specified'}. "
        f"Home base: {persona.home_base or 'not specified'}.\n\n"
        f"Voice: {voice.tone}. Sentence style: {voice.sentence_style}. "
        f"Emoji use: {voice.emoji}. Avoid: {avoid}.\n\n"
        "Rules:\n"
        "- Write in first person as the character.\n"
        "- Do not write hashtags; they are added separately.\n"
        f"- Keep the caption under {cfg.caption.max_chars} characters.\n"
        "- Never claim to be a real human, and never claim to have done "
        "something physically impossible for a digital character (eating, "
        "travelling, meeting people in person) as if it literally happened. "
        "Write about the scene as an image the character is presenting.\n"
        "- No engagement bait, no 'double tap if', no fake giveaways.\n"
        "- alt_text is a plain, literal description of the image for screen "
        "readers. Describe what is visible, not the mood.\n"
    )


def _claude_caption(plan: PostPlan, cfg: Config) -> Caption:
    try:
        import anthropic
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise CaptionError(
            "the 'anthropic' package is not installed; install it with "
            "'pip install ai-influencer[captions]' or set caption.provider = \"template\""
        ) from exc

    client = anthropic.Anthropic()
    user_message = (
        f"{plan.caption_brief}\n\n"
        f"The image was generated from this prompt: {plan.image_prompt}\n\n"
        "Write the caption and the alt text."
    )
    request = {
        "model": cfg.caption.model,
        "max_tokens": 2000,
        "system": _system_prompt(cfg),
        "messages": [{"role": "user", "content": user_message}],
        "output_config": {
            "effort": cfg.caption.effort,
            "format": {"type": "json_schema", "schema": CAPTION_SCHEMA},
        },
    }

    # Server-side fallback keeps a single refusal from breaking a scheduled run.
    # If the account is not enrolled in the beta the request is retried plain.
    try:
        response = client.beta.messages.create(
            **request,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.BadRequestError:
        response = client.messages.create(**request)
    except anthropic.APIStatusError as exc:
        raise CaptionError(f"Claude API error: {exc}") from exc
    except anthropic.APIConnectionError as exc:
        raise CaptionError(f"could not reach the Claude API: {exc}") from exc

    if response.stop_reason == "refusal":
        detail = getattr(response, "stop_details", None)
        category = getattr(detail, "category", None) or "unspecified"
        raise CaptionError(
            f"the model declined to write this caption (category: {category}); "
            "review the scene text in the persona config"
        )

    text = next((b.text for b in response.content if b.type == "text"), "")
    if not text.strip():
        raise CaptionError("the model returned an empty response")

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CaptionError(f"the model returned invalid JSON: {exc}") from exc

    return Caption(
        text=data["caption"].strip(),
        alt_text=data["alt_text"].strip(),
        writer=f"claude:{cfg.caption.model}",
    )


_CLOSERS = (
    "More of this week soon.",
    "Back tomorrow.",
    "That is the whole update.",
    "Saving the rest for later.",
)


def _sentence(text: str) -> str:
    stripped = text.strip().rstrip(".")
    if not stripped:
        return ""
    return f"{stripped[:1].upper()}{stripped[1:]}."


def _template_caption(plan: PostPlan, cfg: Config) -> Caption:
    rng = random.Random(f"{plan.date}:{plan.slot}:{cfg.persona.seed}")
    scene_short = plan.scene.split(",")[0].strip()
    # Theme and scene say different things, so the caption never restates itself.
    lines = [_sentence(plan.theme), _sentence(scene_short)]
    rng.shuffle(lines)
    closer = rng.choice(_CLOSERS)
    text = "\n\n".join([*lines, closer])
    return Caption(
        text=text[: cfg.caption.max_chars],
        alt_text=f"{cfg.persona.name}, {scene_short}.",
        writer="template",
    )


def write(plan: PostPlan, cfg: Config) -> Caption:
    """Produce a caption, apply the AI disclosure, and validate it."""
    provider = cfg.caption.provider
    if provider == "template":
        caption = _template_caption(plan, cfg)
    elif provider == "claude":
        caption = _claude_caption(plan, cfg)
    elif provider == "auto":
        try:
            caption = _claude_caption(plan, cfg)
        except CaptionError:
            caption = _template_caption(plan, cfg)
    else:
        raise CaptionError(
            f"unknown caption provider {provider!r}; expected auto, claude or template"
        )

    labelled = safety.apply_disclosure(caption.text, cfg)
    inline_tags = () if cfg.caption.hashtags_in_first_comment else plan.hashtags
    safety.check_caption(labelled, inline_tags, cfg).raise_for_problems()
    return Caption(text=labelled, alt_text=caption.alt_text, writer=caption.writer)
