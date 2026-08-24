"""Full audit: syntax, logic, config, parsers, engagement matrix correctness."""
import sys
import os

sys.path.insert(0, ".")

PASS = "\033[92m  PASS\033[0m"
FAIL = "\033[91m  FAIL\033[0m"
errors = []


def ok(label):
    print(f"{PASS}  {label}")


def fail(label, reason):
    print(f"{FAIL}  {label}: {reason}")
    errors.append(label)


# ═══════════════════════════════════════════════════
print("\n=== CONFIG ===")
# ═══════════════════════════════════════════════════
try:
    import config as c
    checks = {
        "POSTS_PER_HASHTAG == 100":          c.POSTS_PER_HASHTAG == 100,
        "IMAGES_ONLY == False":              not c.IMAGES_ONLY,
        "MAX_COMMENTS_PER_POST == 150":      c.MAX_COMMENTS_PER_POST == 150,
        "MAX_COMMENT_SCROLL_ATTEMPTS == 30": c.MAX_COMMENT_SCROLL_ATTEMPTS == 30,
        "SCRAPE_MODE set":                   bool(c.SCRAPE_MODE),
        "TARGET_HASHTAGS non-empty":         bool(c.TARGET_HASHTAGS),
        "STORAGE_STATE_PATH set":            bool(c.STORAGE_STATE_PATH),
    }
    for label, cond in checks.items():
        if cond:
            ok(label)
        else:
            fail(label, f"got {getattr(c, label.split()[0], '?')}")
except Exception as e:
    fail("config import", str(e))


# ═══════════════════════════════════════════════════
print("\n=== UTILS ===")
# ═══════════════════════════════════════════════════
try:
    from utils import safe_get, parse_human_number

    cases = [
        ("safe_get nested",    safe_get({"a": {"b": 42}}, "a", "b") == 42),
        ("safe_get missing",   safe_get({"a": None}, "a", "b", default="X") == "X"),
        ("safe_get list idx",  safe_get({"a": [10, 20]}, "a", 1) == 20),
        ("parse_human 1.2K",   parse_human_number("1.2K") == 1200),
        ("parse_human 3.5M",   parse_human_number("3.5M") == 3500000),
        ("parse_human plain",  parse_human_number("999") == 999),
        ("parse_human None",   parse_human_number(None) == 0),
        ("parse_human empty",  parse_human_number("") == 0),
    ]
    for label, cond in cases:
        if cond:
            ok(label)
        else:
            fail(label, "unexpected result")
except Exception as e:
    fail("utils", str(e))


# ═══════════════════════════════════════════════════
print("\n=== PARSERS — extract_metadata_from_api ===")
# ═══════════════════════════════════════════════════
try:
    from parsers import extract_metadata_from_api, _report_metadata_fields

    # --- REST / clips payload ---
    fake_rest = {
        "shortcode": "ABCtest123",
        "is_video": True,
        "product_type": "clips",
        "like_count": 50000,
        "comment_count": 320,
        "video_view_count": 1_000_000,
        "taken_at_timestamp": 1_700_000_000,
        "caption": {"text": "Test caption #space #nasa @nasa"},
        "owner": {
            "username": "test_user",
            "full_name": "Test User",
            "is_verified": True,
            "follower_count": 95000,
            "following_count": 150,
        },
        "location": {"name": "Earth"},
    }
    m = extract_metadata_from_api(fake_rest)

    rest_checks = {
        "shortcode":        m.get("shortcode") == "ABCtest123",
        "post_type=reel":   m.get("post_type") == "reel",
        "like_count":       m.get("like_count") == 50000,
        "comment_count":    m.get("comment_count") == 320,
        "view_count":       m.get("view_count") == 1_000_000,
        "owner_username":   m.get("owner_username") == "test_user",
        "owner_followers":  m.get("owner_followers") == 95000,
        "owner_following":  m.get("owner_following") == 150,
        "hashtag space":    "space" in (m.get("hashtags") or []),
        "mention nasa":     "nasa" in (m.get("mentions") or []),
        "location":         m.get("location") == "Earth",
        "timestamp_iso":    m.get("timestamp_iso") is not None,
    }
    for label, cond in rest_checks.items():
        if cond:
            ok(f"REST — {label}")
        else:
            fail(f"REST — {label}", f"got {m.get(label.split('=')[0].replace(' ','_'))!r}")

    # --- GraphQL edge_ format ---
    fake_graphql = {
        "shortcode": "DEFtest456",
        "is_video": False,
        "__typename": "XDTGraphImage",
        "edge_media_preview_like": {"count": 12000},
        "edge_media_to_comment": {"count": 88},
        "taken_at_timestamp": 1_700_000_000,
        "edge_media_to_caption": {"edges": [{"node": {"text": "#nature walk"}}]},
        "owner": {
            "username": "user2",
            "edge_followed_by": {"count": 5000},
            "edge_follow": {"count": 200},
        },
    }
    m2 = extract_metadata_from_api(fake_graphql)

    gql_checks = {
        "like_count":      m2.get("like_count") == 12000,
        "comment_count":   m2.get("comment_count") == 88,
        "owner_followers": m2.get("owner_followers") == 5000,
        "owner_following": m2.get("owner_following") == 200,
        "post_type=image": m2.get("post_type") == "image",
        "hashtag nature":  "nature" in (m2.get("hashtags") or []),
    }
    for label, cond in gql_checks.items():
        if cond:
            ok(f"GraphQL — {label}")
        else:
            fail(f"GraphQL — {label}", f"got {m2.get(label.split('=')[0])!r}")

    # --- Caption string (not dict) ---
    fake_str_caption = {
        "shortcode": "GHItest789",
        "is_video": False,
        "like_count": 100,
        "comment_count": 5,
        "caption": "Direct string caption #astronomy",
        "owner": {"username": "user3"},
    }
    m3 = extract_metadata_from_api(fake_str_caption)
    if m3.get("hashtags") and "astronomy" in m3["hashtags"]:
        ok("string caption hashtag extraction")
    else:
        fail("string caption hashtag extraction", f"hashtags={m3.get('hashtags')}")

except Exception as e:
    import traceback
    fail("parsers", traceback.format_exc())


# ═══════════════════════════════════════════════════
print("\n=== ENGAGEMENT MATRIX ===")
# ═══════════════════════════════════════════════════
try:
    from engagement_matrix import build_engagement_matrix, percentile, save_report, save_csv

    # percentile math
    vals = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    pct_checks = {
        "P25 of [1..10]": abs(percentile(vals, 25) - 3.25) < 0.001,
        "P75 of [1..10]": abs(percentile(vals, 75) - 7.75) < 0.001,
        "P50 of [1..10]": abs(percentile(vals, 50) - 5.5) < 0.001,
        "P50 single val":  percentile([42], 50) == 42,
        "P50 empty list":  percentile([], 50) == 0.0,
    }
    for label, cond in pct_checks.items():
        if cond:
            ok(label)
        else:
            fail(label, f"got {percentile(vals, int(label.split('P')[1].split()[0]))}")

    # Full matrix on real scraped data
    report = build_engagement_matrix()
    if not report["hashtags"]:
        ok("build_engagement_matrix (no data yet — skipping tier checks)")
    else:
        for tag, hr in report["hashtags"].items():
            posts = hr["posts"]
            # All posts have a tier
            all_have_tier = all(p["tier"] in ("High", "Average", "Low") for p in posts)
            if not all_have_tier:
                fail(f"#{tag} all posts have tier", "some missing")
            else:
                ok(f"#{tag} all posts have tier")

            # Tier boundaries are correct
            tier_ok = True
            for p in posts:
                t = p["total_interactions"]
                if p["tier"] == "High" and t < hr["p75"] - 0.01:
                    fail(f"#{tag} High tier boundary", f"{p['shortcode']} has {t} < p75={hr['p75']}")
                    tier_ok = False
                if p["tier"] == "Low" and t > hr["p25"] + 0.01:
                    fail(f"#{tag} Low tier boundary", f"{p['shortcode']} has {t} > p25={hr['p25']}")
                    tier_ok = False
            if tier_ok:
                ok(f"#{tag} tier boundaries correct (p25={hr['p25']:,.0f}, p75={hr['p75']:,.0f})")

            # total_interactions = likes + comments
            for p in posts:
                expected = p["like_count"] + p["comment_count"]
                if p["total_interactions"] != expected:
                    fail(f"#{tag} total_interactions arithmetic", f"{p['shortcode']}")
                    break
            else:
                ok(f"#{tag} total_interactions = likes+comments")

        # Global section populated
        g = report.get("global", {})
        if g.get("total_posts", 0) > 0:
            ok(f"global section: {g['total_posts']} posts")
        else:
            fail("global section", "empty or missing")

        # All global posts have global_tier
        for tag, hr in report["hashtags"].items():
            bad = [p for p in hr["posts"] if "global_tier" not in p]
            if bad:
                fail(f"#{tag} global_tier populated", f"{len(bad)} posts missing it")
            else:
                ok(f"#{tag} global_tier populated")

        # Save works
        try:
            jp = save_report(report)
            ok(f"save_report -> {os.path.basename(jp)}")
        except Exception as e:
            fail("save_report", str(e))

        try:
            cp = save_csv(report)
            ok(f"save_csv -> {os.path.basename(cp)}")
        except Exception as e:
            fail("save_csv", str(e))

except Exception as e:
    import traceback
    fail("engagement_matrix", traceback.format_exc())


# ═══════════════════════════════════════════════════
print("\n=== OUTPUT DIRECTORIES ===")
# ═══════════════════════════════════════════════════
for d in ["scraped_data", "scraped_data/metadata", "scraped_data/media",
          "scraped_data/comments", "scraped_data/reports"]:
    if os.path.isdir(d):
        files = os.listdir(d)
        ok(f"{d}/ exists ({len(files)} files)")
    else:
        fail(f"{d}/ exists", "directory missing")


# ═══════════════════════════════════════════════════
print()
if errors:
    print(f"\033[91m{'='*50}\033[0m")
    print(f"\033[91m  FAILED: {len(errors)} issue(s)\033[0m")
    for e in errors:
        print(f"    ✗ {e}")
    print(f"\033[91m{'='*50}\033[0m")
    sys.exit(1)
else:
    print(f"\033[92m{'='*50}")
    print(f"  ALL CHECKS PASSED ({0} errors)")
    print(f"{'='*50}\033[0m")
