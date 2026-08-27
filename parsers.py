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


# ──────────────── Post Metadata from API JSON ───────────────────────────

def extract_metadata_from_api(media_info):
    """
    PRIMARY: extract all engagement data directly from the intercepted
    xdt_shortcode_media / items[0] API response.
    This is the most reliable source — no HTML parsing needed.

    Returns a metadata dict with:
      like_count, comment_count, view_count, save_count,
      caption, timestamp, post_type, is_video,
      owner_username, owner_full_name, owner_followers,
      owner_following, owner_posts_count, owner_is_verified
    """
    if not media_info or not isinstance(media_info, dict):
        return {}

    metadata = {}

    # ── Post identity ──
    metadata["shortcode"] = (
        media_info.get("shortcode") or media_info.get("code")
    )
    metadata["url"] = f"https://www.instagram.com/p/{metadata['shortcode']}/" if metadata["shortcode"] else None
    metadata["post_id"] = media_info.get("id") or media_info.get("pk")

    # ── Post type ──
    is_video = media_info.get("is_video", False)
    product_type = media_info.get("product_type", "")
    typename = media_info.get("__typename", "")
    media_type = media_info.get("media_type")  # REST: 1=photo, 2=video, 8=carousel

    if "clips" in product_type or "reel" in product_type or typename == "XDTGraphVideo":
        metadata["post_type"] = "reel"
    elif media_type == 8 or "sidecar" in typename.lower() or "carousel" in product_type:
        metadata["post_type"] = "carousel"
    elif is_video or media_type == 2:
        metadata["post_type"] = "video"
    else:
        metadata["post_type"] = "image"

    metadata["is_video"] = is_video

    # ── Engagement counts ──
    # Likes
    metadata["like_count"] = (
        safe_get(media_info, "edge_media_preview_like", "count")
        or safe_get(media_info, "edge_liked_by", "count")
        or media_info.get("like_count")
        or safe_get(media_info, "likes", "count")
    )

    # Comments
    metadata["comment_count"] = (
        safe_get(media_info, "edge_media_to_comment", "count")
        or safe_get(media_info, "edge_media_to_parent_comment", "count")
        or media_info.get("comment_count")
        or safe_get(media_info, "comments", "count")
    )

    # Views (video/reel)
    metadata["view_count"] = (
        media_info.get("video_view_count")
        or media_info.get("play_count")
        or media_info.get("view_count")
        or media_info.get("ig_play_count")
    )

    # Saves / bookmark count
    metadata["save_count"] = (
        media_info.get("saved_collection_ids_count")  # sometimes present
        or safe_get(media_info, "edge_media_to_collection", "count")
        or None  # saves are often hidden from API
    )

    # ── Caption ──
    caption_raw = (
        safe_get(media_info, "edge_media_to_caption", "edges", 0, "node", "text")
        or safe_get(media_info, "caption", "text")
        or (media_info.get("caption") if isinstance(media_info.get("caption"), str) else None)
    )
    if caption_raw:
        try:
            metadata["caption"] = caption_raw.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
        except Exception:
            metadata["caption"] = str(caption_raw)
    else:
        metadata["caption"] = None

    # ── Timestamp ──
    ts = media_info.get("taken_at_timestamp") or media_info.get("taken_at")
    metadata["timestamp"] = ts
    if ts:
        try:
            metadata["timestamp_iso"] = datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
        except Exception:
            metadata["timestamp_iso"] = None
    else:
        metadata["timestamp_iso"] = None

    # ── Location ──
    loc = media_info.get("location")
    if loc and isinstance(loc, dict):
        metadata["location"] = loc.get("name")
    else:
        metadata["location"] = None

    # ── Owner / Author info ──
    owner = media_info.get("owner") or media_info.get("user") or {}
    if isinstance(owner, dict):
        metadata["owner_username"] = owner.get("username")
        metadata["owner_full_name"] = owner.get("full_name")
        metadata["owner_is_verified"] = owner.get("is_verified", False)
        metadata["owner_profile_pic"] = owner.get("profile_pic_url")

        # Follower/following counts (present when post is on owner's own page)
        metadata["owner_followers"] = (
            safe_get(owner, "edge_followed_by", "count")
            or owner.get("follower_count")
        )
        metadata["owner_following"] = (
            safe_get(owner, "edge_follow", "count")
            or owner.get("following_count")
        )
        metadata["owner_posts_count"] = (
            safe_get(owner, "edge_owner_to_timeline_media", "count")
            or owner.get("media_count")
        )
    else:
        metadata["owner_username"] = None
        metadata["owner_full_name"] = None
        metadata["owner_is_verified"] = None
        metadata["owner_profile_pic"] = None
        metadata["owner_followers"] = None
        metadata["owner_following"] = None
        metadata["owner_posts_count"] = None

    # ── Tags / hashtags from caption ──
    if metadata.get("caption"):
        metadata["hashtags"] = re.findall(r"#(\w+)", metadata["caption"])
        metadata["mentions"] = re.findall(r"@(\w+)", metadata["caption"])
    else:
        metadata["hashtags"] = []
        metadata["mentions"] = []

    # ── Instagram AI-generation flags (from API payload) ──
    metadata["is_generated_media"] = bool(media_info.get("is_generated_media"))
    metadata["ai_agent_data"] = media_info.get("ai_agent_data")  # None if absent

    return metadata


# ──────────────── Post Metadata HTML Fallback ───────────────────────────

def extract_post_metadata(page, shortcode):
    """
    FALLBACK: extract engagement metadata from page HTML when API intercept
    didn't fire. Tries multiple JSON patterns in the page source.
    """
    metadata = {
        "shortcode": shortcode,
        "url": f"https://www.instagram.com/p/{shortcode}/",
        "post_type": "unknown",
        "is_video": False,
        "owner_username": None,
        "owner_full_name": None,
        "owner_followers": None,
        "owner_following": None,
        "owner_posts_count": None,
        "owner_is_verified": None,
        "caption": None,
        "like_count": None,
        "comment_count": None,
        "view_count": None,
        "save_count": None,
        "timestamp": None,
        "timestamp_iso": None,
        "hashtags": [],
        "mentions": [],
        "location": None,
    }

    try:
        html = page.content()
    except Exception:
        return metadata

    # ── Try to find and parse the full media JSON blob ──
    # Instagram embeds the full post JSON in script tags
    for pattern in [
        r'"xdt_shortcode_media"\s*:\s*(\{.*?\})\s*[,}]',
        r'"shortcode_media"\s*:\s*(\{.*?\})\s*[,}]',
        r'"items"\s*:\s*\[(\{.*?\})\]',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                media_info = json.loads(m.group(1))
                result = extract_metadata_from_api(media_info)
                if result.get("like_count") is not None or result.get("owner_username"):
                    result["shortcode"] = shortcode
                    _report_metadata_fields(result)
                    return result
            except Exception:
                pass

    # ── Individual field regex fallbacks ──
    for pattern in [
        r'"edge_media_preview_like"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"like_count"\s*:\s*(\d+)',
        r'"likes"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern, html)
        if m:
            metadata["like_count"] = int(m.group(1))
            break

    for pattern in [
        r'"edge_media_to_comment"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"edge_media_to_parent_comment"\s*:\s*\{\s*"count"\s*:\s*(\d+)',
        r'"comment_count"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern, html)
        if m:
            metadata["comment_count"] = int(m.group(1))
            break

    for pattern in [
        r'"video_view_count"\s*:\s*(\d+)',
        r'"play_count"\s*:\s*(\d+)',
        r'"view_count"\s*:\s*(\d+)',
    ]:
        m = re.search(pattern, html)
        if m:
            metadata["view_count"] = int(m.group(1))
            break

    # Caption
    for pattern in [
        r'"edge_media_to_caption"\s*:\s*\{\s*"edges"\s*:\s*\[\s*\{\s*"node"\s*:\s*\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
        r'"caption"\s*:\s*\{\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"',
    ]:
        m = re.search(pattern, html, re.DOTALL)
        if m:
            try:
                raw = m.group(1)
                decoded = json.loads(f'"{raw}"')
                metadata["caption"] = decoded.encode("utf-8", errors="surrogatepass").decode("utf-8", errors="replace")
            except Exception:
                metadata["caption"] = m.group(1).replace("\\n", "\n").replace("\\/", "/")
            if metadata["caption"]:
                metadata["hashtags"] = re.findall(r"#(\w+)", metadata["caption"])
                metadata["mentions"] = re.findall(r"@(\w+)", metadata["caption"])
            break

    # Timestamp
    for pattern in [r'"taken_at_timestamp"\s*:\s*(\d+)', r'"taken_at"\s*:\s*(\d+)']:
        m = re.search(pattern, html)
        if m:
            ts = int(m.group(1))
            metadata["timestamp"] = ts
            try:
                metadata["timestamp_iso"] = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
            except Exception:
                pass
            break

    # Post type
    is_video = bool(re.search(r'"is_video"\s*:\s*true', html, re.IGNORECASE))
    metadata["is_video"] = is_video
    product_type_m = re.search(r'"product_type"\s*:\s*"(\w+)"', html)
    typename_m = re.search(r'"__typename"\s*:\s*"Graph(\w+)"', html)
    if product_type_m:
        pt = product_type_m.group(1).lower()
        if "clips" in pt or "reel" in pt:
            metadata["post_type"] = "reel"
        elif "carousel" in pt:
            metadata["post_type"] = "carousel"
        elif is_video:
            metadata["post_type"] = "video"
        else:
            metadata["post_type"] = "image"
    elif typename_m:
        tn = typename_m.group(1).lower()
        if "sidecar" in tn:
            metadata["post_type"] = "carousel"
        elif "video" in tn:
            metadata["post_type"] = "reel" if is_video else "video"
        else:
            metadata["post_type"] = "image"
    elif is_video:
        metadata["post_type"] = "video"
    elif re.search(r'"edge_sidecar_to_children"|"carousel_media"', html):
        metadata["post_type"] = "carousel"
    else:
        metadata["post_type"] = "image"

    # Owner
    m = re.search(r'"owner"\s*:\s*\{[^}]*"username"\s*:\s*"([^"]+)"', html)
    if m:
        metadata["owner_username"] = m.group(1)
    m = re.search(r'"owner"\s*:\s*\{[^}]*"full_name"\s*:\s*"([^"]*)"', html)
    if m:
        metadata["owner_full_name"] = m.group(1)
    for pat in [r'"edge_followed_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)', r'"follower_count"\s*:\s*(\d+)']:
        m = re.search(pat, html)
        if m:
            metadata["owner_followers"] = int(m.group(1))
            break
    for pat in [r'"edge_follow"\s*:\s*\{\s*"count"\s*:\s*(\d+)', r'"following_count"\s*:\s*(\d+)']:
        m = re.search(pat, html)
        if m:
            metadata["owner_following"] = int(m.group(1))
            break

    # DOM fallback for likes
    if metadata["like_count"] is None:
        try:
            like_section = page.locator('section:has(button[aria-label*="like" i])').first
            like_text = like_section.text_content(timeout=3000)
            if like_text:
                nums = re.findall(r"([\d,]+)", like_text)
                if nums:
                    metadata["like_count"] = int(nums[-1].replace(",", ""))
        except Exception:
            pass

    _report_metadata_fields(metadata)
    return metadata


def _report_metadata_fields(metadata):
    """Print a summary of what metadata was captured."""
    captured = []
    if metadata.get("like_count") is not None:
        captured.append(f"❤️ {metadata['like_count']:,}")
    if metadata.get("comment_count") is not None:
        captured.append(f"💬 {metadata['comment_count']:,}")
    if metadata.get("view_count") is not None:
        captured.append(f"👁️ {metadata['view_count']:,}")
    if metadata.get("save_count") is not None:
        captured.append(f"🔖 {metadata['save_count']:,}")
    if metadata.get("owner_username"):
        captured.append(f"👤 @{metadata['owner_username']}")
    if metadata.get("owner_followers") is not None:
        captured.append(f"👥 {metadata['owner_followers']:,} followers")
    if captured:
        print(f"      📊 {' | '.join(captured)}")
    else:
        print("      📊 Metadata: (awaiting API intercept)")
