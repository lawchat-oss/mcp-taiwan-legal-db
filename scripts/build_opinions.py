#!/usr/bin/env python3
"""重建意見書全文資料包：mcp_server/data/opinions.zip（維護者離線執行）。

釋字 401 號以後與全部憲判字的意見書，官網頁面只列附件標題，全文在 PDF 附件。
本腳本逐案讀取頁面的意見書附件、下載 PDF、擷取文字，寫入 opinions.zip
（每案一個 member：old/<號次>.json、new/<年>_<號次>.json，內含每份意見書的標題、網址、全文），並在
old_cases.json / new_cases.json 補上 has_opinions 與 opinion_documents。

只收大法官意見書（排除鑑定人、學者、法庭之友、聲請人、機關等提交的「意見書」）。
同一案若同時有「抄本」合訂本（含解釋文、理由書、聲請書）與單份意見書，只取單份；
只有抄本的案件才取抄本，但若網頁本身已有意見書內文（如釋字 499 號），保留網頁內文不用抄本。部分 PDF 的字型缺少字元對照表（或頁面是圖片），抽不出文字；這類改用
scripts/opinion_transcripts/<網址 sha1>.txt 的人工／影像轉錄稿，並標 transcribed=true。
沒有轉錄稿的仍只保留附件連結（chars=0）。

Usage (repo root):
    uv run --no-project --with httpx --with pypdf python scripts/build_opinions.py [--cache DIR]

頁面清單與 PDF 快取在 --cache（預設 .cache/opinions），中斷後可續跑。
對 cons.judicial.gov.tw 首次完整執行約需數十分鐘。
"""
from __future__ import annotations

import argparse
import hashlib
import html
import io
import json
import re
import time
import unicodedata
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import httpx

DATA = Path(__file__).resolve().parent.parent / "mcp_server" / "data"
TRANSCRIPTS = Path(__file__).resolve().parent / "opinion_transcripts"
UA = "mcp-taiwan-legal-db data build (github.com/lawchat-oss/mcp-taiwan-legal-db)"
CJK = "\u3000-\u303f\u4e00-\u9fff\uff00-\uffef"  # CJK punctuation, ideographs, fullwidth forms
# 標題含「意見書」但不是大法官意見書的附件（鑑定、法庭之友、聲請、機關陳述等）
NOT_JUSTICE = re.compile(r"鑑定|法庭之友|聲請|陳述|相關機關|教授|研究員|律師|醫師|監察院|財政部|政府|基金會|聯盟|協會|公會|研究會|函|簡報|補充|辯論|諮詢|君")
# 相容漢字（U+F900 起，Big5 轉出的 PDF 常見）與康熙部首（Word 轉出的 PDF 常見）外觀同一般漢字但編碼不同，
# 不轉換會讓「法律」等關鍵字搜不到。只轉這些區段，不做整體 NFKC，以免全形標點被改成半形。
COMPAT_HAN = re.compile("[\u2e80-\u2fdf\uf900-\ufaff\U0002f800-\U0002fa1f]")
# 標題中的大法官姓名有兩種寫法：「許大法官宗力」（姓 + 大法官 + 名）與「蔡宗珍大法官」（全名 + 大法官）
JUSTICE_SPLIT_NAME = re.compile(
    r"([\u4e00-\u9fff])大法官([\u4e00-\u9fff]{1,2}?)(?=提出|加入|共同|協同|部分|一部|不同|意見|、|，|之|（|\(|）|\)|\.|$)"
)
JUSTICE_FULL_NAME = re.compile(r"([\u4e00-\u9fff]{2,3})大法官(?=提出|加入|、|，|）|\)|$)")
OPINION_TYPE = re.compile(r"(部分不同部分協同|部分協同部分不同|部分協同|部分不同|一部不同|協同|不同)意見書")
# 字型無法解碼時抽出的是古木基、僧伽羅、希臘等不相干文字。不用中文字比例判斷：註腳大量引日、英文法條的
# 意見書（釋字 777 號吳陳鐶）會被誤判，正文亂碼但註腳可讀的（釋字 714 號陳新民、陳春生）又會漏判。
EXPECTED_LETTERS = re.compile(r"[\x00-ſ぀-ヿ㐀-䶿一-鿿＀-￯]")
MAX_GARBLED_RATIO = 0.1  # 正常文件（含 OCR 雜訊）最高約 0.015，亂碼文件最低約 0.3


def is_garbled(text: str) -> bool:
    odd = sum(1 for c in text if re.match("L|M|Cn", unicodedata.category(c)) and not EXPECTED_LETTERS.match(c))
    return odd > MAX_GARBLED_RATIO * len(text)


def clean_pdf_text(raw: str) -> str:
    """把 PDF 排版換行接回段落：縮排開頭的行才是新段落；去掉頁碼行與中文間的多餘空白；相容漢字轉標準漢字。"""
    paras: list[str] = []
    for line in raw.splitlines():
        if not line.strip() or re.fullmatch(r"\s*[-－]?\s*\d{1,3}\s*[-－]?\s*", line):
            continue
        if not paras or re.match("^(\\s{2,}|\u3000)", line):
            paras.append(line.strip())
        else:
            paras[-1] += line.strip()
    text = COMPAT_HAN.sub(lambda m: unicodedata.normalize("NFKC", m.group()), "\n".join(paras))
    return re.sub(rf"(?<=[{CJK}])[ \t]+|[ \t]+(?=[{CJK}])", "", text)


def parse_opinion_title(title: str) -> dict:
    """從標題拆出提出者、加入者與意見書類型；早期標題沒寫姓名時為空清單／None。

    >>> parse_opinion_title("許大法官玉秀提出，林大法官子儀、許大法官宗力加入之部分不同意見書")
    {'authors': ['許玉秀'], 'joined': ['林子儀', '許宗力'], 'type': '部分不同'}
    """
    def names(part: str) -> list[str]:
        part = re.sub(r"^[\d.]*|^.*?判決", "", part)  # 去掉號次與「…判決」前綴，免得被當成全名的一部分
        return ["".join(m) for m in JUSTICE_SPLIT_NAME.findall(part)] or JUSTICE_FULL_NAME.findall(part)

    head, sep, tail = title.partition("提出")
    authors = names(head if sep else title)
    joined = names(tail.rsplit("加入", 1)[0]) if "加入" in tail else []
    kind = OPINION_TYPE.search(title)
    return {"authors": authors, "joined": joined, "type": kind.group(1) if kind else None}


def page_attachments(page: str, url: str) -> list[dict]:
    """頁面上所有下載附件的標題與網址。"""
    atts: list[dict] = []
    for href, label in re.findall(r'<a[^>]+href="([^"]*download[^"]*)"[^>]*>(.*?)</a>', page, re.I | re.S):
        link = urljoin(url, html.unescape(href))
        if all(a["url"] != link for a in atts):
            atts.append({"title": re.sub(r"<[^>]+>|\s+", " ", html.unescape(label)).strip(), "url": link})
    return atts


def opinion_documents(attachments: list[dict], require_justice: bool = False) -> list[dict]:
    """挑出大法官意見書附件；有單份意見書就不取抄本合訂本。

    憲判字（require_justice=True）的大法官意見書標題一律含「大法官」；舊制早期少數
    意見書標題沒有（如「387意見書」），改以排除非大法官文件的關鍵字判斷。
    """
    atts = [
        a for a in attachments
        if "意見書" in a["title"] and "打包" not in a["title"]
        and (
            "大法官" in a["title"]
            # 抄本是法院的合訂本（標題會列出內含聲請書等），不套用排除清單
            or (not require_justice and ("抄本" in a["title"] or not NOT_JUSTICE.search(a["title"])))
        )
    ]
    return [a for a in atts if "抄本" not in a["title"]] or atts


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path(".cache/opinions"))
    args = ap.parse_args()
    from pypdf import PdfReader  # build-time only; not a runtime dependency

    def attachments(entry: dict) -> list[dict]:
        return [{"title": a.get("title") or a.get("label", ""), "url": a["url"]} for a in entry.get("attachments", [])]

    (args.cache / "pdf").mkdir(parents=True, exist_ok=True)
    client = httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": UA})

    datasets = {kind: json.loads((DATA / f"{kind}_cases.json").read_text(encoding="utf-8")) for kind in ("old", "new")}

    # 1. 頁面 → 意見書附件清單（快取於 scan.json）
    scan_path = args.cache / "scan.json"
    scan = json.loads(scan_path.read_text()) if scan_path.exists() else {}
    for kind, cases in datasets.items():
        for key, case in cases.items():
            cid = f"{kind}:{key}"
            if not case.get("source_url") or cid in scan:
                continue
            r = client.get(case["source_url"])
            if r.status_code != 200 or "docdata.aspx" not in str(r.url):
                print(f"page fetch failed, will retry next run: {cid} HTTP {r.status_code}")
                time.sleep(3)
                continue
            scan[cid] = {"url": case["source_url"], "attachments": page_attachments(r.text, case["source_url"])}
            scan_path.write_text(json.dumps(scan, ensure_ascii=False))
            time.sleep(0.7)

    # 2. 下載 PDF（3 條連線，快取於 pdf/<sha1>.bin）
    def url_hash(url: str) -> str:
        return hashlib.sha1(url.encode()).hexdigest()

    def pdf_path(url: str) -> Path:
        return args.cache / "pdf" / (url_hash(url) + ".bin")

    def download(url: str) -> None:
        p = pdf_path(url)
        if p.exists() and p.stat().st_size:
            return
        for attempt in range(3):
            try:
                r = client.get(url)
                r.raise_for_status()
                if not r.content.startswith(b"%PDF"):
                    raise httpx.HTTPError(f"not a PDF: {r.headers.get('content-type')}")
                p.with_suffix(".part").write_bytes(r.content)
                p.with_suffix(".part").rename(p)
                time.sleep(0.3)
                return
            except httpx.HTTPError:
                time.sleep(3 * (attempt + 1))
        print(f"download failed: {url}")

    urls = [a["url"] for cid, v in scan.items() for a in opinion_documents(attachments(v), cid.startswith("new:"))]
    with ThreadPoolExecutor(max_workers=3) as ex:
        list(ex.map(download, urls))

    # 3. 擷取文字 → opinions.zip，並更新 cases JSON
    stats = {"cases": 0, "documents": 0, "extracted": 0, "chars": 0}
    with zipfile.ZipFile(DATA / "opinions.zip", "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for cid, v in sorted(scan.items()):
            kind, key = cid.split(":")
            case = datasets[kind].get(key)
            atts = opinion_documents(attachments(v), require_justice=kind == "new")
            if not case or not atts:
                continue
            html = case.get("opinions") or ""
            titles = {a["title"] for a in attachments(v)}
            html_is_opinion_text = len(html) >= 200 and not all(l.strip() in titles for l in html.splitlines() if l.strip())
            if html_is_opinion_text and all("抄本" in a["title"] for a in atts):
                continue  # 網頁已有意見書內文，比整本抄本（含解釋文、聲請書）精確
            docs = []
            for a in atts:
                p = pdf_path(a["url"])
                text = ""
                if p.exists() and p.read_bytes()[:4] == b"%PDF":
                    try:
                        text = clean_pdf_text("\n".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(p.read_bytes())).pages))
                    except Exception as e:  # 壞檔不中斷整批
                        print(f"extract failed: {cid} {a['title']}: {e}")
                if text and is_garbled(text):
                    print(f"garbled, skipped: {cid} {a['title']}")
                    text = ""
                doc = {"title": a["title"], "url": a["url"], "text": text}
                transcript = TRANSCRIPTS / f"{url_hash(a['url'])}.txt"
                if not text and transcript.exists():
                    doc["text"] = COMPAT_HAN.sub(lambda m: unicodedata.normalize("NFKC", m.group()), transcript.read_text(encoding="utf-8").strip())
                    doc["transcribed"] = True
                docs.append(doc)
            stats["cases"] += 1
            stats["documents"] += len(docs)
            stats["extracted"] += sum(1 for d in docs if d["text"])
            stats["chars"] += sum(len(d["text"]) for d in docs)
            if any(d["text"] for d in docs):
                zf.writestr(f"{kind}/{key}.json", json.dumps({"documents": docs}, ensure_ascii=False))
            case["opinions"] = ""  # 舊資料在此欄只有附件標題；全文改由 opinions.zip 提供
            case["has_opinions"] = any(d["text"] for d in docs)
            case["opinion_documents"] = [
                {"title": d["title"], **parse_opinion_title(d["title"]), "url": d["url"], "chars": len(d["text"]),
                 **({"transcribed": True} if d.get("transcribed") else {})}
                for d in docs
            ]

    for kind, cases in datasets.items():
        (DATA / f"{kind}_cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats), f"opinions.zip={(DATA / 'opinions.zip').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
