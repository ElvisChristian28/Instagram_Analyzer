"""Authentication: login, session validation, and session verification."""

import os
import json
import time

from config import (
    STORAGE_STATE_PATH,
    IG_USERNAME,
    IG_PASSWORD,
    USER_AGENT,
)
from utils import dismiss_cookie_banner, dismiss_login_popup


def is_state_valid():
    """Check if state.json exists and has Instagram cookies."""
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

    browser = playwright.chromium.launch(
        headless=False,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
        ],
    )
    context = browser.new_context(
        viewport={"width": 1280, "height": 800},
        user_agent=USER_AGENT,
    )
    page = context.new_page()

    # ── 1. Open login page (with retry on network errors) ──
    print("\n📌 Opening Instagram login page...")
    for nav_attempt in range(1, 4):
        try:
            page.goto(
                "https://www.instagram.com/accounts/login/",
                wait_until="domcontentloaded",  # less strict than networkidle
                timeout=45000,
            )
            break  # success
        except Exception as nav_err:
            if nav_attempt < 3:
                wait_sec = nav_attempt * 8
                print(f"   ⚠️ Navigation error (attempt {nav_attempt}/3): {nav_err}")
                print(f"   ⏳ Retrying in {wait_sec}s...")
                time.sleep(wait_sec)
            else:
                print(f"   ❌ Could not reach Instagram after 3 attempts: {nav_err}")
                print("   Please check your internet connection and try again.")
                browser.close()
                return

    # Instagram's login page is JS-heavy — give it time to render
    print("   ⏳ Waiting for page to fully render...")
    time.sleep(6)
    dismiss_cookie_banner(page)
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

    # ── 6. Handle 2FA / Suspicious Login prompts ──
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

    # ── 7. Wait for the home feed to load (confirms login succeeded) ──
    print("⏳ Waiting for home feed to load...")
    try:
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

    # ── 8. Dismiss "Save Login Info?" popup ──
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

    # ── 9. Dismiss "Turn on Notifications?" popup ──
    dismiss_login_popup(page)
    time.sleep(1)

    # ── 10. Save session ──
    context.storage_state(path=STORAGE_STATE_PATH)
    current_url = page.url
    print(f"\n✅ Session saved to '{STORAGE_STATE_PATH}'!")
    print(f"   Final URL: {current_url}")
    print("\n⚠️  SECURITY: Clear IG_USERNAME and IG_PASSWORD from config.py now!")
    browser.close()


def verify_session_live(context, retries=3):
    """
    Quick check: navigate to Instagram and see if we're still logged in.
    Returns True  — logged in (or network error, session preserved)
    Returns False — definitively redirected to login page (session expired)

    Network errors (ERR_NETWORK_CHANGED, timeouts) are treated as
    transient and return True to avoid wrongly deleting state.json.
    """
    page = context.new_page()
    last_error = None

    for attempt in range(1, retries + 1):
        try:
            page.goto(
                "https://www.instagram.com/",
                wait_until="domcontentloaded",
                timeout=25000,
            )
            time.sleep(3)
            dismiss_cookie_banner(page)

            current_url = page.url

            # Definitive session expiry — Instagram redirected to login
            if "/accounts/login" in current_url:
                print("⚠️  Session expired — redirected to login page.")
                page.close()
                return False

            print(f"   ✅ Session appears valid (URL: {current_url})")
            page.close()
            return True

        except Exception as e:
            last_error = e
            error_str = str(e)

            # Transient network errors — retry
            transient = any(x in error_str for x in [
                "ERR_NETWORK_CHANGED",
                "ERR_INTERNET_DISCONNECTED",
                "ERR_CONNECTION_RESET",
                "ERR_CONNECTION_TIMED_OUT",
                "net::",
                "Timeout",
                "timeout",
            ])

            if transient and attempt < retries:
                wait = attempt * 5
                print(f"   ⚠️ Network error (attempt {attempt}/{retries}), retrying in {wait}s...")
                time.sleep(wait)
                continue

            # Non-transient error or retries exhausted
            print(f"   ⚠️ Session check failed: {e}")
            if transient:
                # Network is unreliable — don't delete state.json, assume logged in
                print("   ℹ️  Network appears unstable — preserving session file.")
                page.close()
                return True
            else:
                page.close()
                return False

    page.close()
    return True  # Exhausted retries with network errors — preserve session
