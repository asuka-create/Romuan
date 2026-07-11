#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE GIFT「美酒館」マッコリ商品トラッカー

mall.line.me のストアページ (LINE GIFT) から商品一覧を取得し、
「マッコリ」商品を抽出して docs/products.json / products.csv に保存する。

- 1日1回 GitHub Actions から実行される想定。
- 新しく登録された商品を検出し、初回検出日 (first_seen) を記録する。
- 追加ライブラリ不要（Python 標準ライブラリのみ）。

設定は環境変数でも上書き可能:
  SHOP_ID      : LINE GIFT のショップID   (default: 839465)
  SHORT_PATH   : ストアの短縮パス /sb/xxxx (default: d84e5e64)
  KEYWORD      : 抽出キーワード           (default: 空＝全商品 / 例: マッコリ)
  REFRESH_ALL  : "1" で全商品の詳細を毎回取り直す (default: 新規のみ)
"""

import csv
import json
import os
import re
import sys
import time
import html
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta

# ----------------------------------------------------------------------------
# 設定
# ----------------------------------------------------------------------------
SHOP_ID     = os.environ.get("SHOP_ID", "839465")
SHORT_PATH  = os.environ.get("SHORT_PATH", "d84e5e64")
KEYWORD     = os.environ.get("KEYWORD", "")   # 空＝全商品。"マッコリ" 等で絞り込み可
REFRESH_ALL = os.environ.get("REFRESH_ALL", "") == "1"

BASE        = "https://mall.line.me"
SEARCH_URL  = BASE + "/api/item/search"
SHOP_URL    = f"{BASE}/sb/{SHORT_PATH}"          # 商品ページの親URL
JST         = timezone(timedelta(hours=9))

# Supabase（販売登録の自動追加先）。公開キーはGitHub Pagesでも公開済み。
SB_URL   = os.environ.get("SUPABASE_URL", "https://qbfjeitlzrtazavklhde.supabase.co")
SB_KEY   = os.environ.get("SUPABASE_KEY", "sb_publishable_gCDMwBOWaSwq6uh34lWRXA_rYQ6VptD")
SB_SALES = SB_URL + "/rest/v1/romuan_sales"

HERE        = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR    = os.path.join(HERE, "docs")
JSON_PATH   = os.path.join(DOCS_DIR, "products.json")
CSV_PATH    = os.path.join(DOCS_DIR, "products.csv")
META_PATH   = os.path.join(DOCS_DIR, "last_run.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# 詳細ページの説明文セクションのうち、定型文（法令表記など）は除外する
BOILERPLATE_TITLE_KEYWORDS = [
    "酒類販売", "ご購入前", "ご一読", "返品", "交換", "キャンセル",
    "配送", "個人情報", "特定商取引",
]


# ----------------------------------------------------------------------------
# 低レベルユーティリティ
# ----------------------------------------------------------------------------
def fetch(url, is_json=False, retries=3):
    """URL を取得して文字列 (or dict) を返す。失敗時はリトライ。"""
    headers = {"User-Agent": UA, "Accept-Language": "ja,en;q=0.8"}
    if is_json:
        headers["X-Requested-With"] = "XMLHttpRequest"
    last_err = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw) if is_json else raw
        except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError) as e:
            last_err = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"取得失敗: {url} ({last_err})")


def clean_text(s):
    """HTMLタグを除去して空白を正規化する。"""
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t　]+", " ", s)
    s = re.sub(r"\s*\n\s*", "\n", s)
    return s.strip()


def fmt_date(ts):
    """UNIX秒 → JST の 'YYYY-MM-DD'。"""
    if not ts:
        return ""
    try:
        return datetime.fromtimestamp(int(ts), JST).strftime("%Y-%m-%d")
    except (ValueError, OSError):
        return ""


def today_jst():
    return datetime.now(JST).strftime("%Y-%m-%d")


# ----------------------------------------------------------------------------
# 一覧 API
# ----------------------------------------------------------------------------
def fetch_all_items():
    """検索APIを全ページ取得して item のリストを返す。"""
    items = []
    page = 1
    while True:
        url = f"{SEARCH_URL}?shop_id={SHOP_ID}&page={page}&sort=shop_priority"
        data = fetch(url, is_json=True)
        if data.get("code") != 200:
            raise RuntimeError(f"APIエラー: page={page} code={data.get('code')}")
        items.extend(data.get("items", []))
        last_page = data.get("last_page", page)
        if page >= last_page:
            break
        page += 1
        time.sleep(0.5)
    return items


# ----------------------------------------------------------------------------
# 詳細ページ
# ----------------------------------------------------------------------------
def fetch_detail(item_id):
    """商品詳細ページから 完全な商品名 / 詳細説明 / スペックを取得。"""
    url = f"{SHOP_URL}/{item_id}"
    h = fetch(url)

    # 完全な商品名（一覧APIの name は約30文字で切れているため）
    m = re.search(r'class="mdMN03Name"[^>]*>(.*?)</', h, re.S)
    full_name = clean_text(m.group(1)) if m else ""

    # 説明セクション（キャッチコピー = ttl, 本文 = txt）
    ttls = re.findall(r'class="mdMN44Ttl"[^>]*>(.*?)</[^>]+>', h, re.S)
    txts = re.findall(r'class="mdMN44Txt"[^>]*>(.*?)</p>', h, re.S)

    catchphrase = ""
    desc_parts = []
    spec = ""
    for ttl_raw, txt_raw in zip(ttls, txts):
        ttl = clean_text(ttl_raw)
        txt = clean_text(txt_raw)
        if any(k in ttl for k in BOILERPLATE_TITLE_KEYWORDS):
            continue
        if ("商品詳細" in ttl or "商品情報" in ttl or "保存方法" in ttl
                or "【商品情報】" in txt):
            spec = txt
            continue
        if not catchphrase:
            catchphrase = ttl
        if txt:
            desc_parts.append(txt)

    return {
        "name_full": full_name,
        "catchphrase": catchphrase,
        "description": "\n\n".join(desc_parts).strip(),
        "spec": spec,
        "detail_fetched": today_jst(),
    }


# ----------------------------------------------------------------------------
# 商品レコードの組み立て
# ----------------------------------------------------------------------------
def build_record(item):
    """一覧APIの item から保存用の基本レコードを作る。"""
    brand = item.get("brand") or {}
    itype = item.get("type") or {}
    images = item.get("images") or []
    # 全画像URL（表示用の image_url を優先。無ければ他の解像度）
    image_list = []
    for im in images:
        u = im.get("image_url") or im.get("fullscreen_url") or im.get("square_url")
        if u:
            image_list.append(u)
    image_url = image_list[0] if image_list else ""
    pid = str(item.get("id"))
    return {
        "id": pid,
        "name": item.get("name", ""),          # 一覧APIの名前（省略あり）
        "name_full": "",                        # 詳細ページで補完
        "brand": brand.get("name", ""),
        "category": itype.get("group_2", ""),
        "is_liquor": bool(itype.get("is_liquor")),
        "price": item.get("price"),
        "regular_price": item.get("regular_price"),
        "in_stock": bool(item.get("has_stock")),
        "status": item.get("status", ""),
        "sale_start_date": fmt_date(item.get("sold_on")),  # 販売開始日（ストア登録日）
        "age_limit": item.get("option_age_limit", 0),
        "delivery_charge": item.get("delivery_charge"),
        "url": f"{SHOP_URL}/{pid}",
        "image_url": image_url,
        "images": image_list,
        "catchphrase": "",
        "description": "",
        "spec": "",
    }


def load_existing():
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return {p["id"]: p for p in data.get("products", [])}
    return {}


# ----------------------------------------------------------------------------
# Supabase: 新しいLINE商品を販売登録(romuan_sales)へ自動追加
# ----------------------------------------------------------------------------
def sb_request(method, path, body=None):
    headers = {
        "apikey": SB_KEY, "Authorization": "Bearer " + SB_KEY,
        "Content-Type": "application/json",
    }
    if body is not None:
        headers["Prefer"] = "return=minimal"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw.strip() else None


def sync_sales(products):
    """LINEの現行商品のうち、まだ販売登録が無いものをSupabaseへ自動追加する。"""
    try:
        existing = sb_request("GET", SB_SALES + "?select=id,line_id,position") or []
    except Exception as e:
        print(f"  [警告] Supabase読込失敗、販売登録の自動追加をスキップ: {e}", file=sys.stderr)
        return

    have = {str(s.get("line_id")) for s in existing if s.get("line_id")}
    max_num = max_pos = 0
    for s in existing:
        m = re.match(r"SALE(\d+)$", s.get("id", "") or "")
        if m:
            max_num = max(max_num, int(m.group(1)))
        max_pos = max(max_pos, s.get("position") or 0)

    new_rows = []
    for p in products:
        pid = str(p["id"])
        if pid in have:
            continue
        max_num += 1
        max_pos += 1
        new_rows.append({
            "id": f"SALE{max_num:04d}",
            "name": p.get("name_full") or p.get("name") or "",
            "line_id": pid,
            "items": [],
            "channels": {
                "linegift": {"listed": True, "id": pid, "url": f"{SHOP_URL}/{pid}"},
                "yahoo":    {"listed": False, "id": "", "url": ""},
                "shopify":  {"listed": False, "id": "", "url": ""},
            },
            "memo": "",
            "position": max_pos,
        })

    if new_rows:
        try:
            sb_request("POST", SB_SALES, new_rows)
            for r in new_rows:
                print(f"    + 販売登録 追加 {r['id']}  {r['name'][:30]}")
        except Exception as e:
            print(f"  [警告] 販売登録の追加に失敗: {e}", file=sys.stderr)
            return
    print(f"  販売登録 自動追加: {len(new_rows)} 件")


# ----------------------------------------------------------------------------
# メイン
# ----------------------------------------------------------------------------
def main():
    run_date = today_jst()
    print(f"[{run_date}] LINE GIFT トラッカー実行  shop_id={SHOP_ID} keyword={KEYWORD!r}")

    existing = load_existing()
    items = fetch_all_items()
    print(f"  ストア全商品: {len(items)} 件")

    # キーワード抽出
    matched = []
    for it in items:
        name = it.get("name", "")
        if KEYWORD and KEYWORD not in name:
            continue
        matched.append(it)
    print(f"  抽出対象({KEYWORD or '全商品'}): {len(matched)} 件")

    current_ids = set()
    new_products = []
    result = {}

    for it in matched:
        rec = build_record(it)
        pid = rec["id"]
        current_ids.add(pid)
        prev = existing.get(pid)

        if prev:
            # 既存商品: first_seen を引き継ぎ、変動値を更新
            rec["first_seen"] = prev.get("first_seen", run_date)
            # 詳細は前回分を引き継ぐ（REFRESH_ALL 時は取り直す）
            for k in ("name_full", "catchphrase", "description", "spec"):
                rec[k] = prev.get(k, "")
            need_detail = REFRESH_ALL or not rec.get("name_full")
        else:
            # 新規商品
            rec["first_seen"] = run_date
            new_products.append({"id": pid, "name": rec["name"]})
            need_detail = True

        if need_detail:
            try:
                detail = fetch_detail(pid)
                rec.update(detail)
                print(f"    詳細取得: {pid}  {detail['name_full'][:30]}")
                time.sleep(0.7)
            except Exception as e:
                print(f"    [警告] 詳細取得失敗 {pid}: {e}", file=sys.stderr)

        rec["available"] = True
        rec["last_seen"] = run_date
        result[pid] = rec

    # 一覧から消えた（販売終了とみられる）過去の商品も履歴として残す
    removed = []
    for pid, prev in existing.items():
        if pid not in current_ids:
            prev["available"] = False
            result[pid] = prev
            removed.append({"id": pid, "name": prev.get("name", "")})

    # 並び順: 販売中を先に、初回検出日の新しい順 → 販売開始日の新しい順
    products = sorted(
        result.values(),
        key=lambda p: (
            not p.get("available", True),
            p.get("first_seen", ""),
            p.get("sale_start_date", ""),
        ),
        reverse=False,
    )
    products.sort(
        key=lambda p: (p.get("first_seen", ""), p.get("sale_start_date", "")),
        reverse=True,
    )
    products.sort(key=lambda p: not p.get("available", True))

    payload = {
        "shop_name": "美酒館 (LINE GIFT)",
        "shop_url": SHOP_URL,
        "keyword": KEYWORD,
        "updated_at": datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST"),
        "total": len([p for p in products if p.get("available", True)]),
        "products": products,
    }

    os.makedirs(DOCS_DIR, exist_ok=True)
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    write_csv(products)
    write_meta(run_date, new_products, removed, len(matched))

    # 新しいLINE商品を販売登録へ自動追加（掲載中のものだけ）
    sync_sales([p for p in products if p.get("available", True)])

    print(f"  新規: {len(new_products)} 件 / 販売終了: {len(removed)} 件")
    if new_products:
        for np in new_products:
            print(f"    + NEW {np['id']}  {np['name']}")
    print(f"  保存完了 -> {JSON_PATH}")


def write_csv(products):
    cols = [
        ("id", "商品ID"), ("name_full", "商品名"), ("brand", "ブランド"),
        ("category", "カテゴリ"), ("price", "価格"), ("regular_price", "定価"),
        ("in_stock", "在庫"), ("sale_start_date", "販売開始日"),
        ("first_seen", "初回検出日"), ("catchphrase", "キャッチコピー"),
        ("description", "詳細"), ("spec", "商品情報"),
        ("available", "掲載中"), ("url", "URL"),
    ]
    with open(CSV_PATH, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow([c[1] for c in cols])
        for p in products:
            row = []
            for key, _ in cols:
                v = p.get(key, "")
                if isinstance(v, str):
                    v = v.replace("\n", " ")
                row.append(v)
            w.writerow(row)


def write_meta(run_date, new_products, removed, matched_count):
    meta = {}
    if os.path.exists(META_PATH):
        try:
            with open(META_PATH, encoding="utf-8") as f:
                meta = json.load(f)
        except json.JSONDecodeError:
            meta = {}
    runs = meta.get("runs", [])
    runs.insert(0, {
        "date": run_date,
        "matched": matched_count,
        "new": new_products,
        "removed": removed,
    })
    meta["runs"] = runs[:60]  # 直近60回分の履歴を保持
    meta["last_run"] = run_date
    with open(META_PATH, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
