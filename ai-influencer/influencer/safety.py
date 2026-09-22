"""Checks that run before anything is published.

Two of these are not optional and cannot be configured away:

* every caption carries an AI-generated disclosure, because the account is a
  synthetic character and both Instagram's policy and several jurisdictions'
  advertising rules require the content to be labelled;
* prompts are refused if they aim the character at a named real person, since
  that turns a character account into an impersonation account.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Config

# Instagram's own limits.
MAX_CAPTION_CHARS = 2200
MAX_HASHTAGS = 30

# Phrases that indicate the prompt is trying to render a real, identifiable
# person rather than the configured character.
_REAL_PERSON_PATTERNS = (
    r"\blooks? like [A-Z][a-z]+ [A-Z][a-z]+",
    r"\bin the style of [A-Z][a-z]+ [A-Z][a-z]+'?s? (?:face|likeness)",
    r"\bdeepfake\b",
    r"\bcelebrity\s+(?:face|likeness|lookalike)\b",
    r"\bface\s+swap\b",
)


class SafetyError(Exception):
    """Raised when a post must not be published as composed."""


@dataclass(frozen=True)
class CheckResult:
    ok: bool
    problems: tuple[str, ...] = ()

    def raise_for_problems(self) -> None:
        if not self.ok:
            raise SafetyError("; ".join(self.problems))


def check_image_prompt(prompt: str, cfg: Config) -> CheckResult:
    problems: list[str] = []
    lowered = prompt.lower()

    for pattern in _REAL_PERSON_PATTERNS:
        if re.search(pattern, prompt, flags=re.IGNORECASE):
            problems.append(
                "image prompt appears to target a real person's likeness; the "
                "character must be synthetic and not resemble an identifiable individual"
            )
            break

    for topic in cfg.safety.banned_topics:
        if topic.lower() in lowered:
            problems.append(f"image prompt mentions a banned topic: {topic!r}")

    return CheckResult(ok=not problems, problems=tuple(problems))


def has_disclosure(caption: str, cfg: Config) -> bool:
    """True when the caption carries the configured AI label in some form."""
    lowered = caption.lower()
    if cfg.safety.disclosure and cfg.safety.disclosure.lower() in lowered:
        return True
    return any(tag.lower() in lowered for tag in cfg.safety.disclosure_hashtags)


def apply_disclosure(caption: str, cfg: Config) -> str:
    """Append the AI label if the caption does not already carry one."""
    if not cfg.safety.require_disclosure or has_disclosure(caption, cfg):
        return caption
    return f"{caption.rstrip()}\n\n{cfg.safety.disclosure}"


def check_caption(caption: str, hashtags: tuple[str, ...], cfg: Config) -> CheckResult:
    problems: list[str] = []
    lowered = caption.lower()

    if not caption.strip():
        problems.append("caption is empty")

    total = len(caption) + sum(len(t) + 1 for t in hashtags)
    if total > MAX_CAPTION_CHARS:
        problems.append(
            f"caption plus hashtags is {total} characters, over Instagram's "
            f"{MAX_CAPTION_CHARS} limit"
        )

    if len(hashtags) > MAX_HASHTAGS:
        problems.append(
            f"{len(hashtags)} hashtags, over Instagram's limit of {MAX_HASHTAGS}"
        )

    bad_tags = [t for t in hashtags if not re.fullmatch(r"#[0-9A-Za-z_]+", t)]
    if bad_tags:
        problems.append(f"malformed hashtags: {', '.join(bad_tags)}")

    if cfg.safety.require_disclosure and not has_disclosure(caption, cfg):
        problems.append("caption is missing the required AI-generated disclosure")

    for topic in cfg.safety.banned_topics:
        if topic.lower() in lowered:
            problems.append(f"caption mentions a banned topic: {topic!r}")

    return CheckResult(ok=not problems, problems=tuple(problems))


def check_rate_limit(posts_today: int, cfg: Config) -> CheckResult:
    cap = cfg.schedule.max_posts_per_day
    if posts_today >= cap:
        return CheckResult(
            ok=False,
            problems=(f"already published {posts_today} posts today, cap is {cap}",),
        )
    return CheckResult(ok=True)
