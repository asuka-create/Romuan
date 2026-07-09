#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LINE GIFT 商品画像 一括ダウンロード

docs/products.json に保存された各商品の画像URLを読み、
商品IDごとのフォルダを作ってすべての画像を保存する。

    images/
      8438633/
        8438633_1.jpg
        8438633_2.jpg
      8438632/
        ...

使い方:
    python download_images.py            # 未取得の画像だけダウンロード
    python download_images.py --force     # 既存も上書き再取得

出力先はローカルの images/ フォルダ（GitHubにはコミットしない）。
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_PATH = os.path.join(HERE, "docs", "products.json")
OUT_DIR = os.path.join(HERE, "images")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"
FORCE = "--force" in sys.argv


def ext_of(url):
    path = url.split("?")[0]
    for e in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.lower().endswith(e):
            return e
    return ".jpg"


def download(url, dest, retries=3):
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
            with open(dest, "wb") as f:
                f.write(data)
            return len(data)
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            if attempt == retries - 1:
                raise
            time.sleep(1.5 * (attempt + 1))


def main():
    with open(JSON_PATH, encoding="utf-8") as f:
        products = json.load(f)["products"]

    os.makedirs(OUT_DIR, exist_ok=True)
    total_imgs = total_bytes = skipped = failed = 0

    for p in products:
        pid = str(p["id"])
        imgs = p.get("images") or ([p["image_url"]] if p.get("image_url") else [])
        if not imgs:
            continue
        folder = os.path.join(OUT_DIR, pid)
        os.makedirs(folder, exist_ok=True)

        for i, url in enumerate(imgs, 1):
            dest = os.path.join(folder, f"{pid}_{i}{ext_of(url)}")
            if os.path.exists(dest) and not FORCE:
                skipped += 1
                continue
            try:
                n = download(url, dest)
                total_imgs += 1
                total_bytes += n
                print(f"  {pid}_{i}{ext_of(url)}  ({n//1024} KB)")
            except Exception as e:
                failed += 1
                print(f"  [失敗] {pid}_{i}: {e}", file=sys.stderr)
            time.sleep(0.1)

    print("\n--- 完了 ---")
    print(f"  ダウンロード: {total_imgs} 枚 ({total_bytes/1024/1024:.1f} MB)")
    print(f"  スキップ(既存): {skipped} 枚 / 失敗: {failed} 枚")
    print(f"  保存先: {OUT_DIR}")


if __name__ == "__main__":
    main()
