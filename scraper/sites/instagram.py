"""
Instagram public profile scraper using Playwright.

Scrapes public Instagram profiles without requiring login where possible.
Instagram aggressively gate-keeps content, so login credentials significantly
increase the amount of data retrievable.

Usage:
    scraper = InstagramScraper(headless=True)
    results = scraper.scrape("https://www.instagram.com/nasa", max_posts=12)
    scraper.save_json()
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from playwright.sync_api import sync_playwright, Page, TimeoutError as PWTimeout

from scraper.base import BaseScraper
from scraper.utils import clean_text, parse_count, random_delay


class InstagramScraper(BaseScraper):
    """Scrape public Instagram profiles for profile info and recent posts."""

    site_name = "instagram"

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def scrape(
        self,
        target: str,
        max_posts: int = 12,
        username: str | None = None,
        password: str | None = None,
        **kwargs,
    ) -> list[dict[str, Any]]:
        """
        Scrape an Instagram profile.

        Args:
            target:    Full Instagram URL or handle (e.g. 'nasa').
            max_posts: Maximum number of posts to collect.
            username:  Optional Instagram account username for login.
            password:  Optional Instagram account password for login.

        Returns:
            List of dicts — one 'profile_info' entry, then one per post.
        """
        url = self._normalize_url(target)
        self.logger.info("Starting Instagram scrape: %s (max_posts=%d)", url, max_posts)
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
                if username and password:
                    self._login(page, username, password)
                    random_delay(2, 3)

                profile_info = self._scrape_profile(page, url)
                self.results.append({"type": "profile_info", **profile_info})

                posts = self._scrape_posts(page, url, max_posts)
                self.results.extend(posts)

            except Exception as exc:
                self.logger.error("Scrape failed: %s", exc, exc_info=True)
            finally:
                browser.close()

        self.logger.info(
            "Finished. Collected profile_info + %d posts.", len(self.results) - 1
        )
        return self.results

    # ------------------------------------------------------------------ #
    # Internal helpers                                                     #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _normalize_url(target: str) -> str:
        if target.startswith("http"):
            return target.rstrip("/")
        handle = target.lstrip("@/")
        return f"https://www.instagram.com/{handle}"

    def _login(self, page: Page, username: str, password: str):
        self.logger.info("Logging in as %s …", username)
        page.goto("https://www.instagram.com/accounts/login/", wait_until="domcontentloaded")
        random_delay(2, 3)

        self._dismiss_overlays(page)

        page.fill("input[name='username']", username)
        page.fill("input[name='password']", password)
        random_delay(0.5, 1.5)
        page.click("button[type='submit']")

        try:
            page.wait_for_url(re.compile(r"instagram\.com(?!/accounts/login)"), timeout=15_000)
        except PWTimeout:
            raise RuntimeError("Login failed — check credentials or solve CAPTCHA manually.")

        # Dismiss 'Save login info' / 'Turn on notifications' prompts
        random_delay(2, 3)
        self._dismiss_overlays(page)
        self.logger.info("Login successful.")

    def _scrape_profile(self, page: Page, url: str) -> dict[str, Any]:
        self.logger.info("Fetching profile info …")
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        random_delay(2, 4)
        self._dismiss_overlays(page)

        info: dict[str, Any] = {
            "url": url,
            "scraped_at": datetime.utcnow().isoformat(),
        }

        # Username from URL
        match = re.search(r"instagram\.com/([^/?#]+)", url)
        if match:
            info["handle"] = match.group(1)

        # Display name
        try:
            name_el = page.locator("h2, header h1").first
            info["display_name"] = clean_text(name_el.inner_text(timeout=3_000))
        except Exception:
            pass

        # Bio
        try:
            bio_el = page.locator("header section div span, div[data-testid='user-bio']").first
            info["bio"] = clean_text(bio_el.inner_text(timeout=3_000))
        except Exception:
            pass

        # Stats: posts / followers / following
        # Instagram renders these as a list of <li> elements in the header
        try:
            stat_items = page.locator("header ul li, header section ul li").all()
            stat_labels = ["posts", "followers", "following"]
            for i, item in enumerate(stat_items[:3]):
                try:
                    raw = clean_text(item.inner_text(timeout=2_000))
                    # Extract number from text like "1,234\nfollowers"
                    num_match = re.search(r"([\d,.]+[KMB]?)", raw, re.IGNORECASE)
                    if num_match:
                        raw_val = num_match.group(1)
                        label = stat_labels[i] if i < len(stat_labels) else f"stat_{i}"
                        info[f"{label}_raw"] = raw_val
                        info[label] = parse_count(raw_val)
                except Exception:
                    pass
        except Exception:
            pass

        # Profile picture URL
        try:
            avatar = page.locator("header img").first
            info["profile_pic_url"] = avatar.get_attribute("src", timeout=2_000)
        except Exception:
            pass

        # External link in bio
        try:
            ext_link = page.locator("header a[rel='nofollow']").first
            info["website"] = ext_link.get_attribute("href", timeout=2_000)
        except Exception:
            pass

        return info

    def _scrape_posts(self, page: Page, url: str, max_posts: int) -> list[dict[str, Any]]:
        self.logger.info("Collecting up to %d posts …", max_posts)

        # Ensure we're on the profile page
        if page.url.rstrip("/") != url.rstrip("/"):
            page.goto(url, wait_until="domcontentloaded", timeout=30_000)
            random_delay(2, 3)
            self._dismiss_overlays(page)

        posts: list[dict[str, Any]] = []
        seen_urls: set[str] = set()
        scroll_attempts = 0
        max_scrolls = max(20, max_posts * 3)

        while len(posts) < max_posts and scroll_attempts < max_scrolls:
            # Each post thumbnail is an <a> inside the grid
            thumbnails = page.locator("article a[href*='/p/'], main a[href*='/p/']").all()

            for thumb in thumbnails:
                if len(posts) >= max_posts:
                    break
                try:
                    href = thumb.get_attribute("href", timeout=1_000) or ""
                    post_url = "https://www.instagram.com" + href.split("?")[0]
                    if post_url in seen_urls:
                        continue
                    seen_urls.add(post_url)

                    post = self._open_post(page, post_url, thumb)
                    if post:
                        posts.append(post)
                        # Navigate back to profile
                        page.go_back(wait_until="domcontentloaded", timeout=15_000)
                        random_delay(1.5, 3.0)
                        self._dismiss_overlays(page)
                except Exception as exc:
                    self.logger.debug("Skipping thumbnail: %s", exc)

            page.evaluate("window.scrollBy(0, window.innerHeight * 2)")
            random_delay(1.5, 3.0)
            scroll_attempts += 1

        self.logger.info("Collected %d posts.", len(posts))
        return posts

    def _open_post(self, page: Page, post_url: str, thumb) -> dict[str, Any] | None:
        """Open a single post page and extract its data."""
        try:
            # Try to get preview data from the thumbnail before navigating
            preview: dict[str, Any] = {"type": "post", "post_url": post_url}

            # Thumbnail image src (low-res preview)
            try:
                img = thumb.locator("img").first
                preview["thumbnail_url"] = img.get_attribute("src", timeout=1_000)
                preview["alt_text"] = clean_text(
                    img.get_attribute("alt", timeout=500) or ""
                )
            except Exception:
                pass

            # Navigate to the post for full data
            page.goto(post_url, wait_until="domcontentloaded", timeout=20_000)
            random_delay(1.5, 2.5)
            self._dismiss_overlays(page)

            # Caption
            try:
                caption_el = page.locator(
                    "article div[data-testid='post-comment-root'] span, "
                    "article h1, "
                    "div[class*='caption'] span"
                ).first
                preview["caption"] = clean_text(caption_el.inner_text(timeout=3_000))
            except Exception:
                preview["caption"] = preview.get("alt_text", "")

            # Timestamp
            try:
                time_el = page.locator("article time, time[datetime]").first
                dt = time_el.get_attribute("datetime", timeout=2_000)
                preview["timestamp"] = dt
            except Exception:
                pass

            # Like count
            try:
                like_el = page.locator(
                    "section span:has-text('like'), "
                    "button:has-text('like') span, "
                    "a:has-text('like')"
                ).first
                raw = clean_text(like_el.inner_text(timeout=2_000))
                preview["likes_raw"] = raw
                preview["likes"] = parse_count(raw.split()[0])
            except Exception:
                preview["likes"] = None

            # Comment count
            try:
                comment_el = page.locator(
                    "ul li span:has-text('comment'), "
                    "a:has-text('comment')"
                ).first
                raw_c = clean_text(comment_el.inner_text(timeout=2_000))
                preview["comments_raw"] = raw_c
                preview["comments"] = parse_count(raw_c.split()[0])
            except Exception:
                preview["comments"] = None

            # Full-res image / video URLs
            try:
                media_els = page.locator("article img[srcset], article video source").all()
                media_urls = []
                for el in media_els[:6]:
                    src = el.get_attribute("src", timeout=500)
                    if src:
                        media_urls.append(src)
                preview["media_urls"] = media_urls
            except Exception:
                preview["media_urls"] = []

            # Hashtags from caption
            caption_text = preview.get("caption", "")
            preview["hashtags"] = re.findall(r"#\w+", caption_text)

            preview["scraped_at"] = datetime.utcnow().isoformat()
            return preview

        except Exception as exc:
            self.logger.debug("Failed to parse post %s: %s", post_url, exc)
            return None

    @staticmethod
    def _dismiss_overlays(page: Page):
        """Dismiss common Instagram popups (cookie banner, login prompt, notifications)."""
        selectors = [
            # Cookie consent
            "button:has-text('Allow all cookies')",
            "button:has-text('Accept All')",
            "button:has-text('Only allow essential cookies')",
            # Login nag (when not logged in)
            "div[role='dialog'] button:has-text('Not Now')",
            "div[role='dialog'] button:has-text('Not now')",
            # Notifications prompt
            "button:has-text('Not Now')",
            # Generic close
            "button[aria-label='Close']",
            "svg[aria-label='Close']",
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=1_500):
                    btn.click(timeout=2_000)
                    random_delay(0.5, 1.0)
            except Exception:
                pass
