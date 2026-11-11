"""ネットワークにアクセスせず、HTML解析とSQLiteの実際の動作を確認する。"""

import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import requests

import main


def page(repos, pages=1):
    data = {"payload": {"orgReposPageRoute": {
        "repositories": repos, "pageCount": pages, "errorMessage": None,
    }}}
    return '<script type="application/json" data-target="react-app.embeddedData">' + json.dumps(data) + '</script>'


def repo(name="example", stars=1234, language="Python"):
    return {"owner": "google", "name": name, "starsCount": stars,
            "primaryLanguage": {"name": language} if language else None}


class ScrapingTests(unittest.TestCase):
    def test_exact_stars_and_missing_language(self):
        rows, pages = main.parse_page(page([repo(), repo("empty", 0, None)], 2))
        self.assertEqual(rows, [("example", "Python", 1234), ("empty", None, 0)])
        self.assertEqual(pages, 2)

    def test_changed_html_and_invalid_counts_fail(self):
        for html in ("<html>Sign in</html>", page([repo(stars="1.2k")]),
                     page([repo(stars=-1)]), page([], 0)):
            with self.subTest(html=html), self.assertRaises(ValueError):
                main.parse_page(html)

    def test_other_owner_is_rejected(self):
        item = repo()
        item["owner"] = "another-org"
        with self.assertRaises(ValueError):
            main.parse_page(page([item]))

    def test_pages_are_collected_with_wait_and_duplicates_removed(self):
        events = []

        def get(url, **kwargs):
            number = kwargs["params"]["page"]
            events.append(("get", number))
            response = requests.Response()
            response.status_code = 200
            response._content = page([repo("a")] if number == 1 else [repo("a"), repo("b")], 2).encode()
            return response

        with patch("main.requests.get", side_effect=get), patch("main.time.sleep", side_effect=lambda n: events.append(("sleep", n))), patch("main.random.uniform", side_effect=[4, 9]):
            rows = main.scrape()
        self.assertEqual(rows, [("a", "Python", 1234), ("b", "Python", 1234)])
        self.assertEqual(events, [
            ("sleep", 1), ("sleep", 4), ("get", 1),
            ("sleep", 1), ("sleep", 9), ("get", 2),
        ])

    def test_page_limit(self):
        response = requests.Response()
        response.status_code = 200
        response._content = page([repo()], 99).encode()
        with patch("main.requests.get", return_value=response), patch("main.time.sleep"):
            self.assertEqual(main.scrape(max_pages=1), [("example", "Python", 1234)])

    def test_http_failure_is_not_treated_as_empty_result(self):
        response = requests.Response()
        response.status_code = 429
        with patch("main.requests.get", return_value=response), patch("main.time.sleep"):
            with self.assertRaises(requests.HTTPError):
                main.scrape()


class DatabaseTests(unittest.TestCase):
    def test_save_select_and_replace_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "db" / "test.sqlite"
            main.save_repositories([("a", "Python", 1234), ("old", None, 0)], db)
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute("SELECT * FROM repositories ORDER BY name").fetchall(),
                                 [("a", "Python", 1234), ("old", None, 0)])
            main.save_repositories([("a", None, 2000)], db)
            output = io.StringIO()
            with redirect_stdout(output):
                main.show_repositories(db)
            self.assertIn("a\t未設定\t2000", output.getvalue())
            self.assertNotIn("old", output.getvalue())

    def test_failed_save_rolls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "test.sqlite"
            main.save_repositories([("original", "Python", 10)], db)
            with self.assertRaises(sqlite3.IntegrityError):
                main.save_repositories([("bad", None, -1)], db)
            with sqlite3.connect(db) as con:
                self.assertEqual(con.execute("SELECT * FROM repositories").fetchall(), [("original", "Python", 10)])

    def test_select_missing_db_does_not_create_file(self):
        with tempfile.TemporaryDirectory() as directory:
            db = Path(directory) / "missing.sqlite"
            with self.assertRaises(FileNotFoundError):
                main.show_repositories(db)
            self.assertFalse(db.exists())


if __name__ == "__main__":
    unittest.main()
