import unittest

from src.sentiment.analyzer import match_entities


class TestDashboardEntityMatching(unittest.TestCase):
    def test_canonical_name_is_always_matched(self):
        entities = {
            "Microsoft": ("msft",),
            "NVIDIA": ("nvda",),
        }
        self.assertIn("Microsoft", match_entities(
            "Microsoft released a new Windows update.", entities
        ))
        self.assertIn("NVIDIA", match_entities(
            "NVIDIA announces a new GPU.", entities
        ))

    def test_aliases_remain_case_insensitive(self):
        entities = {"Microsoft": ("msft",)}
        self.assertIn("Microsoft", match_entities(
            "MICROSOFT posted MSFT results.", entities
        ))

    def test_word_boundary_prevents_substrings(self):
        entities = {"Microsoft": ("msft",)}
        self.assertNotIn("Microsoft", match_entities(
            "A microsystem should not match the company.", entities
        ))


if __name__ == "__main__":
    unittest.main()
