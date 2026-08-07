"""Profile & post data parsing: GraphQL, REST, embedded JSON, meta tag fallback, engagement metadata."""

import json
import re
from datetime import datetime, timezone

from utils import safe_get, parse_human_number


# ──────────────────────── Profile Parsing ──────────────────────────────

def parse_user_data_graphql(user_info):
    """Parse from older GraphQL edge_* format."""
    profile = {
        "username": user_info.get("username"),
        "full_name": user_info.get("full_name"),
        "biography": user_info.get("biography"),
        "external_url": user_info.get("external_url"),
        "followers_count": safe_get(user_info, "edge_followed_by", "count", default=0),
        "following_count": safe_get(user_info, "edge_follow", "count", default=0),
        "posts_count": safe_get(user_info, "edge_owner_to_timeline_media", "count", default=0),
        "recent_posts": [],
    }
    edges = safe_get(user_info, "edge_owner_to_timeline_media", "edges", default=[])
    for post in edges:
        sc = safe_get(post, "node", "shortcode")
        if sc:
            profile["recent_posts"].append(sc)
    return profile


def parse_user_data_rest(user_info):
    """Parse from newer REST follower_count format."""
    profile = {
        "username": user_info.get("username"),
        "full_name": user_info.get("full_name"),
        "biography": user_info.get("biography"),
        "followers_count": user_info.get("follower_count", 0),
        "following_count": user_info.get("following_count", 0),
        "posts_count": user_info.get("media_count", 0),
        "recent_posts": [],
    }
    edges = safe_get(user_info, "edge_owner_to_timeline_media", "edges", default=[])
    for post in edges:
        sc = safe_get(post, "node", "shortcode")
        if sc:
            profile["recent_posts"].append(sc)
    return profile


def try_parse_user(user_info):
    """Auto-detect format and parse."""
    if not user_info or not isinstance(user_info, dict):
        return None
    if not user_info.get("username"):
        return None
    if "edge_followed_by" in user_info:
        return parse_user_data_graphql(user_info)
    else:
        return parse_user_data_rest(user_info)


# ──────────────── Embedded JSON Extraction (Primary) ──────────────────

def extract_from_page_source(page, username):
    """
    PRIMARY strategy: extract user data from JSON embedded in the HTML.
    Instagram embeds profile data inside <script> tags in various formats.
    """
    try:
        html = page.content()
    except Exception as e:
        print(f"   ⚠️ Could not get page content: {e}")
        return None

    # ── Strategy A: window._sharedData ──
    match = re.search(r"window\._sharedData\s*=\s*(\{.+?\});</script>", html, re.DOTALL)
    if match:
        try:
            shared = json.loads(match.group(1))
            user_info = safe_get(
                shared, "entry_data", "ProfilePage", 0, "graphql", "user"
            )
            result = try_parse_user(user_info)
            if result:
                print("   ✅ Extracted from window._sharedData")
                return result
        except Exception as e:
            print(f"   ⚠️ _sharedData parse error: {e}")

    # ── Strategy B: window.__additionalDataLoaded ──
    match = re.search(
        r"window\.__additionalDataLoaded\s*\(\s*['\"].*?['\"]\s*,\s*(\{.+?\})\s*\)\s*;",
        html,
        re.DOTALL,
    )
    if match:
        try:
            additional = json.loads(match.group(1))
            user_info = safe_get(additional, "graphql", "user")
            result = try_parse_user(user_info)
            if result:
                print("   ✅ Extracted from __additionalDataLoaded")
                return result
        except Exception as e:
            print(f"   ⚠️ __additionalDataLoaded parse error: {e}")

    # ── Strategy C: Search ALL <script> tags for JSON containing user data ──
    script_blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    for block in script_blocks:
        if len(block) < 100 or "{" not in block:
            continue

        json_matches = re.findall(
            r"(\{[\"']username[\"']\s*:\s*[\"']" + re.escape(username) + r"[\"'].*?\})",
            block,
        )

        if not json_matches:
            for json_candidate in re.finditer(
                r'(\{"[a-zA-Z].*?"username"\s*:\s*"' + re.escape(username) + r'".*?\})',
                block,
                re.DOTALL,
            ):
                json_matches.append(json_candidate.group(0))

        if not json_matches and block.strip().startswith("{"):
            json_matches = [block.strip()]

        for raw_json in json_matches:
            try:
                parsed = json.loads(raw_json)
                found = find_user_in_json(parsed, username)
                if found:
                    result = try_parse_user(found)
                    if result:
                        print("   ✅ Extracted from embedded <script> JSON")
                        return result
            except json.JSONDecodeError:
                continue

    # ── Strategy D: Try __NEXT_DATA__ (React/Next.js format) ──
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.DOTALL)
    if match:
        try:
            next_data = json.loads(match.group(1))
            found = find_user_in_json(next_data, username)
            if found:
                result = try_parse_user(found)
                if result:
                    print("   ✅ Extracted from __NEXT_DATA__")
                    return result
        except Exception as e:
            print(f"   ⚠️ __NEXT_DATA__ parse error: {e}")

    return None


def find_user_in_json(obj, username, depth=0):
    """
    Recursively search a JSON object for a dict that looks like
    Instagram user data (has 'username' matching our target).
    """
    if depth > 15:
        return None

    if isinstance(obj, dict):
        if obj.get("username") == username:
            has_followers = (
                "edge_followed_by" in obj
                or "follower_count" in obj
                or "edge_follow" in obj
                or "following_count" in obj
            )
            if has_followers:
                return obj

        for value in obj.values():
            found = find_user_in_json(value, username, depth + 1)
            if found:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = find_user_in_json(item, username, depth + 1)
            if found:
                return found

    return None


# ──────────────── Meta Tag Fallback ────────────────────────────────────

def fallback_meta_scrape(page, username):
    """Last-resort fallback: parse og:description for basic counts."""
    try:
        data = {"username": username, "recent_posts": []}

        desc = None
        try:
            desc = page.locator('meta[property="og:description"]').get_attribute(
                "content", timeout=3000
            )
        except Exception:
            pass

        if desc:
            data["meta_description"] = desc
            nums = re.findall(
                r"([\d,.]+[KMB]?)\s+(Followers|Following|Posts)",
                desc,
                re.IGNORECASE,
            )
            for val, label in nums:
                clean = parse_human_number(val)
                key = label.lower()
                if "follower" in key:
                    data["followers_count"] = clean
                elif "following" in key:
                    data["following_count"] = clean
                elif "post" in key:
                    data["posts_count"] = clean

        title = None
        try:
            title = page.locator('meta[property="og:title"]').get_attribute(
                "content", timeout=3000
            )
        except Exception:
            pass
        if title:
            m = re.match(r"(.+?)\s*\(@", title)
            if m:
                data["full_name"] = m.group(1).strip()

        if data.get("followers_count"):
            print("   ✅ Fallback: got partial data from meta tags")
            return data
    except Exception as e:
        print(f"   ⚠️ Meta scrape failed: {e}")

    return None


# ──────────────── Post Metadata Extraction ─────────────────────────────

def extract_post_metadata(page, shortcode):
    """
    Extract engagement metadata from a post page's HTML source.
    Returns dict with like_count, comment_count, view_count, caption,
    timestamp, post_type, owner_username.
    """
    metadata = {
        "shortcode": shortcode,
        "url": f"https://www.instagram.com/p/{shortcode}/",
        "post_type": "unknown",
        "owner_username": None,
        "caption": None,
        "like_count": None,
        "comment_count": None,
        "view_count": None,
        "timestamp": None,
        "timestamp_iso": None,
    }

    try:
        html = page.content()
    except Exception:
        return metadata

    # ── Like count ──
    for pattern in [
        r'"edge_media_preview_like"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"like_count"\s*:\s*(\d+)',
        r'"likes"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern, html)
        if m:
            metadata["like_count"] = int(m.group(1))
            break

    # ── Comment count ──
    for pattern in [
        r'"edge_media_to_comment"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"edge_media_to_parent_comment"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"comment_count"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern, html)
        if m:
            metadata["comment_count"] = int(m.group(1))
            break

    # ── View count (video/reel only) ──
    for pattern in [
        r'"video_view_count"\s*:\s*(\d+)',
        r'"play_count"\s*:\s*(\d+)',
        r'"view_count"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern, html)
        if m:
            metadata["view_count"] = int(m.group(1))
            break

    # ── Caption ──
    for pattern in [
        r'"edge_media_to_caption"\s*:\s*\{\s*"edges"\s*:\s*\[\s*\{\s*"node"\s*:\s*\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        r'"caption"\s*:\s*\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                raw = m.group(1)
                # Decode JSON-style escapes (\n, \u00e9, etc.)
                # Use json.loads with wrapping quotes to safely decode
                decoded = json.loads(f'"{raw}"')
                # Remove any remaining surrogate characters that can't be UTF-8 encoded
                metadata["caption"] = decoded.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
            except Exception:
                # Fallback: just use raw text, stripping backslash escapes
                metadata["caption"] = raw.replace("\\n", "\n").replace("\\/", "/")
            break

    # ── Timestamp ──
    for pattern in [
        r'"taken_at_timestamp"\s*:\s*(\d+)',
        r'"taken_at"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern, html)
        if m:
            ts = int(m.group(1))
            metadata["timestamp"] = ts
            try:
                metadata["timestamp_iso"] = datetime.fromtimestamp(
                    ts, tz=timezone.utc
                ).isoformat()
            except Exception:
                pass
            break

    # ── Post type ──
    is_video = bool(re.search(r'"is_video"\s*:\s*true', html, re.IGNORECASE))
    product_type_match = re.search(r'"product_type"\s*:\s*"(\w+)"', html)
    typename_match = re.search(r'"__typename"\s*:\s*"Graph(\w+)"', html)

    if product_type_match:
        pt = product_type_match.group(1).lower()
        if "clips" in pt or "reel" in pt:
            metadata["post_type"] = "reel"
        elif "carousel" in pt:
            metadata["post_type"] = "carousel"
        elif is_video:
            metadata["post_type"] = "video"
        else:
            metadata["post_type"] = "image"
    elif typename_match:
        tn = typename_match.group(1).lower()
        if "sidecar" in tn:
            metadata["post_type"] = "carousel"
        elif "video" in tn:
            metadata["post_type"] = "video" if not is_video else "reel"
        else:
            metadata["post_type"] = "image"
    elif is_video:
        metadata["post_type"] = "video"
    else:
        # Check for carousel (sidecar)
        if re.search(r'"edge_sidecar_to_children"', html) or re.search(r'"carousel_media"', html):
            metadata["post_type"] = "carousel"
        else:
            metadata["post_type"] = "image"

    # ── Owner username ──
    m = re.search(r'"owner"\s*:\s*\{[^}]*"username"\s*:\s*"([^"]+)"', html)
    if m:
        metadata["owner_username"] = m.group(1)

    # ── DOM fallback for like count ──
    if metadata["like_count"] is None:
        try:
            # Instagram shows "Liked by X and Y others" or "N likes"
            like_section = page.locator('section:has(button[aria-label*="like" i])').first
            like_text = like_section.text_content(timeout=3000)
            if like_text:
                nums = re.findall(r"([\d,]+)", like_text)
                if nums:
                    metadata["like_count"] = int(nums[-1].replace(",", ""))
        except Exception:
            pass

    source = []
    if metadata["like_count"] is not None:
        source.append("likes")
    if metadata["comment_count"] is not None:
        source.append("comments")
    if metadata["view_count"] is not None:
        source.append("views")
    if metadata["caption"]:
        source.append("caption")
    if source:
        print(f"      📊 Metadata: {', '.join(source)}")

    return metadata
