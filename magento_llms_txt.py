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

DEFAULT_TIMEOUT = 60
MAX_DESCRIPTION_CHARS = 160
DEFAULT_USER_AGENT = "magento-llms-txt/1.0"

STORE_QUERY = """
{
  storeConfig {
    store_name
    base_url
    category_url_suffix
  }
}
"""

def category_query(with_counts: bool = False) -> str:
    """Category tree query, two levels below the root.

    product_count is opt-in. On a store with millions of products Magento
    needs tens of seconds to compute it, and the request times out.
    """
    count = "product_count" if with_counts else ""
    leaf = f"name url_path level include_in_menu description {count}"
    return (
        "{ categoryList { "
        + leaf
        + " children { " + leaf
        + " children { " + leaf + " } } } }"
    )


CATEGORY_QUERY = category_query(False)

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


class GraphQLError(MagentoError):
    """The store answered, but the query itself failed (for example, no such page)."""


class _KeepPostRedirects(urllib.request.HTTPRedirectHandler):
    """Follow 301, 302, 303, 307 and 308 redirects and keep POST and its body.

    urllib turns a redirected POST into a GET, which Magento answers with an
    error. Stores that redirect http to https or www to non-www hit this.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if code not in (301, 302, 303, 307, 308):
            return None
        return urllib.request.Request(
            newurl, data=req.data, headers=dict(req.header_items()),
            method="POST",
        )


def make_fetcher(base_url: str, store_code: str | None = None,
                 timeout: int = DEFAULT_TIMEOUT,
                 user_agent: str = DEFAULT_USER_AGENT) -> Fetch:
    """Build a function that posts GraphQL queries to the store."""
    endpoint = base_url.rstrip("/") + "/graphql"
    opener = urllib.request.build_opener(_KeepPostRedirects())

    def fetch(query: str, variables: dict | None = None) -> dict:
        body = json.dumps({"query": query, "variables": variables or {}})
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": user_agent,
        }
        if store_code:
            headers["Store"] = store_code
        request = urllib.request.Request(
            endpoint, data=body.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with opener.open(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            hint = (" (a firewall or bot filter may be blocking the script, "
                    "try --user-agent)" if exc.code in (403, 429) else "")
            raise MagentoError(
                f"{endpoint} returned HTTP {exc.code}{hint}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise MagentoError(f"Could not reach {endpoint}: {exc}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MagentoError(f"{endpoint} did not return JSON") from exc
        if not isinstance(payload, dict):
            raise MagentoError(f"{endpoint} did not return a GraphQL response")
        if payload.get("errors") and not payload.get("data"):
            message = payload["errors"][0].get("message", "unknown error")
            raise GraphQLError(f"GraphQL error: {message}")
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


def walk_categories(nodes: Iterable[dict], depth: int = 1, max_depth: int = 2,
                    include_hidden: bool = False, skip_empty: bool = False):
    """Yield (depth, category) for the categories worth listing.

    The store root ("Default Category", level 1) is never listed and does not
    count as a level. Hidden categories are not listed either, but their
    children are still checked on their own, because Magento shops often hide
    a container like "Categories" and show what is inside it.
    """
    for node in nodes or []:
        is_root = node.get("level") == 1 or not node.get("url_path")
        hidden = (not include_hidden) and node.get("include_in_menu") in (0, False)
        empty = skip_empty and (node.get("product_count") or 0) <= 0
        if is_root or hidden or empty:
            yield from walk_categories(node.get("children"), depth, max_depth,
                                       include_hidden, skip_empty)
            continue
        yield depth, node
        if depth < max_depth:
            yield from walk_categories(node.get("children"), depth + 1, max_depth,
                                       include_hidden, skip_empty)


def collect_categories(fetch: Fetch, base_url: str, suffix: str,
                       max_depth: int = 2, include_hidden: bool = False,
                       skip_empty: bool = False,
                       exclude: Iterable[str] = (),
                       max_categories: int = 0) -> list[tuple[int, str, str, str]]:
    prefixes = [e.strip("/") for e in exclude if e.strip("/")]
    data = fetch(category_query(skip_empty), None)
    roots = data.get("categoryList") or []
    rows = []
    for depth, node in walk_categories(roots, 1, max_depth, include_hidden,
                                       skip_empty):
        path = node["url_path"].strip("/")
        if any(path == p or path.startswith(p + "/") for p in prefixes):
            continue
        rows.append((
            depth,
            (node.get("name") or node["url_path"]).strip(),
            join_url(base_url, node["url_path"], suffix),
            clean_text(node.get("description")),
        ))
    if max_categories and len(rows) > max_categories:
        print(f"warning: {len(rows)} categories found, keeping the first "
              f"{max_categories}. Use --exclude or --max-categories to adjust.",
              file=sys.stderr)
        rows = rows[:max_categories]
    return rows


def collect_cms_pages(fetch: Fetch, base_url: str,
                      identifiers: Iterable[str]) -> list[tuple[str, str, str]]:
    pages = []
    for identifier in identifiers:
        try:
            data = fetch(CMS_QUERY, {"identifier": identifier})
        except GraphQLError:
            data = {}
        page = data.get("cmsPage")
        if not page:
            print(f"warning: CMS page '{identifier}' not found, skipped",
                  file=sys.stderr)
            continue
        pages.append((
            (page.get("title") or identifier).strip(),
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
    title = html.unescape(title).replace("[", "(").replace("]", ")")
    title = re.sub(r"\s+", " ", title).strip()
    url = url.replace(" ", "%20").replace("(", "%28").replace(")", "%29")
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

    seen: set[str] = set()
    unique = []
    for row in categories:
        if row[2] not in seen:
            seen.add(row[2])
            unique.append(row)
    top = [c for c in unique if c[0] == 1]
    sub = [c for c in unique if c[0] > 1]

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
             max_depth: int = 2, include_hidden: bool = False,
             skip_empty: bool = False, exclude: Iterable[str] = (),
             max_categories: int = 200) -> str:
    store = (fetch(STORE_QUERY, None).get("storeConfig")) or {}
    suffix = store.get("category_url_suffix") or ""
    store_name = name or store.get("store_name") or base_url
    categories = collect_categories(fetch, base_url, suffix, max_depth,
                                    include_hidden, skip_empty, exclude,
                                    max_categories)
    pages = collect_cms_pages(fetch, base_url, cms_pages)
    extras = [parse_extra_link(v) for v in extra_links]
    if not categories and not pages and not extras:
        raise MagentoError(
            "No categories were returned. Check the base URL, "
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
    parser.add_argument("--include-hidden", action="store_true",
                        help="Also list categories hidden from the menu")
    parser.add_argument("--exclude", action="append", default=[],
                        metavar="URL_PATH",
                        help="Skip this category and everything under it, e.g. "
                             "brands. Repeat for more")
    parser.add_argument("--max-categories", type=int, default=200,
                        help="Keep at most this many category links "
                             "(default 200, 0 for no limit)")
    parser.add_argument("--skip-empty", action="store_true",
                        help="Skip categories with no products. Slow on very "
                             "large catalogs, so it is off by default")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT,
                        help="User-Agent header. Change it if a firewall "
                             "blocks the default")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT,
                        help=f"Seconds per request (default {DEFAULT_TIMEOUT})")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None, fetch: Fetch | None = None) -> int:
    args = parse_args(argv)
    fetch = fetch or make_fetcher(args.base_url, args.store_code, args.timeout,
                                  args.user_agent)
    if not args.name:
        print("note: no --name given, so the store name from Magento is used. "
              "That is often a store view name like 'English'.", file=sys.stderr)
    try:
        text = generate(
            fetch, args.base_url, summary=args.description, name=args.name,
            cms_pages=args.cms_page, extra_links=args.link, notes=args.notes,
            max_depth=args.max_depth, include_hidden=args.include_hidden,
            skip_empty=args.skip_empty, exclude=args.exclude,
            max_categories=args.max_categories,
        )
    except (MagentoError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.output == "-":
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        sys.stdout.write(text)
    else:
        try:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError as exc:
            print(f"error: cannot write {args.output}: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
