"""法規頁面 HTML 解析器"""

from bs4 import BeautifulSoup
import re

# 已知無效法規名稱（頁面標題而非法規名稱）
INVALID_LAW_NAMES = {"條文內容", "法規內容", "全國法規資料庫", "歷史法規", ""}

# 頁尾免責聲明/網站資訊的特徵字串（用於過濾垃圾 fallback 內容）
_GARBAGE_INDICATORS = [
    "本網站係提供法規之最新動態資訊",
    "若有任何法律上的疑義",
    "著作權聲明",
    "隱私權保護",
    "網站安全政策",
    "瀏覽人次總計",
    "法規整編資料截止日",
    "本站所提供資料僅供參考",
    "電子報訂閱",
]


def _looks_like_article(text: str) -> bool:
    """驗證文字看起來像法規條文（而非頁尾免責聲明等垃圾內容）"""
    for indicator in _GARBAGE_INDICATORS:
        if indicator in text:
            return False
    return True


def parse_single_article(html: str) -> dict:
    """解析 LawSingle.aspx 頁面，擷取單一條文"""
    soup = BeautifulSoup(html, "lxml")
    result = {
        "law_name": "",
        "article_number": "",
        "article_content": "",
        "last_amended": "",
    }

    # 法規名稱
    # 已知無效值（頁面標題而非法規名稱）
    # 使用模組級常數
    title_el = soup.select_one("h2") or soup.select_one(".law-title") or soup.select_one("title")
    if title_el:
        text = title_el.get_text(strip=True)
        # 從標題中擷取法規名稱（格式通常是「民法-全國法規資料庫」或類似）
        name = text.split("-")[0].strip() if "-" in text else text
        if name not in INVALID_LAW_NAMES:
            result["law_name"] = name

    # 條文內容 — 嘗試多種選擇器
    content_el = (
        soup.select_one(".law-article") or
        soup.select_one("#pnlContent") or
        soup.select_one(".content-law") or
        soup.select_one("pre")
    )
    if content_el:
        text = content_el.get_text(strip=True)
        if _looks_like_article(text):
            result["article_content"] = text
    else:
        # fallback：取 body 中最長的文字段落
        paragraphs = soup.find_all(["p", "div", "td"])
        if paragraphs:
            longest = max(paragraphs, key=lambda p: len(p.get_text()))
            text = longest.get_text(strip=True)
            if len(text) > 20 and _looks_like_article(text):
                result["article_content"] = text

    # 條號
    article_match = re.search(r"第\s*(\d+[-之]?\d*)\s*條", result["article_content"])
    if article_match:
        result["article_number"] = article_match.group(1)

    return result


def parse_law_all(html: str) -> dict:
    """解析 LawAll.aspx 頁面，擷取法規全文"""
    soup = BeautifulSoup(html, "lxml")
    result = {
        "law_name": "",
        "last_amended": "",
        "articles": [],
        "structure": [],
    }

    # 法規名稱
    # 使用模組級常數
    title_el = soup.select_one("h2") or soup.select_one("title")
    if title_el:
        name = title_el.get_text(strip=True).split("-")[0].strip()
        if name not in INVALID_LAW_NAMES:
            result["law_name"] = name

    # 章節標題 + 條文 — law.moj.gov.tw 結構：
    #   章節標題: div.h3.char-{1,2,3}（編/章/節），位於 .law-reg-content 容器內
    #   條文: div.row > div.col-no + div.col-data
    # 解析策略：有 .law-reg-content 時 DOM 順序遍歷（保留章節位置），否則 select 全文
    content_root = soup.select_one(".law-reg-content")

    if content_root:
        # 有結構化容器 → DOM 順序遍歷，提取章節 + 條文
        pending_chapters: list[dict] = []
        for el in content_root.children:
            if not hasattr(el, "get"):
                continue
            classes = el.get("class", [])

            # 章節標題（div.h3）
            if "h3" in classes:
                text = re.sub(r"\s+", "", el.get_text(strip=True))
                if text:
                    level = 1 if "char-1" in classes else (2 if "char-2" in classes else 3)
                    pending_chapters.append({"title": text, "level": level})
                continue

            # 條文（div.row）
            if "row" in classes:
                col_no = el.select_one(".col-no")
                col_data = el.select_one(".col-data")
                if col_no and col_data:
                    number_text = col_no.get_text(strip=True)
                    content_text = col_data.get_text(strip=True)
                    num_match = re.search(r"第\s*(\S+?)\s*條", number_text)
                    if num_match and content_text:
                        article_num = num_match.group(1)
                        result["articles"].append({
                            "number": article_num,
                            "content": content_text,
                        })
                        # 回填暫存的章節標題
                        for ch in pending_chapters:
                            ch["first_article"] = article_num
                            result["structure"].append(ch)
                        pending_chapters = []

        # 尾部沒有條文的章節（防禦性處理）
        for ch in pending_chapters:
            result["structure"].append(ch)
    else:
        # 無 .law-reg-content → 退回 select 全文（無章節結構）
        for row in soup.select("div.row"):
            col_no = row.select_one(".col-no")
            col_data = row.select_one(".col-data")
            if col_no and col_data:
                number_text = col_no.get_text(strip=True)
                content_text = col_data.get_text(strip=True)
                num_match = re.search(r"第\s*(\S+?)\s*條", number_text)
                if num_match and content_text:
                    result["articles"].append({
                        "number": num_match.group(1),
                        "content": content_text,
                    })

    # fallback: 舊版表格結構（tr > td）
    if not result["articles"]:
        for row in soup.select("tr"):
            cells = row.select("td")
            if len(cells) >= 2:
                number_text = cells[0].get_text(strip=True)
                content_text = cells[1].get_text(strip=True)
                num_match = re.search(r"第\s*(\S+?)\s*條", number_text)
                if num_match and content_text:
                    result["articles"].append({
                        "number": num_match.group(1),
                        "content": content_text,
                    })

    return result
