"""Diagnostic: inspect what the API actually returns for a reel post."""
import json
import re
import time

from playwright.sync_api import sync_playwright

SHORTCODE = "Db0ca2lgKN8"  # a reel from the run

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    ctx = browser.new_context(
        storage_state="state.json",
        viewport={"width": 1280, "height": 800},
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/127.0.0.0 Safari/537.36",
    )

    captured = {}

    def on_response(resp):
        if "graphql" in resp.url or "api/v1" in resp.url:
            try:
                if resp.status == 200 and "json" in resp.headers.get("content-type", ""):
                    data = resp.json()
                    for key in ["xdt_shortcode_media", "shortcode_media"]:
                        mi = data.get("data", {}).get(key)
                        if mi:
                            captured["media_info"] = mi
                            captured["api_url"] = resp.url
            except Exception:
                pass

    page = ctx.new_page()
    page.on("response", on_response)
    page.goto(
        f"https://www.instagram.com/reel/{SHORTCODE}/",
        wait_until="domcontentloaded",
        timeout=25000,
    )
    time.sleep(6)

    if captured.get("media_info"):
        mi = captured["media_info"]
        print("=== API INTERCEPTED ===")
        print(f"API URL: {captured.get('api_url', 'unknown')}")
        print(f"Top-level keys: {list(mi.keys())}")
        print()

        # Video URL
        print(f"is_video:        {mi.get('is_video')}")
        print(f"video_url:       {str(mi.get('video_url', ''))[:100]}")
        print(f"video_view_count:{mi.get('video_view_count')}")
        print(f"play_count:      {mi.get('play_count')}")
        print(f"like_count:      {mi.get('like_count')}")
        print(f"comment_count:   {mi.get('comment_count')}")
        print()

        owner = mi.get("owner") or mi.get("user") or {}
        print(f"Owner keys: {list(owner.keys()) if isinstance(owner, dict) else type(owner)}")
        print(f"  username:      {owner.get('username') if isinstance(owner, dict) else None}")
        print(f"  follower_count:{owner.get('follower_count') if isinstance(owner, dict) else None}")
        print(f"  full_name:     {owner.get('full_name') if isinstance(owner, dict) else None}")

        # Dump to file for full inspection
        with open("reel_api_dump.json", "w", encoding="utf-8") as f:
            json.dump(mi, f, indent=2, ensure_ascii=False, default=str)
        print("\n✅ Full API response saved to reel_api_dump.json")

    else:
        print("No API intercept fired — checking page source...")
        html = page.content()

        # video_url
        m = re.search(r'"video_url"\s*:\s*"([^"]+)"', html)
        print(f"video_url in source: {bool(m)}")
        if m:
            print(f"  {m.group(1)[:100]}")

        # owner block
        m2 = re.search(r'"owner"\s*:\s*\{(.+?)\}', html, re.DOTALL)
        if m2:
            print(f"Owner block: {m2.group(1)[:300]}")

    browser.close()
