#!/usr/bin/env python3
"""Open a browser to log in to Echo360 and save session cookies for the web app."""

import json
import os
import sys
import time

PERSISTENT_SESSION_FOLDER = "_browser_persistent_session"
COOKIES_FILE = os.path.join(PERSISTENT_SESSION_FOLDER, "cookies.json")
DEFAULT_URL = "https://echo360.net.au"


def get_webdriver():
    from selenium import webdriver
    options = webdriver.ChromeOptions()
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation"])
    return webdriver.Chrome(options=options)


def main():
    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL
    print(f"Opening {url} ...")
    print("Log in with your university SSO credentials.")
    print("The window will close automatically once an ECHO_JWT cookie is detected.\n")

    driver = get_webdriver()
    driver.get(url)

    try:
        while True:
            try:
                cookies = driver.get_cookies()
            except Exception:
                print("Browser closed before login completed.")
                sys.exit(1)

            if any("ECHO_JWT" in c.get("name", "") for c in cookies):
                os.makedirs(PERSISTENT_SESSION_FOLDER, exist_ok=True)
                with open(COOKIES_FILE, "w") as f:
                    json.dump(cookies, f)
                print(f"\nSession saved to {COOKIES_FILE}")
                print("Restart the Docker container to pick up the new session:")
                print("  docker compose restart")
                break

            time.sleep(2)
    except KeyboardInterrupt:
        print("\nCancelled.")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
