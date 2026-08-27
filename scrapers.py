"""Profile scraping, post media/reel download, metadata, and comment extraction."""

import os
import json
import time
import random
import re
import urllib.request

from config import (
    USER_AGENT,
    MAX_COMMENTS_PER_POST,
    COMMENT_SCROLL_PAUSE,
    MAX_COMMENT_SCROLL_ATTEMPTS,
    IMAGES_ONLY,
)
from utils import safe_get, download_file, dismiss_cookie_banner, dismiss_login_popup
from parsers import (
    try_parse_user,
    extract_from_page_source,
    fallback_meta_scrape,
    extract_post_metadata,
    extract_metadata_from_api,
)


# ──────────────── Profile Scraper ──────────────────────────────────────

def scrape_profile(context, username):
    """
    Navigates to a profile and extracts data using 3 strategies:
      1. Embedded JSON in page source (most reliable)
      2. API response interception (GraphQL / REST)
      3. Meta tag fallback (partial data only)
    """
    page = context.new_page()
    api_profile_data = {}

    # ── Strategy 2: API response interception (runs in background) ──
    def handle_response(response):
        nonlocal api_profile_data
        url = response.url
        is_api = any(
            p in url
            for p in [
                "web_profile_info",
                "/graphql",
                "/api/v1/users/",
                "/api/graphql",
            ]
        )
        if not is_api:
            return

        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct and "javascript" not in ct:
                return
            if response.status != 200:
                return

            data = response.json()

            # Try multiple locations for user data
            user_info = (
                safe_get(data, "data", "user")
                or data.get("user")
            )
            if not user_info or not isinstance(user_info, dict):
                # Try xdt_ keys
                data_obj = data.get("data")
                if isinstance(data_obj, dict):
                    for key, val in data_obj.items():
                        if isinstance(val, dict) and val.get("username"):
                            user_info = val
                            break

            result = try_parse_user(user_info)
            if result:
                api_profile_data = result
                print(f"   ✅ API intercept captured data from: ...{url[-60:]}")
        except Exception as e:
            print(f"   ⚠️ API parse error ({url[-50:]}): {e}")

    page.on("response", handle_response)

    # ── Navigate to profile ──
    print(f"\n🔍 Scanning Profile @{username}...")
    try:
        page.goto(
            f"https://www.instagram.com/{username}/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
    except Exception as e:
        print(f"   ⚠️ Navigation error: {e}")

    dismiss_cookie_banner(page)
    dismiss_login_popup(page)

    # Wait for page to fully settle
    time.sleep(random.uniform(3.0, 5.0))

    # ── Strategy 1: Embedded JSON (primary — most reliable) ──
    profile_data = extract_from_page_source(page, username)

    # ── Check API interception result ──
    if not profile_data and api_profile_data:
        profile_data = api_profile_data
        print("   ✅ Using data from API interception")

    # ── Scroll to trigger lazy loads if nothing yet ──
    if not profile_data:
        print("   ⏳ No data yet, scrolling to trigger lazy loads...")
        page.mouse.wheel(0, 600)
        time.sleep(random.uniform(2.0, 3.0))
        page.mouse.wheel(0, 400)
        time.sleep(random.uniform(2.0, 3.0))

        # Try embedded JSON again after scroll
        profile_data = extract_from_page_source(page, username)

    # ── Check API interception again ──
    if not profile_data and api_profile_data:
        profile_data = api_profile_data

    # ── Strategy 3: Meta tag fallback ──
    if not profile_data:
        profile_data = fallback_meta_scrape(page, username)

    # ── If we STILL have nothing, try to get shortcodes from visible posts ──
    if profile_data and not profile_data.get("recent_posts"):
        print("   🔎 Scanning visible post links for shortcodes...")
        try:
            links = page.locator('a[href*="/p/"], a[href*="/reel/"]').all()
            for link in links[:12]:
                href = link.get_attribute("href")
                if href:
                    match = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", href)
                    if match:
                        sc = match.group(1)
                        if sc not in profile_data["recent_posts"]:
                            profile_data["recent_posts"].append(sc)
            if profile_data["recent_posts"]:
                print(f"   ✅ Found {len(profile_data['recent_posts'])} post shortcodes from page links")
        except Exception as e:
            print(f"   ⚠️ Link scan failed: {e}")

    # ── Extract thumbnail images directly from the profile grid ──
    if profile_data:
        print("   🖼️  Extracting post thumbnails from profile grid...")
        profile_data.setdefault("post_thumbnails", {})
        try:
            post_links = page.locator('a[href*="/p/"], a[href*="/reel/"]').all()
            for link in post_links[:12]:
                href = link.get_attribute("href") or ""
                sc_match = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", href)
                if not sc_match:
                    continue
                sc = sc_match.group(1)
                try:
                    img = link.locator("img").first
                    if img.is_visible(timeout=1000):
                        src = img.get_attribute("src")
                        if src and src.startswith("http"):
                            profile_data["post_thumbnails"][sc] = src
                except Exception:
                    pass
            count = len(profile_data["post_thumbnails"])
            if count:
                print(f"   ✅ Got {count} thumbnail URLs from profile grid")
        except Exception as e:
            print(f"   ⚠️ Thumbnail extraction failed: {e}")

    page.close()
    return profile_data


# ──────────── Post Media, Metadata & Comment Extraction ───────────────

def scrape_post_media_and_comments(context, shortcode, media_dir, comments_dir, metadata_dir):
    """
    Navigates to a specific post to:
      1. Download images AND videos/reels
      2. Extract engagement metadata (likes, views, comments, caption)
      3. Collect top N comments (scrolling to load more)
    """
    page = context.new_page()
    post_url = f"https://www.instagram.com/p/{shortcode}/"
    post_data = {
        "shortcode": shortcode,
        "media_urls": [],   # List of (url, extension) tuples
        "comments": [],     # List of comment dicts
        "metadata": {},     # Engagement metadata
        "is_video": False,  # Set True if API confirms this is a video/reel
    }

    # ── API response interception (captures media + metadata + comments in background) ──
    def handle_post_response(response):
        nonlocal post_data
        url = response.url
        is_api = any(
            p in url
            for p in ["graphql", "/api/v1/media/", "/api/graphql", "/api/v1/comments/"]
        )
        if not is_api:
            return

        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            if response.status != 200:
                return

            data = response.json()

            # ── Primary: extract media info from xdt_shortcode_media payload ──
            media_info = (
                safe_get(data, "data", "xdt_shortcode_media")
                or safe_get(data, "data", "shortcode_media")
                or safe_get(data, "graphql", "shortcode_media")
                or safe_get(data, "items", 0)  # REST v1 format
            )

            if media_info:
                # Set video flag
                if media_info.get("is_video") or media_info.get("product_type") == "clips":
                    post_data["is_video"] = True

                # Extract all metadata directly from API response (most reliable)
                if not post_data.get("metadata"):
                    api_meta = extract_metadata_from_api(media_info)
                    if api_meta:
                        post_data["metadata"] = api_meta

                # Extract media URLs
                if not post_data["media_urls"]:
                    _extract_media_from_api(media_info, post_data)

            # ── Try to extract comments from API ──
            _extract_comments_from_api(data, media_info, post_data)

        except Exception:
            pass  # Silently handle API parse errors
    # ── Also intercept video CDN URLs from network traffic ──
    captured_video_urls = set()

    def handle_video_cdn(response):
        """Capture video CDN URLs from network responses."""
        url = response.url
        try:
            ct = response.headers.get("content-type", "")
            # Capture video content from CDN
            if ("video" in ct or url.endswith(".mp4")) and "cdninstagram" in url:
                if response.status == 200:
                    captured_video_urls.add(url.split("?")[0] + "?" + url.split("?")[1] if "?" in url else url)
        except Exception:
            pass

    page.on("response", handle_post_response)
    page.on("response", handle_video_cdn)

    # ── Navigate to post ──
    print(f"   ⬇️  Loading post: {shortcode}...")
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass

    # Wait for page to render, then aggressively dismiss all popups
    time.sleep(random.uniform(2.0, 3.0))
    dismiss_cookie_banner(page)
    dismiss_login_popup(page)
    time.sleep(1)
    dismiss_login_popup(page)
    time.sleep(1)

    # ── Click on the post area to enable interaction (helps load comments) ──
    try:
        # Click on the main content area to focus the page
        article = page.locator("article").first
        if article.is_visible(timeout=2000):
            article.click(position={"x": 10, "y": 10}, timeout=2000)
            time.sleep(0.5)
    except Exception:
        pass

    # Wait for API to respond (metadata + media URLs)
    waited = 0.0
    while (not post_data["media_urls"] or not post_data.get("metadata")) and waited < 10:
        time.sleep(0.5)
        waited += 0.5

    # ── Extract metadata: use API result if captured, else HTML fallback ──
    if not post_data.get("metadata"):
        post_data["metadata"] = extract_post_metadata(page, shortcode)
    else:
        from parsers import _report_metadata_fields
        _report_metadata_fields(post_data["metadata"])

    # ── Enrich owner profile if followers/following missing ──
    meta = post_data.get("metadata", {})
    owner_un = meta.get("owner_username")
    if owner_un and meta.get("owner_followers") is None:
        _fetch_owner_profile(meta, owner_un)

    # ── AI content filter: skip post if AI-generated ──
    from ai_filter import is_ai_generated
    ai_check = is_ai_generated(post_data.get("metadata", {}))
    if ai_check["is_ai"]:
        print(f"      🤖 SKIPPED (AI content): {ai_check['reason']}")
        page.close()
        return {**post_data, "_skipped_ai": True, "_ai_reason": ai_check["reason"]}

    # ── EARLY FLUSH: save metadata NOW before any download/comment steps ──
    # This ensures metadata survives even if the run is cancelled mid-post.
    if post_data.get("metadata"):
        _flush_metadata(post_data["metadata"], shortcode, metadata_dir)


    # ── Media extraction fallbacks ──
    if not post_data["media_urls"]:
        _extract_media_from_page_source(page, post_data)

    if not post_data["media_urls"]:
        _extract_media_from_dom(page, post_data)


    # ── Add captured video CDN URLs ──
    if captured_video_urls:
        existing_urls = {u for u, _ in post_data["media_urls"]}
        for vurl in captured_video_urls:
            if vurl not in existing_urls:
                post_data["media_urls"].append((vurl, ".mp4"))
                print(f"      ✅ Captured video from CDN")

    # ── For reels: if no video found, try /reel/ URL with DOM video element grab ──
    has_video = any(ext == ".mp4" for _, ext in post_data["media_urls"])
    is_reel = post_data["metadata"].get("post_type") in ("reel", "video") or post_data["is_video"]

    if is_reel and not has_video:
        print("      🎬 Reel detected — attempting video download...")
        try:
            reel_url = f"https://www.instagram.com/reel/{shortcode}/"
            page.goto(reel_url, wait_until="domcontentloaded", timeout=20000)
            time.sleep(random.uniform(3.0, 5.0))
            dismiss_login_popup(page)
            time.sleep(3)

            # Strategy 1: Read <video> src directly from live DOM via JS
            try:
                video_srcs = page.evaluate("""
                    () => {
                        const vids = document.querySelectorAll('video');
                        const srcs = [];
                        vids.forEach(v => {
                            if (v.src && v.src.startsWith('http')) srcs.push(v.src);
                            v.querySelectorAll('source').forEach(s => {
                                if (s.src && s.src.startsWith('http')) srcs.push(s.src);
                            });
                        });
                        return [...new Set(srcs)];
                    }
                """)
                for vsrc in (video_srcs or []):
                    if vsrc and vsrc not in {u for u, _ in post_data["media_urls"]}:
                        post_data["media_urls"].append((vsrc, ".mp4"))
                        print(f"      ✅ Got video from DOM <video> element")
            except Exception:
                pass

            # Strategy 2: Check CDN captures (network intercept during reel page load)
            if captured_video_urls:
                existing_urls = {u for u, _ in post_data["media_urls"]}
                for vurl in captured_video_urls:
                    if vurl not in existing_urls:
                        post_data["media_urls"].append((vurl, ".mp4"))
                        print(f"      ✅ Captured reel video from CDN")

            # Strategy 3: Page source regex (video_url, video_versions)
            if not any(ext == ".mp4" for _, ext in post_data["media_urls"]):
                _extract_media_from_page_source(page, post_data)

            # Strategy 4: Try window.__additionalDataLoaded via JS
            if not any(ext == ".mp4" for _, ext in post_data["media_urls"]):
                try:
                    vid_url = page.evaluate("""
                        () => {
                            try {
                                // Try Redux/Relay store
                                const keys = Object.keys(window.__relay_store__ || {});
                                for (const k of keys) {
                                    const n = window.__relay_store__[k];
                                    if (n && n.video_url) return n.video_url;
                                    if (n && n.videoUrl) return n.videoUrl;
                                }
                            } catch(e) {}
                            return null;
                        }
                    """)
                    if vid_url:
                        post_data["media_urls"].append((vid_url, ".mp4"))
                        print("      ✅ Got video from JS store")
                except Exception:
                    pass

        except Exception as e:
            print(f"      ⚠️ Reel video fetch failed: {e}")


    if not post_data["media_urls"]:
        _extract_media_oembed(shortcode, post_data)

    # ── Comment extraction (scroll to load up to MAX_COMMENTS) ──
    _scroll_and_collect_comments(page, post_data)

    page.close()

    # ── Download media ──
    # In IMAGES_ONLY mode: check the API-confirmed is_video flag first,
    # then fall back to checking if all collected URLs are .mp4
    is_video_post = post_data["is_video"] or post_data["metadata"].get("post_type") in ("reel", "video")

    if IMAGES_ONLY and is_video_post:
        print("      🎬 Video/Reel detected — skipping media download (IMAGES_ONLY=True)")
    elif post_data["media_urls"]:
        # Filter out .mp4 if IMAGES_ONLY
        urls_to_download = [
            (url, ext) for url, ext in post_data["media_urls"]
            if not (IMAGES_ONLY and ext == ".mp4")
        ]
        img_count = sum(1 for _, ext in urls_to_download if ext == ".jpg")
        vid_count = sum(1 for _, ext in urls_to_download if ext == ".mp4")
        parts = []
        if img_count:
            parts.append(f"{img_count} image(s)")
        if vid_count:
            parts.append(f"{vid_count} video(s)")
        if parts:
            print(f"      📸 Found {' + '.join(parts)}. Downloading...")
            for i, (url, ext) in enumerate(urls_to_download):
                if url:
                    filepath = os.path.join(media_dir, f"{shortcode}_{i}{ext}")
                    success = download_file(url, filepath)
                    if success:
                        print(f"      ✅ Saved: {shortcode}_{i}{ext}")
        else:
            print("      ℹ️  All media skipped (IMAGES_ONLY=True, only videos found)")
    else:
        print("      ❌ No media URLs found for this post.")

    # ── Final metadata flush (overwrites early flush with completed state) ──
    if post_data.get("metadata"):
        # Stamp actual downloaded comment count into metadata
        post_data["metadata"]["comments_collected"] = len(post_data["comments"])
        _flush_metadata(post_data["metadata"], shortcode, metadata_dir)

    # ── Save comments ──
    if post_data["comments"]:
        print(
            f"      💬 Collected {len(post_data['comments'])} comments. Saving..."
        )
        filepath = os.path.join(comments_dir, f"{shortcode}_comments.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(post_data["comments"], f, indent=4, ensure_ascii=False)
    else:
        print("      💬 No comments captured.")

    return post_data


# ──────────────── Media Extraction Helpers ─────────────────────────────

def _extract_media_from_api(media_info, post_data):
    """Extract media URLs from API-intercepted JSON."""
    # Carousel / sidecar (GraphQL format)
    if safe_get(media_info, "edge_sidecar_to_children"):
        for child in safe_get(
            media_info, "edge_sidecar_to_children", "edges", default=[]
        ):
            node = child.get("node", {})
            if node.get("is_video"):
                vurl = node.get("video_url")
                if vurl:
                    post_data["media_urls"].append((vurl, ".mp4"))
            else:
                post_data["media_urls"].append(
                    (node.get("display_url"), ".jpg")
                )
    # Carousel (REST v1 format)
    elif safe_get(media_info, "carousel_media"):
        for item in media_info["carousel_media"]:
            if item.get("video_versions"):
                post_data["media_urls"].append(
                    (item["video_versions"][0].get("url"), ".mp4")
                )
            elif item.get("image_versions2"):
                post_data["media_urls"].append(
                    (safe_get(item, "image_versions2", "candidates", 0, "url"), ".jpg")
                )
    # Single post
    else:
        if media_info.get("is_video") or media_info.get("video_versions"):
            vurl = (
                media_info.get("video_url")
                or safe_get(media_info, "video_versions", 0, "url")
            )
            if vurl:
                post_data["media_urls"].append((vurl, ".mp4"))
            # Also get the thumbnail/cover image
            display = (
                media_info.get("display_url")
                or safe_get(media_info, "image_versions2", "candidates", 0, "url")
            )
            if display:
                post_data["media_urls"].append((display, ".jpg"))
        else:
            display = (
                media_info.get("display_url")
                or safe_get(media_info, "image_versions2", "candidates", 0, "url")
            )
            if display:
                post_data["media_urls"].append((display, ".jpg"))

    if post_data["media_urls"]:
        print(f"      ✅ API intercept: {len(post_data['media_urls'])} media URL(s)")


def _extract_media_from_page_source(page, post_data):
    """Extract media URLs from embedded JSON in page HTML."""
    print("      🔄 Trying to extract media from page source...")
    try:
        html = page.content()

        # ── Video URLs (multiple patterns) ──
        video_urls = set()

        # Pattern 1: "video_url":"https://..."
        for m in re.finditer(r'"video_url"\s*:\s*"(https?://[^"]+)"', html):
            clean = m.group(1).replace("\\u0026", "&").replace("\\/", "/")
            video_urls.add(clean)

        # Pattern 2: "video_versions":[{"url":"https://..."}]
        for m in re.finditer(
            r'"video_versions"\s*:\s*\[\s*\{[^}]*"url"\s*:\s*"(https?://[^"]+)"',
            html,
        ):
            clean = m.group(1).replace("\\u0026", "&").replace("\\/", "/")
            video_urls.add(clean)

        # Add all video URLs
        for vurl in video_urls:
            post_data["media_urls"].append((vurl, ".mp4"))

        # ── Image URLs ──
        image_urls = set()
        for m in re.finditer(r'"display_url"\s*:\s*"(https?://[^"]+)"', html):
            clean = m.group(1).replace("\\u0026", "&").replace("\\/", "/")
            image_urls.add(clean)

        for iurl in image_urls:
            post_data["media_urls"].append((iurl, ".jpg"))

        if post_data["media_urls"]:
            vids = sum(1 for _, e in post_data["media_urls"] if e == ".mp4")
            imgs = sum(1 for _, e in post_data["media_urls"] if e == ".jpg")
            print(f"      ✅ Page source: {vids} video(s), {imgs} image(s)")

    except Exception as e:
        print(f"      ⚠️ Page source regex failed: {e}")


def _extract_media_from_dom(page, post_data):
    """Extract media from visible DOM elements."""
    print("      🔄 Trying visible DOM images/videos...")
    try:
        seen_srcs = set()

        # ── Videos first (higher priority) ──
        video_selectors = [
            'video[src*="cdninstagram"]',
            'video source[src*="cdninstagram"]',
            'article video[src]',
            'video source[src]',
        ]
        for vsel in video_selectors:
            try:
                elements = page.locator(vsel).all()
                for el in elements:
                    try:
                        src = el.get_attribute("src", timeout=2000)
                        if src and src.startswith("http") and src not in seen_srcs:
                            seen_srcs.add(src)
                            post_data["media_urls"].append((src, ".mp4"))
                    except Exception:
                        pass
            except Exception:
                pass

        # ── Images ──
        img_selectors = [
            'article img[src*="cdninstagram"]',
            'img[src*="cdninstagram.com"][style*="object-fit"]',
            'div[role="presentation"] img[src*="cdninstagram"]',
            'img[srcset*="cdninstagram"]',
            'main img[src*="cdninstagram"]',
        ]
        for sel in img_selectors:
            try:
                imgs = page.locator(sel).all()
                for img in imgs:
                    try:
                        src = img.get_attribute("src", timeout=2000)
                        if src and src.startswith("http") and src not in seen_srcs:
                            # Skip tiny profile pics and icons
                            width = img.get_attribute("width")
                            if width and width.isdigit() and int(width) < 100:
                                continue
                            seen_srcs.add(src)
                            post_data["media_urls"].append((src, ".jpg"))
                    except Exception:
                        pass
            except Exception:
                pass
            if post_data["media_urls"]:
                break

        if post_data["media_urls"]:
            print(f"      ✅ Found {len(post_data['media_urls'])} from DOM elements")
    except Exception as e:
        print(f"      ⚠️ DOM extraction failed: {e}")


def _extract_media_oembed(shortcode, post_data):
    """Last-resort: try oEmbed API for a thumbnail."""
    print("      🔄 Trying oEmbed API...")
    try:
        oembed_url = f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis"
        req = urllib.request.Request(oembed_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            oembed = json.loads(resp.read())
            thumb = oembed.get("thumbnail_url") or safe_get(
                oembed, "graphql", "shortcode_media", "display_url"
            )
            if thumb:
                post_data["media_urls"].append((thumb, ".jpg"))
                print("      ✅ Got thumbnail from oEmbed/API")
    except Exception:
        pass


def _flush_metadata(metadata: dict, shortcode: str, metadata_dir: str) -> None:
    """
    Write metadata dict to disk immediately (atomic write via temp file).
    Called twice: once right after capture, once after comments are done.
    Using a temp file prevents corrupted JSON if the process is killed mid-write.
    """
    try:
        filepath = os.path.join(metadata_dir, f"{shortcode}_metadata.json")
        tmp_path  = filepath + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
        os.replace(tmp_path, filepath)   # atomic on all OSes
    except Exception as e:
        print(f"      ⚠️ Metadata flush failed: {e}")


def _fetch_owner_profile(meta, username):
    """
    Enrich metadata with owner follower/following counts by calling
    Instagram's web_profile_info endpoint. Updates meta dict in-place.
    """
    try:
        url = f"https://www.instagram.com/api/v1/users/web_profile_info/?username={username}"
        req = urllib.request.Request(url, headers={
            "User-Agent": USER_AGENT,
            "X-IG-App-ID": "936619743392459",
            "Accept": "application/json",
        })
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            user = safe_get(data, "data", "user") or {}
            if not user:
                return
            meta["owner_full_name"] = user.get("full_name") or meta.get("owner_full_name")
            meta["owner_is_verified"] = user.get("is_verified", meta.get("owner_is_verified"))
            meta["owner_followers"] = (
                safe_get(user, "edge_followed_by", "count")
                or user.get("follower_count")
            )
            meta["owner_following"] = (
                safe_get(user, "edge_follow", "count")
                or user.get("following_count")
            )
            meta["owner_posts_count"] = (
                safe_get(user, "edge_owner_to_timeline_media", "count")
                or user.get("media_count")
            )
            if meta.get("owner_followers") is not None:
                print(f"      👥 Owner: @{username} | {meta['owner_followers']:,} followers")
    except Exception:
        pass  # Non-critical — skip silently


# ──────────────── Comment Extraction Helpers ───────────────────────────

def _extract_comments_from_api(data, media_info, post_data):
    """Extract comments from API response JSON."""
    if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
        return

    comment_sources = []

    # From media_info (initial post load)
    if media_info:
        comment_sources.extend([
            safe_get(media_info, "edge_media_to_parent_comment", "edges", default=[]),
            safe_get(media_info, "edge_media_to_comment", "edges", default=[]),
            safe_get(media_info, "edge_media_preview_comment", "edges", default=[]),
        ])
        # REST v1 format: "comments" list
        rest_comments = media_info.get("comments", [])
        if isinstance(rest_comments, list):
            for c in rest_comments:
                if isinstance(c, dict):
                    _add_comment(post_data, {
                        "username": safe_get(c, "user", "username") or c.get("username"),
                        "text": c.get("text"),
                        "likes": c.get("comment_like_count", 0),
                        "created_at": c.get("created_at"),
                    })

    # From comment pagination responses
    comment_edges = safe_get(data, "data", "xdt_shortcode_media", "edge_media_to_parent_comment", "edges", default=[])
    if comment_edges:
        comment_sources.append(comment_edges)

    # Also try direct comment pagination
    direct_comments = safe_get(data, "comments", default=[])
    if isinstance(direct_comments, list):
        for c in direct_comments:
            if isinstance(c, dict):
                _add_comment(post_data, {
                    "username": safe_get(c, "user", "username") or c.get("username"),
                    "text": c.get("text"),
                    "likes": c.get("comment_like_count", 0),
                    "created_at": c.get("created_at"),
                })

    # Process GraphQL edge format
    for edges in comment_sources:
        if not edges:
            continue
        for edge in edges:
            if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
                return
            node = edge.get("node", edge)  # Some formats skip the "node" wrapper
            if not isinstance(node, dict):
                continue
            _add_comment(post_data, {
                "username": safe_get(node, "owner", "username") or node.get("username"),
                "text": node.get("text"),
                "likes": safe_get(node, "edge_liked_by", "count", default=0) or node.get("comment_like_count", 0),
                "created_at": node.get("created_at") or node.get("created_at_utc"),
            })


def _add_comment(post_data, comment):
    """Add a comment to post_data, deduplicating by (username, text)."""
    if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
        return
    if not comment.get("text"):
        return

    # Deduplicate
    key = (comment.get("username", ""), comment.get("text", ""))
    for existing in post_data["comments"]:
        if (existing.get("username", ""), existing.get("text", "")) == key:
            return

    post_data["comments"].append(comment)


def _scroll_and_collect_comments(page, post_data):
    """Scroll, click load-more, and collect up to MAX_COMMENTS_PER_POST comments."""
    if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
        return

    print(f"      💬 Loading comments (target: {MAX_COMMENTS_PER_POST})...")

    # ── Step 1: Scroll down to the comments section ──
    try:
        page.mouse.wheel(0, 1000)
        time.sleep(2)
    except Exception:
        pass

    # ── Step 2: Click “View all N comments” link if present ──
    try:
        for sel in [
            'a:has-text("View all")',
            'button:has-text("View all")',
            'span:has-text("View all")',
            'a[href*="/comments/"]',
        ]:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    time.sleep(3)
                    print("      ✅ Clicked 'View all comments'")
                    break
            except Exception:
                continue
    except Exception:
        pass

    # ── Step 3: Initial DOM extraction after page settles ──
    _extract_comments_from_dom(page, post_data)

    # ── Step 4: Scroll + click loop ──
    prev_count = len(post_data["comments"])
    consecutive_no_new = 0

    for attempt in range(MAX_COMMENT_SCROLL_ATTEMPTS):
        if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
            break

        # Click all visible “Load more comments” / “+” buttons
        try:
            more_selectors = [
                'button:has-text("Load more comments")',
                'button[aria-label*="Load more comments"]',
                'li button[aria-label*="Load more"]',
                'button svg[aria-label="Load more comments"]',
                'ul li button:has(svg[aria-label])',
                # The small "+" chevron button between comment threads
                'button._abl-',
                'div[role="button"]:has-text("View")',
            ]
            for sel in more_selectors:
                try:
                    btns = page.locator(sel).all()
                    for btn in btns[:3]:  # click up to 3 at a time
                        if btn.is_visible(timeout=500):
                            btn.click()
                            time.sleep(1)
                except Exception:
                    continue
        except Exception:
            pass

        # Scroll into comments area
        page.mouse.wheel(0, 500)
        time.sleep(COMMENT_SCROLL_PAUSE)

        # Extract from DOM
        _extract_comments_from_dom(page, post_data)

        current_count = len(post_data["comments"])
        if current_count == prev_count:
            consecutive_no_new += 1
            # Extra scroll to try to trigger lazy load
            page.mouse.wheel(0, 800)
            time.sleep(COMMENT_SCROLL_PAUSE + 1)
            _extract_comments_from_dom(page, post_data)
            if len(post_data["comments"]) == prev_count and consecutive_no_new >= 5:
                break  # Genuinely no more comments loading
        else:
            consecutive_no_new = 0

        prev_count = len(post_data["comments"])

    if post_data["comments"]:
        print(f"      💬 Collected {len(post_data['comments'])} comments from DOM")


def _extract_comments_from_dom(page, post_data):
    """Extract comments from visible DOM elements and page source JSON."""
    if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
        return

    # ── Strategy 1: Direct DOM element scraping (most reliable for live page) ──
    try:
        # Instagram comment structure: <ul> list where each <li> is a comment
        # The comment author is in an <a> tag, text in a <span>
        comment_items = page.evaluate("""
            () => {
                const results = [];
                // Primary: article comment list items
                const lists = document.querySelectorAll(
                    'article ul li, div[role="dialog"] ul li'
                );
                lists.forEach(li => {
                    try {
                        // Username: first <a> link that’s not a post/reel/explore link
                        const links = li.querySelectorAll('a[href]');
                        let username = null;
                        for (const a of links) {
                            const href = a.getAttribute('href') || '';
                            if (!href.includes('/p/') && !href.includes('/reel/') &&
                                !href.includes('/explore/') && !href.includes('/accounts/') &&
                                href.startsWith('/')) {
                                username = href.replace(/^\//,'').replace(/\/$/,'');
                                break;
                            }
                        }
                        if (!username) return;

                        // Text: all <span> content joined, minus the username at start
                        const spans = li.querySelectorAll('span');
                        let text = '';
                        spans.forEach(s => {
                            const t = s.innerText || s.textContent || '';
                            if (t && !t.includes(username)) text += t + ' ';
                        });
                        text = text.trim();

                        // Timestamp and likes
                        let created_at = null;
                        const timeEl = li.querySelector('time');
                        if (timeEl) created_at = timeEl.getAttribute('datetime');

                        let likes = 0;
                        const likeEl = li.querySelector('[aria-label*="like"], [aria-label*="Like"]');
                        if (likeEl) {
                            const m = (likeEl.getAttribute('aria-label') || '').match(/(\d+)/);
                            if (m) likes = parseInt(m[1]);
                        }

                        if (username && text && text.length > 1) {
                            results.push({username, text, likes, created_at});
                        }
                    } catch(e) {}
                });
                return results;
            }
        """)

        for item in (comment_items or []):
            if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
                break
            if item.get("username") and item.get("text"):
                _add_comment(post_data, {
                    "username": item["username"],
                    "text": item["text"].strip(),
                    "likes": item.get("likes", 0),
                    "created_at": item.get("created_at"),
                })
    except Exception:
        pass

    # ── Strategy 2: Page source JSON regex (catches API-embedded comments) ──
    if len(post_data["comments"]) < MAX_COMMENTS_PER_POST:
        try:
            html = page.content()
            # Pattern for GraphQL edge comments: {"node":{"text":"...","owner":{"username":"..."}}}
            for m in re.finditer(
                r'"node"\s*:\s*\{[^{}]*?"text"\s*:\s*"((?:[^"\\]|\\.)*)"[^{}]*?'  
                r'"owner"\s*:\s*\{[^}]*?"username"\s*:\s*"([^"]+)"',
                html, re.DOTALL
            ):
                if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
                    break
                text_raw, username = m.group(1), m.group(2)
                try:
                    text = json.loads(f'"{text_raw}"')
                    text = text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
                except Exception:
                    text = text_raw.replace("\\n", "\n")
                if text and username:
                    _add_comment(post_data, {"username": username, "text": text, "likes": 0, "created_at": None})

            # Also try REST v1 format: {"text":"...","user":{"username":"..."}}
            for m in re.finditer(
                r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"[^{}]{0,200}?'  
                r'"user"\s*:\s*\{[^}]*?"username"\s*:\s*"([^"]+)"',
                html, re.DOTALL
            ):
                if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
                    break
                text_raw, username = m.group(1), m.group(2)
                try:
                    text = json.loads(f'"{text_raw}"')
                    text = text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
                except Exception:
                    text = text_raw.replace("\\n", "\n")
                if text and username and len(text) > 1:
                    _add_comment(post_data, {"username": username, "text": text, "likes": 0, "created_at": None})
        except Exception:
            pass

