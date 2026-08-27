"""Quick test of the AI filter detection logic."""
from ai_filter import is_ai_generated

tests = [
    # (metadata dict, expect_is_ai, label)
    # ── Should be CAUGHT ──
    ({"hashtags": ["midjourney", "space", "galaxy"], "caption": "Wow!", "mentions": []},
     True, "MJ hashtag"),
    ({"hashtags": ["photography", "nature"], "caption": "Made with AI by deepdream tools", "mentions": []},
     True, "AI keyword in caption"),
    ({"hashtags": ["aiart", "creative"], "caption": "Beautiful sunset", "mentions": []},
     True, "aiart hashtag"),
    ({"hashtags": [], "caption": "/imagine prompt galaxy --ar 16:9 --v 6", "mentions": []},
     True, "MidJourney CLI syntax"),
    ({"is_generated_media": True, "hashtags": [], "caption": "", "mentions": []},
     True, "Instagram API flag"),
    ({"hashtags": ["stablediffusion", "art"], "caption": "Test", "mentions": []},
     True, "stable diffusion hashtag"),
    ({"hashtags": ["dalle3", "openai"], "caption": "DALL-E generated image", "mentions": []},
     True, "dalle hashtag + keyword"),
    ({"hashtags": [], "caption": "This image was generated using generative AI tools", "mentions": []},
     True, "generative AI phrase"),
    # ── Should PASS through ──
    ({"hashtags": ["astrophotography", "nasa", "space"], "caption": "Captured at ISO 3200 with Canon", "mentions": []},
     False, "real astrophotography"),
    ({"hashtags": ["crochet", "handmade", "yarn"], "caption": "Finished my blanket today!", "mentions": []},
     False, "crochet post"),
    ({"hashtags": ["nature", "wildlife"], "caption": "Spotted this eagle at dawn", "mentions": ["natgeo"]},
     False, "wildlife photo"),
    ({"hashtags": ["digitalart"], "caption": "Drew this in Procreate #digitalart #illustration", "mentions": []},
     False, "Procreate (not AI)"),
]

all_ok = True
for meta, expect_ai, label in tests:
    result = is_ai_generated(meta)
    correct = result["is_ai"] == expect_ai
    status = "✅" if correct else "❌"
    verdict = "AI" if result["is_ai"] else "GENUINE"
    reason = result["reason"][:65] if result["reason"] else "(passed)"
    print(f"{status} [{label:<32}] → {verdict:<8}  {reason}")
    if not correct:
        all_ok = False
        print(f"       Expected: {'AI' if expect_ai else 'GENUINE'}")

print()
print("All filter tests PASSED ✅" if all_ok else "❌ SOME TESTS FAILED")
