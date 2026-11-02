#!/usr/bin/env python3
"""Parser tests that do not touch the network."""

import unittest

from checker import extract_anchors, match_owned, mentions_other_site, mine_edges, rel_flags

OWNED = {
    "alpha.example", "beta.example", "gamma.example", "delta.example",
    "echo.example", "foxtrot.example", "northwind.example",
}


class CheckerTests(unittest.TestCase):
    def test_match_owned_ignores_lookalikes(self):
        self.assertEqual(match_owned("www.alpha.example", OWNED), "alpha.example")
        self.assertEqual(match_owned("img.alpha.example", OWNED), "alpha.example")
        self.assertEqual(match_owned("blog.northwind.example", OWNED), "northwind.example")
        self.assertIsNone(match_owned("notalpha.example", OWNED))
        self.assertIsNone(match_owned("beta.example.invalid", OWNED))
        self.assertIsNone(match_owned("fakebeta.example", OWNED))

    def test_rel_flags(self):
        self.assertEqual(rel_flags("noopener noreferrer nofollow sponsored"), (True, True))
        self.assertEqual(rel_flags("noopener"), (False, False))
        self.assertEqual(rel_flags("ugc"), (True, False))

    def test_only_real_anchors_count(self):
        html = """
        <html><head>
          <link rel="preload" href="https://img.alpha.example/a.webp">
          <title>Example</title>
        </head><body>
          <a href="https://gamma.example" rel="nofollow sponsored">Gamma</a>
          <a href="https://www.delta.example/zh/">Delta</a>
          <p>northwind.example is only mentioned in text.</p>
          <a href="/blog/what-is-northwind">What is Northwind?</a>
          <img src="https://beta.example/logo.png">
          <a href="https://out.example/go?url=https://foxtrot.example/guest">marketplace</a>
        </body></html>
        """
        edges = {}
        mine_edges(html, "https://echo.example/blog", "echo.example", OWNED | {"echo.example"}, edges)
        self.assertEqual(set(edges), {"gamma.example", "delta.example", "foxtrot.example"})
        self.assertTrue(edges["gamma.example"]["examples"][0]["nofollow"])
        self.assertTrue(edges["gamma.example"]["examples"][0]["sponsored"])
        self.assertEqual(edges["gamma.example"]["examples"][0]["anchor"], "Gamma")
        self.assertEqual(edges["delta.example"]["examples"][0]["target_url"], "https://www.delta.example/zh/")
        self.assertIn("foxtrot.example/guest", edges["foxtrot.example"]["examples"][0]["target_url"])
        anchors = extract_anchors(html)
        self.assertEqual(len(anchors), 4)

    def test_mentions_other_site_uses_path_aliases(self):
        owned = OWNED | {"harbor.example"}
        self.assertTrue(mentions_other_site("https://harbor.example/products/alpha-example", "harbor.example", owned))
        self.assertTrue(mentions_other_site("https://harbor.example/products/beta-example", "harbor.example", owned))
        self.assertTrue(mentions_other_site("https://harbor.example/blog/northwind", "harbor.example", owned))
        self.assertTrue(mentions_other_site("https://harbor.example/products/northwindexample", "harbor.example", owned))
        self.assertFalse(mentions_other_site("https://harbor.example/blog/how-to-name-a-startup", "harbor.example", owned))
        self.assertFalse(mentions_other_site("https://alpha.example/skins/ak-47", "alpha.example", owned))


if __name__ == "__main__":
    unittest.main()
