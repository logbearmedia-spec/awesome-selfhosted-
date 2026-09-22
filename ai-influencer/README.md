# ai-influencer

Self-hosted automation that runs an Instagram page for an AI character: it
decides what to post, renders the image, writes the caption in the character's
voice, publishes it, and remembers what it did so tomorrow is different.

One command does a full post:

```bash
influencer -c config/persona.toml run
```

Point a scheduler at that and the account runs itself.

## What this actually does, and what it doesn't

It **does** plan a content calendar, generate images with a consistent
character, write captions, publish through the Instagram Graph API, keep a
durable posting history, and enforce the AI-disclosure label on every caption.

It **does not** reply to comments or DMs, follow accounts, or engage with anyone
else's posts. That is deliberate. Automated engagement is what gets accounts
restricted, and the Graph API does not expose most of it anyway.

Before anything works you need an **Instagram Professional account** (Business
or Creator) linked to a Facebook Page, plus a Meta app with the
`instagram_basic`, `instagram_content_publish` and `pages_read_engagement`
permissions. Personal accounts cannot publish through the API at all. Meta caps
publishing at **50 posts per rolling 24 hours**; this tool caps itself far lower.

## Quick start

```bash
cd ai-influencer
pip install -e '.[captions,dev]'

# Everything below runs offline, with no API keys and nothing published.
influencer -c config/persona.toml plan --days 7
influencer -c config/persona.toml run --date 2026-03-04 --dry-run
influencer -c config/persona.toml status
```

A dry run does the whole pipeline except the Instagram call: it renders a
placeholder image to `out/media/`, writes a caption, and records the slot. That
is enough to see a month of the account's output before you connect anything.

`influencer doctor` tells you what is still missing:

```bash
influencer -c config/persona.toml doctor
influencer -c config/persona.toml doctor --check-instagram   # also calls the API
```

## The character

`config/persona.toml` is the whole content plan and holds no secrets — every
credential in it is a `${ENV_VAR}` reference — so it is meant to be committed.

Three things keep the character recognisable from post to post:

1. **`persona.appearance`** — a locked description pasted verbatim into every
   image prompt.
2. **`persona.seed`** — a fixed generator seed.
3. **`image.options.reference_image`** — a clean, front-facing portrait of the
   character. This matters most. Put the file at
   `ai-influencer/assets/character-reference.jpg` and providers with an
   image-conditioning model will hold the same face across posts.

Content comes from **pillars**: named themes, each with a pool of scenes. The
planner rotates across pillars and scenes, avoiding whatever it used recently,
so the feed does not repeat itself. Planning is deterministic — the same date
always produces the same plan — which is why `plan` can show you next week
before it happens. The shipped persona has 5 pillars and 42 scenes, about six
weeks of daily posts before a scene comes round again. Add more scenes to
stretch that.

## Providers

Everything is pluggable and offline by default.

| Slot | Options | Notes |
|---|---|---|
| Images | `dryrun`, `comfyui`, `replicate` | `comfyui` is the self-hosted one |
| Captions | `auto`, `claude`, `template` | `auto` uses Claude, falls back to the template writer |
| Media hosting | `local`, `s3` | `s3` covers MinIO, Garage and Ceph via `path_style` |

**Captions** go through Claude (`claude-opus-5`) with a system prompt built from
the persona's voice, and come back as structured JSON so the caption and the
alt text are always separated. Without `ANTHROPIC_API_KEY` the `auto` provider
silently falls back to a template writer, so a scheduled run never fails just
because the API is unreachable. Hashtags are never model-written — they come
from the planner, which keeps them deterministic and inside the 30-tag limit.

**Media hosting is not optional for publishing.** Instagram fetches media from a
URL rather than accepting an upload, so the image has to be on public HTTP
before a post can be created. `local` + `public_base_url` works if you already
serve a directory; otherwise use `s3`, which is signed here with SigV4 directly
and needs no boto3.

For ComfyUI, point `image.options.workflow` at an API-format workflow JSON.
These tokens are substituted before it is queued, each JSON-escaped so a prompt
containing quotes cannot break the document: `%prompt%`, `%negative%`, `%seed%`,
`%width%`, `%height%`, `%reference%` (the uploaded reference image's filename).

## Scheduling

`schedule.times` sets the posting slots. `run` with no arguments posts only the
slot that is currently due, and a slot stays due for `slot_grace_minutes` after
its time — so a scheduler that fires late still posts instead of silently
skipping the day, and you can safely run the job more often than you post.

`.github/workflows/ai-influencer.yml` runs it on GitHub Actions and commits
`out/state.json` back to the branch. **That commit is load-bearing**: the state
file is the account's memory, and without it every run would believe it was the
first and post the same thing. For cron on your own box:

```cron
0,30 17,18 * * * cd /srv/ai-influencer && ./.venv/bin/influencer -c config/persona.toml run
```

## Safety

Two rules are enforced in code and cannot be configured away, because both
protect you rather than the tool:

- **Every caption carries an AI-generated disclosure.** Meta's policy and a
  growing number of advertising rules require synthetic content to be labelled,
  and an unlabelled synthetic persona is the fastest route to a ban.
- **Prompts that target a real, identifiable person are refused.** A synthetic
  character is fine. Putting a real person's likeness behind an automated
  account is impersonation, and in most places it is also illegal.

Use a character you have the rights to. If your reference image came from a
generator, you are fine. If it is a photograph of a real person, do not use it —
that is the one input that turns this from a character account into an
impersonation account.

Configurable on top of that: `safety.banned_topics` blocks substrings in prompts
and captions (the default list covers medical, financial and weight-loss claims,
which attract both platform enforcement and regulators), and
`schedule.max_posts_per_day` caps the cadence well under Meta's limit.

## Commands

| Command | What it does |
|---|---|
| `plan --days N` | Show upcoming posts without making anything |
| `run` | Produce and publish the slot that is due now |
| `run --dry-run` | Full pipeline, never calls Instagram |
| `run --no-publish` | Render for real, but stop before publishing |
| `run --date D --slot N --force` | Re-do a specific slot |
| `status` | Posting history and today's count |
| `doctor` | Check config, credentials and the reference image |

## Tests

```bash
python -m pytest
```

43 tests, all offline — they cover planner determinism and rotation, the safety
rules, state durability, and a full dry-run post.
