"""Shared helper utilities for the Instagram scraper."""

import time
import urllib.request

from config import USER_AGENT


def safe_get(d, *keys, default=None):
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


def download_file(url, filepath):
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


def dismiss_cookie_banner(page):
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


def dismiss_login_popup(page):
    """
    Dismiss Instagram popups that block content:
      - "Never miss a post" signup modal (X button)
      - "Turn on Notifications" (Not Now)
      - "Log in to continue" overlay
    """
    # ── 1. Close the "Never miss a post" / signup modal via X button ──
    try:
        close_selectors = [
            # The X button inside the modal dialog
            'div[role="dialog"] button svg[aria-label="Close"]',
            'div[role="dialog"] button[aria-label="Close"]',
            'div[role="dialog"] div[role="button"] svg[aria-label="Close"]',
            # Generic close buttons
            'button[aria-label="Close"]',
            'div[role="button"][aria-label="Close"]',
            # The X button may also be a plain svg click target
            'div[role="dialog"] svg[aria-label="Close"]',
        ]
        for sel in close_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1500):
                    btn.click()
                    time.sleep(1)
                    print("      ✅ Dismissed signup/login modal (X)")
                    return
            except Exception:
                continue
    except Exception:
        pass

    # ── 2. Try clicking "Not Now" / "Cancel" / "Close" text buttons ──
    try:
        for text in ["Not Now", "Not now", "Cancel", "Close"]:
            btn = page.locator(f"button:has-text('{text}')").first
            if btn.is_visible(timeout=1500):
                btn.click()
                time.sleep(0.5)
                print(f"      ✅ Dismissed popup ('{text}')")
                return
    except Exception:
        pass

    # ── 3. Try pressing Escape to dismiss any modal ──
    try:
        dialog = page.locator('div[role="dialog"]').first
        if dialog.is_visible(timeout=1000):
            page.keyboard.press("Escape")
            time.sleep(0.5)
            # Check if it was dismissed
            if not dialog.is_visible(timeout=500):
                print("      ✅ Dismissed modal (Escape key)")
                return
    except Exception:
        pass


def parse_human_number(s):
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
