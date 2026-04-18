"""LinkedIn browser session with cookie persistence.

`BrowserSession` wraps an undetected-chromedriver instance. Cookies are
saved to disk after login so you don't have to solve captchas every run.
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

import undetected_chromedriver as uc
from selenium import webdriver
from selenium.common.exceptions import WebDriverException

from job_apply_ai.config import get_config
from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)

LINKEDIN_HOME = "https://www.linkedin.com/"
LINKEDIN_LOGIN = "https://www.linkedin.com/login"
LINKEDIN_FEED = "https://www.linkedin.com/feed/"


class SessionError(RuntimeError):
    pass


class BrowserSession:
    """Chrome driver + cookie persistence for LinkedIn."""

    def __init__(
        self,
        cookies_path: Optional[Path] = None,
        headless: Optional[bool] = None,
    ):
        cfg = get_config()
        self.cookies_path = cookies_path or cfg.applicator.cookies_path
        self.scraper_cfg = cfg.scraper
        self.headless = cfg.scraper.headless if headless is None else headless
        self.driver: Optional[uc.Chrome] = None

    def start(self) -> uc.Chrome:
        options = webdriver.ChromeOptions()
        if self.headless:
            options.add_argument("--headless=new")
        for flag in (
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-notifications",
            "--disable-extensions",
            "--disable-gpu",
            "--window-size=1920,1080",
        ):
            options.add_argument(flag)
        options.add_argument(f"user-agent={self.scraper_cfg.user_agent}")

        try:
            self.driver = uc.Chrome(options=options)
        except WebDriverException as exc:
            raise SessionError(
                "Could not start Chrome. Install Google Chrome and re-run. "
                f"Underlying error: {exc}"
            ) from exc

        self._restore_cookies()
        return self.driver

    def stop(self) -> None:
        if self.driver is not None:
            try:
                self.driver.quit()
            except Exception:
                pass
            self.driver = None

    @contextmanager
    def running(self) -> Iterator[uc.Chrome]:
        try:
            yield self.start()
        finally:
            self.stop()

    def _restore_cookies(self) -> None:
        if not self.cookies_path.is_file():
            logger.info("No saved cookies at %s — login required", self.cookies_path)
            return
        try:
            with self.cookies_path.open("r", encoding="utf-8") as fh:
                cookies = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Could not read cookies: %s", exc)
            return

        self.driver.get(LINKEDIN_HOME)
        for cookie in cookies:
            cookie.pop("sameSite", None)
            try:
                self.driver.add_cookie(cookie)
            except WebDriverException as exc:
                logger.debug("Skipping cookie %s: %s", cookie.get("name"), exc)
        logger.info("Restored %d cookies from %s", len(cookies), self.cookies_path)

    def save_cookies(self) -> None:
        if self.driver is None:
            raise SessionError("Browser not started")
        cookies = self.driver.get_cookies()
        self.cookies_path.parent.mkdir(parents=True, exist_ok=True)
        with self.cookies_path.open("w", encoding="utf-8") as fh:
            json.dump(cookies, fh, indent=2)
        logger.info("Saved %d cookies to %s", len(cookies), self.cookies_path)

    def is_logged_in(self) -> bool:
        if self.driver is None:
            raise SessionError("Browser not started")
        self.driver.get(LINKEDIN_FEED)
        time.sleep(2)
        # If LinkedIn bounces us to /login or /checkpoint, we're not in.
        current = self.driver.current_url
        return "login" not in current and "checkpoint" not in current


def interactive_login(cookies_path: Optional[Path] = None) -> None:
    """Open a non-headless browser and wait for the user to log in manually.

    Saves cookies once the feed loads. This is how you bootstrap a session
    without handing your password to a script.
    """
    session = BrowserSession(cookies_path=cookies_path, headless=False)
    driver = session.start()
    try:
        driver.get(LINKEDIN_LOGIN)
        print(
            "\n>>> Log in to LinkedIn in the browser window, solve any captcha,\n"
            "    then return here and press Enter to save your session.\n"
        )
        input()
        driver.get(LINKEDIN_FEED)
        time.sleep(3)
        if "login" in driver.current_url or "checkpoint" in driver.current_url:
            raise SessionError(
                "Still on the login/checkpoint page. Finish the flow, then re-run."
            )
        session.save_cookies()
        print(f"Saved cookies to {session.cookies_path}.")
    finally:
        session.stop()
