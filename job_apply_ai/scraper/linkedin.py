"""LinkedIn job scraper.

Pulls tunables (timeouts, selectors, retry counts, user agent, URL template)
from `job_apply_ai.config`, so overriding a setting via env or `.env` is
enough to adapt to LinkedIn DOM changes without code edits.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import List, Optional, Tuple
from urllib.parse import quote_plus

import pandas as pd
import undetected_chromedriver as uc
from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from job_apply_ai.config import ScraperConfig, get_config
from job_apply_ai.utils.logging import get_logger

logger = get_logger(__name__)


class ScraperError(RuntimeError):
    """Raised when the scraper cannot recover (e.g. Chrome not installed)."""


class LinkedInScraper:
    """Scrape public LinkedIn job listings."""

    def __init__(self, headless: Optional[bool] = None, config: Optional[ScraperConfig] = None):
        self.config = config or get_config().scraper
        self.headless = self.config.headless if headless is None else headless

    def _configure_driver(self):
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
        options.add_argument(f"user-agent={self.config.user_agent}")

        try:
            return uc.Chrome(options=options)
        except WebDriverException as exc:
            raise ScraperError(
                "Could not start Chrome. Install Google Chrome and ensure it is "
                "on PATH, then re-run. Original error: " + str(exc)
            ) from exc

    def _find_first(self, parent, selectors: List[str], by=By.CSS_SELECTOR):
        """Return the first element matching any of `selectors`, else None."""
        for selector in selectors:
            try:
                return parent.find_element(by, selector)
            except NoSuchElementException:
                continue
        return None

    def _wait_for_any(self, driver, selectors: List[str], timeout: int, by=By.CLASS_NAME):
        """Wait until any of the given selectors is present. Returns the one that matched."""
        wait = WebDriverWait(driver, timeout)
        for selector in selectors:
            try:
                wait.until(EC.presence_of_element_located((by, selector)))
                return selector
            except TimeoutException:
                continue
        return None

    def scrape_job_listings(
        self,
        keyword: str,
        location: str,
        max_jobs: int = 10,
        max_days_old: Optional[int] = None,
    ) -> List[dict]:
        """Return a list of job dicts from the LinkedIn public search page."""
        max_days_old = max_days_old if max_days_old is not None else self.config.max_days_old
        logger.info("Searching LinkedIn for '%s' in '%s'", keyword, location)

        search_url = self.config.search_url_template.format(
            keyword=quote_plus(keyword),
            location=quote_plus(location),
        )

        last_error: Optional[Exception] = None
        for attempt in range(1, self.config.max_retries + 2):
            driver = None
            try:
                driver = self._configure_driver()
                driver.get(search_url)

                for _ in range(self.config.scroll_count):
                    driver.execute_script(f"window.scrollBy(0, {self.config.scroll_pixels});")
                    time.sleep(self.config.scroll_pause)

                matched_selector = self._wait_for_any(
                    driver,
                    self.config.job_card_selectors,
                    timeout=self.config.page_timeout,
                    by=By.CLASS_NAME,
                )
                if not matched_selector:
                    logger.warning(
                        "No job cards found using selectors %s. LinkedIn may have "
                        "changed its DOM — override JOBIT_JOB_CARD_SELECTORS in .env.",
                        self.config.job_card_selectors,
                    )
                    return []

                job_elements = driver.find_elements(By.CLASS_NAME, matched_selector)
                jobs = self._extract_jobs(job_elements, max_jobs, max_days_old)
                logger.info("Scraped %d job listings (attempt %d)", len(jobs), attempt)
                return jobs

            except (TimeoutException, WebDriverException) as exc:
                last_error = exc
                logger.warning(
                    "Scrape attempt %d/%d failed: %s",
                    attempt,
                    self.config.max_retries + 1,
                    exc,
                )
                if attempt <= self.config.max_retries:
                    time.sleep(self.config.retry_backoff ** attempt)
            finally:
                if driver is not None:
                    try:
                        driver.quit()
                    except Exception:
                        pass

        logger.error("Giving up after %d attempts: %s", self.config.max_retries + 1, last_error)
        return []

    def _extract_jobs(self, job_elements, max_jobs: int, max_days_old: int) -> List[dict]:
        jobs: List[dict] = []
        today = datetime.today()

        for job in job_elements[:max_jobs]:
            try:
                title_el = self._find_first(job, ["h3", ".base-search-card__title"])
                company_el = self._find_first(job, ["h4", ".base-search-card__subtitle"])
                link_el = self._find_first(job, ["a"], by=By.TAG_NAME)

                if not (title_el and company_el and link_el):
                    continue

                title = title_el.text.strip()
                company = company_el.text.strip()
                link = link_el.get_attribute("href")

                days_ago: object = "Unknown"
                try:
                    date_element = job.find_element(By.CSS_SELECTOR, "time")
                    posted_time = date_element.get_attribute("datetime")
                    if posted_time:
                        posted_date = datetime.strptime(posted_time[:10], "%Y-%m-%d")
                        delta_days = (today - posted_date).days
                        if delta_days > max_days_old:
                            logger.debug("Skipping %s (posted %d days ago)", title, delta_days)
                            continue
                        days_ago = delta_days
                except NoSuchElementException:
                    pass

                jobs.append(
                    {
                        "title": title,
                        "company": company,
                        "link": link,
                        "source": "LinkedIn",
                        "posted_days_ago": days_ago,
                    }
                )
            except Exception as exc:  # defensive — one bad card shouldn't kill the run
                logger.debug("Skipping malformed job card: %s", exc)
                continue
        return jobs

    def fetch_job_description(self, job_url: str) -> Tuple[str, str, str]:
        """Return (title, company, description) for a single posting."""
        logger.info("Fetching description from %s", job_url)

        driver = None
        try:
            driver = self._configure_driver()
            driver.get(job_url)

            title = self._wait_for_text(driver, self.config.title_selectors, By.CSS_SELECTOR)
            company = self._wait_for_text(driver, self.config.company_selectors, By.CSS_SELECTOR)
            description = self._wait_for_text(driver, self.config.description_selectors, By.CLASS_NAME)

            return title, company, description

        except WebDriverException as exc:
            logger.error("Error fetching description: %s", exc)
            return "", "", ""
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    def _wait_for_text(self, driver, selectors: List[str], by) -> str:
        wait = WebDriverWait(driver, self.config.page_timeout)
        for selector in selectors:
            try:
                el = wait.until(EC.presence_of_element_located((by, selector)))
                text = el.text.strip()
                if text:
                    return text
            except TimeoutException:
                continue
        logger.warning("None of these selectors yielded text: %s", selectors)
        return ""

    def save_jobs_to_excel(self, jobs: List[dict], filename: Optional[str] = None) -> Optional[str]:
        """Write jobs to an Excel file. Returns the path, or None if no jobs."""
        if not jobs:
            logger.warning("No jobs to save")
            return None

        if filename is None:
            today_date = datetime.today().strftime("%Y-%m-%d")
            filename = str(get_config().paths.jobs_dir / f"linkedin_jobs_{today_date}.xlsx")

        df = pd.DataFrame(jobs)
        df.to_excel(filename, index=False)
        logger.info("Saved %d jobs to %s", len(jobs), filename)
        return filename


def main():
    keyword = input("Enter job title (e.g., Software Engineer): ")
    location = input("Enter location (e.g., Remote, New York, Berlin): ")

    scraper = LinkedInScraper()
    jobs = scraper.scrape_job_listings(keyword, location)

    if not jobs:
        print("\nNo LinkedIn jobs found.")
        return

    for i, job in enumerate(jobs):
        logger.info("Fetching description for job %d/%d: %s", i + 1, len(jobs), job["title"])
        _, _, description = scraper.fetch_job_description(job["link"])
        jobs[i]["description"] = description

    filename = scraper.save_jobs_to_excel(jobs)
    print(f"\nJobs saved to {filename}")


if __name__ == "__main__":
    main()
