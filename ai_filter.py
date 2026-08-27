"""
ai_filter.py
────────────
Detects AI-generated content from Instagram post metadata.

Detection happens in three layers (in order of confidence):
  1. Instagram API flag  — checks `is_generated_media`, `ai_agent_data`,
                           `sponsor_tags`, and similar fields baked into the
                           xdt_shortcode_media payload.
  2. Hashtag matching    — checks every hashtag extracted from the caption
                           against a configurable blocklist.
  3. Caption keyword scan — searches the raw caption text for known AI tool
                           phrases (e.g. "made with midjourney", "--ar 16:9").

Usage:
    from ai_filter import is_ai_generated

    result = is_ai_generated(metadata)
    if result["is_ai"]:
        print(f"Skipping AI post: {result['reason']}")
"""

import re
from config import (
    AI_FILTER_ENABLED,
    AI_FILTER_HASHTAGS,
    AI_FILTER_KEYWORDS,
    AI_FILTER_BLOCKED_USERNAMES,
)

# ── Pre-compile for speed ──
_KW_PATTERNS = [re.compile(re.escape(kw), re.IGNORECASE) for kw in AI_FILTER_KEYWORDS]

# Normalise hashtag blocklist to lowercase once at import time
_BLOCKED_TAGS = {t.lower().strip("#") for t in AI_FILTER_HASHTAGS}


def is_ai_generated(metadata: dict) -> dict:
    """
    Analyse a post's metadata dict and decide whether it is AI-generated.

    Parameters
    ----------
    metadata : dict
        The post metadata dict produced by extract_metadata_from_api()
        or extract_post_metadata().

    Returns
    -------
    dict with keys:
        "is_ai"  : bool  — True if the post appears to be AI-generated
        "reason" : str   — human-readable explanation (empty string if not AI)
        "layer"  : str   — which detection layer triggered: "api", "hashtag",
                           "keyword", "username", or ""
    """
    if not AI_FILTER_ENABLED:
        return {"is_ai": False, "reason": "", "layer": ""}

    # ── Layer 1: Instagram API flags ─────────────────────────────────
    # These fields appear in the raw xdt_shortcode_media payload.
    # We store them through extract_metadata_from_api if present.
    if metadata.get("is_generated_media"):
        return {
            "is_ai": True,
            "reason": "Instagram flagged this as AI-generated (is_generated_media=True)",
            "layer": "api",
        }

    if metadata.get("ai_agent_data"):
        return {
            "is_ai": True,
            "reason": f"Instagram AI agent data present: {metadata['ai_agent_data']}",
            "layer": "api",
        }

    # ── Layer 2: Blocked username ────────────────────────────────────
    owner = (metadata.get("owner_username") or "").lower()
    if owner and owner in AI_FILTER_BLOCKED_USERNAMES:
        return {
            "is_ai": True,
            "reason": f"Owner @{owner} is on the AI-account blocklist",
            "layer": "username",
        }

    # ── Layer 3: Hashtag matching ────────────────────────────────────
    post_tags = {t.lower().strip("#") for t in (metadata.get("hashtags") or [])}
    matched_tags = post_tags & _BLOCKED_TAGS
    if matched_tags:
        return {
            "is_ai": True,
            "reason": f"AI hashtag(s) detected: #{', #'.join(sorted(matched_tags))}",
            "layer": "hashtag",
        }

    # ── Layer 4: Caption keyword scan ───────────────────────────────
    caption = metadata.get("caption") or ""
    if caption:
        for pattern in _KW_PATTERNS:
            m = pattern.search(caption)
            if m:
                return {
                    "is_ai": True,
                    "reason": f"AI keyword in caption: '{m.group(0)}'",
                    "layer": "keyword",
                }

    return {"is_ai": False, "reason": "", "layer": ""}


def filter_summary(skipped_log: list[dict]) -> str:
    """
    Return a human-readable summary string from a list of skipped-post records.

    Each record should have keys: shortcode, reason, layer.
    """
    if not skipped_log:
        return "No AI posts filtered."

    by_layer: dict[str, int] = {}
    for rec in skipped_log:
        layer = rec.get("layer", "unknown")
        by_layer[layer] = by_layer.get(layer, 0) + 1

    lines = [f"  🤖 AI filter: skipped {len(skipped_log)} post(s)"]
    for layer, count in sorted(by_layer.items()):
        emoji = {"api": "🔵", "hashtag": "🟠", "keyword": "🟡", "username": "🔴"}.get(layer, "⚪")
        lines.append(f"      {emoji} {layer}: {count}")
    return "\n".join(lines)
