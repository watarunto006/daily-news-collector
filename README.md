# daily-news

毎朝実行する DID/VC・AI ニュース日次収集ツール。

## 仕組み

```
1. Google News RSS + 外部API (Hacker News等) から記事を収集  ... Python
2. 公開日で厳密にフィルタ（過去24時間以内のみ）              ... Python
3. タイトル類似度で重複排除                                  ... Python
4. Claude で関連性判定 + 日本語要約                           ... LLM
5. Markdown レポート生成                                     ... Python
```

URL・公開日・出力フォーマットはすべて Python が保証する。Claude は関連性の判定と要約のみを担当する。

## GitHub Action として使う

Private リポジトリに workflow ファイルを1つ置くだけで利用できる。

### 最小構成

```yaml
name: Daily News
on:
  schedule:
    - cron: '0 0 * * 1-5'  # JST 9:00（平日のみ）
  workflow_dispatch:

jobs:
  collect:
    runs-on: ubuntu-latest
    steps:
      - uses: watarunto006/daily-news-collector@v1
        id: news
        with:
          topics: 'ai-dev,did-vc'
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
```

### Slack 通知と組み合わせる

```yaml
      - uses: watarunto006/daily-news-collector@v1
        id: news
        with:
          topics: 'ai-dev'
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}

      - name: Read report
        id: report
        run: |
          # 最初のレポートファイルを読む
          REPORT_FILE=$(echo "${{ steps.news.outputs.report-paths }}" | head -1)
          echo "content<<EOF" >> $GITHUB_OUTPUT
          cat "$REPORT_FILE" >> $GITHUB_OUTPUT
          echo "EOF" >> $GITHUB_OUTPUT

      - uses: slackapi/slack-github-action@v2
        with:
          webhook-url: ${{ secrets.SLACK_WEBHOOK_URL }}
          payload: |
            {"text": "${{ steps.report.outputs.content }}"}
```

### Action の入力

| input | required | default | 説明 |
|---|---|---|---|
| `topics` | false | `all` | カンマ区切りのトピックID |
| `anthropic_api_key` | true | - | Anthropic API Key |
| `model` | false | `claude-sonnet-4-6` | Claude モデルID |
| `config` | false | - | カスタム config.json のパス |
| `date` | false | - | レポート日付（YYYY-MM-DD） |

### Action の出力

| output | 説明 |
|---|---|
| `report-paths` | 生成されたレポートファイルパス（改行区切り）|
| `report-dir` | レポート出力ディレクトリ |
| `topics-collected` | 収集したトピックID（カンマ区切り）|

### カスタム config を使う

リポジトリに独自の `config.json` を置いて、トピックやキーワードをカスタマイズできる。

```yaml
      - uses: actions/checkout@v4
      - uses: watarunto006/daily-news-collector@v1
        with:
          anthropic_api_key: ${{ secrets.ANTHROPIC_API_KEY }}
          config: './my-config.json'
```

## API モード（ローカル実行）

`ANTHROPIC_API_KEY` 環境変数を設定すると、`claude -p` の代わりに Anthropic Messages API を直接呼ぶ。Claude Code が不要になる。

```bash
pip install -r requirements.txt
ANTHROPIC_API_KEY=sk-ant-xxx python3 collect.py ai-dev
```

モデルは `CLAUDE_MODEL` 環境変数で変更可能（デフォルト: `claude-sonnet-4-6`）。

## セットアップ（ローカル CLI モード）

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

| モード | 必要なもの |
|---|---|
| CLI モード（ローカル） | Python 3.11+, [Claude Code](https://docs.anthropic.com/en/docs/claude-code) |
| API モード（ローカル） | Python 3.11+, `pip install -r requirements.txt`, `ANTHROPIC_API_KEY` |
| GitHub Action | `ANTHROPIC_API_KEY`（secrets に設定） |

## 使い方

```bash
# 1トピック実行
python3 collect.py did-vc
python3 collect.py ai-dev
python3 collect.py ai-business
python3 collect.py ai-research

# カンマ区切りで複数トピック
python3 collect.py ai-dev,did-vc

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
├── action.yml            # GitHub Action 定義
├── requirements.txt      # Python 依存（API モード / Action 用）
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

`prompt_template.md` を編集する。このプロンプトは Claude に渡され、記事リスト（JSON）の関連性判定と要約を行う。出力は JSON 形式で受け取り、Python 側でレポートに整形する。

## 制約事項

- Google News RSS の URL はリダイレクトURL（`news.google.com/rss/articles/...`）。クリックすると実際の記事ページに遷移する
- Claude の関連性判定は LLM の判断に依存するため、稀に誤分類が起きる。ただし URL・日付・フォーマットには影響しない
- Google News RSS は過去24時間のフィルタ（`when:1d`）を使用しているが、Python 側でも公開日を厳密に検証している
