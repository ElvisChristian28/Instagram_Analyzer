"""Entry point: orchestrates login, scraping, and saving.

Modes (set SCRAPE_MODE in config.py):
  "hashtag" — discovers posts via TARGET_HASHTAGS, collects engagement + images
  "profile" — scrapes a fixed list of TARGET_USERNAMES (original mode)
"""

import os
import json
import time
import random
from playwright.sync_api import sync_playwright

from config import (
    STORAGE_STATE_PATH,
    HEADLESS,
    FORCE_RELOGIN,
    USER_AGENT,
    POST_DELAY_MIN,
    POST_DELAY_MAX,
    PROFILE_DELAY_MIN,
    PROFILE_DELAY_MAX,
    COOLDOWN_EVERY_N_PROFILES,
    COOLDOWN_DURATION_MIN,
    COOLDOWN_DURATION_MAX,
    SCRAPE_MODE,
    TARGET_HASHTAGS,
    POSTS_PER_HASHTAG,
    IMAGES_ONLY,
    TARGET_USERNAMES,
    MAX_POSTS_TO_DOWNLOAD,
)
from auth import is_state_valid, save_login_session, verify_session_live
from scrapers import scrape_profile, scrape_post_media_and_comments
from hashtag_scraper import scrape_hashtag
from utils import download_file


# ────────────────────────────── Main ────────────────────────────────────

def main():
    with sync_playwright() as p:

        # ── Handle login ──
        if FORCE_RELOGIN and os.path.exists(STORAGE_STATE_PATH):
            os.remove(STORAGE_STATE_PATH)
            print("🗑️  Deleted old state.json (FORCE_RELOGIN=True)")

        if not is_state_valid():
            if os.path.exists(STORAGE_STATE_PATH):
                os.remove(STORAGE_STATE_PATH)
            save_login_session(p)
            if not is_state_valid():
                print("❌ Login failed. Please try again.")
                return
            print("\n✅ Login successful! Starting scrape...\n")

        # ── Launch browser ──
        mode_label = f"hashtag mode ({len(TARGET_HASHTAGS)} hashtag(s), {POSTS_PER_HASHTAG} posts each)" \
            if SCRAPE_MODE == "hashtag" else f"profile mode ({len(TARGET_USERNAMES)} profiles)"
        print(f"🚀 Launching browser... [{mode_label}]")
        if IMAGES_ONLY:
            print("   🖼️  IMAGES_ONLY=True — video/reel downloads are skipped")

        browser = p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
            ],
        )

        context = browser.new_context(
            storage_state=STORAGE_STATE_PATH,
            viewport={"width": 1280, "height": 800},
            user_agent=USER_AGENT,
        )

        # ── Verify session ──
        print("🔐 Verifying login session...")
        session_ok = verify_session_live(context)
        if not session_ok:
            print("❌ Session expired (redirected to login). Deleting state.json — re-run to log in again.")
            browser.close()
            if os.path.exists(STORAGE_STATE_PATH):
                os.remove(STORAGE_STATE_PATH)
            return

        # ── Setup output dirs ──
        output_dir = "scraped_data"
        media_dir = os.path.join(output_dir, "media")
        comments_dir = os.path.join(output_dir, "comments")
        metadata_dir = os.path.join(output_dir, "metadata")
        reports_dir = os.path.join(output_dir, "reports")
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(media_dir, exist_ok=True)
        os.makedirs(comments_dir, exist_ok=True)
        os.makedirs(metadata_dir, exist_ok=True)
        os.makedirs(reports_dir, exist_ok=True)


        # ── Dispatch to correct mode ──
        if SCRAPE_MODE == "hashtag":
            _run_hashtag_mode(context, output_dir, media_dir, comments_dir, metadata_dir)
        else:
            _run_profile_mode(context, output_dir, media_dir, comments_dir, metadata_dir)

        browser.close()
        print(f"\n🎉 Done! Check the 'scraped_data' folder.")
        print(f"   📁 media/    — images{'' if IMAGES_ONLY else ' + videos/reels'}")
        print(f"   📁 metadata/ — engagement data per post")
        print(f"   📁 comments/ — top 150 comments per post")
        print(f"   📁 reports/  — per-hashtag + combined engagement matrices")

        # ── Combined cross-hashtag matrix (all hashtags together) ──
        if SCRAPE_MODE == "hashtag" and len(TARGET_HASHTAGS) > 1:
            print("\n📊 Generating COMBINED cross-hashtag engagement matrix...")
            try:
                from engagement_matrix import build_engagement_matrix, print_summary, save_report, save_csv
                report = build_engagement_matrix()   # no filter = all hashtags
                if report["hashtags"]:
                    print_summary(report)
                    json_path = save_report(report)
                    csv_path  = save_csv(report)
                    print(f"💾 Combined JSON → {json_path}")
                    print(f"💾 Combined CSV  → {csv_path}")
            except Exception as em_err:
                print(f"   ⚠️ Combined matrix error: {em_err}")


# ────────────────────────── Hashtag Mode ────────────────────────────────

def _run_hashtag_mode(context, output_dir, media_dir, comments_dir, metadata_dir):
    """Discover posts by hashtag, then scrape each post for images + metadata + comments."""
    total_tags = len(TARGET_HASHTAGS)

    for tag_idx, hashtag in enumerate(TARGET_HASHTAGS, 1):
        print(f"\n{'═' * 55}")
        print(f"🔖 Hashtag {tag_idx}/{total_tags}: #{hashtag}")
        print(f"{'═' * 55}")

        # ── Step 1: Discover shortcodes from the hashtag explore page ──
        tag_result = scrape_hashtag(context, hashtag)
        shortcodes = tag_result["shortcodes"]
        grid_data = tag_result["grid_data"]

        if not shortcodes:
            print(f"   ⚠️ No posts found for #{hashtag}")
            continue

        # Save the hashtag index (shortcodes + grid-level metadata)
        tag_index_path = os.path.join(output_dir, f"{hashtag}_index.json")
        with open(tag_index_path, "w", encoding="utf-8") as f:
            json.dump(tag_result, f, indent=4, ensure_ascii=False)
        print(f"   💾 Saved hashtag index → {hashtag}_index.json")

        # ── Step 2: Scrape each post ──
        total_posts = len(shortcodes)
        print(f"\n   📥 Scraping {total_posts} posts for #{hashtag}...")

        stats = {"images": 0, "videos": 0, "videos_skipped": 0, "metadata": 0, "comments": 0, "ai_skipped": 0}

        for post_idx, sc in enumerate(shortcodes, 1):
            # Rate-limited delay
            delay = random.uniform(POST_DELAY_MIN, POST_DELAY_MAX)
            if post_idx > 1:
                time.sleep(delay)

            # Progress indicator every 10 posts
            if post_idx % 10 == 0 or post_idx == 1:
                pct = int(post_idx / total_posts * 100)
                print(f"\n   ── Post {post_idx}/{total_posts} ({pct}%) ──")

            result = scrape_post_media_and_comments(
                context, sc, media_dir, comments_dir, metadata_dir
            )

            # ── AI filter: skip entirely if flagged ──
            if result.get("_skipped_ai"):
                stats["ai_skipped"] += 1
                continue

            # Update stats
            meta = result.get("metadata", {})
            is_vid = result.get("is_video", False) or meta.get("post_type") in ("reel", "video")
            if is_vid and IMAGES_ONLY:
                stats["videos_skipped"] += 1
            else:
                stats["images"] += sum(1 for _, ext in result.get("media_urls", []) if ext == ".jpg")
                stats["videos"] += sum(1 for _, ext in result.get("media_urls", []) if ext == ".mp4")
            if meta:
                stats["metadata"] += 1
            if result.get("comments"):
                stats["comments"] += 1

            # Print engagement summary
            summary_parts = []
            if meta.get("post_type"):
                summary_parts.append(f"Type: {meta['post_type']}")
            if meta.get("like_count") is not None:
                summary_parts.append(f"❤️ {meta['like_count']:,}")
            if meta.get("view_count") is not None:
                summary_parts.append(f"👁️ {meta['view_count']:,}")
            if meta.get("comment_count") is not None:
                summary_parts.append(f"💬 {meta['comment_count']:,}")
            if summary_parts:
                print(f"      📈 {' | '.join(summary_parts)}")


            # Cooldown every 50 posts
            if post_idx % 50 == 0 and post_idx < total_posts:
                cooldown = random.uniform(COOLDOWN_DURATION_MIN, COOLDOWN_DURATION_MAX)
                print(f"\n   ☕ Cooldown after {post_idx} posts — waiting {cooldown:.0f}s...")
                time.sleep(cooldown)

        # ── Hashtag summary ──
        genuine = total_posts - stats["ai_skipped"]
        print(f"\n   ✅ #{hashtag} complete:")
        print(f"      🖼️  Images saved:       {stats['images']}")
        print(f"      🎬  Videos saved:       {stats['videos']}")
        if IMAGES_ONLY:
            print(f"      🎬  Videos skipped:    {stats['videos_skipped']}")
        print(f"      📊  Metadata saved:     {stats['metadata']}")
        print(f"      💬  Comment files:      {stats['comments']}")
        print(f"      🤖  AI posts filtered:  {stats['ai_skipped']} / {total_posts} "
              f"({stats['ai_skipped']/max(total_posts,1)*100:.0f}%) — "
              f"{genuine} genuine posts kept")

        # ── Auto engagement matrix for THIS hashtag ──
        print(f"\n   📊 Generating engagement matrix for #{hashtag}...")
        try:
            from engagement_matrix import (
                build_engagement_matrix, print_summary, save_report, save_csv
            )
            report = build_engagement_matrix(hashtags=[hashtag])
            if report["hashtags"]:
                print_summary(report)
                json_path = save_report(report)
                csv_path  = save_csv(report)
                print(f"   💾 Matrix JSON → {json_path}")
                print(f"   💾 Matrix CSV  → {csv_path}")
            else:
                print(f"   ⚠️  No metadata found for #{hashtag} — matrix skipped.")
        except Exception as em_err:
            print(f"   ⚠️  Engagement matrix error: {em_err}")

        # Delay before next hashtag
        if tag_idx < total_tags:
            pause = random.uniform(PROFILE_DELAY_MIN, PROFILE_DELAY_MAX)
            print(f"\n⏳ Pausing {pause:.1f}s before next hashtag...")
            time.sleep(pause)



# ────────────────────────── Profile Mode ────────────────────────────────

def _run_profile_mode(context, output_dir, media_dir, comments_dir, metadata_dir):
    """Original profile-based scraping mode."""
    total = len(TARGET_USERNAMES)

    for idx, username in enumerate(TARGET_USERNAMES, 1):
        print(f"\n{'─' * 50}")
        print(f"📋 Profile {idx}/{total}: @{username}")
        print(f"{'─' * 50}")

        profile_data = scrape_profile(context, username)

        if profile_data:
            file_path = os.path.join(output_dir, f"{username}_profile.json")
            with open(file_path, "w", encoding="utf-8") as f:
                json.dump(profile_data, f, indent=4, ensure_ascii=False)

            fc = profile_data.get("followers_count")
            if isinstance(fc, (int, float)):
                print(f"✅ Profile saved. Followers: {fc:,}")
            else:
                print(f"✅ Profile saved. Followers: {fc}")

            # Save thumbnails
            thumbnails = profile_data.get("post_thumbnails", {})
            if thumbnails:
                print(f"   🖼️  Saving {len(thumbnails)} profile grid thumbnails...")
                for sc, thumb_url in thumbnails.items():
                    filepath = os.path.join(media_dir, f"{sc}_thumb.jpg")
                    if not os.path.exists(filepath):
                        download_file(thumb_url, filepath)

            # Scrape individual posts
            shortcodes = profile_data.get("recent_posts", [])[:MAX_POSTS_TO_DOWNLOAD]
            if shortcodes:
                print(f"   📥 Scraping {len(shortcodes)} posts...")
                for post_idx, sc in enumerate(shortcodes):
                    if sc:
                        delay = random.uniform(POST_DELAY_MIN, POST_DELAY_MAX)
                        if post_idx > 0:
                            print(f"      ⏱️  Rate limit pause: {delay:.1f}s")
                        time.sleep(delay)

                        result = scrape_post_media_and_comments(
                            context, sc, media_dir, comments_dir, metadata_dir
                        )
                        meta = result.get("metadata", {})
                        summary_parts = []
                        if meta.get("post_type"):
                            summary_parts.append(f"Type: {meta['post_type']}")
                        if meta.get("like_count") is not None:
                            summary_parts.append(f"❤️ {meta['like_count']:,}")
                        if meta.get("view_count") is not None:
                            summary_parts.append(f"👁️ {meta['view_count']:,}")
                        if meta.get("comment_count") is not None:
                            summary_parts.append(f"💬 {meta['comment_count']:,}")
                        if summary_parts:
                            print(f"      📈 {' | '.join(summary_parts)}")
            else:
                print("   ℹ️  No post shortcodes found.")
        else:
            print(f"⚠️ Failed to extract data for @{username}.")

        if idx < total:
            if COOLDOWN_EVERY_N_PROFILES and idx % COOLDOWN_EVERY_N_PROFILES == 0:
                cooldown = random.uniform(COOLDOWN_DURATION_MIN, COOLDOWN_DURATION_MAX)
                print(f"\n☕ Cooldown after {idx} profiles — waiting {cooldown:.0f}s...")
                time.sleep(cooldown)
            else:
                delay = random.uniform(PROFILE_DELAY_MIN, PROFILE_DELAY_MAX)
                print(f"⏳ Pausing {delay:.1f}s before next profile...")
                time.sleep(delay)


if __name__ == "__main__":
    main()
