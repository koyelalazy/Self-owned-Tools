"""Refresh chiikawa/catalog.json from the regional-goods list on jp-api.com.

Only adds items: existing entries (and their hand-set locations) are never changed or removed.
New items get a location guessed from the name; anything not guessed stays blank and is
set in the app. Standard library only, so the GitHub Action needs no installs.
"""
import json
import re
import sys
import time
import urllib.request
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

CATALOG = Path(__file__).with_name("catalog.json")
LIST_URL = "https://www.jp-api.com/contents/NOD62/PGE{}/"
DOMAIN = "https://www.jp-api.com"
MAX_PAGES = 40

# Japanese place word in the item name -> location label used in the app.
LOC_HINTS = {
    "北海道": "北海道", "小樽": "北海道", "札幌": "札幌", "函館": "函館", "富良野": "北海道",
    "稚内": "北海道", "利尻": "北海道", "礼文": "北海道", "旭川": "北海道", "釧路": "北海道", "知床": "北海道",
    "青森": "青森", "ねぶた": "青森", "秋田": "秋田", "竿灯": "秋田", "岩手": "岩手", "わんこそば": "岩手",
    "仙台": "宮城", "宮城": "宮城", "山形": "山形", "福島": "福島", "会津": "福島",
    "東京": "東京", "浅草": "東京", "雷門": "雷門", "スカイツリー": "東京", "横浜": "橫濱", "鎌倉": "鎌倉",
    "箱根": "箱根", "大涌谷": "大涌谷", "日光": "日光", "栃木": "栃木", "足利": "足利", "群馬": "群馬",
    "草津": "群馬", "茨城": "茨城", "水戸": "茨城", "千葉": "千葉", "埼玉": "埼玉", "深谷": "埼玉",
    "新潟": "新潟", "富山": "富山", "金沢": "石川", "石川": "石川", "福井": "福井", "山梨": "山梨",
    "信州": "信州", "長野": "信州", "飛騨": "飛驒", "高山": "飛驒", "静岡": "靜岡", "富士山": "富士山",
    "名古屋": "名古屋", "三重": "三重", "伊勢": "三重",
    "京都": "京都", "伏見稲荷": "京都", "金閣": "金閣寺", "清水": "清水", "嵐山": "京都", "滋賀": "滋賀",
    "大阪": "大阪", "道頓堀": "大阪", "通天閣": "大阪", "たこ焼": "大阪", "神戸": "神戶", "姫路": "姬路",
    "淡路": "淡路", "奈良": "奈良", "和歌山": "和歌山",
    "広島": "広島", "宮島": "宮島", "尾道": "尾道", "岡山": "岡山", "倉敷": "岡山", "島根": "島根",
    "出雲": "島根", "鳥取": "山陰", "山口": "山口", "瀬戸内": "瀨戶內",
    "香川": "香川", "讃岐": "香川", "うどん": "香川", "愛媛": "愛媛", "道後": "愛媛", "高知": "高知",
    "鳴門": "鳴門",
    "福岡": "福岡", "博多": "福岡", "北九州": "北九州", "長崎": "長崎", "佐賀": "佐賀", "熊本": "熊本",
    "大分": "大分", "別府": "大分", "宮崎": "宮崎", "鹿児島": "鹿児島", "奄美": "奄美",
    "沖縄": "沖繩", "首里": "沖繩",
    "香港": "香港", "マカオ": "澳門", "台湾": "台灣", "ソウル": "韓國", "韓国": "韓國", "オーストラリア": "澳洲",
}


def guess_loc(name):
    for word, loc in LOC_HINTS.items():
        if word in name:
            return loc
    return ""


def guess_cat(name):
    if "ダイカットキーホルダー" in name:
        return "keyholder"
    if "ぬいぐるみ" in name or "マスコット" in name:
        return "plush"
    if "ソックス" in name or "靴下" in name:
        return "socks"
    return "other"


class ListParser(HTMLParser):
    """Collects item links: any <a> whose href (or inner <img src>) points at a tphoto_<id> image.
    The name comes from the link's title, falling back to the <img alt>. Class names are not relied on,
    since the site's markup has changed before."""

    def __init__(self):
        super().__init__()
        self.items = []
        self.open = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a":
            self.open = {"name": fix_text(a.get("title") or "").strip(), "href": a.get("href") or ""}
        elif tag == "img" and self.open is not None:
            if not self.open["name"]:
                self.open["name"] = fix_text(a.get("alt") or "").strip()
            if "tphoto_" not in self.open["href"]:
                self.open["href"] = a.get("src") or self.open["href"]

    def handle_endtag(self, tag):
        if tag == "a" and self.open is not None:
            if self.open["name"] and "tphoto_" in self.open["href"]:
                self.items.append(self.open)
            self.open = None


def fix_text(s):
    """Pages are read as latin-1 so no byte is lost, then each name is decoded on its own:
    the site has served pages whose markup and item names use different encodings."""
    try:
        raw = s.encode("latin-1")
    except UnicodeEncodeError:  # an HTML entity already produced real characters
        return s
    for enc in ("utf-8", "cp932", "euc_jp"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (self-owned-tools catalog refresh)"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("latin-1")


def crawl():
    found, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        try:
            html = fetch(LIST_URL.format(page))
        except Exception as e:  # a missing page past the end shows up as 404
            print(f"page {page}: {e}")
            break
        p = ListParser()
        p.feed(html)
        fresh = [i for i in p.items if i["href"] not in seen]
        if not fresh:  # past the last page the site may repeat a page instead of 404ing
            break
        seen.update(i["href"] for i in fresh)
        found.extend(fresh)
        time.sleep(1)
    return found


def write_catalog(cat):
    lines = [json.dumps(i, ensure_ascii=False) for i in cat["items"]]
    head = json.dumps({k: v for k, v in cat.items() if k != "items"}, ensure_ascii=False)[:-1]
    CATALOG.write_text(head + ',"items":[\n' + ",\n".join(lines) + "\n]}\n", encoding="utf-8")


def main():
    cat = json.loads(CATALOG.read_text(encoding="utf-8"))
    # Drop crawled names that failed to decode so this run adds them again.
    cat["items"] = [i for i in cat["items"] if not (i.get("added") and "\ufffd" in i["name"])]
    known = {i["id"] for i in cat["items"]}
    added = []
    for raw in crawl():
        img = raw["href"] if raw["href"].startswith("http") else DOMAIN + raw["href"]
        m = re.search(r"tphoto_(\d+)_", img)
        if not m or m.group(1) in known:
            continue
        known.add(m.group(1))
        added.append({"id": m.group(1), "name": raw["name"], "loc": guess_loc(raw["name"]),
                      "cat": guess_cat(raw["name"]), "img": img, "added": date.today().isoformat()})
    if added:
        cat["items"] = sorted(added + cat["items"], key=lambda i: -int(i["id"]))
        cat["updated"] = date.today().isoformat()
    write_catalog(cat)
    print(f"{len(added)} new item(s)")
    for i in added:
        print(" ", i["id"], i["name"], i["loc"] or "(no location)")


if __name__ == "__main__":
    sys.exit(main())
