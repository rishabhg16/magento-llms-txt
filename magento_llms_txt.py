#!/usr/bin/env python3
"""Generate an llms.txt file for a Magento 2 store using its GraphQL API.

Only the standard library is used, so there is nothing to install.

Example:
    python magento_llms_txt.py --base-url https://shop.example.com \
        --description "Spare parts for tractors and diggers." \
        --cms-page about-us --cms-page shipping-returns \
        --output llms.txt
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import urllib.error
import urllib.request
from typing import Callable, Iterable, Optional

DEFAULT_TIMEOUT = 20
MAX_DESCRIPTION_CHARS = 160

STORE_QUERY = """
{
  storeConfig {
    store_name
    base_url
    category_url_suffix
  }
}
"""

CATEGORY_QUERY = """
{
  categoryList {
    name
    url_path
    include_in_menu
    product_count
    description
    children {
      name
      url_path
      include_in_menu
      product_count
      description
      children {
        name
        url_path
        include_in_menu
        product_count
        description
      }
    }
  }
}
"""

CMS_QUERY = """
query CmsPage($identifier: String!) {
  cmsPage(identifier: $identifier) {
    title
    url_key
    meta_description
  }
}
"""

# A fetch function takes (query, variables) and returns the "data" dict.
Fetch = Callable[[str, Optional[dict]], dict]


class MagentoError(RuntimeError):
    """Raised when the store cannot be queried."""


def make_fetcher(base_url: str, store_code: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT) -> Fetch:
    """Build a function that posts GraphQL queries to the store."""
    endpoint = base_url.rstrip("/") + "/graphql"

    def fetch(query: str, variables: dict | None = None) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}})
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "magento-llms-txt/1.0",
        }
        if store_code:
            headers["Store"] = store_code
        request = urllib.request.Request(
            endpoint, data=body.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MagentoError(f"{endpoint} returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise MagentoError(f"Could not reach {endpoint}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise MagentoError(f"{endpoint} did not return JSON") from exc
        if payload.get("errors") and not payload.get("data"):
            message = payload["errors"][0].get("message", "unknown error")
            raise MagentoError(f"GraphQL error: {message}")
        return payload.get("data") or {}

    return fetch


def clean_text(value: str | None, limit: int = MAX_DESCRIPTION_CHARS) -> str:
    """Strip HTML, collapse whitespace and trim to a sentence-sized length."""
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(",;:-")
    return cut + "..."


def join_url(base_url: str, path: str, suffix: str = "") -> str:
    path = (path or "").strip("/")
    suffix = suffix or ""
    return f"{base_url.rstrip('/')}/{path}{suffix}"


def walk_categories(nodes: Iterable[dict], depth: int = 1, max_depth: int = 2):
    """Yield (depth, category) for visible categories that have products.

    Magento's root category ("Default Category") has no url_path. It is not
    listed itself, but its children are the store's real top-level categories,
    so we descend into it without counting it as a level.
    """
    for node in nodes or []:
        if not node.get("url_path"):
            yield from walk_categories(node.get("children"), depth, max_depth)
            continue
        if node.get("include_in_menu") in (0, False):
            continue
        if (node.get("product_count") or 0) <= 0:
            continue
        yield depth, node
        if depth < max_depth:
            yield from walk_categories(node.get("children"), depth + 1, max_depth)


def collect_categories(fetch: Fetch, base_url: str, suffix: str,
                       max_depth: int = 2) -> list[tuple[int, str, str, str]]:
    data = fetch(CATEGORY_QUERY, None)
    roots = data.get("categoryList") or []
    rows = []
    for depth, node in walk_categories(roots, 1, max_depth):
        rows.append((
            depth,
            node["name"].strip(),
            join_url(base_url, node["url_path"], suffix),
            clean_text(node.get("description")),
        ))
    return rows


def collect_cms_pages(fetch: Fetch, base_url: str,
                      identifiers: Iterable[str]) -> list[tuple[str, str, str]]:
    pages = []
    for identifier in identifiers:
        data = fetch(CMS_QUERY, {"identifier": identifier})
        page = data.get("cmsPage")
        if not page:
            print(f"warning: CMS page '{identifier}' not found, skipped",
                  file=sys.stderr)
            continue
        pages.append((
            page["title"].strip(),
            join_url(base_url, page.get("url_key") or identifier),
            clean_text(page.get("meta_description")),
        ))
    return pages


def parse_extra_link(value: str) -> tuple[str, str, str]:
    """Parse 'Title|https://url|optional description'."""
    parts = [p.strip() for p in value.split("|")]
    if len(parts) < 2 or not parts[0] or not parts[1].startswith("http"):
        raise ValueError(
            f"Bad --link '{value}'. Use: Title|https://example.com/page|description"
        )
    description = parts[2] if len(parts) > 2 else ""
    return parts[0], parts[1], description


def link_line(title: str, url: str, description: str = "") -> str:
    line = f"- [{title}]({url})"
    if description:
        line += f": {description}"
    return line


def build_llms_txt(name: str, summary: str,
                   categories: list[tuple[int, str, str, str]],
                   pages: list[tuple[str, str, str]],
                   extra_links: list[tuple[str, str, str]],
                   notes: str = "") -> str:
    lines = [f"# {name}", ""]
    if summary:
        lines += [f"> {summary}", ""]
    if notes:
        lines += [notes.strip(), ""]

    top = [c for c in categories if c[0] == 1]
    sub = [c for c in categories if c[0] > 1]

    if top:
        lines += ["## Product categories", ""]
        lines += [link_line(t, u, d) for _, t, u, d in top]
        lines.append("")
    if pages:
        lines += ["## Help and company pages", ""]
        lines += [link_line(t, u, d) for t, u, d in pages]
        lines.append("")
    if extra_links:
        lines += ["## More", ""]
        lines += [link_line(t, u, d) for t, u, d in extra_links]
        lines.append("")
    if sub:
        lines += ["## Optional", ""]
        lines += [link_line(t, u, d) for _, t, u, d in sub]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def generate(fetch: Fetch, base_url: str, summary: str = "",
             name: str | None = None, cms_pages: Iterable[str] = (),
             extra_links: Iterable[str] = (), notes: str = "",
             max_depth: int = 2) -> str:
    store = (fetch(STORE_QUERY, None).get("storeConfig")) or {}
    suffix = store.get("category_url_suffix") or ""
    store_name = name or store.get("store_name") or base_url
    categories = collect_categories(fetch, base_url, suffix, max_depth)
    pages = collect_cms_pages(fetch, base_url, cms_pages)
    extras = [parse_extra_link(v) for v in extra_links]
    if not categories and not pages and not extras:
        raise MagentoError(
            "No categories with products were returned. Check the base URL, "
            "the --store-code and that GraphQL is enabled."
        )
    return build_llms_txt(store_name, summary, categories, pages, extras, notes)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate llms.txt for a Magento 2 store from its GraphQL API."
    )
    parser.add_argument("--base-url", required=True,
                        help="Storefront URL, e.g. https://shop.example.com")
    parser.add_argument("--store-code", help="Magento store view code, e.g. de")
    parser.add_argument("--name", help="Override the store name")
    parser.add_argument("--description", default="",
                        help="One-sentence summary shown under the title")
    parser.add_argument("--notes", default="",
                        help="Optional paragraph, e.g. who you sell to and where")
    parser.add_argument("--cms-page", action="append", default=[],
                        metavar="IDENTIFIER",
                        help="CMS page identifier to include. Repeat for more")
    parser.add_argument("--link", action="append", default=[],
                        metavar="TITLE|URL|DESC",
                        help="Extra link. Repeat for more")
    parser.add_argument("--max-depth", type=int, choices=(1, 2), default=2,
                        help="Category levels to include (default 2)")
    parser.add_argument("--output", "-o", default="-",
                        help="File to write, or - for stdout (default)")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, fetch: Fetch | None = None) -> int:
    args = parse_args(argv)
    fetch = fetch or make_fetcher(args.base_url, args.store_code, args.timeout)
    try:
        text = generate(
            fetch, args.base_url, summary=args.description, name=args.name,
            cms_pages=args.cms_page, extra_links=args.link, notes=args.notes,
            max_depth=args.max_depth,
        )
    except (MagentoError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output == "-":
        sys.stdout.write(text)
    else:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
