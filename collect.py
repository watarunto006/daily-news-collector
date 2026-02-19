#!/usr/bin/env python3
"""
DID/VC・AI ニュース日次収集スクリプト

【情報収集の仕組み】

1. Google News RSS + 外部API (Hacker News等) から記事を収集  (Python)
2. 公開日で厳密にフィルタ（過去24時間以内のみ）            (Python)
3. タイトル類似度で重複排除                                (Python)
4. Claude Code で関連性判定 + 要約                         (claude -p)
5. Markdown レポート生成                                   (Python)

URL・日付・フォーマットはすべて Python が保証する。
Claude は関連性判定と要約のみを担当し、再現性を確保する。

使い方:
  python3 collect.py did-vc
  python3 collect.py all
  python3 collect.py ai-dev --date 2026-02-18
"""

import json
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.environ.get("DAILY_NEWS_CONFIG") or os.path.join(SCRIPT_DIR, "config.json")
PROMPT_TEMPLATE_FILE = os.path.join(SCRIPT_DIR, "prompt_template.md")
REPORTS_DIR = os.environ.get("DAILY_NEWS_REPORTS_DIR") or os.path.join(SCRIPT_DIR, "reports")

JST = timezone(timedelta(hours=9))


# =============================================================================
# 記事収集
# =============================================================================

def fetch_google_news_rss(keyword: str, lang: str, since: datetime) -> list[dict]:
    """Google News RSS から記事を取得する。"""
    encoded = urllib.parse.quote(keyword)
    if lang == "ja":
        url = f"https://news.google.com/rss/search?q={encoded}+when:1d&hl=ja&gl=JP&ceid=JP:ja"
    else:
        url = f"https://news.google.com/rss/search?q={encoded}+when:1d&hl=en&gl=US&ceid=US:en"

    articles = []
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "daily-news-collector/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            xml_data = resp.read()
        root = ET.fromstring(xml_data)

        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link = item.findtext("link", "")
            pub_date_str = item.findtext("pubDate", "")
            source_el = item.find("source")
            source = source_el.text if source_el is not None else ""

            pub_date = parse_rss_date(pub_date_str)
            if pub_date is None or pub_date < since:
                continue

            articles.append({
                "title": title,
                "url": link,
                "published": pub_date.isoformat(),
                "source": source,
                "lang": lang,
                "via": "google_news",
            })
    except Exception as e:
        print(f"  [WARN] Google News RSS failed for '{keyword}': {e}", file=sys.stderr)

    return articles


def fetch_hackernews(queries: list[str], since_ts: int) -> list[dict]:
    """Hacker News Algolia API から記事を取得する。"""
    articles = []
    seen_urls = set()

    for query in queries:
        url = (
            "https://hn.algolia.com/api/v1/search_by_date"
            f"?query={urllib.parse.quote(query)}"
            f"&tags=story"
            f"&numericFilters=created_at_i>{since_ts}"
            f"&hitsPerPage=10"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "daily-news-collector/1.0"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as e:
            print(f"  [WARN] HN query '{query}' failed: {e}", file=sys.stderr)
            continue

        for hit in data.get("hits", []):
            article_url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit['objectID']}"
            if article_url in seen_urls:
                continue
            seen_urls.add(article_url)

            articles.append({
                "title": hit.get("title", ""),
                "url": article_url,
                "published": hit.get("created_at", ""),
                "source": "Hacker News",
                "lang": "en",
                "via": "hackernews",
                "hn_url": f"https://news.ycombinator.com/item?id={hit['objectID']}",
                "points": hit.get("points", 0),
            })

    return articles


def parse_rss_date(date_str: str) -> datetime | None:
    """RSS の日付文字列をパースする。"""
    formats = [
        "%a, %d %b %Y %H:%M:%S %z",
        "%a, %d %b %Y %H:%M:%S GMT",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


# =============================================================================
# フィルタリング・重複排除
# =============================================================================

def filter_by_date(articles: list[dict], since: datetime) -> list[dict]:
    """公開日が since 以降の記事のみ残す。"""
    result = []
    for a in articles:
        try:
            pub = datetime.fromisoformat(a["published"])
            if pub.tzinfo is None:
                pub = pub.replace(tzinfo=timezone.utc)
            if pub >= since:
                result.append(a)
        except (ValueError, KeyError):
            continue
    return result


def deduplicate(articles: list[dict]) -> list[dict]:
    """タイトルの単語集合の Jaccard 係数で重複排除（閾値 0.6）。"""
    def tokenize(title: str) -> set[str]:
        return set(re.findall(r'\w+', title.lower()))

    result = []
    seen_tokens = []
    for a in articles:
        tokens = tokenize(a["title"])
        is_dup = False
        for seen in seen_tokens:
            intersection = tokens & seen
            union = tokens | seen
            if union and len(intersection) / len(union) > 0.6:
                is_dup = True
                break
        if not is_dup:
            result.append(a)
            seen_tokens.append(tokens)
    return result


def filter_excluded_keywords(articles: list[dict], exclude: list[str]) -> list[dict]:
    """除外キーワードを含む記事を除外。"""
    if not exclude:
        return articles
    result = []
    for a in articles:
        title_lower = a["title"].lower()
        if not any(kw.lower() in title_lower for kw in exclude):
            result.append(a)
    return result


# =============================================================================
# Claude で関連性判定 + 要約
# =============================================================================

def call_claude(articles: list[dict], topic_label: str) -> dict | None:
    """claude -p で記事の関連性判定と要約を行う。"""
    with open(PROMPT_TEMPLATE_FILE) as f:
        template = f.read()

    # 記事に ID を付与
    for i, a in enumerate(articles):
        a["id"] = str(i)

    articles_json = json.dumps(articles, ensure_ascii=False, indent=2)
    prompt = template.replace("{{TOPIC_LABEL}}", topic_label)
    prompt = prompt.replace("{{ARTICLES_JSON}}", articles_json)

    try:
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)  # ネストセッション検出を回避
        result = subprocess.run(
            ["claude", "-p"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
        )
        output = result.stdout.strip()
    except subprocess.TimeoutExpired:
        print("  [ERROR] claude -p timed out", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("  [ERROR] claude command not found", file=sys.stderr)
        return None

    # JSON 部分を抽出
    json_match = re.search(r'\{[\s\S]*\}', output)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            print("  [WARN] Failed to parse Claude output as JSON", file=sys.stderr)
            print(f"  Output: {output[:500]}", file=sys.stderr)
            return None
    else:
        print("  [WARN] No JSON found in Claude output", file=sys.stderr)
        print(f"  Output: {output[:500]}", file=sys.stderr)
        return None


def call_claude_api(articles: list[dict], topic_label: str, model: str) -> dict | None:
    """Anthropic Messages API で記事の関連性判定と要約を行う。"""
    from anthropic import Anthropic

    with open(PROMPT_TEMPLATE_FILE) as f:
        template = f.read()

    for i, a in enumerate(articles):
        a["id"] = str(i)

    articles_json = json.dumps(articles, ensure_ascii=False, indent=2)
    prompt = template.replace("{{TOPIC_LABEL}}", topic_label)
    prompt = prompt.replace("{{ARTICLES_JSON}}", articles_json)

    try:
        client = Anthropic()
        message = client.messages.create(
            model=model,
            max_tokens=16384,
            messages=[{"role": "user", "content": prompt}],
        )
        output = message.content[0].text.strip()
    except Exception as e:
        print(f"  [ERROR] Anthropic API call failed: {e}", file=sys.stderr)
        return None

    json_match = re.search(r'\{[\s\S]*\}', output)
    if json_match:
        try:
            return json.loads(json_match.group())
        except json.JSONDecodeError:
            print("  [WARN] Failed to parse API output as JSON", file=sys.stderr)
            print(f"  Output: {output[:500]}", file=sys.stderr)
            return None
    else:
        print("  [WARN] No JSON found in API output", file=sys.stderr)
        print(f"  Output: {output[:500]}", file=sys.stderr)
        return None


def analyze_with_claude(articles: list[dict], topic_label: str) -> dict | None:
    """ANTHROPIC_API_KEY の有無で API モードと CLI モードを切り替える。"""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    model = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
    if api_key:
        print(f"  [MODE] API モード (model={model})", file=sys.stderr)
        return call_claude_api(articles, topic_label, model)
    else:
        print("  [MODE] CLI モード (claude -p)", file=sys.stderr)
        return call_claude(articles, topic_label)


# =============================================================================
# レポート生成
# =============================================================================

def merge_results(
    articles: list[dict],
    claude_result: dict | None,
) -> tuple[list[dict], list[dict]]:
    """Claude の結果と元記事をマージし、(selected, excluded) を返す。"""
    excluded = []
    if claude_result and claude_result.get("articles"):
        rated = {a["id"]: a for a in claude_result["articles"]}
        selected = []
        for a in articles:
            if a["id"] in rated:
                a["summary_ja"] = rated[a["id"]].get("summary_ja", "")
                a["importance"] = rated[a["id"]].get("importance", "medium")
                selected.append(a)
            else:
                excluded.append(a)
    else:
        selected = articles
        for a in selected:
            a["summary_ja"] = ""
            a["importance"] = "medium"

    importance_order = {"high": 0, "medium": 1, "low": 2}
    selected.sort(key=lambda a: importance_order.get(a.get("importance", "medium"), 1))
    return selected, excluded


def generate_report_json(
    topic_id: str,
    topic_label: str,
    selected: list[dict],
    excluded: list[dict],
    claude_result: dict | None,
    date_str: str,
    total_fetched: int,
    total_after_filter: int,
) -> dict:
    """構造化された JSON レポートを生成する。"""
    def clean_article(a: dict) -> dict:
        return {
            "title": a.get("title", ""),
            "url": a.get("url", ""),
            "published": a.get("published", ""),
            "source": a.get("source", ""),
            "lang": a.get("lang", ""),
            "summary_ja": a.get("summary_ja", ""),
            "importance": a.get("importance", "medium"),
            "hn_url": a.get("hn_url"),
        }

    en_articles = [clean_article(a) for a in selected if a.get("lang") == "en"]
    ja_articles = [clean_article(a) for a in selected if a.get("lang") == "ja"]

    return {
        "topic": topic_id,
        "label": topic_label,
        "date": date_str,
        "collected_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M"),
        "highlights": claude_result.get("highlights", "") if claude_result else "",
        "articles_en": en_articles,
        "articles_ja": ja_articles,
        "meta": {
            "total_fetched": total_fetched,
            "after_filter": total_after_filter,
            "selected_en": len(en_articles),
            "selected_ja": len(ja_articles),
            "excluded": len(excluded),
            "excluded_reasons": claude_result.get("excluded_reasons", "") if claude_result else "",
        },
    }


def generate_report(
    topic_label: str,
    selected: list[dict],
    excluded: list[dict],
    claude_result: dict | None,
    date_str: str,
    total_fetched: int,
    total_after_filter: int,
) -> str:
    """Markdown レポートを生成する。"""
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# {topic_label} - {date_str}",
        "",
        f"> 収集時刻: {now} JST",
        "> 対象期間: 過去24時間",
        "",
    ]

    # 英語・日本語に分割
    en_articles = [a for a in selected if a.get("lang") == "en"]
    ja_articles = [a for a in selected if a.get("lang") == "ja"]

    # English News
    lines.append("## English News")
    lines.append("")
    if en_articles:
        for i, a in enumerate(en_articles, 1):
            lines.append(f"### {i}. {a['title']}")
            lines.append(f"- **Published**: {a['published']}")
            lines.append(f"- **Source**: {a['source']}")
            lines.append(f"- **URL**: {a['url']}")
            if a.get("hn_url"):
                lines.append(f"- **HN**: {a['hn_url']}")
            if a.get("summary_ja"):
                lines.append(f"- **Summary**: {a['summary_ja']}")
            lines.append("")
    else:
        lines.append("該当なし")
        lines.append("")

    # 日本語ニュース
    lines.append("## 日本語ニュース")
    lines.append("")
    if ja_articles:
        for i, a in enumerate(ja_articles, 1):
            lines.append(f"### {i}. {a['title']}")
            lines.append(f"- **公開日**: {a['published']}")
            lines.append(f"- **Source**: {a['source']}")
            lines.append(f"- **URL**: {a['url']}")
            if a.get("summary_ja"):
                lines.append(f"- **要約**: {a['summary_ja']}")
            lines.append("")
    else:
        lines.append("該当なし")
        lines.append("")

    # ハイライト
    highlights = ""
    if claude_result:
        highlights = claude_result.get("highlights", "")
    if highlights:
        lines.append("## 本日のハイライト")
        lines.append("")
        lines.append(highlights)
        lines.append("")

    # 除外記事一覧
    if excluded:
        # Claude が返した日本語タイトルをマージ
        excluded_ja = {}
        if claude_result and claude_result.get("excluded"):
            for e in claude_result["excluded"]:
                excluded_ja[e["id"]] = e.get("title_ja", "")

        lines.append("## 除外された記事")
        lines.append("")
        for a in excluded:
            title = excluded_ja.get(a["id"], "") or a["title"]
            lines.append(f"- {title}")
            lines.append(f"  {a['url']}")
        lines.append("")

    lines.append("## 収集メタ情報")
    lines.append(f"- 記事取得数（フィルタ前）: {total_fetched}")
    lines.append(f"- 日付・重複フィルタ後: {total_after_filter}")
    lines.append(f"- 最終掲載記事数: 英語 {len(en_articles)}件 / 日本語 {len(ja_articles)}件")
    lines.append(f"- AI除外記事数: {len(excluded)}件")
    excluded_reasons = claude_result.get("excluded_reasons", "") if claude_result else ""
    if excluded_reasons:
        lines.append(f"- 除外理由: {excluded_reasons}")
    lines.append("")

    return "\n".join(lines)


# =============================================================================
# メイン
# =============================================================================

def collect_topic(topic_id: str, topic_config: dict, date_str: str) -> tuple[str, str]:
    """1つのトピックについて収集・分析・レポート生成を行う。(md_path, json_path) を返す。"""
    label = topic_config["label"]
    print(f"--- {topic_id} 収集開始 ---", file=sys.stderr)

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    since_ts = int(since.timestamp())

    all_articles = []

    # Google News RSS（英語）
    print("  Google News (EN)...", file=sys.stderr)
    for kw in topic_config.get("keywords_en", []):
        arts = fetch_google_news_rss(kw, "en", since)
        all_articles.extend(arts)
        print(f"    '{kw}' → {len(arts)}件", file=sys.stderr)

    # Google News RSS（日本語）
    print("  Google News (JA)...", file=sys.stderr)
    for kw in topic_config.get("keywords_ja", []):
        arts = fetch_google_news_rss(kw, "ja", since)
        all_articles.extend(arts)
        print(f"    '{kw}' → {len(arts)}件", file=sys.stderr)

    # 外部API
    for api in topic_config.get("apis", []):
        if api["type"] == "hackernews":
            print("  Hacker News...", file=sys.stderr)
            arts = fetch_hackernews(api["queries"], since_ts)
            all_articles.extend(arts)
            print(f"    → {len(arts)}件", file=sys.stderr)

    total_fetched = len(all_articles)
    print(f"  合計取得: {total_fetched}件", file=sys.stderr)

    # 日付フィルタ（Python で厳密に）
    all_articles = filter_by_date(all_articles, since)

    # 除外キーワード
    all_articles = filter_excluded_keywords(all_articles, topic_config.get("exclude_keywords", []))

    # 重複排除
    all_articles = deduplicate(all_articles)
    total_after_filter = len(all_articles)
    print(f"  フィルタ後: {total_after_filter}件", file=sys.stderr)

    # Claude で関連性判定 + 要約
    claude_result = None
    if all_articles:
        print("  Claude で分析中...", file=sys.stderr)
        claude_result = analyze_with_claude(all_articles, label)
        if claude_result:
            print(f"  → {len(claude_result.get('articles', []))}件を採用", file=sys.stderr)
        else:
            print("  → Claude 分析失敗（記事は要約なしで掲載）", file=sys.stderr)

    # マージ
    selected, excluded = merge_results(all_articles, claude_result)

    # ファイル出力
    output_dir = os.path.join(REPORTS_DIR, topic_id)
    os.makedirs(output_dir, exist_ok=True)

    # Markdown
    report = generate_report(label, selected, excluded, claude_result, date_str, total_fetched, total_after_filter)
    md_file = os.path.join(output_dir, f"{date_str}.md")
    with open(md_file, "w") as f:
        f.write(report)

    # JSON
    report_json = generate_report_json(
        topic_id, label, selected, excluded, claude_result, date_str, total_fetched, total_after_filter
    )
    json_file = os.path.join(output_dir, f"{date_str}.json")
    with open(json_file, "w") as f:
        json.dump(report_json, f, ensure_ascii=False, indent=2)

    print(f"--- {topic_id} 収集完了: {md_file} / {json_file} ---", file=sys.stderr)
    return md_file, json_file


def main():
    import argparse

    parser = argparse.ArgumentParser(description="ニュース日次収集")
    parser.add_argument("topic", help="トピック名 or 'all'（カンマ区切りで複数指定可）")
    parser.add_argument("--date", default=datetime.now(JST).strftime("%Y-%m-%d"), help="レポートの日付")
    args = parser.parse_args()

    with open(CONFIG_FILE) as f:
        config = json.load(f)

    topics = config["topics"]
    valid_topics = list(topics.keys())

    # 実行するトピックを決定（カンマ区切り対応）
    if args.topic == "all":
        target_topics = valid_topics
    else:
        target_topics = [t.strip() for t in args.topic.split(",")]
        for t in target_topics:
            if t not in topics:
                print(f"エラー: 不明なトピック '{t}'", file=sys.stderr)
                print(f"有効なトピック: {', '.join(valid_topics)}, all", file=sys.stderr)
                sys.exit(1)

    report_paths = []
    json_paths = []
    collected_topics = []
    for tid in target_topics:
        md_file, json_file = collect_topic(tid, topics[tid], args.date)
        report_paths.append(md_file)
        json_paths.append(json_file)
        collected_topics.append(tid)

    # GitHub Actions 用の出力
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a") as f:
            f.write(f"report-dir={REPORTS_DIR}\n")
            f.write(f"topics-collected={','.join(collected_topics)}\n")
            f.write("report-paths<<EOF\n")
            for p in report_paths:
                f.write(f"{p}\n")
            f.write("EOF\n")
            f.write("report-json-paths<<EOF\n")
            for p in json_paths:
                f.write(f"{p}\n")
            f.write("EOF\n")


if __name__ == "__main__":
    main()
