from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from bs4 import BeautifulSoup, Tag


DEFAULT_SEARCH_PATHS = (
    "/search?q={query}",
    "/search?query={query}",
    "/search?s={query}",
    "/catalogsearch/result/?q={query}",
    "/products?search={query}",
    "/collections/all?sort_by=relevance&q={query}",
)

PRODUCT_CARD_SELECTORS = (
    "[data-product]",
    "[data-testid*='product' i]",
    "[class*='product' i]",
    "[class*='item' i]",
    "[class*='card' i]",
    "li",
    "article",
)

TITLE_SELECTORS = (
    "[data-testid*='title' i]",
    "[data-testid*='name' i]",
    "[class*='title' i]",
    "[class*='name' i]",
    "[class*='product' i] a",
    "h1",
    "h2",
    "h3",
    "a",
)

PRICE_SELECTORS = (
    "[data-testid*='price' i]",
    "[class*='price' i]",
    "[itemprop='price']",
    "[aria-label*='price' i]",
)

PRICE_RE = re.compile(
    r"(?P<currency>[$\u20ac\u00a3]|USD|EUR|GBP)\s*(?P<amount>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)"
    r"|(?P<amount2>\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{2})?|\d+(?:[.,]\d{2})?)\s*(?P<currency2>[$\u20ac\u00a3]|USD|EUR|GBP)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Product:
    name: str
    model: str
    price: str
    url: str


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def build_search_urls(shop_url: str, query: str) -> list[str]:
    parsed = urlparse(shop_url if "://" in shop_url else f"https://{shop_url}")
    base = f"{parsed.scheme}://{parsed.netloc}"

    urls: list[str] = []
    if parsed.query:
        query_params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        for key in ("q", "query", "search", "s", "keyword", "keywords"):
            updated = query_params | {key: query}
            urls.append(urlunparse(parsed._replace(query=urlencode(updated), fragment="")))
    else:
        urls.append(urlunparse(parsed._replace(fragment="")))

    encoded_query = requests.utils.quote(query)
    urls.extend(urljoin(base, path.format(query=encoded_query)) for path in DEFAULT_SEARCH_PATHS)
    return list(dict.fromkeys(urls))


def fetch(url: str, timeout: int = 15) -> str:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    response = requests.get(url, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def text_from_first(element: Tag, selectors: Iterable[str]) -> str:
    for selector in selectors:
        found = element.select_one(selector)
        if found:
            text = normalize_space(found.get_text(" ", strip=True))
            if text:
                return text
    return ""


def price_from_text(text: str) -> str:
    match = PRICE_RE.search(text)
    if not match:
        return ""

    currency = match.group("currency") or match.group("currency2") or ""
    amount = match.group("amount") or match.group("amount2") or ""
    return normalize_space(f"{currency} {amount}")


def price_from_element(element: Tag) -> str:
    for selector in PRICE_SELECTORS:
        found = element.select_one(selector)
        if not found:
            continue
        content = found.get("content") or found.get("aria-label") or found.get_text(" ", strip=True)
        price = price_from_text(str(content))
        if price:
            return price
    return price_from_text(element.get_text(" ", strip=True))


def extract_model(name: str) -> str:
    model_patterns = (
        r"\b[A-Z]{1,5}[- ]?\d{2,6}[A-Z0-9-]*\b",
        r"\b\d{2,5}[A-Z]{1,4}\b",
        r"\b[A-Z0-9]{3,}[-/][A-Z0-9-]{2,}\b",
    )
    for pattern in model_patterns:
        match = re.search(pattern, name)
        if match:
            return match.group(0)
    return ""


def looks_relevant(name: str, query: str) -> bool:
    query_words = {word.lower() for word in re.findall(r"[a-zA-Z0-9]+", query) if len(word) > 1}
    if not query_words:
        return True
    name_words = {word.lower() for word in re.findall(r"[a-zA-Z0-9]+", name)}
    return bool(query_words & name_words)


def product_url(card: Tag, page_url: str) -> str:
    link = card if card.name == "a" else card.select_one("a[href]")
    if not isinstance(link, Tag):
        return page_url
    href = link.get("href")
    return urljoin(page_url, str(href)) if href else page_url


def parse_products(html: str, page_url: str, query: str, limit: int) -> list[Product]:
    soup = BeautifulSoup(html, "html.parser")
    products: list[Product] = []
    seen: set[tuple[str, str, str]] = set()

    for selector in PRODUCT_CARD_SELECTORS:
        for card in soup.select(selector):
            text = normalize_space(card.get_text(" ", strip=True))
            if len(text) < 8:
                continue

            price = price_from_element(card)
            if not price:
                continue

            name = text_from_first(card, TITLE_SELECTORS) or text
            name = clean_name(name, price)
            if not name or not looks_relevant(name, query):
                continue

            product = Product(
                name=name,
                model=extract_model(name),
                price=price,
                url=product_url(card, page_url),
            )
            key = (product.name.lower(), product.price, product.url)
            if key in seen:
                continue
            seen.add(key)
            products.append(product)

            if len(products) >= limit:
                return products

    return products


def clean_name(name: str, price: str) -> str:
    cleaned = normalize_space(name.replace(price, ""))
    cleaned = PRICE_RE.sub("", cleaned)
    return normalize_space(cleaned)


def crawl(shop_url: str, product_name: str, limit: int = 10) -> list[Product]:
    errors: list[str] = []
    for url in build_search_urls(shop_url, product_name):
        try:
            html = fetch(url)
        except requests.RequestException as exc:
            errors.append(f"{url}: {exc}")
            continue

        products = parse_products(html, url, product_name, limit)
        if products:
            return products

    if errors:
        print("Could not extract products. Last fetch errors:", file=sys.stderr)
        for error in errors[-3:]:
            print(f"  - {error}", file=sys.stderr)
    return []


def print_table(products: list[Product]) -> None:
    if not products:
        print("No matching products found.")
        return

    rows = [("Product name", "Model", "Price", "URL")]
    rows.extend((product.name, product.model or "-", product.price, product.url) for product in products)
    widths = [max(len(row[index]) for row in rows) for index in range(4)]

    for index, row in enumerate(rows):
        print(" | ".join(value.ljust(widths[column]) for column, value in enumerate(row)))
        if index == 0:
            print("-+-".join("-" * width for width in widths))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search a webshop for a product and list product names, model numbers, prices, and URLs."
    )
    parser.add_argument("website", nargs="?", help="Webshop URL, for example https://example.com")
    parser.add_argument("product", nargs="?", help="Product to search for, for example iPhone 15")
    parser.add_argument("--limit", type=int, default=10, help="Maximum number of products to show")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    website = args.website or input("Webshop URL: ").strip()
    product = args.product or input("Product name: ").strip()

    if not website or not product:
        print("Please provide both a webshop URL and a product name.", file=sys.stderr)
        return 2

    products = crawl(website, product, limit=max(1, args.limit))
    print_table(products)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
