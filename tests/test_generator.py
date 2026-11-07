import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import magento_llms_txt as m  # noqa: E402

STORE = {"storeConfig": {"store_name": "Acme Parts", "base_url": "x",
                         "category_url_suffix": ".html"}}

CATEGORIES = {"categoryList": [{
    "name": "Root", "url_path": None, "level": 1, "include_in_menu": 1, "product_count": 9,
    "description": None,
    "children": [
        {"name": "Filters", "level": 2, "url_path": "filters", "include_in_menu": 1,
         "product_count": 120,
         "description": "<p>Oil, air &amp; fuel filters for <b>tractors</b>.</p>",
         "children": [
             {"name": "Oil filters", "level": 3, "url_path": "filters/oil", "include_in_menu": 1,
              "product_count": 40, "description": ""},
             {"name": "Hidden", "level": 3, "url_path": "filters/hidden", "include_in_menu": 0,
              "product_count": 5, "description": ""},
             {"name": "Empty", "level": 3, "url_path": "filters/empty", "include_in_menu": 1,
              "product_count": 0, "description": ""},
         ]},
        {"name": "Brakes", "level": 2, "url_path": "brakes", "include_in_menu": 1,
         "product_count": 30, "description": None, "children": []},
    ],
}]}


def fake_fetch(query, variables=None):
    if "storeConfig" in query:
        return STORE
    if "categoryList" in query:
        return CATEGORIES
    if "cmsPage" in query:
        ident = (variables or {}).get("identifier")
        if ident == "about-us":
            return {"cmsPage": {"title": "About us", "url_key": "about-us",
                                "meta_description": "Who we are."}}
        return {"cmsPage": None}
    raise AssertionError("unexpected query")


class CleanTextTests(unittest.TestCase):
    def test_strips_html_and_entities(self):
        self.assertEqual(m.clean_text("<p>A &amp; <b>B</b></p>"), "A & B")

    def test_truncates_on_word_boundary(self):
        out = m.clean_text("word " * 100, limit=20)
        self.assertTrue(out.endswith("..."))
        self.assertLessEqual(len(out), 23)

    def test_empty(self):
        self.assertEqual(m.clean_text(None), "")


class GenerateTests(unittest.TestCase):
    def setUp(self):
        self.text = m.generate(
            fake_fetch, "https://shop.example.com/",
            summary="Spare parts.", cms_pages=["about-us", "missing"],
            extra_links=["Contact|https://shop.example.com/contact|Talk to us"],
        )

    def test_title_and_summary(self):
        lines = self.text.splitlines()
        self.assertEqual(lines[0], "# Acme Parts")
        self.assertEqual(lines[2], "> Spare parts.")

    def test_top_level_category_with_suffix_and_clean_description(self):
        self.assertIn(
            "- [Filters](https://shop.example.com/filters.html): "
            "Oil, air & fuel filters for tractors.", self.text)

    def test_hidden_categories_skipped_by_default(self):
        self.assertNotIn("Hidden", self.text)

    def test_empty_categories_kept_by_default(self):
        self.assertIn("[Empty]", self.text)

    def test_skip_empty_option(self):
        text = m.generate(fake_fetch, "https://shop.example.com",
                          skip_empty=True)
        self.assertNotIn("[Empty]", text)
        self.assertIn("[Oil filters]", text)

    def test_include_hidden_option(self):
        text = m.generate(fake_fetch, "https://shop.example.com",
                          include_hidden=True)
        self.assertIn("[Hidden]", text)

    def test_product_count_not_requested_by_default(self):
        self.assertNotIn("product_count", m.category_query(False))
        self.assertIn("product_count", m.category_query(True))

    def test_subcategories_go_under_optional(self):
        optional = self.text.split("## Optional")[1]
        self.assertIn("[Oil filters](https://shop.example.com/filters/oil.html)",
                      optional)

    def test_cms_page_and_missing_page(self):
        self.assertIn("[About us](https://shop.example.com/about-us): Who we are.",
                      self.text)
        self.assertNotIn("missing", self.text)

    def test_extra_link(self):
        self.assertIn("- [Contact](https://shop.example.com/contact): Talk to us",
                      self.text)

    def test_section_order_matches_spec(self):
        order = [l for l in self.text.splitlines() if l.startswith("## ")]
        self.assertEqual(order, ["## Product categories",
                                 "## Help and company pages", "## More",
                                 "## Optional"])

    def test_ends_with_single_newline(self):
        self.assertTrue(self.text.endswith("\n"))
        self.assertFalse(self.text.endswith("\n\n"))

    def test_max_depth_one_drops_subcategories(self):
        text = m.generate(fake_fetch, "https://shop.example.com", max_depth=1)
        self.assertNotIn("Oil filters", text)
        self.assertNotIn("## Optional", text)

    def test_no_data_raises(self):
        def empty(query, variables=None):
            return {"storeConfig": {}, "categoryList": []}
        with self.assertRaises(m.MagentoError):
            m.generate(empty, "https://shop.example.com")


class WalkTests(unittest.TestCase):
    def test_hidden_container_still_lists_visible_children(self):
        tree = [{"name": "Root", "level": 1, "url_path": None, "children": [
            {"name": "Categories", "level": 2, "url_path": "categories",
             "include_in_menu": 0, "children": [
                 {"name": "Tyres", "level": 3, "url_path": "categories/tyres",
                  "include_in_menu": 1}]}]}]
        rows = list(m.walk_categories(tree))
        self.assertEqual([(d, n["name"]) for d, n in rows], [(1, "Tyres")])

    def test_cms_graphql_error_is_skipped_not_fatal(self):
        def fetch(query, variables=None):
            raise m.GraphQLError("The CMS page doesn't exist")
        self.assertEqual(m.collect_cms_pages(fetch, "https://x.example", ["a"]), [])


class LinkLineTests(unittest.TestCase):
    def test_brackets_and_entities_in_title(self):
        line = m.link_line("Hose [8mm] &amp; clamp", "https://x.example/a b")
        self.assertEqual(line, "- [Hose (8mm) & clamp](https://x.example/a%20b)")

    def test_duplicate_urls_listed_once(self):
        rows = [(1, "A", "https://x.example/a", ""), (1, "A2", "https://x.example/a", "")]
        text = m.build_llms_txt("S", "", rows, [], [])
        self.assertEqual(text.count("https://x.example/a"), 1)


class LinkParsingTests(unittest.TestCase):
    def test_bad_link_rejected(self):
        with self.assertRaises(ValueError):
            m.parse_extra_link("just a title")
        with self.assertRaises(ValueError):
            m.parse_extra_link("Title|not-a-url")


class CliTests(unittest.TestCase):
    def test_writes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "llms.txt")
            code = m.main(["--base-url", "https://shop.example.com",
                           "--output", out], fetch=fake_fetch)
            self.assertEqual(code, 0)
            with open(out, encoding="utf-8") as handle:
                self.assertTrue(handle.read().startswith("# Acme Parts"))

    def test_unwritable_output_returns_nonzero(self):
        self.assertEqual(m.main(["--base-url", "https://x.example", "--output",
                                 "/nonexistent-dir/llms.txt"], fetch=fake_fetch), 1)

    def test_error_returns_nonzero(self):
        def boom(query, variables=None):
            raise m.MagentoError("down")
        self.assertEqual(
            m.main(["--base-url", "https://x.example"], fetch=boom), 1)


class FakeMagento(BaseHTTPRequestHandler):
    seen_store_headers = []

    def do_POST(self):
        if self.path != "/graphql":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length))
        FakeMagento.seen_store_headers.append(self.headers.get("Store"))
        data = fake_fetch(body["query"], body.get("variables"))
        payload = json.dumps({"data": data}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class RedirectingMagento(BaseHTTPRequestHandler):
    """Redirects /old/graphql to /graphql, as an http to https redirect would."""

    def do_POST(self):
        if self.path == "/old/graphql":
            self.send_response(301)
            self.send_header("Location", "/graphql")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        FakeMagento.do_POST(self)

    log_message = FakeMagento.log_message


class RedirectTests(unittest.TestCase):
    def test_post_survives_redirect(self):
        server = HTTPServer(("127.0.0.1", 0), RedirectingMagento)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            base = f"http://127.0.0.1:{server.server_address[1]}/old"
            data = m.make_fetcher(base)(m.STORE_QUERY, None)
            self.assertEqual(data["storeConfig"]["store_name"], "Acme Parts")
        finally:
            server.shutdown()
            server.server_close()


class HttpTests(unittest.TestCase):
    """Exercises the real urllib code path against a local fake GraphQL server."""

    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeMagento)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_end_to_end_over_http(self):
        base = f"http://127.0.0.1:{self.port}"
        fetch = m.make_fetcher(base, store_code="de")
        text = m.generate(fetch, base, summary="Parts.")
        self.assertIn("# Acme Parts", text)
        self.assertIn(f"[Filters]({base}/filters.html)", text)
        self.assertIn("de", FakeMagento.seen_store_headers)

    def test_unreachable_store_gives_clean_error(self):
        fetch = m.make_fetcher("http://127.0.0.1:1", timeout=2)
        with self.assertRaises(m.MagentoError):
            fetch(m.STORE_QUERY, None)

    def test_wrong_path_gives_clean_error(self):
        fetch = m.make_fetcher(f"http://127.0.0.1:{self.port}/nope")
        with self.assertRaises(m.MagentoError):
            fetch(m.STORE_QUERY, None)


if __name__ == "__main__":
    unittest.main()
