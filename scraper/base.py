"""Base scraper class that all site-specific scrapers inherit from."""

import json
import csv
import logging
import os
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Any

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


class BaseScraper(ABC):
    """Abstract base class for all scrapers."""

    site_name: str = "base"

    def __init__(self, output_dir: str = "output", headless: bool = True):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.headless = headless
        self.logger = logging.getLogger(self.__class__.__name__)
        self.results: list[dict[str, Any]] = []

    @abstractmethod
    def scrape(self, target: str, **kwargs) -> list[dict[str, Any]]:
        """Scrape the target URL or identifier and return results."""

    def save_json(self, filename: str | None = None) -> Path:
        """Save results to a JSON file."""
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.site_name}_{ts}.json"
        path = self.output_dir / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.results, f, indent=2, ensure_ascii=False, default=str)
        self.logger.info("Saved %d results to %s", len(self.results), path)
        return path

    def save_csv(self, filename: str | None = None) -> Path:
        """Save results to a CSV file."""
        if not self.results:
            self.logger.warning("No results to save.")
            return self.output_dir / "empty.csv"
        if not filename:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{self.site_name}_{ts}.csv"
        path = self.output_dir / filename
        keys = list(self.results[0].keys())
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(self.results)
        self.logger.info("Saved %d results to %s", len(self.results), path)
        return path

    def clear(self):
        """Clear accumulated results."""
        self.results = []
