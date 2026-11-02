import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
import xml.dom.minidom
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import generate  # noqa: E402

NOW = dt.datetime(2026, 11, 2, 12, 0, tzinfo=dt.timezone.utc)


def live_like_data():
    """Synthetic live-style payload used only for tests."""
    events = []
    for i in range(0, 60, 2):
        day = NOW - dt.timedelta(days=i)
        for _ in range(1 + i % 4):
            events.append({"type": "PushEvent", "repo": "iam-alenjoseph/portfolio",
                           "created_at": day.isoformat()})
    return {
        "generated": NOW.isoformat(),
        "synthetic": False,
        "user": {"login": "iam-alenjoseph", "public_repos": 23, "followers": 1,
                 "following": 1, "created_at": "2025-06-01T00:00:00Z"},
        "repos": [
            {"name": "portfolio", "description": "my portfolio web", "language": "TypeScript",
             "stargazers_count": 3, "forks_count": 1, "pushed_at": (NOW - dt.timedelta(days=2)).isoformat(),
             "html_url": "https://github.com/iam-alenjoseph/portfolio",
             "languages": {"TypeScript": 52000, "CSS": 9000, "HTML": 1500}},
            {"name": "pharmacare", "description": "", "language": "PHP", "stargazers_count": 0,
             "forks_count": 0, "pushed_at": (NOW - dt.timedelta(days=40)).isoformat(),
             "html_url": "https://github.com/iam-alenjoseph/pharmacare",
             "languages": {"PHP": 80000, "JavaScript": 12000}},
        ],
        "events": events,
    }


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        shutil.copy(ROOT / "config.json", self.tmp / "config.json")
        shutil.copy(ROOT / "README.md", self.tmp / "README.md")
        self.cfg = json.loads((self.tmp / "config.json").read_text(encoding="utf-8"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _check_all_svgs(self):
        files = list((self.tmp / "assets").glob("*.svg"))
        self.assertTrue(files)
        for f in files:
            xml.dom.minidom.parse(str(f))  # raises on malformed XML

    def test_snapshot_build(self):
        data = json.loads((ROOT / "data" / "snapshot.json").read_text(encoding="utf-8"))
        generate.build_all(self.tmp, self.cfg, data)
        self._check_all_svgs()

    def test_live_like_build(self):
        generate.build_all(self.tmp, self.cfg, live_like_data())
        self._check_all_svgs()
        readme = (self.tmp / "README.md").read_text(encoding="utf-8")
        self.assertIn("Pushed to", readme)
        self.assertIn("card-portfolio", readme)

    def test_markers_required(self):
        with self.assertRaises(ValueError):
            generate.replace_block("no markers", "PROJECTS", "x")

    def test_language_percentages_sum(self):
        langs, has_bytes = generate.language_totals(live_like_data())
        self.assertTrue(has_bytes)
        self.assertEqual(langs[0][0], "PHP")

    def test_rel_time(self):
        past = (NOW - dt.timedelta(days=45)).isoformat()
        self.assertEqual(generate.rel_time(past, NOW), "1mo ago")
        self.assertIsNone(generate.rel_time(None, NOW))

    def test_activity_streak(self):
        s = generate.activity_summary(live_like_data(), NOW)
        self.assertEqual(s["top_repo"], "portfolio")
        self.assertGreaterEqual(s["active_days"], 1)


if __name__ == "__main__":
    unittest.main()
