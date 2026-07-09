# 美酒館 マッコリ商品トラッカー (LINE GIFT)

LINE GIFT のストア「美酒館」から**マッコリ商品**を毎日自動で取得し、
新商品を検出してリスト化・Web公開するツールです。

- 対象ストア: https://mall.line.me/sb/d84e5e64 （美酒館 / shop_id `839465`）
- データ更新: **1日1回**（GitHub Actions が自動実行）
- 公開ページ: `https://<あなたのユーザー名>.github.io/<リポジトリ名>/`
- 追加ライブラリ不要（Python 標準ライブラリのみ）

## 取得できる情報（1商品あたり）

| 項目 | 説明 |
|------|------|
| 商品ID (`id`) | LINE GIFT の商品ID |
| 商品名 (`name_full`) | 完全な商品名（詳細ページから取得） |
| 詳細 (`description` / `spec`) | 商品説明・原材料・内容量・アルコール度数など |
| キャッチコピー (`catchphrase`) | 商品ページの見出し |
| 価格 (`price` / `regular_price`) | 販売価格・定価 |
| ブランド (`brand`) / カテゴリ (`category`) | |
| 在庫 (`in_stock`) / 状態 (`status`) | |
| 販売開始日 (`sale_start_date`) | ストアでの登録日 |
| **初回検出日 (`first_seen`)** | このツールが最初に見つけた日（＝新商品判定に使用） |
| 画像URL (`image_url`) / 商品URL (`url`) | |
| 年齢制限 (`age_limit`) / 送料 (`delivery_charge`) | |

新しく登録された商品は自動で追加され、公開ページで **NEW** バッジが付きます
（初回検出から7日間）。ストアから消えた商品は「販売終了」として履歴に残します。

## ファイル構成

```
line_gift_tracker/
├─ scraper.py               … 取得スクリプト本体
├─ docs/                    … GitHub Pages で公開するフォルダ
│  ├─ index.html            … 閲覧用ページ（検索・並べ替え・NEW表示）
│  ├─ products.json         … 商品データ（自動生成）
│  ├─ products.csv          … Excel等で開けるCSV（自動生成）
│  └─ last_run.json         … 実行履歴・新商品ログ（自動生成）
└─ .github/workflows/daily.yml … 毎日実行する自動化設定
```

## セットアップ（GitHub で自動運用する手順）

1. **GitHub で空のリポジトリを作成**（例: `bijoukan-makgeolli`）。

2. **この `line_gift_tracker` フォルダの中身をリポジトリに push** する：
   ```bash
   cd line_gift_tracker
   git init
   git add .
   git commit -m "init: LINE GIFT makgeolli tracker"
   git branch -M main
   git remote add origin https://github.com/<ユーザー名>/<リポジトリ名>.git
   git push -u origin main
   ```

3. **GitHub Pages を有効化**：
   リポジトリの `Settings → Pages` →
   Source を「Deploy from a branch」、Branch を `main` / フォルダを `/docs` に設定して保存。
   数分後に `https://<ユーザー名>.github.io/<リポジトリ名>/` で閲覧できます。

4. **自動更新はそのままでOK**：
   `.github/workflows/daily.yml` により毎日 09:00(JST) に自動取得します。
   すぐ試したい場合は `Actions` タブ → `daily-scrape` → `Run workflow` で手動実行できます。
   （Actions の書き込み権限は `Settings → Actions → General → Workflow permissions`
   で「Read and write permissions」になっていることを確認してください）

## 手元で実行する場合

```bash
cd line_gift_tracker
python scraper.py
# docs/index.html をブラウザで開けば確認できます
#   python -m http.server 8000 --directory docs  → http://localhost:8000
```

## 設定（環境変数で変更可）

| 変数 | 既定値 | 説明 |
|------|--------|------|
| `KEYWORD` | `マッコリ` | 抽出キーワード。空にすると全商品を対象 |
| `SHOP_ID` | `839465` | 対象ストアのID |
| `SHORT_PATH` | `d84e5e64` | ストアの短縮パス |
| `REFRESH_ALL` | （未設定） | `1` で全商品の詳細を毎回取り直す |

例: 全商品を対象にする → `KEYWORD= python scraper.py`

## 注意事項

- 個人的な情報収集の範囲での利用を想定しています。アクセス頻度は1日1回に抑えています。
- LINE GIFT 側のページ構造が変わると取得が失敗する場合があります。
  その際は `scraper.py` の抽出部分（`fetch_detail` / API URL）の調整が必要です。
