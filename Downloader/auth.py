"""Authentication helper for K2 Kaggle Replay Downloader.

Manages Kaggle login state and session persistence in `auth.json`.
Completely standalone and self-contained.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from playwright.async_api import async_playwright

logger = logging.getLogger("K2.auth")
BASE_DIR = Path(__file__).resolve().parent
AUTH_FILE = BASE_DIR / "auth.json"

KAGGLE_URL = "https://www.kaggle.com"


def get_auth_file() -> Path:
    """Return a path to valid auth.json, checking local folder first."""
    return AUTH_FILE


async def interactive_login(output_file: Path | None = None) -> Path:
    """Launch a visible browser window for the user to log in to Kaggle."""
    target_path = output_file or AUTH_FILE
    print("\n" + "=" * 60, flush=True)
    print("  KAGGLE LOGIN REQUIRED FOR K2", flush=True)
    print("=" * 60, flush=True)
    print("Opening Chrome... Please log into your Kaggle account.", flush=True)
    print("1. Complete your Google or Email login in the Chrome window.", flush=True)
    print("2. Once you reach the Kaggle homepage, it will save automatically.", flush=True)
    print("=" * 60 + "\n", flush=True)

    async with async_playwright() as pw:
        launch_kwargs = {
            "headless": False,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--start-maximized",
                "--no-sandbox",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        try:
            browser = await pw.chromium.launch(channel="chrome", **launch_kwargs)
        except Exception:
            browser = await pw.chromium.launch(**launch_kwargs)

        context = await browser.new_context(
            viewport=None,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()
        await page.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        await page.goto("https://www.kaggle.com/account/login")

        print("Waiting for you to complete login in Chrome...", flush=True)
        logged_in = False

        for _ in range(600):  # Wait up to 10 minutes
            await asyncio.sleep(1.5)
            try:
                current_url = page.url.lower()

                # Ignore Google OAuth / Sign-in pages while user is typing credentials
                if "google.com" in current_url or "accounts.google" in current_url:
                    continue

                # Ignore the initial Kaggle login page
                if "/account/login" in current_url or "/login" in current_url:
                    continue

                # Check if back on Kaggle
                if "kaggle.com" in current_url:
                    cookies = await context.cookies()
                    cookie_names = {c["name"]: c.get("value", "") for c in cookies if "kaggle.com" in c.get("domain", "")}

                    # Genuine logged-in indicators on Kaggle
                    has_auth_cookie = bool(
                        cookie_names.get("__Host-KAGGLEID")
                        or (cookie_names.get("ka_sessionid") and cookie_names.get("CLIENT-TOKEN"))
                    )

                    # Check for avatar or profile indicator in the DOM
                    has_avatar = False
                    try:
                        has_avatar = await page.evaluate(
                            r"""() => {
                                const avatar = document.querySelector('img[alt*="avatar" i], img[src*="user-avatars"], a[href^="/settings"], a[href^="/account"]');
                                return avatar !== null;
                            }"""
                        )
                    except Exception:
                        pass

                    if has_auth_cookie or has_avatar:
                        # Give 2 extra seconds for all session cookies to settle
                        await asyncio.sleep(2.0)
                        logged_in = True
                        break
            except Exception:
                # If user closed the window or navigated away, check if cookies exist
                try:
                    cookies = await context.cookies()
                    if any(c["name"] in ("__Host-KAGGLEID", "ka_sessionid") for c in cookies):
                        logged_in = True
                except Exception:
                    pass
                break

        if logged_in:
            await context.storage_state(path=str(target_path))
            print(f"Login successful! Authentication saved to: {target_path}\n", flush=True)
        else:
            print("Login was cancelled or timed out.\n", flush=True)

        try:
            await browser.close()
        except Exception:
            pass

    return target_path


def ensure_auth() -> Path:
    """Ensure a valid auth.json exists, prompting login if missing."""
    auth_path = get_auth_file()
    if not auth_path.exists() or auth_path.stat().st_size < 50:
        return asyncio.run(interactive_login(auth_path))
    return auth_path


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(interactive_login())
