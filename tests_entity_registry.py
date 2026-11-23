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

    def test_llm_company_and_model_aliases(self):
        entities = {
            "OpenAI": ("chatgpt", "gpt-4o", "gpt-5", "o1"),
            "Anthropic": ("claude", "claude 3.5", "claude opus"),
            "Gemini": ("gemini", "gemini 2.5", "google deepmind"),
        }
        openai_text = "ChatGPT and GPT-5 are OpenAI models."
        claude_text = "Claude Opus from Anthropic is useful."
        gemini_text = "Google DeepMind releases Gemini 2.5."
        self.assertIn("OpenAI", match_entities(openai_text, entities))
        self.assertIn("Anthropic", match_entities(claude_text, entities))
        self.assertIn("Gemini", match_entities(gemini_text, entities))


if __name__ == "__main__":
    unittest.main()
