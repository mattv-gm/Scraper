#!/usr/bin/env python3
"""
Website Scraper — CLI entry point.

Examples
--------
# Scrape a public Facebook page (headless, no login):
    python main.py facebook nasa --max-posts 15

# Scrape with login credentials:
    python main.py facebook nasa --email you@example.com --password secret

# Show output as JSON to stdout instead of saving a file:
    python main.py facebook nasa --stdout

# Save as CSV:
    python main.py facebook nasa --format csv
"""

import argparse
import json
import logging
import os
import sys

from scraper.sites.facebook import FacebookScraper


SCRAPERS = {
    "facebook": FacebookScraper,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scraper",
        description="Multi-site web scraper — currently supports: facebook",
    )
    parser.add_argument(
        "site",
        choices=list(SCRAPERS.keys()),
        help="Target site to scrape.",
    )
    parser.add_argument(
        "target",
        help="Page handle or full URL (e.g. 'nasa' or 'https://www.facebook.com/nasa').",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=10,
        metavar="N",
        help="Maximum number of posts to collect (default: 10).",
    )
    parser.add_argument(
        "--email",
        default=os.getenv("FB_EMAIL"),
        help="Account email for login (or set FB_EMAIL env var).",
    )
    parser.add_argument(
        "--password",
        default=os.getenv("FB_PASSWORD"),
        help="Account password for login (or set FB_PASSWORD env var).",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Directory to write output files (default: output/).",
    )
    parser.add_argument(
        "--format",
        choices=["json", "csv", "both"],
        default="json",
        help="Output file format (default: json).",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print results to stdout instead of (or in addition to) saving a file.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Launch browser in a visible window (disables headless mode).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug-level logging.",
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    ScraperClass = SCRAPERS[args.site]
    scraper = ScraperClass(output_dir=args.output_dir, headless=not args.show)

    # Site-specific kwargs
    kwargs: dict = {"max_posts": args.max_posts}
    if args.email:
        kwargs["email"] = args.email
    if args.password:
        kwargs["password"] = args.password

    results = scraper.scrape(args.target, **kwargs)

    if not results:
        print("No results collected.", file=sys.stderr)
        sys.exit(1)

    if args.stdout:
        print(json.dumps(results, indent=2, ensure_ascii=False, default=str))

    if args.format in ("json", "both"):
        path = scraper.save_json()
        if not args.stdout:
            print(f"Saved JSON: {path}")

    if args.format in ("csv", "both"):
        path = scraper.save_csv()
        if not args.stdout:
            print(f"Saved CSV: {path}")


if __name__ == "__main__":
    main()
