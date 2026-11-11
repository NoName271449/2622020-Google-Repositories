"""Googleのリポジトリ一覧をHTMLから取得し、SQLiteに保存して表示する。"""

import argparse
import json
import random
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

import requests
from bs4 import BeautifulSoup


URL = "https://github.com/orgs/google/repositories"
DB_PATH = Path(__file__).resolve().parent / "db" / "google_repositories.sqlite"


def parse_page(html):
    """HTMLに埋め込まれた一覧データを読み、行のリストと総ページ数を返す。"""
    soup = BeautifulSoup(html, "html.parser")
    element = soup.select_one('script[data-target="react-app.embeddedData"]')
    if element is None:
        raise ValueError("一覧データが見つかりません。GitHubのHTML構造を確認してください。")

    try:
        data = json.loads(element.get_text())["payload"]["orgReposPageRoute"]
        pages = data["pageCount"]
        if data.get("errorMessage") or type(pages) is not int or pages < 1:
            raise ValueError("一覧のページ情報が不正です。")
        repositories = data["repositories"]
        if not isinstance(repositories, list) or not repositories:
            raise ValueError("リポジトリ一覧が空です。")

        rows = []
        for repository in repositories:
            name = repository["name"]
            language = repository["primaryLanguage"]
            language = language["name"] if language else None
            stars = repository["starsCount"]
            if repository["owner"] != "google" or not isinstance(name, str) or not name:
                raise ValueError("Google以外、または名前が不正なリポジトリです。")
            if language is not None and not isinstance(language, str):
                raise ValueError("主要言語の形式が不正です。")
            if type(stars) is not int or stars < 0:
                raise ValueError("スター数が0以上の整数ではありません。")
            rows.append((name, language, stars))
        return rows, pages
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise ValueError("一覧データの形式が変わった可能性があります。") from error


def scrape(max_pages=None):
    """APIを使わず、公開一覧ページのHTMLを順番に取得する。"""
    repositories = {}
    page = 1
    last_page = 1
    while page <= last_page:
        # 1秒間隔では39ページ目、3秒間隔でも70ページ目でHTTP 429になった。
        # アクセス頻度を下げるため、指定の1秒にランダムな4〜9秒を追加する。
        # 合計5〜10秒待つが、制限を必ず防げるわけではない。429の場合は停止する。
        time.sleep(1)
        time.sleep(random.uniform(4, 9))
        response = requests.get(
            URL,
            params={"type": "all", "sort": "name", "direction": "asc", "page": page},
            headers={"User-Agent": "DSProgramming1-RepositoryScraper/1.0"},
            timeout=30,
        )
        response.raise_for_status()
        rows, total_pages = parse_page(response.text)
        if page == 1:
            last_page = min(total_pages, max_pages) if max_pages else total_pages
        previous_count = len(repositories)
        for row in rows:
            repositories[row[0]] = row
        if len(repositories) == previous_count:
            raise ValueError("新しいリポジトリがありません。同じページの繰り返しを検出しました。")
        print(f"取得: {page}/{last_page}ページ（重複除外後 {len(repositories)}件）", file=sys.stderr)
        page += 1
    return list(repositories.values())


def save_repositories(rows, db_path=DB_PATH):
    """全取得に成功してから、トランザクション内で保存内容を置き換える。"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS repositories (
                name TEXT PRIMARY KEY NOT NULL,
                language TEXT,
                stars INTEGER NOT NULL CHECK (stars >= 0)
            )
        """)
        with con:
            con.execute("DELETE FROM repositories")
            # ?を使って値を渡す（文字列をSQLに直接連結しない）。
            con.executemany(
                "INSERT INTO repositories (name, language, stars) VALUES (?, ?, ?)", rows
            )


def show_repositories(db_path=DB_PATH):
    """保存済みのデータをSELECT文で取得し、スター数の多い順に表示する。"""
    if not Path(db_path).is_file():
        raise FileNotFoundError("DBがありません。先にスクレイピングを実行してください。")
    with closing(sqlite3.connect(db_path)) as con:
        cur = con.cursor()
        cur.execute("SELECT name, language, stars FROM repositories ORDER BY stars DESC, name ASC")
        print("リポジトリ名\t主要言語\tスター数")
        for name, language, stars in cur:
            print(f"{name}\t{language or '未設定'}\t{stars}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-pages", type=int, help="確認用の取得ページ数（省略すると全ページ）")
    parser.add_argument("--select-only", action="store_true", help="通信せず、保存済みDBをSELECT表示")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="保存先のSQLiteファイル")
    args = parser.parse_args()
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pagesには1以上を指定してください。")
    if args.select_only and args.max_pages is not None:
        parser.error("--select-onlyと--max-pagesは同時に指定できません。")

    try:
        if not args.select_only:
            rows = scrape(args.max_pages)
            save_repositories(rows, args.db)
            print(f"保存: {len(rows)}件 → {args.db}", file=sys.stderr)
        show_repositories(args.db)
    except (requests.RequestException, ValueError, sqlite3.Error, OSError) as error:
        print(f"エラー: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
