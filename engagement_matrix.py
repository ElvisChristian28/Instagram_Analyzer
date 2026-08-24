"""
engagement_matrix.py
────────────────────
Reads scraped post metadata and produces an engagement matrix with:
  - Raw metrics: likes, comments, views per post
  - Total interactions = likes + comments
  - Percentile-based performance tier: High / Average / Low
  - Per-hashtag and cross-hashtag summary statistics

Usage (standalone):
    python engagement_matrix.py

Usage (from code):
    from engagement_matrix import build_engagement_matrix, print_summary
    report = build_engagement_matrix()
    print_summary(report)
"""

import json
import os
import glob
import math
from datetime import datetime


# ──────────────── Constants ────────────────────────────────────────────

METADATA_DIR  = os.path.join("scraped_data", "metadata")
REPORTS_DIR   = os.path.join("scraped_data", "reports")
INDEX_PATTERN = os.path.join("scraped_data", "*_index.json")

# Percentile thresholds for tiering
LOW_PERCENTILE  = 25   # Below this  → "Low"
HIGH_PERCENTILE = 75   # Above this  → "High"
# Between 25th and 75th percentile   → "Average"


# ──────────────── Math helpers ─────────────────────────────────────────

def percentile(values: list[float], p: float) -> float:
    """
    Compute the p-th percentile of a sorted list using linear interpolation
    (equivalent to numpy.percentile with interpolation='linear').
    """
    if not values:
        return 0.0
    sv = sorted(values)
    n  = len(sv)
    if n == 1:
        return sv[0]
    idx  = (p / 100) * (n - 1)
    lo   = int(idx)
    hi   = lo + 1
    frac = idx - lo
    if hi >= n:
        return sv[-1]
    return sv[lo] + frac * (sv[hi] - sv[lo])


def safe_int(v) -> int:
    """Return integer value or 0 if None / non-numeric."""
    try:
        return int(v) if v is not None else 0
    except (TypeError, ValueError):
        return 0


# ──────────────── Metadata loading ────────────────────────────────────

def _load_index_files() -> dict[str, list[str]]:
    """
    Return {hashtag: [shortcode, ...]} from *_index.json files.
    Falls back to inferring hashtags from metadata filenames if no index
    files exist.
    """
    hashtag_map: dict[str, list[str]] = {}

    for idx_path in glob.glob(INDEX_PATTERN):
        try:
            with open(idx_path, encoding="utf-8") as f:
                data = json.load(f)
            tag       = data.get("hashtag", os.path.basename(idx_path).replace("_index.json", ""))
            shortcodes = data.get("shortcodes", [])
            if shortcodes:
                hashtag_map[tag] = shortcodes
        except Exception:
            pass

    return hashtag_map


def _load_all_metadata() -> dict[str, dict]:
    """Return {shortcode: metadata_dict} for every *_metadata.json file."""
    all_meta: dict[str, dict] = {}
    for path in glob.glob(os.path.join(METADATA_DIR, "*_metadata.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            sc = data.get("shortcode") or os.path.basename(path).replace("_metadata.json", "")
            all_meta[sc] = data
        except Exception:
            pass
    return all_meta


# ──────────────── Core matrix builder ─────────────────────────────────

def build_engagement_matrix(
    hashtags: list[str] | None = None,
) -> dict:
    """
    Build the engagement matrix.

    Parameters
    ----------
    hashtags : list of str, optional
        Restrict analysis to these hashtags. Default: all found in index files.

    Returns
    -------
    dict with keys:
        "generated_at"  : ISO timestamp
        "hashtags"      : {hashtag: HashtagReport}
        "global"        : GlobalReport

    HashtagReport keys:
        "hashtag", "post_count", "p25", "p75", "mean", "median",
        "posts" : list of PostRecord

    PostRecord keys:
        "shortcode", "url", "post_type", "like_count", "comment_count",
        "view_count", "total_interactions", "tier",
        "owner_username", "timestamp_iso", "caption_snippet"
    """
    index_map  = _load_index_files()
    all_meta   = _load_all_metadata()

    # If no index files exist, group all metadata under "__all__"
    if not index_map:
        index_map["__all__"] = list(all_meta.keys())

    # Filter to requested hashtags
    if hashtags:
        index_map = {k: v for k, v in index_map.items() if k in hashtags}

    report = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "hashtags": {},
        "global": {},
    }

    all_posts_global: list[dict] = []

    for tag, shortcodes in sorted(index_map.items()):
        posts: list[dict] = []

        for sc in shortcodes:
            meta = all_meta.get(sc)
            if not meta:
                continue

            likes    = safe_int(meta.get("like_count"))
            comments = safe_int(meta.get("comment_count"))
            views    = safe_int(meta.get("view_count"))
            total    = likes + comments

            caption  = meta.get("caption") or ""
            snippet  = caption[:120].replace("\n", " ").strip() if caption else ""

            posts.append({
                "shortcode":          sc,
                "url":                meta.get("url", f"https://www.instagram.com/p/{sc}/"),
                "post_type":          meta.get("post_type", "unknown"),
                "like_count":         likes,
                "comment_count":      comments,
                "view_count":         views,
                "total_interactions": total,
                "tier":               None,          # filled below
                "owner_username":     meta.get("owner_username"),
                "owner_followers":    meta.get("owner_followers"),
                "timestamp_iso":      meta.get("timestamp_iso"),
                "caption_snippet":    snippet,
                "hashtags":           meta.get("hashtags", []),
            })

        if not posts:
            continue

        # ── Percentile thresholds ──
        totals = [p["total_interactions"] for p in posts]
        p25    = percentile(totals, LOW_PERCENTILE)
        p75    = percentile(totals, HIGH_PERCENTILE)
        mean   = sum(totals) / len(totals)
        median = percentile(totals, 50)

        # ── Assign tiers ──
        for post in posts:
            t = post["total_interactions"]
            if t >= p75:
                post["tier"] = "High"
            elif t <= p25:
                post["tier"] = "Low"
            else:
                post["tier"] = "Average"

        # ── Tier distribution ──
        tier_counts = {"High": 0, "Average": 0, "Low": 0}
        for p in posts:
            tier_counts[p["tier"]] += 1

        # ── Top 5 posts by interactions ──
        top5 = sorted(posts, key=lambda x: x["total_interactions"], reverse=True)[:5]

        report["hashtags"][tag] = {
            "hashtag":    tag,
            "post_count": len(posts),
            "p25":        round(p25, 1),
            "p75":        round(p75, 1),
            "mean":       round(mean, 1),
            "median":     round(median, 1),
            "min":        min(totals),
            "max":        max(totals),
            "tier_distribution": tier_counts,
            "top5_posts": top5,
            "posts":      posts,
        }

        all_posts_global.extend(posts)

    # ── Global stats across all hashtags ──
    if all_posts_global:
        g_totals = [p["total_interactions"] for p in all_posts_global]
        g_p25    = percentile(g_totals, LOW_PERCENTILE)
        g_p75    = percentile(g_totals, HIGH_PERCENTILE)
        g_mean   = sum(g_totals) / len(g_totals)

        # Re-tier globally
        for post in all_posts_global:
            t = post["total_interactions"]
            if t >= g_p75:
                post["global_tier"] = "High"
            elif t <= g_p25:
                post["global_tier"] = "Low"
            else:
                post["global_tier"] = "Average"

        g_tier_counts = {"High": 0, "Average": 0, "Low": 0}
        for p in all_posts_global:
            g_tier_counts[p["global_tier"]] += 1

        report["global"] = {
            "total_posts":       len(all_posts_global),
            "p25":               round(g_p25, 1),
            "p75":               round(g_p75, 1),
            "mean":              round(g_mean, 1),
            "median":            round(percentile(g_totals, 50), 1),
            "min":               min(g_totals),
            "max":               max(g_totals),
            "tier_distribution": g_tier_counts,
            "top10_posts":       sorted(all_posts_global, key=lambda x: x["total_interactions"], reverse=True)[:10],
        }

    return report


# ──────────────── Pretty printing ─────────────────────────────────────

def print_summary(report: dict) -> None:
    """Print a human-readable engagement matrix summary to stdout."""
    sep  = "═" * 72
    sep2 = "─" * 72

    print(f"\n{sep}")
    print(f"  📊  ENGAGEMENT MATRIX REPORT")
    print(f"  Generated: {report['generated_at']}")
    print(f"{sep}")

    for tag, hr in report["hashtags"].items():
        print(f"\n{'▌ #' + tag.upper():}")
        print(sep2)
        print(f"  Posts analysed : {hr['post_count']}")
        print(f"  Interactions   : min={hr['min']:,}  max={hr['max']:,}")
        print(f"                   mean={hr['mean']:,.1f}  median={hr['median']:,.1f}")
        print(f"  25th pct (Low threshold)  : {hr['p25']:,.1f}")
        print(f"  75th pct (High threshold) : {hr['p75']:,.1f}")

        td = hr["tier_distribution"]
        total = max(hr["post_count"], 1)
        print(f"\n  Performance Tier Distribution:")
        for tier, count in [("High", td["High"]), ("Average", td["Average"]), ("Low", td["Low"])]:
            bar_len  = int((count / total) * 30)
            bar      = "█" * bar_len
            emoji    = {"High": "🟢", "Average": "🟡", "Low": "🔴"}[tier]
            print(f"    {emoji} {tier:<8} {count:>4} posts  {bar}")

        print(f"\n  🏆 Top 5 Posts:")
        for i, p in enumerate(hr["top5_posts"], 1):
            owner = f"@{p['owner_username']}" if p["owner_username"] else "unknown"
            print(f"    {i}. [{p['tier']:<7}] ❤️ {p['like_count']:>8,}  💬 {p['comment_count']:>6,}"
                  f"  {owner:<20}  {p['url']}")

    # Global summary
    g = report.get("global", {})
    if g:
        print(f"\n{sep}")
        print(f"  🌐  GLOBAL SUMMARY ({g['total_posts']} posts across all hashtags)")
        print(sep2)
        print(f"  Interactions: min={g['min']:,}  max={g['max']:,}")
        print(f"                mean={g['mean']:,.1f}  median={g['median']:,.1f}")
        print(f"  25th pct : {g['p25']:,.1f}   75th pct : {g['p75']:,.1f}")
        td = g["tier_distribution"]
        total = max(g["total_posts"], 1)
        print(f"\n  Global Tier Distribution:")
        for tier, count in [("High", td["High"]), ("Average", td["Average"]), ("Low", td["Low"])]:
            pct = count / total * 100
            emoji = {"High": "🟢", "Average": "🟡", "Low": "🔴"}[tier]
            print(f"    {emoji} {tier:<8} {count:>4} posts  ({pct:.1f}%)")

        print(f"\n  🏆 Top 10 Posts Globally:")
        for i, p in enumerate(g["top10_posts"], 1):
            owner = f"@{p['owner_username']}" if p["owner_username"] else "unknown"
            tag   = p.get("hashtags", ["?"])[0] if p.get("hashtags") else "?"
            print(f"    {i:>2}. [{p.get('global_tier','?'):<7}] #{tag:<12}"
                  f"  ❤️ {p['like_count']:>8,}  💬 {p['comment_count']:>6,}"
                  f"  {owner}")

    print(f"\n{sep}\n")


# ──────────────── Save report ──────────────────────────────────────────

def save_report(report: dict, output_dir: str = REPORTS_DIR) -> str:
    """Save the full matrix as a JSON file. Returns the output path."""
    os.makedirs(output_dir, exist_ok=True)
    ts       = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(output_dir, f"engagement_matrix_{ts}.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False, default=str)
    return out_path


def save_csv(report: dict, output_dir: str = REPORTS_DIR) -> str:
    """Save a flat CSV of all posts with their tiers. Returns the output path."""
    import csv
    os.makedirs(output_dir, exist_ok=True)
    ts       = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    out_path = os.path.join(output_dir, f"engagement_matrix_{ts}.csv")

    # Collect all posts with hashtag label
    rows = []
    for tag, hr in report["hashtags"].items():
        for p in hr["posts"]:
            rows.append({
                "hashtag":           tag,
                "shortcode":         p["shortcode"],
                "url":               p["url"],
                "post_type":         p["post_type"],
                "like_count":        p["like_count"],
                "comment_count":     p["comment_count"],
                "view_count":        p["view_count"],
                "total_interactions":p["total_interactions"],
                "tier":              p["tier"],
                "global_tier":       p.get("global_tier", ""),
                "owner_username":    p.get("owner_username", ""),
                "owner_followers":   p.get("owner_followers", ""),
                "timestamp_iso":     p.get("timestamp_iso", ""),
                "caption_snippet":   p.get("caption_snippet", ""),
            })

    if rows:
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    return out_path


# ──────────────── CLI entry point ─────────────────────────────────────

if __name__ == "__main__":
    import sys

    # Optional: pass hashtag names as CLI args to restrict analysis
    # e.g.  python engagement_matrix.py astronomy space
    target_hashtags = sys.argv[1:] if len(sys.argv) > 1 else None

    if not os.path.isdir(METADATA_DIR):
        print(f"❌ Metadata directory not found: {METADATA_DIR}")
        print("   Run the scraper first: python main.py")
        sys.exit(1)

    print("⏳ Building engagement matrix...")
    report = build_engagement_matrix(hashtags=target_hashtags)

    if not report["hashtags"]:
        print("❌ No data found. Run the scraper first: python main.py")
        sys.exit(1)

    # Print to console
    print_summary(report)

    # Save JSON + CSV
    json_path = save_report(report)
    csv_path  = save_csv(report)
    print(f"💾 JSON report : {json_path}")
    print(f"💾 CSV  report : {csv_path}")
