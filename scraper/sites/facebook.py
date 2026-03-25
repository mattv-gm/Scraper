"""
Facebook public page scraper using Playwright.

Scrapes public Facebook pages without requiring login where possible.
For content behind the login wall, optional credentials can be supplied.

Usage:
    scraper = FacebookScraper(headless=True)
    results = scraper.scrape("https://www.facebook.com/some.page", max_posts=20)
    scraper.save_json()
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

from scraper.base import BaseScraper
from scraper.utils import clean_text, parse_count, random_delay


# Selectors — Facebook changes these periodically; adjust as needed.
_POST_ARTICLE = "div[data-ad-preview='message'], div[role='article']"
_LIKE_COUNT = "[aria-label*='reaction'], [aria-label*='like']"
_COMMENT_COUNT = "span[class]:has-text('comment')"


class FacebookScraper(BaseScraper):
    """Scrape public Facebook pages for page info and recent posts."""

    site_name = "facebook"

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def scrape(
        self,
        target: str,
        max_posts: int = 10,
        email: str | None = None,
        password: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Scrape a Facebook page.

        Args:
            target:    Full Facebook page URL or page handle (e.g. 'nasa').
            max_posts: Maximum number of posts to collect.
            email:     Optional Facebook account email for login.
            password:  Optional Facebook account password for login.

        Returns:
            List of dicts — one per post, plus a 'page_info' entry.
        """
        url = self._normalize_url(target)
        self.logger.info("Starting Facebook scrape: %s (max_posts=%d)", url, max_posts)
        self.clear()

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=self.headless)
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
                locale="en-US",
            )
            page = context.new_page()

            try:
                if email and password:
                    self._login(page, email, password)

                page_info = self._scrape_page_info(page, url)
                self.results.append({"type": "page_info", **page_info})

                posts = self._scrape_posts(page, url, max_posts)
                self.results.extend(posts)

            except Exception as exc:
                self.logger.error("Scrape failed: %s", exc, exc_info=True)
            finally:
                browser.close()

        self.logger.info(
            "Finished. Collected page_info + %d posts.", len(self.results) - 1
        )
        return self.results

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_url(target: str) -> str:
        """Accept a handle like 'nasa' or a full URL."""
        if target.startswith("http"):
            return target.rstrip("/")
        handle = target.lstrip("@/")
        return f"https://www.facebook.com/{handle}"

    def _login(self, page: Page, email: str, password: str):
        """Log in to Facebook with the supplied credentials."""
        self.logger.info("Logging in as %s …", email)
        page.goto("https://www.facebook.com/login", wait_until="domcontentloaded")
        random_delay(1, 2)

        page.fill("#email", email)
        page.fill("#pass", password)
        random_delay(0.5, 1.5)
        page.click("#loginbutton")
        page.wait_for_load_state("networkidle", timeout=15_000)

        if "login" in page.url:
            raise RuntimeError(
                "Login failed — check credentials or solve CAPTCHA manually."
            )
        self.logger.info("Login successful.")

    def _scrape_page_info(self, page: Page, url: str) -> dict[str, Any]:
        """Navigate to the page and extract high-level page metadata."""
        self.logger.info("Fetching page info …")
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        random_delay(2, 4)

        # Dismiss cookie / login prompts if present
        self._dismiss_overlays(page)

        info: dict[str, Any] = {
            "url": url,
            "scraped_at": datetime.utcnow().isoformat(),
        }

        # Page title / name
        try:
            title = page.title()
            info["page_title"] = clean_text(title.split("|")[0])
        except Exception:
            pass

        # Follower / like count shown in the page header
        for label_pattern in [r"follower", r"like", r"people follow"]:
            try:
                locator = page.locator(
                    f"span:has-text('follower'), span:has-text('like'), "
                    f"span:has-text('people follow')"
                ).first
                raw = locator.inner_text(timeout=3_000)
                info["followers_raw"] = clean_text(raw)
                info["followers"] = parse_count(raw.split()[0])
                break
            except Exception:
                pass

        # Category / about snippet
        try:
            about = page.locator("div[data-key='about_section_basic'] span").first
            info["category"] = clean_text(about.inner_text(timeout=3_000))
        except Exception:
            pass

        return info

    def _scrape_posts(
        self, page: Page, url: str, max_posts: int
    ) -> list[dict[str, Any]]:
        """Scroll the timeline and collect posts."""
        self.logger.info("Collecting up to %d posts …", max_posts)

        # Navigate to the posts / feed tab
        posts_url = url if url.endswith("/posts") else url + "/posts"
        try:
            page.goto(posts_url, wait_until="domcontentloaded", timeout=30_000)
            random_delay(2, 3)
            self._dismiss_overlays(page)
        except PWTimeout:
            self.logger.warning("Could not load /posts tab; scraping main page feed.")

        posts: list[dict[str, Any]] = []
        seen_texts: set[str] = set()
        scroll_attempts = 0
        max_scrolls = max(20, max_posts * 2)

        while len(posts) < max_posts and scroll_attempts < max_scrolls:
            articles = page.locator("div[role='article']").all()
            for article in articles:
                if len(posts) >= max_posts:
                    break
                post = self._parse_article(article)
                if not post:
                    continue
                key = post.get("text", "")[:120]
                if key and key in seen_texts:
                    continue
                seen_texts.add(key)
                posts.append(post)

            # Scroll down to load more
            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            random_delay(1.5, 3.0)
            scroll_attempts += 1

        self.logger.info("Collected %d posts.", len(posts))
        return posts

    def _parse_article(self, article) -> dict[str, Any] | None:
        """Extract data from a single post article element."""
        try:
            post: dict[str, Any] = {"type": "post"}

            # Post text
            try:
                text_el = article.locator("div[data-ad-comet-preview='message'], "
                                          "div[dir='auto']").first
                post["text"] = clean_text(text_el.inner_text(timeout=2_000))
            except Exception:
                post["text"] = ""

            # Timestamp / post URL
            try:
                link = article.locator("a[href*='/posts/'], a[href*='story_fbid']").first
                href = link.get_attribute("href", timeout=2_000) or ""
                post["post_url"] = re.sub(r"\?.*", "", href)
                ts_el = link.locator("span[data-utime], abbr[data-utime]").first
                utime = ts_el.get_attribute("data-utime", timeout=1_000)
                if utime:
                    post["timestamp"] = datetime.utcfromtimestamp(
                        int(utime)
                    ).isoformat()
            except Exception:
                pass

            # Reaction / like count
            try:
                react_el = article.locator(
                    "span[aria-label*='reaction'], span[aria-label*='like']"
                ).first
                raw = react_el.get_attribute("aria-label", timeout=1_000) or ""
                post["reactions_raw"] = clean_text(raw)
                post["reactions"] = parse_count(raw.split()[0]) if raw else None
            except Exception:
                post["reactions"] = None

            # Comment count
            try:
                comment_el = article.locator(
                    "span:has-text('comment'), a:has-text('comment')"
                ).first
                raw_c = clean_text(comment_el.inner_text(timeout=1_000))
                post["comments_raw"] = raw_c
                post["comments"] = parse_count(raw_c.split()[0])
            except Exception:
                post["comments"] = None

            # Share count
            try:
                share_el = article.locator(
                    "span:has-text('share'), a:has-text('share')"
                ).first
                raw_s = clean_text(share_el.inner_text(timeout=1_000))
                post["shares_raw"] = raw_s
                post["shares"] = parse_count(raw_s.split()[0])
            except Exception:
                post["shares"] = None

            # Attached images
            try:
                imgs = article.locator("img[src*='fbcdn']").all()
                post["images"] = [
                    img.get_attribute("src", timeout=500) for img in imgs[:5]
                ]
            except Exception:
                post["images"] = []

            post["scraped_at"] = datetime.utcnow().isoformat()
            return post

        except Exception as exc:
            self.logger.debug("Failed to parse article: %s", exc)
            return None

    @staticmethod
    def _dismiss_overlays(page: Page):
        """Try to close login prompts or cookie banners."""
        dismiss_selectors = [
            "[aria-label='Close']",
            "[data-cookiebanner='accept_button']",
            "button:has-text('Accept All')",
            "button:has-text('Only allow essential cookies')",
            "div[role='dialog'] [role='button']:has-text('Not Now')",
            "div[role='dialog'] [role='button']:has-text('Close')",
        ]
        for sel in dismiss_selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1_500):
                    btn.click(timeout=2_000)
                    random_delay(0.5, 1.0)
            except Exception:
                pass
