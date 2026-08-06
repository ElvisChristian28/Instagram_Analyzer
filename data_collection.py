import os
import json
import time
import random
import re
import urllib.request
from playwright.sync_api import sync_playwright

# ──────────────────────────── Configuration ────────────────────────────
STORAGE_STATE_PATH = "state.json"
TARGET_USERNAMES = ["nasa", "natgeo"]
HEADLESS = False
MAX_POSTS_TO_DOWNLOAD = 3

# Set True to delete state.json and force a fresh login
FORCE_RELOGIN = False

# ── Login Credentials ──
# The script will type these into Instagram automatically.
# ⚠️  CHANGE YOUR PASSWORD after state.json is created, then clear these.
IG_USERNAME = "elcianna_6"
IG_PASSWORD = "anncia-elvis0628"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)


# ──────────────────────────── Helpers ──────────────────────────────────

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


def _download_file(url, filepath):
    """Downloads a file from a URL to the local disk."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=30) as response:
            with open(filepath, "wb") as out_file:
                out_file.write(response.read())
        return True
    except Exception as e:
        print(f"      ⚠️ Download failed: {e}")
        return False


def _dismiss_cookie_banner(page):
    """Attempt to dismiss Instagram's cookie consent banner."""
    try:
        for selector in [
            "button:has-text('Allow all cookies')",
            "button:has-text('Allow essential and optional cookies')",
            "button:has-text('Accept All')",
            "button:has-text('Accept')",
            "button:has-text('Decline optional cookies')",
        ]:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(1)
                print("   🍪 Dismissed cookie banner.")
                return
    except Exception:
        pass


def _dismiss_login_popup(page):
    """Dismiss the 'Log in to continue' or 'Turn on Notifications' popups."""
    try:
        for text in ["Not Now", "Not now", "Cancel", "Close"]:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=2000):
                btn.click()
                time.sleep(0.5)
                return
    except Exception:
        pass


# ──────────────────────────── Login ────────────────────────────────────

def _is_state_valid():
    """Check if state.json exists and has cookies."""
    if not os.path.exists(STORAGE_STATE_PATH):
        return False
    try:
        with open(STORAGE_STATE_PATH, "r", encoding="utf-8") as f:
            state = json.load(f)
        cookies = state.get("cookies", [])
        if not cookies:
            print("⚠️  state.json exists but has no cookies.")
            return False
        ig_cookies = [c for c in cookies if "instagram" in c.get("domain", "")]
        if not ig_cookies:
            print("⚠️  state.json has no Instagram cookies.")
            return False
        return True
    except (json.JSONDecodeError, Exception) as e:
        print(f"⚠️  state.json is corrupted: {e}")
        return False


def save_login_session(playwright):
    """
    Launches a browser, AUTOMATICALLY types the credentials,
    clicks Login, waits for the home feed, then saves state.json.
    """
    print("\n" + "=" * 60)
    print("  AUTOMATED LOGIN")
    print("=" * 60)

    browser = playwright.chromium.launch(headless=False)
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=USER_AGENT,
    )
    page = context.new_page()

    # ── 1. Open login page ──
    print("\n📌 Opening Instagram login page...")
    page.goto(
        "https://www.instagram.com/accounts/login/",
        wait_until="networkidle",
        timeout=60000,
    )

    # Instagram's login page is JS-heavy — give it plenty of time to render
    print("   ⏳ Waiting for page to fully render...")
    time.sleep(8)
    _dismiss_cookie_banner(page)
    time.sleep(2)

    # Debug: show what page we're on
    print(f"   Page title: {page.title()}")
    print(f"   Page URL:   {page.url}")

    # ── 2. Find and fill the username field ──
    USERNAME_SELECTORS = [
        'input[name="username"]',
        'input[aria-label*="username" i]',
        'input[aria-label*="phone" i]',
        'input[aria-label*="email" i]',
        'input[autocomplete="username"]',
        'form input[type="text"]',
    ]

    username_input = None
    for sel in USERNAME_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                username_input = loc
                print(f"   ✅ Found username field: {sel}")
                break
        except Exception:
            continue

    if not username_input:
        print("   ❌ Could not find username input! Dumping visible inputs...")
        try:
            all_inputs = page.locator("input").all()
            for i, inp in enumerate(all_inputs):
                attrs = {}
                for attr in ["name", "type", "aria-label", "placeholder", "autocomplete"]:
                    try:
                        val = inp.get_attribute(attr, timeout=1000)
                        if val:
                            attrs[attr] = val
                    except Exception:
                        pass
                print(f"      input[{i}]: {attrs}")
        except Exception as e:
            print(f"      Could not list inputs: {e}")
        print("\n   💡 The page may have a different layout. Please report the output above.")
        browser.close()
        return

    print(f"⌨️  Typing username: {IG_USERNAME}")
    username_input.click()
    time.sleep(0.5)
    # Use type() for keystroke-by-keystroke (more reliable than fill on some pages)
    username_input.fill("")
    username_input.type(IG_USERNAME, delay=50)
    time.sleep(0.5)

    # ── 3. Find and fill the password field ──
    PASSWORD_SELECTORS = [
        'input[name="password"]',
        'input[type="password"]',
        'input[aria-label*="password" i]',
        'input[autocomplete="current-password"]',
    ]

    password_input = None
    for sel in PASSWORD_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.is_visible(timeout=2000):
                password_input = loc
                print(f"   ✅ Found password field: {sel}")
                break
        except Exception:
            continue

    if not password_input:
        print("   ❌ Could not find password input!")
        browser.close()
        return

    print("⌨️  Typing password...")
    password_input.click()
    time.sleep(0.5)
    password_input.fill("")
    password_input.type(IG_PASSWORD, delay=50)
    time.sleep(0.5)

    # ── 4. Click Login button ──
    print("🔐 Clicking Login...")
    LOGIN_SELECTORS = [
        'button[type="submit"]',
        'button:has-text("Log in")',
        'button:has-text("Log In")',
        'div[role="button"]:has-text("Log in")',
    ]
    for sel in LOGIN_SELECTORS:
        try:
            btn = page.locator(sel).first
            if btn.is_visible(timeout=2000):
                btn.click()
                print(f"   ✅ Clicked: {sel}")
                break
        except Exception:
            continue
    time.sleep(5)

    # ── 5. Check for login errors ──
    try:
        error_msg = page.locator('[role="alert"], #slfErrorAlert, [data-testid="login-error-message"]').first
        if error_msg.is_visible(timeout=3000):
            error_text = error_msg.text_content()
            print(f"❌ Login error from Instagram: {error_text}")
            print("   Please check your username/password and try again.")
            browser.close()
            return
    except Exception:
        pass  # No error — that's good

    # ── 5. Handle 2FA / Suspicious Login prompts ──
    # If Instagram asks for a security code, wait for the user to handle it
    try:
        twofa_input = page.locator('input[name="verificationCode"], input[name="security_code"]').first
        if twofa_input.is_visible(timeout=5000):
            print()
            print("🔒 Instagram is asking for a 2FA / security code!")
            print("👉 Enter the code in the BROWSER, then press ENTER here.")
            print()
            try:
                input("⏳ Press ENTER after you've entered the code >>> ")
            except EOFError:
                print("   Waiting 120 seconds for 2FA...")
                page.wait_for_timeout(120_000)
    except Exception:
        pass  # No 2FA prompt

    # ── 6. Wait for the home feed to load (confirms login succeeded) ──
    print("⏳ Waiting for home feed to load...")
    try:
        # Wait for URL to change away from /accounts/login
        page.wait_for_url(
            lambda url: "/accounts/login" not in url,
            timeout=60000,
        )
        print(f"   ✅ Redirected to: {page.url}")
    except Exception:
        print(f"   ⚠️ Still on: {page.url} (might need 2FA or manual action)")
        print("   👉 If the browser needs input, do it now, then press ENTER here.")
        try:
            input("⏳ Press ENTER when ready >>> ")
        except EOFError:
            page.wait_for_timeout(60_000)

    time.sleep(2)

    # ── 7. Dismiss "Save Login Info?" popup ──
    try:
        for text in ["Save Info", "Save info", "Save Your Login Info", "Save"]:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=3000):
                btn.click()
                print("   💾 Accepted 'Save Login Info'")
                time.sleep(1)
                break
    except Exception:
        pass

    # ── 8. Dismiss "Turn on Notifications?" popup ──
    _dismiss_login_popup(page)
    time.sleep(1)

    # ── 9. Save session ──
    context.storage_state(path=STORAGE_STATE_PATH)
    current_url = page.url
    print(f"\n✅ Session saved to '{STORAGE_STATE_PATH}'!")
    print(f"   Final URL: {current_url}")
    print("\n⚠️  SECURITY: Clear IG_USERNAME and IG_PASSWORD from the script now!")
    browser.close()


def _verify_session_live(context):
    """
    Quick check: navigate to Instagram and see if we're still logged in.
    Returns True if logged in, False if redirected to login page.
    """
    page = context.new_page()
    try:
        page.goto("https://www.instagram.com/", wait_until="domcontentloaded", timeout=20000)
        time.sleep(3)
        _dismiss_cookie_banner(page)

        current_url = page.url
        # If redirected to login page, session is expired
        if "/accounts/login" in current_url:
            print("⚠️  Session expired — redirected to login page.")
            return False

        # Look for signs of being logged in (e.g., profile icon, search bar)
        # The home feed should have certain elements
        print(f"   ✅ Session appears valid (URL: {current_url})")
        return True
    except Exception as e:
        print(f"   ⚠️ Session check failed: {e}")
        return False
    finally:
        page.close()


# ──────────────────────── Profile Parsing ──────────────────────────────

def _parse_user_data_graphql(user_info):
    """Parse from older GraphQL edge_* format."""
    profile = {
        "username": user_info.get("username"),
        "full_name": user_info.get("full_name"),
        "biography": user_info.get("biography"),
        "external_url": user_info.get("external_url"),
        "followers_count": _safe_get(user_info, "edge_followed_by", "count", default=0),
        "following_count": _safe_get(user_info, "edge_follow", "count", default=0),
        "posts_count": _safe_get(user_info, "edge_owner_to_timeline_media", "count", default=0),
        "recent_posts": [],
    }
    edges = _safe_get(user_info, "edge_owner_to_timeline_media", "edges", default=[])
    for post in edges:
        sc = _safe_get(post, "node", "shortcode")
        if sc:
            profile["recent_posts"].append(sc)
    return profile


def _parse_user_data_rest(user_info):
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
    edges = _safe_get(user_info, "edge_owner_to_timeline_media", "edges", default=[])
    for post in edges:
        sc = _safe_get(post, "node", "shortcode")
        if sc:
            profile["recent_posts"].append(sc)
    return profile


def _try_parse_user(user_info):
    """Auto-detect format and parse."""
    if not user_info or not isinstance(user_info, dict):
        return None
    if not user_info.get("username"):
        return None
    if "edge_followed_by" in user_info:
        return _parse_user_data_graphql(user_info)
    else:
        return _parse_user_data_rest(user_info)


# ──────────────── Embedded JSON Extraction (Primary) ──────────────────

def _extract_from_page_source(page, username):
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
            user_info = _safe_get(
                shared, "entry_data", "ProfilePage", 0, "graphql", "user"
            )
            result = _try_parse_user(user_info)
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
            user_info = _safe_get(additional, "graphql", "user")
            result = _try_parse_user(user_info)
            if result:
                print("   ✅ Extracted from __additionalDataLoaded")
                return result
        except Exception as e:
            print(f"   ⚠️ __additionalDataLoaded parse error: {e}")

    # ── Strategy C: Search ALL <script> tags for JSON containing user data ──
    script_blocks = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    for block in script_blocks:
        # Skip tiny scripts and non-JSON scripts
        if len(block) < 100 or "{" not in block:
            continue

        # Look for JSON objects that contain the target username
        json_matches = re.findall(r"(\{[\"']username[\"']\s*:\s*[\"']" + re.escape(username) + r"[\"'].*?\})", block)

        # Also try to find large JSON blobs
        if not json_matches:
            # Try to extract JSON that starts with { and is at least substantial
            for json_candidate in re.finditer(r'(\{"[a-zA-Z].*?"username"\s*:\s*"' + re.escape(username) + r'".*?\})', block, re.DOTALL):
                json_matches.append(json_candidate.group(0))

        # Try the whole block as JSON
        if not json_matches and block.strip().startswith("{"):
            json_matches = [block.strip()]

        for raw_json in json_matches:
            try:
                parsed = json.loads(raw_json)
                # Walk the parsed JSON tree to find user data
                found = _find_user_in_json(parsed, username)
                if found:
                    result = _try_parse_user(found)
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
            found = _find_user_in_json(next_data, username)
            if found:
                result = _try_parse_user(found)
                if result:
                    print("   ✅ Extracted from __NEXT_DATA__")
                    return result
        except Exception as e:
            print(f"   ⚠️ __NEXT_DATA__ parse error: {e}")

    return None


def _find_user_in_json(obj, username, depth=0):
    """
    Recursively search a JSON object for a dict that looks like
    Instagram user data (has 'username' matching our target).
    """
    if depth > 15:
        return None

    if isinstance(obj, dict):
        # Check if THIS dict is the user object
        if obj.get("username") == username:
            # Verify it has follower-related keys (not just any dict with username)
            has_followers = (
                "edge_followed_by" in obj
                or "follower_count" in obj
                or "edge_follow" in obj
                or "following_count" in obj
            )
            if has_followers:
                return obj

        # Recurse into values
        for value in obj.values():
            found = _find_user_in_json(value, username, depth + 1)
            if found:
                return found

    elif isinstance(obj, list):
        for item in obj:
            found = _find_user_in_json(item, username, depth + 1)
            if found:
                return found

    return None


# ──────────────── Meta Tag Fallback ────────────────────────────────────

def _fallback_meta_scrape(page, username):
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
                clean = _parse_human_number(val)
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
                _safe_get(data, "data", "user")
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

            result = _try_parse_user(user_info)
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

    _dismiss_cookie_banner(page)
    _dismiss_login_popup(page)

    # Wait for page to fully settle
    time.sleep(random.uniform(3.0, 5.0))

    # ── Strategy 1: Embedded JSON (primary — most reliable) ──
    profile_data = _extract_from_page_source(page, username)

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
        profile_data = _extract_from_page_source(page, username)

    # ── Check API interception again ──
    if not profile_data and api_profile_data:
        profile_data = api_profile_data

    # ── Strategy 3: Meta tag fallback ──
    if not profile_data:
        profile_data = _fallback_meta_scrape(page, username)

    # ── If we STILL have nothing, try to get shortcodes from visible posts ──
    if profile_data and not profile_data.get("recent_posts"):
        print("   🔎 Scanning visible post links for shortcodes...")
        try:
            links = page.locator('a[href*="/p/"]').all()
            for link in links[:12]:
                href = link.get_attribute("href")
                if href:
                    match = re.search(r"/p/([A-Za-z0-9_-]+)", href)
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
            post_links = page.locator('a[href*="/p/"]').all()
            for link in post_links[:12]:
                href = link.get_attribute("href") or ""
                sc_match = re.search(r"/p/([A-Za-z0-9_-]+)", href)
                if not sc_match:
                    continue
                sc = sc_match.group(1)
                # Find the <img> inside this post link
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


# ──────────── Post Media & Comment Extraction ─────────────────────────

def scrape_post_media_and_comments(context, shortcode, media_dir, comments_dir):
    """Navigates to a specific post to download media and comments."""
    page = context.new_page()
    post_url = f"https://www.instagram.com/p/{shortcode}/"
    post_data = {"shortcode": shortcode, "media_urls": [], "comments": []}

    def handle_post_response(response):
        nonlocal post_data
        url = response.url
        if not any(p in url for p in ["graphql", "/api/v1/media/", "/api/graphql"]):
            return

        try:
            ct = response.headers.get("content-type", "")
            if "json" not in ct:
                return
            if response.status != 200:
                return

            data = response.json()

            # Try multiple keys for post media data
            media_info = (
                _safe_get(data, "data", "xdt_shortcode_media")
                or _safe_get(data, "data", "shortcode_media")
                or _safe_get(data, "graphql", "shortcode_media")
                or _safe_get(data, "items", 0)  # REST v1 format
            )
            if not media_info:
                return

            # Extract media URLs
            if _safe_get(media_info, "edge_sidecar_to_children"):
                for child in _safe_get(
                    media_info, "edge_sidecar_to_children", "edges", default=[]
                ):
                    node = child.get("node", {})
                    if node.get("is_video"):
                        post_data["media_urls"].append(
                            (node.get("video_url"), ".mp4")
                        )
                    else:
                        post_data["media_urls"].append(
                            (node.get("display_url"), ".jpg")
                        )
            elif _safe_get(media_info, "carousel_media"):
                # REST v1 carousel format
                for item in media_info["carousel_media"]:
                    if item.get("video_versions"):
                        post_data["media_urls"].append(
                            (item["video_versions"][0].get("url"), ".mp4")
                        )
                    elif item.get("image_versions2"):
                        post_data["media_urls"].append(
                            (_safe_get(item, "image_versions2", "candidates", 0, "url"), ".jpg")
                        )
            else:
                if media_info.get("is_video"):
                    post_data["media_urls"].append(
                        (media_info.get("video_url"), ".mp4")
                    )
                elif media_info.get("video_versions"):
                    post_data["media_urls"].append(
                        (media_info["video_versions"][0].get("url"), ".mp4")
                    )
                else:
                    display = (
                        media_info.get("display_url")
                        or _safe_get(media_info, "image_versions2", "candidates", 0, "url")
                    )
                    post_data["media_urls"].append((display, ".jpg"))

            # Extract comments
            comment_edges = _safe_get(
                media_info, "edge_media_to_parent_comment", "edges", default=[]
            )
            for edge in comment_edges:
                node = edge.get("node", {})
                post_data["comments"].append(
                    {
                        "username": _safe_get(node, "owner", "username"),
                        "text": node.get("text"),
                        "likes": _safe_get(node, "edge_liked_by", "count", default=0),
                        "created_at": node.get("created_at"),
                    }
                )
        except Exception as e:
            print(f"      ⚠️ Post parse error: {e}")

    page.on("response", handle_post_response)

    print(f"   ⬇️  Loading post: {shortcode}...")
    try:
        page.goto(post_url, wait_until="domcontentloaded", timeout=20000)
    except Exception:
        pass

    _dismiss_cookie_banner(page)
    _dismiss_login_popup(page)

    # Wait for API to respond
    waited = 0.0
    while not post_data["media_urls"] and waited < 10:
        time.sleep(0.5)
        waited += 0.5

    # If API interception didn't get media, try page source
    if not post_data["media_urls"]:
        print("      🔄 Trying to extract media from page source...")
        try:
            html = page.content()
            # Look for display_url or video_url in embedded JSON
            urls = re.findall(
                r'"(display_url|video_url)"\s*:\s*"(https?://[^"]+)"', html
            )
            seen = set()
            for url_type, url_val in urls:
                clean_url = url_val.replace("\\u0026", "&").replace("\\/", "/")
                if clean_url not in seen:
                    seen.add(clean_url)
                    ext = ".mp4" if "video" in url_type else ".jpg"
                    post_data["media_urls"].append((clean_url, ext))
        except Exception as e:
            print(f"      ⚠️ Page source regex failed: {e}")

    # Fallback 2: Extract from visible <img> / <video> elements in the DOM
    if not post_data["media_urls"]:
        print("      🔄 Trying visible DOM images/videos...")
        try:
            # Main post image(s)
            imgs = page.locator('article img[src*="instagram"]').all()
            seen_srcs = set()
            for img in imgs:
                try:
                    src = img.get_attribute("src", timeout=2000)
                    if src and src.startswith("http") and src not in seen_srcs:
                        # Skip tiny profile pics (usually < 150px)
                        width = img.get_attribute("width")
                        if width and int(width) < 100:
                            continue
                        seen_srcs.add(src)
                        post_data["media_urls"].append((src, ".jpg"))
                except Exception:
                    pass
            # Videos
            videos = page.locator('article video[src]').all()
            for vid in videos:
                try:
                    src = vid.get_attribute("src", timeout=2000)
                    if src and src.startswith("http") and src not in seen_srcs:
                        seen_srcs.add(src)
                        post_data["media_urls"].append((src, ".mp4"))
                except Exception:
                    pass
            if post_data["media_urls"]:
                print(f"      ✅ Found {len(post_data['media_urls'])} from DOM elements")
        except Exception as e:
            print(f"      ⚠️ DOM extraction failed: {e}")

    # Fallback 3: Instagram oEmbed API (gives thumbnail only, no video)
    if not post_data["media_urls"]:
        print("      🔄 Trying oEmbed API...")
        try:
            oembed_url = f"https://www.instagram.com/p/{shortcode}/?__a=1&__d=dis"
            req = urllib.request.Request(oembed_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                oembed = json.loads(resp.read())
                thumb = oembed.get("thumbnail_url") or _safe_get(oembed, "graphql", "shortcode_media", "display_url")
                if thumb:
                    post_data["media_urls"].append((thumb, ".jpg"))
                    print("      ✅ Got thumbnail from oEmbed/API")
        except Exception:
            pass

    page.close()

    # ── Download media ──
    if post_data["media_urls"]:
        print(
            f"      📸 Found {len(post_data['media_urls'])} media file(s). Downloading..."
        )
        for i, (url, ext) in enumerate(post_data["media_urls"]):
            if url:
                filepath = os.path.join(media_dir, f"{shortcode}_{i}{ext}")
                success = _download_file(url, filepath)
                if success:
                    print(f"      ✅ Saved: {shortcode}_{i}{ext}")
    else:
        print("      ❌ No media URLs found for this post.")

    # ── Save comments ──
    if post_data["comments"]:
        print(
            f"      💬 Found {len(post_data['comments'])} top-level comments. Saving..."
        )
        filepath = os.path.join(comments_dir, f"{shortcode}_comments.json")
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(post_data["comments"], f, indent=4, ensure_ascii=False)

    return post_data


# ──────────────────────────── Main ─────────────────────────────────────

def main():
    with sync_playwright() as p:

        # ── Handle login ──
        if FORCE_RELOGIN and os.path.exists(STORAGE_STATE_PATH):
            os.remove(STORAGE_STATE_PATH)
            print("🗑️  Deleted old state.json (FORCE_RELOGIN=True)")

        if not _is_state_valid():
            if os.path.exists(STORAGE_STATE_PATH):
                os.remove(STORAGE_STATE_PATH)
            save_login_session(p)
            if not _is_state_valid():
                print("❌ Login failed. Please try again.")
                return
            print("\n✅ Login successful! Starting scrape...\n")

        # ── Launch browser ──
        print("🚀 Launching browser...")
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
        if not _verify_session_live(context):
            print("❌ Session expired. Deleting state.json — please re-run to log in again.")
            browser.close()
            os.remove(STORAGE_STATE_PATH)
            return

        # ── Setup output dirs ──
        output_dir = "scraped_data"
        media_dir = os.path.join(output_dir, "media")
        comments_dir = os.path.join(output_dir, "comments")
        os.makedirs(output_dir, exist_ok=True)
        os.makedirs(media_dir, exist_ok=True)
        os.makedirs(comments_dir, exist_ok=True)

        # ── Scrape each profile ──
        for username in TARGET_USERNAMES:
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
                            _download_file(thumb_url, filepath)

                # Then try full-res media from individual post pages
                shortcodes = profile_data.get("recent_posts", [])[:MAX_POSTS_TO_DOWNLOAD]
                if shortcodes:
                    print(
                        f"   📥 Attempting full-res download for {len(shortcodes)} posts..."
                    )
                    for sc in shortcodes:
                        if sc:
                            time.sleep(random.uniform(3.0, 6.0))
                            scrape_post_media_and_comments(
                                context, sc, media_dir, comments_dir
                            )
                else:
                    print("   ℹ️  No post shortcodes found to download.")
            else:
                print(f"⚠️ Failed to extract any data for @{username}.")
                print("   Try:")
                print("   1. Set FORCE_RELOGIN = True and re-run")
                print("   2. Check that the account exists and is public")

            delay = random.uniform(8.0, 15.0)
            print(f"⏳ Pausing {delay:.1f}s before next profile...\n")
            time.sleep(delay)

        browser.close()
        print("🎉 Done! Check the 'scraped_data' folder.")


if __name__ == "__main__":
    main()