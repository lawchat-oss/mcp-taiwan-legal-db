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
    uv run python scripts/build_opinions.py [--cache DIR]

頁面清單與 PDF 快取在 --cache（預設 .cache/opinions），中斷後可續跑。
對 cons.judicial.gov.tw 首次完整執行約需數十分鐘。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

from mcp_server.tools.constitutional_court import opinion_documents, page_attachments, parse_opinion_title
from mcp_server.tools.pdf_text import clean_pdf_text, is_garbled, normalize_han

DATA = Path(__file__).resolve().parent.parent / "mcp_server" / "data"
TRANSCRIPTS = Path(__file__).resolve().parent / "opinion_transcripts"
UA = "mcp-taiwan-legal-db data build (github.com/lawchat-oss/mcp-taiwan-legal-db)"
def url_hash(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()


def extract_document(att: dict, pdf: bytes, cid: str) -> dict:
    """單份意見書 PDF → {title, url, text[, transcribed]}；抽不出或亂碼時改用 opinion_transcripts 的轉錄稿。"""
    from pypdf import PdfReader

    text = ""
    if pdf[:4] == b"%PDF":
        try:
            text = clean_pdf_text("\n".join(pg.extract_text() or "" for pg in PdfReader(io.BytesIO(pdf)).pages))
        except Exception as e:  # 壞檔不中斷整批
            print(f"extract failed: {cid} {att['title']}: {e}")
    if text and is_garbled(text):
        print(f"garbled, skipped: {cid} {att['title']}")
        text = ""
    doc = {"title": att["title"], "url": att["url"], "text": text}
    transcript = TRANSCRIPTS / f"{url_hash(att['url'])}.txt"
    if not text and transcript.exists():
        doc["text"] = normalize_han(transcript.read_text(encoding="utf-8").strip())
        doc["transcribed"] = True
    return doc


def set_case_opinions(case: dict, docs: list[dict]) -> None:
    """在 cases JSON 的案件上補 has_opinions 與 opinion_documents（全文另存 opinions.zip）。"""
    case["opinions"] = ""  # 舊資料在此欄只有附件標題；全文改由 opinions.zip 提供
    case["has_opinions"] = any(d["text"] for d in docs)
    case["opinion_documents"] = [
        {"title": d["title"], **parse_opinion_title(d["title"]), "url": d["url"], "chars": len(d["text"]),
         **({"transcribed": True} if d.get("transcribed") else {})}
        for d in docs
    ]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", type=Path, default=Path(".cache/opinions"))
    args = ap.parse_args()
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
                docs.append(extract_document(a, p.read_bytes() if p.exists() else b"", cid))
            stats["cases"] += 1
            stats["documents"] += len(docs)
            stats["extracted"] += sum(1 for d in docs if d["text"])
            stats["chars"] += sum(len(d["text"]) for d in docs)
            if any(d["text"] for d in docs):
                zf.writestr(f"{kind}/{key}.json", json.dumps({"documents": docs}, ensure_ascii=False))
            set_case_opinions(case, docs)

    for kind, cases in datasets.items():
        (DATA / f"{kind}_cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(stats), f"opinions.zip={(DATA / 'opinions.zip').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
