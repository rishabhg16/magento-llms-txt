# magento-llms-txt

[![Tests](https://github.com/rishabhg16/magento-llms-txt/actions/workflows/tests.yml/badge.svg)](https://github.com/rishabhg16/magento-llms-txt/actions/workflows/tests.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Dependencies none](https://img.shields.io/badge/dependencies-none-brightgreen)

An llms.txt generator for Magento 2 stores. It reads your categories and CMS pages from the GraphQL API and writes a clean `llms.txt` file, so ChatGPT, Claude, Perplexity and other AI assistants can find what your store sells.

`llms.txt` is a plain markdown file at the root of your site. It gives AI assistants and search agents a short map of your store. See https://llmstxt.org for the format.

- One Python file, standard library only. Nothing to install.
- Built for Magento 2.4 and newer with GraphQL on. Multi-store setups work through `--store-code`.
- Safe on large catalogs. It skips the slow product counts by default, so it finishes on stores with millions of products.
- Read only. It sends public storefront queries and needs no login or API key.
- Tested on Python 3.9, 3.11 and 3.13 in CI.

## Quick start

```
python magento_llms_txt.py \
  --base-url https://shop.example.com \
  --description "Spare parts for tractors and construction machines." \
  --cms-page about-us --cms-page shipping-returns \
  --output llms.txt
```

Then upload `llms.txt` to the web root of your store, so it opens at `https://shop.example.com/llms.txt`.

## Options

| Option | What it does |
| --- | --- |
| `--base-url` | Your storefront URL. Required. |
| `--store-code` | Store view code, for multi-store setups (for example `de`). Sent as the `Store` header. |
| `--name` | Set the store name. Recommended. Magento often returns a store view name like "English" instead of your brand. |
| `--description` | One sentence shown under the title. Write this yourself. It matters most. |
| `--notes` | Optional paragraph, for example who you sell to and which countries you ship to. |
| `--cms-page` | CMS page identifier to list. Repeat for more pages. |
| `--link` | Extra link as `Title\|URL\|description`. Repeat for more. |
| `--max-depth` | `1` for top categories only, `2` to include subcategories (default). |
| `--exclude` | Skip a category and everything under it, by URL path (for example `brands`). Repeat for more. |
| `--max-categories` | Keep at most this many category links. Default 200, `0` for no limit. Big stores can have thousands of brand pages, and llms.txt should stay short. |
| `--include-hidden` | Also list categories hidden from the menu. |
| `--skip-empty` | Skip categories with no products. Slow on very large catalogs, so it is off by default. |
| `--user-agent` | Change the User-Agent header if a firewall blocks the default. |
| `--timeout` | Seconds to wait per request. Default 60. |
| `--output` | File to write. Default is stdout. |

For a multi-language store, run it once per store view and publish each file on its own domain or path.

## What you get

```
# Acme Parts

> Spare parts for farm and construction machines.

## Product categories

- [Filters](https://shop.example.com/filters.html): Oil, air & fuel filters for tractors.
- [Brakes](https://shop.example.com/brakes.html)

## Help and company pages

- [About us](https://shop.example.com/about-us): Who we are.

## Optional

- [Oil filters](https://shop.example.com/filters/oil.html)
```

Top-level categories go in the main section. Subcategories go under `## Optional`, which the spec uses for links an agent can skip when its context is short.

## What it skips

- Categories hidden from the menu (use `--include-hidden` to keep them). A hidden parent is skipped, but its visible children are still listed.
- Categories with no products, only when you pass `--skip-empty`.
- Duplicate URLs.
- CMS pages that do not exist. It prints a warning and carries on.

## How it works

It sends three kinds of GraphQL queries to `/graphql`: store config (name and category URL suffix), the category tree, and one query per CMS page you ask for. Nothing is written to your store. No login is needed because these are public storefront queries.

## Tests

```
python -m unittest discover -s tests -v
```

The tests cover the text cleaning, the file format, hidden and empty categories, and the real HTTP code against a small fake Magento server that runs on your machine. The queries were also checked by hand against a live Magento 2 store with millions of products. That check is why `product_count` is off by default: on a catalog that size Magento could not compute it before the request timed out. If it fails on your Magento version, please open an issue with the error message.

## Limits

- Magento 2.4 or newer with GraphQL enabled.
- It lists categories and CMS pages, not individual products. A store with tens of thousands of products does not want every product in `llms.txt`.
- Redirects (http to https, www to non-www) are followed and the query is sent again as a POST.
- Descriptions come from your category and CMS meta descriptions, so the output is only as good as those.

## License

MIT
