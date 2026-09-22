"""The shipped ComfyUI template is only valid JSON after substitution."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

WORKFLOW = Path(__file__).resolve().parents[1] / "workflows" / "character.json"

# Mirrors the substitution in imagegen._generate_comfyui.
TOKENS = {
    "%prompt%": 'a woman at a window, "golden hour", 50mm\nsecond line',
    "%negative%": "text, watermark",
    "%seed%": "774102",
    "%width%": "1080",
    "%height%": "1350",
    "%reference%": "character-reference.jpg",
}


def _substitute(template: str, tokens: dict[str, str]) -> str:
    for token, value in tokens.items():
        template = template.replace(token, json.dumps(value)[1:-1])
    return template


@pytest.fixture()
def rendered() -> dict:
    return json.loads(_substitute(WORKFLOW.read_text(encoding="utf-8"), TOKENS))


def test_template_has_no_leftover_tokens(rendered):
    assert "%" not in json.dumps(rendered).replace("%reference%", "")


def test_numeric_tokens_land_as_numbers(rendered):
    assert rendered["5"]["inputs"]["width"] == 1080
    assert rendered["5"]["inputs"]["height"] == 1350
    assert rendered["3"]["inputs"]["seed"] == 774102


def test_awkward_prompt_text_survives_escaping(rendered):
    assert rendered["6"]["inputs"]["text"] == TOKENS["%prompt%"]
    assert rendered["7"]["inputs"]["text"] == TOKENS["%negative%"]


def test_graph_is_wired_end_to_end(rendered):
    sampler = rendered["3"]["inputs"]
    assert sampler["positive"] == ["6", 0]
    assert sampler["negative"] == ["7", 0]
    assert sampler["latent_image"] == ["5", 0]
    assert rendered["8"]["inputs"]["samples"] == ["3", 0]
    assert rendered["9"]["inputs"]["images"] == ["8", 0]


def test_a_prompt_full_of_quotes_cannot_break_the_document():
    nasty = dict(TOKENS, **{"%prompt%": '", "evil": "injected'})
    parsed = json.loads(_substitute(WORKFLOW.read_text(encoding="utf-8"), nasty))
    assert parsed["6"]["inputs"]["text"] == nasty["%prompt%"]
    assert "evil" not in parsed["6"]["inputs"]
