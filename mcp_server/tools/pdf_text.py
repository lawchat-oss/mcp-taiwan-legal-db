"""PDF → 純文字（大法官意見書、訴願決定書、公平會處分書共用）。"""

from __future__ import annotations

import io
import logging
import re
import unicodedata

logger = logging.getLogger(__name__)

CJK = "　-〿一-鿿＀-￯"  # CJK punctuation, ideographs, fullwidth forms
# 相容漢字（U+F900 起，Big5 轉出的 PDF 常見）與康熙部首（Word 轉出的 PDF 常見）外觀同一般漢字但編碼不同，
# 不轉換會讓「法律」等關鍵字搜不到。只轉這些區段，不做整體 NFKC，以免全形標點被改成半形。
COMPAT_HAN = re.compile("[⺀-⿟豈-﫿\U0002f800-\U0002fa1f]")
# 字型無法解碼時抽出的是古木基、僧伽羅、希臘等不相干文字。不用中文字比例判斷：註腳大量引日、英文法條的
# 意見書（釋字 777 號吳陳鐶）會被誤判，正文亂碼但註腳可讀的（釋字 714 號陳新民、陳春生）又會漏判。
EXPECTED_LETTERS = re.compile("[\x00-ſ぀-ヿ㐀-䶿一-鿿＀-￯]")
MAX_GARBLED_RATIO = 0.1  # 正常文件（含 OCR 雜訊）最高約 0.015，亂碼文件最低約 0.3


def is_garbled(text: str) -> bool:
    odd = sum(1 for c in text if re.match("L|M|Cn", unicodedata.category(c)) and not EXPECTED_LETTERS.match(c))
    return odd > MAX_GARBLED_RATIO * len(text)


def normalize_han(text: str) -> str:
    return COMPAT_HAN.sub(lambda m: unicodedata.normalize("NFKC", m.group()), text)


def clean_pdf_text(raw: str) -> str:
    """把 PDF 排版換行接回段落：縮排開頭的行才是新段落；去掉頁碼行與中文間的多餘空白；相容漢字轉標準漢字。"""
    paras: list[str] = []
    for line in raw.splitlines():
        if not line.strip() or re.fullmatch(r"\s*[-－]?\s*\d{1,3}\s*[-－]?\s*", line):
            continue
        if not paras or re.match("^(\\s{2,}|　)", line):
            paras.append(line.strip())
        else:
            paras[-1] += line.strip()
    return re.sub(rf"(?<=[{CJK}])[ \t]+|[ \t]+(?=[{CJK}])", "", normalize_han("\n".join(paras)))


def pdf_to_text(blob: bytes) -> str:
    """擷取 PDF 文字；不是 PDF、壞檔、或字型無法解碼（亂碼）時回空字串，由呼叫端改給 PDF 連結。"""
    if blob[:4] != b"%PDF":
        return ""
    from pypdf import PdfReader

    try:
        text = clean_pdf_text("\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(blob)).pages))
    except Exception as e:  # pypdf 對損壞檔案會拋各種例外
        logger.warning("PDF 文字擷取失敗：%s", e)
        return ""
    return "" if is_garbled(text) else text
