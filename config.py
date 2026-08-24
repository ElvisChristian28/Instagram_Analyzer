"""Configuration constants for the Instagram scraper."""

# ──────────────────────────── Mode Selection ────────────────────────────
# Set to "hashtag" to scrape by hashtag, or "profile" to scrape by username.
SCRAPE_MODE = "hashtag"  # "hashtag" | "profile"
#crochet#crochetersofinstagram#crochetaddict#crochetlove#crochetinspiration#crocheting#handmadecrochet#crochetcommunity#ilovecrochet#makersgonnamake
# ── Hashtag Mode ──
# TARGET_HASHTAGS = ["crochet", "crochetersofinstagram", "crochetaddict", "crochetlove", "crochetinspiration","crocheting","handmadecrochet","crochetcommunity","ilovecrochet","makersgonnamake"]

TARGET_HASHTAGS = ["crochet", "crochetersofinstagram", "crochetaddict"]
POSTS_PER_HASHTAG = 100         # Target number of posts to collect per hashtag
HASHTAG_SCROLL_PAUSE_MIN = 3.0   # Seconds to wait between scroll steps
HASHTAG_SCROLL_PAUSE_MAX = 6.0
IMAGES_ONLY = False              # False = download images AND videos/reels

# ── Profile Mode ──
TARGET_USERNAMES = ["nasa", "natgeo"]
MAX_POSTS_TO_DOWNLOAD = 3

# ──────────────────────────── Auth ─────────────────────────────────────
STORAGE_STATE_PATH = "state.json"
HEADLESS = False
FORCE_RELOGIN = False

# Login Credentials (used for automated login only)
# ⚠️  CHANGE YOUR PASSWORD after state.json is created, then clear these.
IG_USERNAME = "elcianna_6"
IG_PASSWORD = "anncia-elvis0628"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/127.0.0.0 Safari/537.36"
)

# ──────────────────────────── Rate Limiting ─────────────────────────────
POST_DELAY_MIN = 5.0
POST_DELAY_MAX = 10.0

PROFILE_DELAY_MIN = 15.0
PROFILE_DELAY_MAX = 30.0

COOLDOWN_EVERY_N_PROFILES = 5
COOLDOWN_DURATION_MIN = 60.0
COOLDOWN_DURATION_MAX = 120.0

# ──────────────────────────── Comments ──────────────────────────────────
MAX_COMMENTS_PER_POST = 150
COMMENT_SCROLL_PAUSE = 2.5
MAX_COMMENT_SCROLL_ATTEMPTS = 30
