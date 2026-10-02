#!/usr/bin/env python3
"""把官網新公布的憲判字補進資料包（維護者離線執行；只抓還沒收錄的案件）。

對每一件新案：抓 docdata 頁面存全欄位（理由、相關法令等不截斷）進 mcp_server/data/new_cases.json，
下載大法官意見書 PDF、擷取全文追加進 opinions.zip（規則與 build_opinions.py 相同）。
抽不出文字的 PDF 要先在 scripts/opinion_transcripts/ 放轉錄稿再重跑該案（刪掉 new_cases.json 裡的那筆即可）。

Usage (repo root):
    uv run python scripts/build_new_cases.py
"""
from __future__ import annotations

import json
import time
import zipfile

import httpx

from build_opinions import DATA, UA, extract_document, set_case_opinions
from mcp_server.tools import constitutional_court as cc


def main() -> None:
    path = DATA / "new_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    mapping = cc._load_new_listing()
    missing = sorted(k for k in mapping if f"{k[0]}_{k[1]}" not in cases)
    print(f"listing {len(mapping)}, bundled {len(cases)}, missing {len(missing)}")
    client = httpx.Client(timeout=120, follow_redirects=True, headers={"User-Agent": UA})

    with zipfile.ZipFile(DATA / "opinions.zip", "a", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for year, number in missing:
            key = f"{year}_{number}"
            r = cc._fetch(f"{cc.BASE}/docdata.aspx", params={"fid": "38", "id": mapping[(year, number)]})
            r.raise_for_status()
            parsed = cc._parse_doc_page(r.text)
            if (err := cc._sanity_check(parsed, cc.NEW_CRITICAL, str(r.url))) is not None:
                raise SystemExit(f"{key}: {err}")
            reasoning = parsed.get("理由", "")
            case = {
                "case_number": parsed.get("判決字號", f"{year}年憲判字第{number}號"),
                "date": parsed.get("判決日期", ""),
                "petitioner": parsed.get("聲請人", ""),
                "issue_summary": parsed.get("案由", ""),
                "main_text": parsed.get("主文", ""),
                "main_text_truncated": False,
                "summary": parsed.get("判決摘要", ""),
                "summary_truncated": False,
                "related_statutes": parsed.get("相關法令", ""),
                "has_reasoning": cc._is_substantive(reasoning),
                "reasoning": reasoning,
                "source_url": str(r.url),
            }
            docs = []
            for att in cc.opinion_documents(cc.page_attachments(r.text, str(r.url)), require_justice=True):
                pdf = client.get(att["url"])
                pdf.raise_for_status()
                docs.append(extract_document(att, pdf.content, f"new:{key}"))
                time.sleep(0.5)
            if any(d["text"] for d in docs):
                zf.writestr(f"new/{key}.json", json.dumps({"documents": docs}, ensure_ascii=False))
            set_case_opinions(case, docs)
            cases[key] = case
            print(f"  {key} {case['date']} reasoning={len(reasoning)} opinions="
                  + ", ".join(f"{d['title'][-20:]}:{len(d['text'])}" for d in docs))
            time.sleep(0.7)

    ordered = dict(sorted(cases.items(), key=lambda kv: tuple(map(int, kv[0].split("_")))))
    path.write_text(json.dumps(ordered, ensure_ascii=False), encoding="utf-8")
    print(f"new_cases.json: {len(ordered)} cases")


if __name__ == "__main__":
    main()
