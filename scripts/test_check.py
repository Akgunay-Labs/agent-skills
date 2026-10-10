import unittest, sys, pathlib, tempfile
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import check  # noqa: E402

class FrontmatterTests(unittest.TestCase):
    def test_parses_name_and_description(self):
        fm = check.parse_frontmatter("---\nname: rex\ndescription: Use when x.\n---\n# body\n")
        self.assertEqual(fm, {"name": "rex", "description": "Use when x."})

    def test_missing_frontmatter_is_none(self):
        self.assertIsNone(check.parse_frontmatter("# no frontmatter\n"))

    def test_validate_rejects_bad_name_and_long_description(self):
        probs = check.validate_skill({"name": "Bad Name", "description": "x" * 501}, body_words=10)
        self.assertTrue(any("name" in p for p in probs))
        self.assertTrue(any("description" in p for p in probs))

    def test_validate_requires_use_when_and_word_budget(self):
        probs = check.validate_skill({"name": "ok-name", "description": "Does things."}, body_words=1500)
        self.assertTrue(any("Use when" in p for p in probs))
        self.assertTrue(any("words" in p for p in probs))

    def test_validate_clean(self):
        self.assertEqual(check.validate_skill({"name": "ok-name", "description": "Use when testing."}, body_words=100), [])

    def test_find_skills_lists_dirs_with_skill_md(self):
        with tempfile.TemporaryDirectory() as d:
            root = pathlib.Path(d); (root / "skills" / "a").mkdir(parents=True); (root / "skills" / "a" / "SKILL.md").write_text("---\nname: a\ndescription: Use when a.\n---\n")
            (root / "skills" / "junk").mkdir()
            self.assertEqual([p.name for p in check.find_skills(root)], ["a"])

if __name__ == "__main__":
    unittest.main()
