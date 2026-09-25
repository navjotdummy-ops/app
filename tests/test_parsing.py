"""Unit tests for the pure helper functions in reel_stats.py (no network needed)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import reel_stats as rs  # noqa: E402


class TestHelpers(unittest.TestCase):
    def test_normalize_header(self):
        self.assertEqual(rs.normalize_header("  Reel Link "), "reellink")
        self.assertEqual(rs.normalize_header("LAST_UPDATED"), "lastupdated")

    def test_shortcode_from_url(self):
        self.assertEqual(rs.shortcode_from_url("https://www.instagram.com/reel/C1a_b-2Xy9z/"), "C1a_b-2Xy9z")
        self.assertEqual(rs.shortcode_from_url("https://instagram.com/reels/AbC123/?igsh=xyz"), "AbC123")
        self.assertEqual(rs.shortcode_from_url("https://www.instagram.com/p/AbC123"), "AbC123")
        self.assertEqual(rs.shortcode_from_url("https://www.instagram.com/someuser/reel/AbC123/"), "AbC123")
        self.assertIsNone(rs.shortcode_from_url("https://www.instagram.com/someuser/"))
        self.assertIsNone(rs.shortcode_from_url(""))

    def test_parse_count(self):
        self.assertEqual(rs.parse_count("1,234"), (1234, False))
        self.assertEqual(rs.parse_count("1.2K"), (1200, True))
        self.assertEqual(rs.parse_count("3.4M"), (3_400_000, True))
        self.assertEqual(rs.parse_count("12k"), (12_000, True))
        self.assertEqual(rs.parse_count("987"), (987, False))
        self.assertEqual(rs.parse_count("views"), (None, False))
        self.assertEqual(rs.parse_count(""), (None, False))


class TestJsonExtraction(unittest.TestCase):
    def test_api_v1_shape(self):
        data = {"items": [{"code": "ABC", "play_count": 15342, "ig_play_count": 15342,
                           "like_count": 812, "comment_count": 44,
                           "user": {"username": "creator_one", "full_name": "Creator One"}}]}
        stats = rs.extract_stats_from_json(data, "ABC")
        self.assertEqual((stats.views, stats.likes, stats.comments), (15342, 812, 44))
        self.assertEqual(stats.owner_handle, "creator_one")
        self.assertEqual(stats.owner_name, "Creator One")
        self.assertFalse(stats.approx)
        self.assertFalse(stats.likes_hidden)

    def test_graphql_shape(self):
        data = {"data": {"xdt_shortcode_media": {
            "shortcode": "XYZ", "video_play_count": 999, "video_view_count": 900,
            "edge_media_preview_like": {"count": 55},
            "edge_media_to_parent_comment": {"count": 7},
            "owner": {"username": "someone"}}}}
        stats = rs.extract_stats_from_json(data, "XYZ")
        self.assertEqual((stats.views, stats.likes, stats.comments), (999, 55, 7))

    def test_hidden_likes(self):
        data = {"items": [{"code": "HID", "play_count": 10, "comment_count": 1,
                           "like_and_view_counts_disabled": True}]}
        stats = rs.extract_stats_from_json(data, "HID")
        self.assertTrue(stats.likes_hidden)
        self.assertIsNone(stats.likes)

    def test_wrong_shortcode_ignored(self):
        data = {"items": [{"code": "OTHER", "play_count": 10, "like_count": 1, "comment_count": 1}]}
        self.assertIsNone(rs.extract_stats_from_json(data, "MINE"))

    def test_merge_missing(self):
        a = rs.ReelStats(likes=5, comments=2, sources=["network"])
        b = rs.ReelStats(views=1200, approx=True, sources=["Reels tab"])
        a.merge_missing(b)
        self.assertEqual(a.views, 1200)
        self.assertTrue(a.approx)
        self.assertEqual(a.sources, ["network", "Reels tab"])


class TestTextExtraction(unittest.TestCase):
    def test_visible_text(self):
        text = "creator_one\n1,204 likes\n38 comments\n12.5K views\nView all comments"
        stats = rs.extract_stats_from_text(text)
        self.assertEqual((stats.views, stats.likes, stats.comments), (12_500, 1204, 38))
        self.assertTrue(stats.approx)

    def test_hidden_likes_text(self):
        stats = rs.extract_stats_from_text("Liked by friend_a and others\n3 comments")
        self.assertIsNone(stats.likes)
        self.assertTrue(stats.likes_hidden)
        self.assertEqual(stats.comments, 3)

    def test_status(self):
        self.assertEqual(rs.build_status(rs.ReelStats(), []), "OK")
        self.assertEqual(rs.build_status(rs.ReelStats(approx=True), ["views from Reels tab"]),
                         "OK (approx); views from Reels tab")


if __name__ == "__main__":
    unittest.main()
