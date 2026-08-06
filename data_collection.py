import os
import json
import time
import random
import re
import traceback
from playwright.sync_api import sync_playwright

# Configuration
STORAGE_STATE_PATH = "state.json"
TARGET_USERNAMES = ["nasa", "natgeo"]
HEADLESS = False


def save_login_session(playwright):
    """Launches a browser for manual login and saves 'state.json'."""
    print("\n--- Initializing Manual Login Session ---")
    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/127.0.0.0 Safari/537.36"
        ),
    )
    page = context.new_page()

    page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")

    # Dismiss cookie consent if it appears
    _dismiss_cookie_banner(page)

    print("👉 Please log into Instagram in the browser window.")
    print("👉 Once fully logged in and the home feed is visible,")
    print("   press ENTER here to save the session...")

    input()  # Wait for user confirmation

    context.storage_state(path=STORAGE_STATE_PATH)
    print(f"✅ Authentication state saved to '{STORAGE_STATE_PATH}'!")
    browser.close()


def _dismiss_cookie_banner(page):
    """Attempt to dismiss Instagram's cookie consent banner."""
    try:
        for selector in [
            "button:has-text('Allow all cookies')",
            "button:has-text('Allow essential and optional cookies')",
            "button:has-text('Accept All')",
            "button:has-text('Accept')",
        ]:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click()
                print("   🍪 Dismissed cookie banner.")
                return
    except Exception:
        pass  # No cookie banner — that's fine


def _safe_get(d, *keys, default=None):
    """Safely traverse nested dicts without KeyError."""
    current = d
    for key in keys:
        if isinstance(current, dict):
            current = current.get(key)
        else:
            return default
        if current is None:
            return default
    return current


def _parse_user_data_graphql(user_info):
    """
    Parse user data from the OLDER GraphQL / edge_* response format.
    Keys: edge_followed_by, edge_follow, edge_owner_to_timeline_media
    """
    profile = {
        "username": user_info.get("username"),
        "full_name": user_info.get("full_name"),
        "biography": user_info.get("biography"),
        "external_url": user_info.get("external_url"),
        "followers_count": _safe_get(user_info, "edge_followed_by", "count", default=0),
        "following_count": _safe_get(user_info, "edge_follow", "count", default=0),
        "is_private": user_info.get("is_private"),
        "is_verified": user_info.get("is_verified"),
        "posts_count": _safe_get(user_info, "edge_owner_to_timeline_media", "count", default=0),
        "profile_pic_url": user_info.get("profile_pic_url_hd", user_info.get("profile_pic_url")),
        "recent_posts": [],
    }

    edges = _safe_get(user_info, "edge_owner_to_timeline_media", "edges", default=[])
    for post in edges:
        node = post.get("node", {})
        caption = ""
        caption_edges = _safe_get(node, "edge_media_to_caption", "edges", default=[])
        if caption_edges:
            caption = _safe_get(caption_edges[0], "node", "text", default="")

        profile["recent_posts"].append({
            "shortcode": node.get("shortcode"),
            "is_video": node.get("is_video"),
            "likes": _safe_get(node, "edge_liked_by", "count", default=0),
            "comments": _safe_get(node, "edge_media_to_comment", "count", default=0),
            "caption": caption,
            "display_url": node.get("display_url"),
            "video_views": node.get("video_view_count", 0),
        })

    return profile


def _parse_user_data_rest(user_info):
    """
    Parse user data from the NEWER REST / api/v1 response format.
    Keys: follower_count, following_count, media_count
    """
    external_url = user_info.get("external_url")
    if not external_url:
        bio_links = user_info.get("bio_links")
        if bio_links and isinstance(bio_links, list) and len(bio_links) > 0:
            external_url = bio_links[0].get("url")

    profile = {
        "username": user_info.get("username"),
        "full_name": user_info.get("full_name"),
        "biography": user_info.get("biography"),
        "external_url": external_url,
        "followers_count": user_info.get("follower_count", 0),
        "following_count": user_info.get("following_count", 0),
        "is_private": user_info.get("is_private"),
        "is_verified": user_info.get("is_verified"),
        "posts_count": user_info.get("media_count", 0),
        "profile_pic_url": _safe_get(user_info, "hd_profile_pic_url_info", "url")
                           or user_info.get("profile_pic_url"),
        "recent_posts": [],
    }

    # Newer format may still include edge_owner_to_timeline_media
    edges = _safe_get(user_info, "edge_owner_to_timeline_media", "edges", default=[])
    for post in edges:
        node = post.get("node", {})
        caption = ""
        caption_edges = _safe_get(node, "edge_media_to_caption", "edges", default=[])
        if caption_edges:
            caption = _safe_get(caption_edges[0], "node", "text", default="")
        profile["recent_posts"].append({
            "shortcode": node.get("shortcode"),
            "is_video": node.get("is_video"),
            "likes": _safe_get(node, "edge_liked_by", "count", default=0),
            "comments": _safe_get(node, "edge_media_to_comment", "count", default=0),
            "caption": caption,
            "display_url": node.get("display_url"),
            "video_views": node.get("video_view_count", 0),
        })

    return profile


def scrape_profile(context, username):
    """Navigates to a profile and intercepts the JSON API payload."""
    page = context.new_page()
    profile_data = {}
    captured_responses = []

    def handle_response(response):
        nonlocal profile_data

        url = response.url

        # Match known Instagram API endpoints
        is_api_hit = any(pattern in url for pattern in [
            "web_profile_info",
            "/graphql",
            "/api/v1/users/",
            "/api/graphql",
        ])

        if not is_api_hit:
            return

        try:
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type and "text/javascript" not in content_type:
                return

            if response.status != 200:
                return

            data = response.json()
            captured_responses.append(url)

            # Strategy 1: data -> user (GraphQL / web_profile_info)
            user_info = _safe_get(data, "data", "user")
            if user_info and isinstance(user_info, dict) and user_info.get("username"):
                if "edge_followed_by" in user_info:
                    profile_data = _parse_user_data_graphql(user_info)
                else:
                    profile_data = _parse_user_data_rest(user_info)
                print(f"   ✅ Captured profile data from: {url[:100]}...")
                return

            # Strategy 2: user at top level (some REST responses)
            user_info = data.get("user")
            if isinstance(user_info, dict) and user_info.get("username"):
                profile_data = _parse_user_data_rest(user_info)
                print(f"   ✅ Captured profile data from: {url[:100]}...")
                return

            # Strategy 3: graphql nested under different keys (xdt_api__v1__...)
            data_obj = data.get("data")
            if isinstance(data_obj, dict):
                for key, value in data_obj.items():
                    if isinstance(value, dict) and value.get("username"):
                        profile_data = _parse_user_data_rest(value)
                        print(f"   ✅ Captured profile data from key '{key}': {url[:80]}...")
                        return

        except Exception as e:
            # Log instead of silently swallowing
            print(f"   ⚠️ Error parsing response from {url[:80]}: {e}")

    page.on("response", handle_response)

    print(f"\n🔍 Navigating to @{username}...")
    try:
        page.goto(
            f"https://www.instagram.com/{username}/",
            wait_until="domcontentloaded",
            timeout=30000,
        )
    except Exception as e:
        print(f"   ⚠️ Navigation warning (may still work): {e}")

    # Dismiss cookie banner if it appears
    _dismiss_cookie_banner(page)

    # Wait for the API response to arrive (poll with timeout)
    max_wait = 15
    waited = 0.0
    while not profile_data and waited < max_wait:
        time.sleep(0.5)
        waited += 0.5

    if not profile_data:
        print("   ⏳ No API interception yet. Scrolling to trigger lazy loads...")

    # Simulate human behavior
    time.sleep(random.uniform(1.5, 2.5))
    page.mouse.wheel(0, 500)
    time.sleep(random.uniform(1.0, 2.0))
    page.mouse.wheel(0, 300)
    time.sleep(random.uniform(1.0, 2.0))

    # Fallback: scrape from page meta tags
    if not profile_data:
        print("   🔄 Attempting fallback: parsing page source meta tags...")
        profile_data = _fallback_meta_scrape(page, username)

    if captured_responses:
        print(f"   📡 Total API responses intercepted: {len(captured_responses)}")
    else:
        print("   ❌ No matching API responses were intercepted.")

    page.close()
    return profile_data


def _parse_human_number(s):
    """Convert '1,234' or '1.2M' or '500K' to int."""
    s = s.strip().replace(",", "")
    multiplier = 1
    if s.upper().endswith("K"):
        multiplier = 1_000
        s = s[:-1]
    elif s.upper().endswith("M"):
        multiplier = 1_000_000
        s = s[:-1]
    elif s.upper().endswith("B"):
        multiplier = 1_000_000_000
        s = s[:-1]
    try:
        return int(float(s) * multiplier)
    except ValueError:
        return 0


def _fallback_meta_scrape(page, username):
    """
    Fallback: extract basic profile info from the page's meta tags
    when API interception fails.
    """
    try:
        data = {"username": username, "recent_posts": []}

        # og:description often contains "X Followers, Y Following, Z Posts - ..."
        try:
            desc = page.locator('meta[property="og:description"]').get_attribute("content", timeout=3000)
        except Exception:
            desc = None

        if desc:
            data["meta_description"] = desc
            nums = re.findall(r"([\d,.]+[KMB]?)\s+(Followers|Following|Posts)", desc, re.IGNORECASE)
            for val, label in nums:
                clean = _parse_human_number(val)
                if "follower" in label.lower():
                    data["followers_count"] = clean
                elif "following" in label.lower():
                    data["following_count"] = clean
                elif "post" in label.lower():
                    data["posts_count"] = clean

        try:
            title = page.locator('meta[property="og:title"]').get_attribute("content", timeout=3000)
        except Exception:
            title = None

        if title:
            m = re.match(r"(.+?)\s*\(@", title)
            if m:
                data["full_name"] = m.group(1).strip()

        try:
            pic = page.locator('meta[property="og:image"]').get_attribute("content", timeout=3000)
        except Exception:
            pic = None

        if pic:
            data["profile_pic_url"] = pic

        if data.get("followers_count"):
            print("   ✅ Fallback scrape succeeded (partial data from meta tags).")
            return data

    except Exception as e:
        print(f"   ⚠️ Fallback meta scrape failed: {e}")

    return {}


def main():
    with sync_playwright() as p:
        # Step 1: Login check
        if not os.path.exists(STORAGE_STATE_PATH):
            save_login_session(p)
            print("Session saved. Re-run this cell to begin scraping.")
            return

        # Validate that state.json is not empty / corrupted
        try:
            with open(STORAGE_STATE_PATH, "r") as f:
                state = json.load(f)
            if not state.get("cookies"):
                print("⚠️ state.json has no cookies. Deleting and re-logging in...")
                os.remove(STORAGE_STATE_PATH)
                save_login_session(p)
                print("Session saved. Re-run this cell to begin scraping.")
                return
        except (json.JSONDecodeError, KeyError):
            print("⚠️ state.json is corrupted. Deleting and re-logging in...")
            os.remove(STORAGE_STATE_PATH)
            save_login_session(p)
            print("Session saved. Re-run this cell to begin scraping.")
            return

        # Step 2: Launch browser using the saved storage state
        print("🚀 Launching browser context...")
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
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
        )

        output_dir = "scraped_data"
        os.makedirs(output_dir, exist_ok=True)

        # Step 3: Scrape profiles
        for username in TARGET_USERNAMES:
            data = scrape_profile(context, username)

            if data:
                file_path = os.path.join(output_dir, f"{username}.json")
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=4, ensure_ascii=False)
                print(f"✅ Successfully extracted @{username} -> Saved to '{file_path}'")

                # FIX: Safe formatting — original crashed if followers_count was None
                fc = data.get("followers_count")
                if isinstance(fc, (int, float)):
                    print(f"   Followers: {fc:,}")
                else:
                    print(f"   Followers: {fc}")

                print(f"   Fetched {len(data.get('recent_posts', []))} recent posts.")
            else:
                print(f"⚠️ Failed to extract any data for @{username}.")
                print("   Possible causes:")
                print("   - Your login session (state.json) may have expired. Delete it and re-login.")
                print("   - Instagram may have rate-limited or blocked this session.")
                print("   - The account may be private or not exist.")

            delay = random.uniform(5.0, 10.0)
            print(f"⏳ Pausing for {delay:.1f} seconds...")
            time.sleep(delay)

        browser.close()
        print("\n🎉 All target profiles processed!")


# Run directly in Jupyter or as a script
if __name__ == "__main__":
    main()
else:
    main()