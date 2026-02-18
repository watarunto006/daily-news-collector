# daily-news

毎朝実行する DID/VC・AI ニュース日次収集ツール。

## 仕組み

```
1. Google News RSS + 外部API (Hacker News等) から記事を収集  ... Python
2. 公開日で厳密にフィルタ（過去24時間以内のみ）              ... Python
3. タイトル類似度で重複排除                                  ... Python
4. Claude Code (claude -p) で関連性判定 + 日本語要約          ... LLM
5. Markdown レポート生成                                     ... Python
```

URL・公開日・出力フォーマットはすべて Python が保証する。Claude は関連性の判定と要約のみを担当する。

## セットアップ

### Python のインストール

**macOS** (Homebrew):

```bash
brew install python
```

**macOS** (公式インストーラ):

https://www.python.org/downloads/ からダウンロードしてインストール。

**Ubuntu / Debian**:

```bash
sudo apt update && sudo apt install python3
```

インストール確認:

```bash
python3 --version  # 3.11 以上
```

### Claude Code のインストール

```bash
npm install -g @anthropic-ai/claude-code
```

初回起動時に認証を完了させる:

```bash
claude
```

詳細: https://docs.anthropic.com/en/docs/claude-code

### このリポジトリのクローン

```bash
git clone https://github.com/watarunto006/daily-news-collector.git
cd daily-news-collector
```

## 前提条件

- Python 3.11+
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) がインストール済みで認証済みであること

外部ライブラリは不要（Python 標準ライブラリのみ使用）。

## 使い方

```bash
# 1トピック実行
python3 collect.py did-vc
python3 collect.py ai-dev
python3 collect.py ai-business
python3 collect.py ai-research

# 全トピック一括実行
python3 collect.py all

# 日付を指定（レポートのファイル名に使用）
python3 collect.py ai-dev --date 2026-02-18
```

レポートは `reports/<topic>/<date>.md` に出力される。

## ファイル構成

```
daily-news/
├── README.md
├── collect.py            # メインスクリプト
├── config.json           # トピック定義（検索キーワード、APIソース）
├── prompt_template.md    # Claude に渡すプロンプト（関連性判定+要約用）
└── reports/
    ├── did-vc/
    │   └── 2026-02-18.md
    ├── ai-business/
    ├── ai-dev/
    └── ai-research/
```

## トピックのカスタマイズ

`config.json` を編集する。

### トピックを追加する

```json
{
  "topics": {
    "web3": {
      "label": "Web3 ニュースダイジェスト",
      "keywords_en": ["Web3", "blockchain application"],
      "keywords_ja": ["Web3 ブロックチェーン"],
      "exclude_keywords": ["bitcoin price"],
      "apis": []
    }
  }
}
```

### 検索キーワードを変更する

`keywords_en` / `keywords_ja` 配列を編集する。各キーワードが Google News RSS の検索クエリとして使用される。

### 除外キーワードを設定する

`exclude_keywords` に設定した文字列がタイトルに含まれる記事は自動除外される（大文字小文字区別なし）。

## APIソースの追加

`config.json` の各トピックの `apis` 配列で、外部APIからの記事取得を設定できる。

### Hacker News

```json
{
  "apis": [
    {
      "type": "hackernews",
      "queries": ["LLM", "AI agent"]
    }
  ]
}
```

[Hacker News Algolia API](https://hn.algolia.com/api) を使用し、過去24時間の記事をクエリごとに最大10件取得する。

### 新しいAPIソースタイプを追加する

`collect.py` 内の `fetch_hackernews()` を参考に、新しい取得関数を追加する。関数は `list[dict]` を返し、各 dict に `title`, `url`, `published`, `source`, `lang` を含める。

## Claude プロンプトのカスタマイズ

`prompt_template.md` を編集する。このプロンプトは `claude -p` に渡され、記事リスト（JSON）の関連性判定と要約を行う。出力は JSON 形式で受け取り、Python 側でレポートに整形する。

## 制約事項

- Google News RSS の URL はリダイレクトURL（`news.google.com/rss/articles/...`）。クリックすると実際の記事ページに遷移する
- Claude の関連性判定は LLM の判断に依存するため、稀に誤分類が起きる。ただし URL・日付・フォーマットには影響しない
- Google News RSS は過去24時間のフィルタ（`when:1d`）を使用しているが、Python 側でも公開日を厳密に検証している
