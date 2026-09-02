#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Download (copy) the front-end used by the demo page:
https://demo.joytest.org.cn/demo/t/xajtdxsnb_20260123/1

The output is written to ./mirror/ and can be served with a simple static
server (for example: python -m http.server 8000 --directory mirror).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "mirror"

ENTRY = "https://demo.joytest.org.cn/demo/t/xajtdxsnb_20260123/1"
BASE = "https://demo.joytest.org.cn"
CDN = "https://ucdn.joytest.org.cn/client/20260602"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
}
SEAT_HEADERS = {
    "X-Seat": "1",
    "X-Mode": "demo",
    "Accept": "application/json, text/plain, */*",
    "Referer": ENTRY,
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

def download(session: requests.Session, url: str, relpath: str, headers=None, force: bool = False) -> bool:
    path = OUT / relpath
    if path.exists() and path.stat().st_size > 0 and not force:
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        r = session.get(url, headers=headers, timeout=60)
    except Exception as e:
        print(f"  ERROR {url}: {e}")
        return False
    if r.status_code != 200:
        print(f"  MISS {r.status_code} {url}")
        return False
    path.write_bytes(r.content)
    print(f"  OK {len(r.content):>9} {relpath}")
    return True


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    s = requests.Session()
    s.headers.update(HEADERS)

    print("== Fetch entry page ==")
    r = s.get(ENTRY, timeout=60)
    r.raise_for_status()
    index_text = r.text
    # Rewrite remote CDN URLs to the local mirror paths.
    index_text = index_text.replace("https://ucdn.joytest.org.cn/client/20260602/", "/client/")
    index_text = index_text.replace(
        'window.ASSET_PATH ="https://ucdn.joytest.org.cn/client/20260602/"',
        'window.ASSET_PATH ="/client/"',
    )
    (OUT / "index.html").write_text(index_text, encoding="utf-8")
    print(f"  saved index.html ({len(index_text)} bytes)")

    print("== Fetch seat session ==")
    session_url = f"{BASE}/seat/session/?rng={int(time.time() * 1000)}"
    sr = s.get(session_url, headers=SEAT_HEADERS, timeout=60)
    print(f"  session status {sr.status_code}")
    if sr.status_code == 200:
        (OUT / "seat").mkdir(parents=True, exist_ok=True)
        (OUT / "seat" / "session.json").write_bytes(sr.content)
        sess = sr.json()
        session_id = sess.get("session", {}).get("id", "")
        skin = sess.get("session", {}).get("config", {}).get("skin", "")
        skin_md5 = sess.get("session", {}).get("config", {}).get("skin_md5", "")
        print(f"  session_id={session_id} skin={skin} md5={skin_md5}")

        if session_id:
            print("== Fetch seat CSS JSON ==")
            css_url = f"{BASE}/seat/css/{session_id}"
            cr = s.get(css_url, headers=SEAT_HEADERS, timeout=60)
            if cr.status_code == 200:
                (OUT / "seat" / "css").mkdir(parents=True, exist_ok=True)
                (OUT / "seat" / "css" / f"{session_id}.json").write_bytes(cr.content)
                print(f"  saved seat/css/{session_id}.json ({len(cr.content)} bytes)")
            else:
                print(f"  seat css status {cr.status_code}")

        if skin:
            print("== Fetch seat skin assets ==")
            skin_base = f"{BASE}/seat/skin/{skin}"
            skin_local = skin.replace(":", "_")
            skin_files = [
                "bg.jpg",
                "logo.png",
                "title.png",
                "notice.html",
                "css/basic.css",
                "images/bg.jpg",
                "images/intro/01.jpg",
                "images/intro/02.jpg",
                "images/intro/sc-area.jpg",
                "images/intro/mc-area.jpg",
                "js/jquery-1.10.2.min.js",
                "js/notice.js",
                "jt_custom.js",
            ]
            for name in skin_files:
                url = f"{skin_base}/{name}"
                if name == "jt_custom.js" and skin_md5:
                    url += f"?_version={skin_md5}"
                download(s, url, f"seat/skin/{skin_local}/{name}")
    else:
        print("  failed to get session; continuing with static resources only")

    print("== Fetch core Angular/CDN files ==")
    # Files explicitly referenced by the initial HTML.
    cdn_roots = [
        "favicon.ico",
        "runtime.39bc6e824872697d.js",
        "polyfills.6a5bac4e1539c87f.js",
        "scripts.7a7184ede54fefde.js",
        "main.eba826630a0f652a.js",
        "styles.6a0bebae203db4c5.css",
        "170.c58b077fac0855af.js",
        "410.4ed0becfa74e2ec6.js",
        "assets/imgs/app.icns",
        "assets/imgs/loading.gif",
        "assets/imgs/app-loading.gif",
        "assets/js/annotator/jquery-1.11.1.js",
        "assets/js/annotator/wgxpath.install.js",
        "assets/js/annotator/gettext.js",
        "assets/js/annotator/annotator-full.min.js",
        "assets/js/annotator/annotorious.okfn.js",
        "assets/js/annotator/locale/zh_CN/annotator.po",
        "assets/calc/basic.html",
        "assets/html/iframe_calculator.html",
        "assets/vendor/financial_calculator/finance-cal.html",
        "assets/imgs/item-type/hotspot.png",
        "assets/js/mathlive/formula.svg",
        "assets/i18n/zh.json",
        "assets/i18n/en.json",
    ]
    for rel in cdn_roots:
        download(s, f"{CDN}/{rel}", f"client/{rel}")

    print("== Fetch CSS-referenced assets ==")
    css_path = OUT / "client" / "styles.6a0bebae203db4c5.css"
    if css_path.exists():
        css_text = css_path.read_text(encoding="utf-8", errors="ignore")
        css_urls = set()
        for m in re.finditer(r"url\((?!data:)([^)]+)\)", css_text):
            u = m.group(1).strip().strip("\"'").split("?")[0].split("#")[0]
            if u and not u.startswith("data:"):
                css_urls.add(u)
        for rel in sorted(css_urls):
            download(s, f"{CDN}/{rel}", f"client/{rel}")

    print("== Fetch hashed image assets from main bundle ==")
    main_path = OUT / "client" / "main.eba826630a0f652a.js"
    if main_path.exists():
        main_text = main_path.read_text(encoding="utf-8", errors="ignore")
        hashed = sorted(set(re.findall(
            r"[A-Za-z0-9_./-]+?\.[0-9a-f]{16}\.(?:png|jpe?g|gif|svg|woff2?|ttf|eot|css|js|json|html|cur|ico)",
            main_text, re.I
        )))
        for rel in hashed:
            # These assets are referenced as /client/<name> at runtime, and are
            # also available on the CDN under client/<version>/<name>.
            download(s, f"{CDN}/{rel}", f"client/{rel}")

    print("== Fetch demo-relative client assets ==")
    demo_client_files = [
        "assets/i18n/zh.json",
        "assets/i18n/en.json",
        "assets/imgs/loading.gif",
        "assets/js/annotator/locale/zh_CN/annotator.po",
    ]
    # Some files are only reachable through demo.joytest.org.cn with the
    # session cookies (e.g. skin assets). They were already handled above.
    for rel in demo_client_files:
        download(s, f"{BASE}/client/{rel}", f"client/{rel}")

    print("== Generate simple README ==")
    (OUT / "README.md").write_text(
        "# 前端镜像\n\n"
        f"来源: {ENTRY}\n\n"
        "这是该页面加载的 Angular 前端静态资源副本，并包含登录后流程所用的接口快照。\n\n"
        "## 本地预览\n\n"
        "在仓库根目录运行：\n\n"
        "```bash\n"
        "python serve_mirror.py 8000\n"
        "```\n\n"
        "然后打开 http://localhost:8000/ ，服务会自动跳转到演示页路径。\n\n"
        "本地可以浏览登录、考生信息确认、考生须知、试卷说明、单元说明、正式答题和暂离锁屏等界面。\n\n"
        "说明：接口数据是抓取到的一次性快照，用于前端界面预览；真实考试动态数据、提交答案、时间控制等需要原始后端。\n",
        encoding="utf-8",
    )

    print("\nDone.")


if __name__ == "__main__":
    main()
