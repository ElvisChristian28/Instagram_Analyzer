import instaloader
import browser_cookie3
import sys

USERNAME = "elcianna_6"

# 1. Initialize Instaloader and spoof a normal browser
L = instaloader.Instaloader()
L.context.user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"

print("🔍 Scanning browsers for active Instagram logins...")

cj = None
# List of browsers to check
browsers = [
    ("Chrome", browser_cookie3.chrome),
    ("Edge", browser_cookie3.edge),
    ("Brave", browser_cookie3.brave)
]

# 2. Extract cookies automatically
for browser_name, browser_func in browsers:
    try:
        print(f"Checking {browser_name}...")
        cj = browser_func(domain_name='instagram.com')
        print(f"✅ Extracted cookies from {browser_name}!")
        break
    except Exception:
        continue

if not cj:
    print("❌ Could not find Instagram cookies. Please ensure you are logged in on Chrome or Edge.")
    sys.exit(1)

# 3. Inject the complete cookie jar into Instaloader
L.context._session.cookies.update(cj)

# Update the CSRF header if the token is present
for cookie in cj:
    if cookie.name == 'csrftoken':
        L.context._session.headers.update({"X-CSRFToken": cookie.value})

# 4. Test and Save
try:
    print(f"Testing authentication...")
    L.test_login()
    L.save_session_to_file(filename=f"{USERNAME}_session")
    print(f"\n✅ SUCCESS! Complete session saved to '{USERNAME}_session'.")
    print("You can now run your main data collection script!")
except Exception as e:
    print(f"\n❌ Authentication failed: {e}")