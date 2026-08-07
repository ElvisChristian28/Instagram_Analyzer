"""Entry point: orchestrates login, scraping, and saving."""

import os
import json
import time
import random
from playwright.sync_api import sync_playwright

from config import (
    STORAGE_STATE_PATH,
    TARGET_USERNAMES,
    HEADLESS,
    MAX_POSTS_TO_DOWNLOAD,
    FORCE_RELOGIN,
    USER_AGENT,
    POST_DELAY_MIN,
    POST_DELAY_MAX,
    PROFILE_DELAY_MIN,
    PROFILE_DELAY_MAX,
    COOLDOWN_EVERY_N_PROFILES,
    COOLDOWN_DURATION_MIN,
    COOLDOWN_DURATION_MAX,
)
from auth import is_state_valid, save_login_session, verify_session_live
from scrapers import scrape_profile, scrape_post_media_and_comments
from utils import download_file


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
        total = len(TARGET_USERNAMES)
        print(f"🚀 Launching browser... ({total} profiles to scrape)")
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

        # ── Verify session is actually logged in ──
        print("🔐 Verifying login session...")
        if not verify_session_live(context):
            print("❌ Session expired. Deleting state.json — please re-run to log in again.")
            browser.close()
            os.remove(STORAGE_STATE_PATH)
            return

        # ── Setup output dirs ──
        output_dir = "scraped_data"
        media_dir = os.path.join(output_dir, "media")
        comments_dir = os.path.join(output_dir, "comments")
        metadata_dir = os.path.join(output_dir, "metadata")
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(media_dir, exist_ok=True)
        os.makedirs(comments_dir, exist_ok=True)
        os.makedirs(metadata_dir, exist_ok=True)

        # ── Scrape each profile ──
        for idx, username in enumerate(TARGET_USERNAMES, 1):
            print(f"\n{'─' * 50}")
            print(f"📋 Profile {idx}/{total}")
            print(f"{'─' * 50}")

            profile_data = scrape_profile(context, username)

            if profile_data:
                # Save profile JSON
                file_path = os.path.join(output_dir, f"{username}_profile.json")
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(profile_data, f, indent=4, ensure_ascii=False)

                fc = profile_data.get("followers_count")
                if isinstance(fc, (int, float)):
                    print(f"✅ Profile saved. Followers: {fc:,}")
                else:
                    print(f"✅ Profile saved. Followers: {fc}")

                # Download thumbnails from profile grid first (always works)
                thumbnails = profile_data.get("post_thumbnails", {})
                if thumbnails:
                    print(f"   🖼️  Saving {len(thumbnails)} profile grid thumbnails...")
                    for sc, thumb_url in thumbnails.items():
                        filepath = os.path.join(media_dir, f"{sc}_thumb.jpg")
                        if not os.path.exists(filepath):
                            download_file(thumb_url, filepath)

                # Then scrape individual post pages for media + metadata + comments
                shortcodes = profile_data.get("recent_posts", [])[:MAX_POSTS_TO_DOWNLOAD]
                if shortcodes:
                    print(
                        f"   📥 Scraping {len(shortcodes)} posts (media + metadata + comments)..."
                    )
                    for post_idx, sc in enumerate(shortcodes):
                        if sc:
                            # Rate-limited delay between post visits
                            delay = random.uniform(POST_DELAY_MIN, POST_DELAY_MAX)
                            if post_idx > 0:
                                print(f"      ⏱️  Rate limit pause: {delay:.1f}s")
                            time.sleep(delay)

                            result = scrape_post_media_and_comments(
                                context, sc, media_dir, comments_dir, metadata_dir
                            )

                            # Print engagement summary
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
                    print("   ℹ️  No post shortcodes found to download.")
            else:
                print(f"⚠️ Failed to extract any data for @{username}.")
                print("   Try:")
                print("   1. Set FORCE_RELOGIN = True and re-run")
                print("   2. Check that the account exists and is public")

            # ── Rate limiting between profiles ──
            if idx < total:
                # Cooldown break every N profiles
                if COOLDOWN_EVERY_N_PROFILES and idx % COOLDOWN_EVERY_N_PROFILES == 0:
                    cooldown = random.uniform(COOLDOWN_DURATION_MIN, COOLDOWN_DURATION_MAX)
                    remaining = total - idx
                    print(f"\n☕ Cooldown break after {idx} profiles ({remaining} remaining)...")
                    print(f"   Waiting {cooldown:.0f}s to avoid rate limits...")
                    time.sleep(cooldown)
                else:
                    delay = random.uniform(PROFILE_DELAY_MIN, PROFILE_DELAY_MAX)
                    print(f"⏳ Pausing {delay:.1f}s before next profile...")
                    time.sleep(delay)

        browser.close()
        print(f"\n🎉 Done! Scraped {total} profiles. Check the 'scraped_data' folder.")
        print(f"   📁 media/    — images + videos/reels")
        print(f"   📁 metadata/ — engagement data (likes, views, comments, captions)")
        print(f"   📁 comments/ — top comments per post")


if __name__ == "__main__":
    main()
