"""Configuration constants for the Instagram scraper."""

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

# ──────────────────────────── Rate Limiting ─────────────────────────────
# Instagram rate limits: ~200 requests/hour for logged-in users.
# These settings keep you well under the limit even with many accounts.

# Delay between visiting individual post pages (seconds, randomized ±30%)
POST_DELAY_MIN = 5.0
POST_DELAY_MAX = 10.0

# Delay between scraping different profiles (seconds)
PROFILE_DELAY_MIN = 15.0
PROFILE_DELAY_MAX = 30.0

# After every N profiles, take a long break to cool down
COOLDOWN_EVERY_N_PROFILES = 5
COOLDOWN_DURATION_MIN = 60.0   # 1 minute minimum
COOLDOWN_DURATION_MAX = 120.0  # 2 minutes maximum

# ──────────────────────────── Comments ──────────────────────────────────
MAX_COMMENTS_PER_POST = 100     # Collect up to this many comments per post
COMMENT_SCROLL_PAUSE = 2.0     # Seconds to wait between comment-load scrolls
MAX_COMMENT_SCROLL_ATTEMPTS = 20  # Max scroll/click attempts to load comments
