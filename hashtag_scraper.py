"""Hashtag-based post discovery: scrapes explore/tags pages to collect shortcodes + engagement."""

import re
import time
import random
import json

from config import (
    POSTS_PER_HASHTAG,
    HASHTAG_SCROLL_PAUSE_MIN,
    HASHTAG_SCROLL_PAUSE_MAX,
)
from utils import dismiss_cookie_banner, dismiss_login_popup, safe_get


# ────────────────────── Hashtag Explore Scraper ──────────────────────────

def scrape_hashtag(context, hashtag):
    """
    Navigate to instagram.com/explore/tags/{hashtag}/ and collect:
      - post shortcodes (up to POSTS_PER_HASHTAG)
      - basic engagement snapshot from grid (like/comment counts where available)

    Returns a dict:
      {
        "hashtag": str,
        "posts_found": int,
        "shortcodes": [str, ...],        # unique, ordered
        "grid_data": {shortcode: {...}}  # metadata captured from grid tiles
      }
    """
    page = context.new_page()
    hashtag = hashtag.lstrip("#").strip()
    url = f"https://www.instagram.com/explore/tags/{hashtag}/"

    result = {
        "hashtag": hashtag,
        "posts_found": 0,
        "shortcodes": [],
        "grid_data": {},
    }
    seen_shortcodes = set()

    # ── Intercept GraphQL tag responses to grab engagement data ──
    def handle_response(response):
        resp_url = response.url
        if not any(p in resp_url for p in ["graphql", "/api/graphql", "/api/v1/tags/"]):
            return
        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            if response.status != 200:
                return
            data = response.json()
            _parse_tag_api_response(data, result, seen_shortcodes)
        except Exception:
            pass

    page.on("response", handle_response)

    print(f"\n🔖 Scraping hashtag: #{hashtag} (target: {POSTS_PER_HASHTAG} posts)")

    # ── Navigate with retry ──
    nav_ok = False
    for nav_attempt in range(1, 4):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=35000)
            nav_ok = True
            break
        except Exception as e:
            err_str = str(e)
            is_transient = any(x in err_str for x in [
                "ERR_INTERNET_DISCONNECTED", "ERR_NETWORK_CHANGED",
                "ERR_CONNECTION_RESET", "Timeout", "timeout",
            ])
            if is_transient and nav_attempt < 3:
                wait_sec = nav_attempt * 10
                print(f"   ⚠️ Navigation error (attempt {nav_attempt}/3), retrying in {wait_sec}s...")
                time.sleep(wait_sec)
            else:
                print(f"   ❌ Could not load #{hashtag} after 3 attempts: {e}")
                page.close()
                return result

    # Give the page a longer warm-up — explore/tags pages lazy-load aggressively
    time.sleep(random.uniform(6.0, 10.0))
    dismiss_cookie_banner(page)
    dismiss_login_popup(page)
    time.sleep(2)
    dismiss_login_popup(page)
    time.sleep(2)

    # Scroll once to trigger lazy load, then wait again
    try:
        page.mouse.wheel(0, 400)
    except Exception:
        pass
    time.sleep(5)

    # ── Initial DOM scan ──
    _extract_shortcodes_from_dom(page, result, seen_shortcodes)
    if result["shortcodes"]:
        print(f"   📦 Initial scan: {len(result['shortcodes'])} posts found")
    else:
        print("   ⏳ No posts on initial scan — page may still be loading...")
        time.sleep(5)
        _extract_shortcodes_from_dom(page, result, seen_shortcodes)


    # ── Scroll to load more posts ──
    scroll_attempts = 0
    max_scroll_attempts = max(100, POSTS_PER_HASHTAG // 4)  # each scroll ~4 new posts
    consecutive_no_new = 0

    while len(result["shortcodes"]) < POSTS_PER_HASHTAG and scroll_attempts < max_scroll_attempts:
        prev_count = len(result["shortcodes"])

        # Vary scroll distance — bigger jumps push past lazy-load walls
        scroll_dist = random.randint(800, 2000)
        page.mouse.wheel(0, scroll_dist)
        time.sleep(random.uniform(HASHTAG_SCROLL_PAUSE_MIN, HASHTAG_SCROLL_PAUSE_MAX))

        # Extract from DOM after scroll
        _extract_shortcodes_from_dom(page, result, seen_shortcodes)

        current_count = len(result["shortcodes"])
        scroll_attempts += 1

        if current_count == prev_count:
            consecutive_no_new += 1

            # Try clicking "Load more" / "Show more" buttons
            try:
                for btn_text in ["Load more", "Show more", "See more posts"]:
                    btn = page.locator(f'button:has-text("{btn_text}")').first
                    if btn.is_visible(timeout=1000):
                        btn.click()
                        print(f"   🖱️  Clicked '{btn_text}' button")
                        time.sleep(4)  # longer wait for new tiles to render
                        _extract_shortcodes_from_dom(page, result, seen_shortcodes)
                        break
            except Exception:
                pass

            # Every 8 stuck scrolls, try scrolling back up slightly then down
            if consecutive_no_new % 8 == 0 and consecutive_no_new > 0:
                try:
                    page.mouse.wheel(0, -400)
                    time.sleep(2)
                    page.mouse.wheel(0, 1200)
                    time.sleep(4)
                    _extract_shortcodes_from_dom(page, result, seen_shortcodes)
                except Exception:
                    pass

            if consecutive_no_new >= 15:
                print(f"   ⚠️ No new posts after 15 scrolls — stopping at {len(result['shortcodes'])}")
                break
        else:
            consecutive_no_new = 0
            current_count = len(result["shortcodes"])
            pct = min(100, int(current_count / POSTS_PER_HASHTAG * 100))
            print(f"   📦 Collected {current_count}/{POSTS_PER_HASHTAG} shortcodes ({pct}%)")

    result["posts_found"] = len(result["shortcodes"])
    print(f"   ✅ #{hashtag}: collected {result['posts_found']} unique shortcodes")
    page.close()
    return result


# ────────────────────── Helper: DOM Extraction ───────────────────────────

def _extract_shortcodes_from_dom(page, result, seen_shortcodes):
    """Parse all post links from the current DOM state."""
    try:
        # Hashtag grid links — both /p/ (post) and /reel/ URLs
        links = page.locator('a[href*="/p/"], a[href*="/reel/"]').all()
        for link in links:
            try:
                href = link.get_attribute("href", timeout=300)
                if not href:
                    continue
                m = re.search(r"/(?:p|reel)/([A-Za-z0-9_-]+)", href)
                if not m:
                    continue
                sc = m.group(1)
                if sc in seen_shortcodes:
                    continue
                seen_shortcodes.add(sc)
                result["shortcodes"].append(sc)

                # Try to get thumbnail + basic data from grid tile
                try:
                    img = link.locator("img").first
                    src = img.get_attribute("src", timeout=300)
                    alt = img.get_attribute("alt", timeout=300)
                    if sc not in result["grid_data"]:
                        result["grid_data"][sc] = {
                            "shortcode": sc,
                            "thumbnail_url": src,
                            "alt_text": alt,
                            "post_type": "reel" if "/reel/" in href else "post",
                        }
                except Exception:
                    if sc not in result["grid_data"]:
                        result["grid_data"][sc] = {
                            "shortcode": sc,
                            "thumbnail_url": None,
                            "alt_text": None,
                            "post_type": "reel" if "/reel/" in href else "post",
                        }
            except Exception:
                continue
    except Exception:
        pass


# ────────────────────── Helper: API Response Parsing ─────────────────────

def _parse_tag_api_response(data, result, seen_shortcodes):
    """Parse GraphQL tag API response to extract shortcodes and engagement."""
    # GraphQL v1 format: data.hashtag.edge_hashtag_to_media.edges
    hashtag_obj = (
        safe_get(data, "data", "hashtag")
        or safe_get(data, "graphql", "hashtag")
    )
    if hashtag_obj:
        for edge_key in [
            "edge_hashtag_to_media",
            "edge_hashtag_to_top_posts",
            "edge_hashtag_to_content_advisory",
        ]:
            edges = safe_get(hashtag_obj, edge_key, "edges", default=[])
            for edge in edges:
                _add_shortcode_from_edge(edge, result, seen_shortcodes)
        return

    # REST v1 format: items list
    items = data.get("items", [])
    for item in items:
        sc = item.get("code") or item.get("shortcode")
        if sc and sc not in seen_shortcodes:
            seen_shortcodes.add(sc)
            result["shortcodes"].append(sc)
            result["grid_data"][sc] = {
                "shortcode": sc,
                "thumbnail_url": safe_get(item, "image_versions2", "candidates", 0, "url"),
                "like_count": item.get("like_count"),
                "comment_count": item.get("comment_count"),
                "is_video": item.get("is_video", False),
                "post_type": "reel" if item.get("product_type") == "clips" else ("video" if item.get("is_video") else "image"),
            }


def _add_shortcode_from_edge(edge, result, seen_shortcodes):
    """Extract shortcode + basic engagement from a GraphQL edge node."""
    node = edge.get("node", {})
    sc = node.get("shortcode")
    if not sc or sc in seen_shortcodes:
        return
    seen_shortcodes.add(sc)
    result["shortcodes"].append(sc)
    result["grid_data"][sc] = {
        "shortcode": sc,
        "thumbnail_url": node.get("display_url") or node.get("thumbnail_src"),
        "like_count": safe_get(node, "edge_media_preview_like", "count"),
        "comment_count": safe_get(node, "edge_media_to_comment", "count"),
        "is_video": node.get("is_video", False),
        "post_type": "reel" if node.get("product_type") == "clips" else (
            "video" if node.get("is_video") else "image"
        ),
        "owner_username": safe_get(node, "owner", "username"),
    }
