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
)
from utils import safe_get, download_file, dismiss_cookie_banner, dismiss_login_popup
from parsers import (
    try_parse_user,
    extract_from_page_source,
    fallback_meta_scrape,
    extract_post_metadata,
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
    }

    # ── API response interception (captures media + comments in background) ──
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

            # ── Try to extract media info ──
            media_info = (
                safe_get(data, "data", "xdt_shortcode_media")
                or safe_get(data, "data", "shortcode_media")
                or safe_get(data, "graphql", "shortcode_media")
                or safe_get(data, "items", 0)  # REST v1 format
            )

            if media_info and not post_data["media_urls"]:
                _extract_media_from_api(media_info, post_data)

            # ── Try to extract comments ──
            _extract_comments_from_api(data, media_info, post_data)

        except Exception as e:
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

    # Wait for API to respond
    waited = 0.0
    while not post_data["media_urls"] and waited < 8:
        time.sleep(0.5)
        waited += 0.5

    # ── Extract metadata BEFORE closing page ──
    post_data["metadata"] = extract_post_metadata(page, shortcode)

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

    # ── For reels: if no video found, try /reel/ URL ──
    has_video = any(ext == ".mp4" for _, ext in post_data["media_urls"])
    is_reel = post_data["metadata"].get("post_type") in ("reel", "video")

    if is_reel and not has_video:
        print("      🎬 Reel detected but no video yet — trying /reel/ URL...")
        try:
            reel_url = f"https://www.instagram.com/reel/{shortcode}/"
            page.goto(reel_url, wait_until="domcontentloaded", timeout=15000)
            time.sleep(random.uniform(2.0, 3.0))
            dismiss_login_popup(page)
            time.sleep(2)

            # Try page source on reel page
            _extract_media_from_page_source(page, post_data)

            # Check CDN captures after reel page load
            if captured_video_urls:
                existing_urls = {u for u, _ in post_data["media_urls"]}
                for vurl in captured_video_urls:
                    if vurl not in existing_urls:
                        post_data["media_urls"].append((vurl, ".mp4"))
                        print(f"      ✅ Captured reel video from CDN")
        except Exception as e:
            print(f"      ⚠️ Reel URL failed: {e}")

    if not post_data["media_urls"]:
        _extract_media_oembed(shortcode, post_data)

    # ── Comment extraction (scroll to load up to MAX_COMMENTS) ──
    _scroll_and_collect_comments(page, post_data)

    page.close()

    # ── Download media ──
    if post_data["media_urls"]:
        img_count = sum(1 for _, ext in post_data["media_urls"] if ext == ".jpg")
        vid_count = sum(1 for _, ext in post_data["media_urls"] if ext == ".mp4")
        parts = []
        if img_count:
            parts.append(f"{img_count} image(s)")
        if vid_count:
            parts.append(f"{vid_count} video(s)")
        print(f"      📸 Found {' + '.join(parts)}. Downloading...")

        for i, (url, ext) in enumerate(post_data["media_urls"]):
            if url:
                filepath = os.path.join(media_dir, f"{shortcode}_{i}{ext}")
                success = download_file(url, filepath)
                if success:
                    print(f"      ✅ Saved: {shortcode}_{i}{ext}")
    else:
        print("      ❌ No media URLs found for this post.")

    # ── Save metadata ──
    if post_data["metadata"]:
        filepath = os.path.join(metadata_dir, f"{shortcode}_metadata.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(post_data["metadata"], f, indent=4, ensure_ascii=False)

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
    """Scroll down and click 'load more comments' to collect up to MAX_COMMENTS."""
    if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
        return

    print(f"      💬 Scrolling to load comments (target: {MAX_COMMENTS_PER_POST})...")

    # ── Step 1: Click "View all N comments" if present ──
    try:
        view_all_selectors = [
            'a:has-text("View all")',
            'button:has-text("View all")',
            'span:has-text("View all")',
        ]
        for sel in view_all_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    time.sleep(COMMENT_SCROLL_PAUSE)
                    print("      ✅ Clicked 'View all comments'")
                    break
            except Exception:
                continue
    except Exception:
        pass

    # ── Step 2: Scroll and click "Load more" / "+" buttons ──
    prev_count = len(post_data["comments"])

    for attempt in range(MAX_COMMENT_SCROLL_ATTEMPTS):
        if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
            break

        # Try clicking "load more comments" buttons
        try:
            more_selectors = [
                'button:has-text("Load more comments")',
                'li button[aria-label*="Load more comments"]',
                'button svg[aria-label="Load more comments"]',
                'ul button:has(svg)',  # The "+" icon button
            ]
            clicked = False
            for sel in more_selectors:
                try:
                    btn = page.locator(sel).first
                    if btn.is_visible(timeout=1000):
                        btn.click()
                        clicked = True
                        break
                except Exception:
                    continue
        except Exception:
            pass

        # Scroll down into comments section
        page.mouse.wheel(0, 400)
        time.sleep(COMMENT_SCROLL_PAUSE)

        # ── Extract comments from the visible DOM ──
        _extract_comments_from_dom(page, post_data)

        # Check if we got new comments
        current_count = len(post_data["comments"])
        if current_count == prev_count:
            # No new comments loaded — try one more scroll
            page.mouse.wheel(0, 600)
            time.sleep(COMMENT_SCROLL_PAUSE)
            _extract_comments_from_dom(page, post_data)
            if len(post_data["comments"]) == prev_count:
                break  # No more comments to load
        prev_count = len(post_data["comments"])


def _extract_comments_from_dom(page, post_data):
    """Extract comments from visible DOM and page source JSON."""
    if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
        return

    # ── Strategy 1: Extract from page source JSON (most reliable) ──
    try:
        html = page.content()

        # Look for comment data in embedded JSON
        # Pattern: "text":"comment text","created_at":...,"owner":{"username":"user"}
        comment_pattern = re.compile(
            r'"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,'
            r'[^}]*?"owner"\s*:\s*\{\s*[^}]*?"username"\s*:\s*"([^"]+)"',
            re.DOTALL,
        )
        # Also reverse pattern: "owner" before "text"
        comment_pattern2 = re.compile(
            r'"owner"\s*:\s*\{\s*[^}]*?"username"\s*:\s*"([^"]+)"[^}]*?\}\s*,'
            r'[^}]*?"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*,',
            re.DOTALL,
        )

        for m in comment_pattern.finditer(html):
            if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
                break
            text_raw = m.group(1)
            username = m.group(2)
            try:
                text = json.loads(f'"{text_raw}"')
                text = text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
            except Exception:
                text = text_raw.replace("\\n", "\n")
            _add_comment(post_data, {
                "username": username,
                "text": text,
                "likes": 0,
                "created_at": None,
            })

        for m in comment_pattern2.finditer(html):
            if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
                break
            username = m.group(1)
            text_raw = m.group(2)
            try:
                text = json.loads(f'"{text_raw}"')
                text = text.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
            except Exception:
                text = text_raw.replace("\\n", "\n")
            _add_comment(post_data, {
                "username": username,
                "text": text,
                "likes": 0,
                "created_at": None,
            })

    except Exception:
        pass

    # ── Strategy 2: Extract from visible DOM elements ──
    try:
        # Instagram renders comments as items with a link to the user profile
        # and text content. Try to find all comment containers.
        comment_containers = page.locator(
            'ul li:has(a[href*="/"]):has(span), '
            'div[role="button"]:has(a[href*="/"]):has(span)'
        ).all()

        for item in comment_containers:
            if len(post_data["comments"]) >= MAX_COMMENTS_PER_POST:
                break

            try:
                # Get username — it's always in a link
                username = None
                try:
                    user_link = item.locator('a[href*="/"]').first
                    href = user_link.get_attribute("href", timeout=300)
                    if href and not any(x in href for x in ["/p/", "/reel/", "/explore/", "/accounts/"]):
                        username = href.strip("/").split("/")[-1]
                except Exception:
                    pass

                if not username or username in ("", "explore", "accounts"):
                    continue

                # Get the full text content and remove the username from it
                full_text = item.text_content(timeout=500)
                if not full_text:
                    continue

                # The comment text is everything after the username
                text = full_text.strip()
                if text.startswith(username):
                    text = text[len(username):].strip()

                # Remove trailing metadata (likes, time, reply)
                text = re.sub(r'\d+[smhdw]\d*\s*(like|Reply|Translate).*$', '', text, flags=re.IGNORECASE).strip()
                text = re.sub(r'\s*(Reply|Translate|See translation)\s*$', '', text, flags=re.IGNORECASE).strip()

                if not text or len(text) < 2:
                    continue

                # Skip if it looks like a section header or the caption
                if text.lower() in ("log in", "sign up", "follow", "more"):
                    continue

                _add_comment(post_data, {
                    "username": username,
                    "text": text,
                    "likes": 0,
                    "created_at": None,
                })

            except Exception:
                continue

    except Exception:
        pass

