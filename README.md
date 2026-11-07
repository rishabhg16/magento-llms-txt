# magento-llms-txt

Generate an `llms.txt` file for a Magento 2 store, straight from its GraphQL API.

`llms.txt` is a plain markdown file at the root of your site. It gives AI assistants and search agents a short, clean map of what your store sells and where to find it. See https://llmstxt.org for the format.

This tool reads your visible categories and a few CMS pages, then writes the file. It has no dependencies. It only needs Python 3.9 or newer.

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
| `--name` | Override the store name from Magento. |
| `--description` | One sentence shown under the title. Write this yourself. It matters most. |
| `--notes` | Optional paragraph, for example who you sell to and which countries you ship to. |
| `--cms-page` | CMS page identifier to list. Repeat for more pages. |
| `--link` | Extra link as `Title\|URL\|description`. Repeat for more. |
| `--max-depth` | `1` for top categories only, `2` to include subcategories (default). |
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

- Categories hidden from the menu.
- Categories with no products.
- CMS pages that do not exist. It prints a warning and carries on.

## How it works

It sends three kinds of GraphQL queries to `/graphql`: store config (name and category URL suffix), the category tree, and one query per CMS page you ask for. Nothing is written to your store. No login is needed because these are public storefront queries.

## Tests

```
python -m unittest discover -s tests -v
```

The tests cover the text cleaning, the file format, hidden and empty categories, and the real HTTP code against a small fake Magento server that runs on your machine. They have not been run against a live store yet. If it fails on your Magento version, please open an issue with the error message.

## Limits

- Magento 2.4 or newer with GraphQL enabled.
- It lists categories and CMS pages, not individual products. A store with tens of thousands of products does not want every product in `llms.txt`.
- Descriptions come from your category and CMS meta descriptions, so the output is only as good as those.

## License

MIT
