"""從全國法規資料庫官方 API 更新 pcode_all.json（v2 含廢止標記）

用法：
    .venv/bin/python scripts/update_pcode_all.py

官方 API：
    - 法律：https://law.moj.gov.tw/api/Ch/Law/JSON（ZIP → ChLaw.json）
    - 命令：https://law.moj.gov.tw/api/Ch/Order/JSON（ZIP → ChOrder.json）

每部法規的 LawAbandonNote 欄位：空=現行，'廢'=已廢止
"""

import io
import json
import logging
import os
import tempfile
import time
import zipfile
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx

logger = logging.getLogger(__name__)

PCODE_ALL_PATH = Path(__file__).resolve().parent / "data" / "pcode_all.json"
LAW_HISTORIES_PATH = Path(__file__).resolve().parent / "data" / "law_histories.json"

LAW_API_URL = "https://law.moj.gov.tw/api/Ch/Law/JSON"
ORDER_API_URL = "https://law.moj.gov.tw/api/Ch/Order/JSON"

# 下載 timeout（官方 API 回傳 ~30MB ZIP，可能較慢）
DOWNLOAD_TIMEOUT = 180.0

# 台灣時區（政府法規更新以台灣時間為準）
TW_TZ = ZoneInfo("Asia/Taipei")

# 資料驗證下限（目前 11,749，設 10K 防止空資料覆蓋）
MIN_EXPECTED_COUNT = 10_000


def _extract_pcode_from_url(law_url: str) -> str:
    """從 LawURL 擷取 pcode

    例如 'https://law.moj.gov.tw/LawClass/LawAll.aspx?pcode=B0000001' → 'B0000001'
    """
    parsed = urlparse(law_url)
    qs = parse_qs(parsed.query)
    pcodes = qs.get("pcode", qs.get("PCode", []))
    return pcodes[0] if pcodes else ""


def _fetch_and_parse(url: str, client: httpx.Client, max_retries: int = 3) -> list[dict]:
    """下載官方 API ZIP，解壓取得法規 JSON 清單（含重試）"""
    last_error: Exception | None = None
    for attempt in range(max_retries):
        try:
            logger.info("下載: %s (attempt %d/%d)", url, attempt + 1, max_retries)
            resp = client.get(url, timeout=DOWNLOAD_TIMEOUT)
            resp.raise_for_status()

            with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
                json_files = [n for n in zf.namelist() if n.endswith(".json")]
                if not json_files:
                    raise ValueError(f"ZIP 中找不到 JSON 檔案: {zf.namelist()}")
                with zf.open(json_files[0]) as f:
                    data = json.load(f)

            # API 回傳可能是 list 或 dict with 'Laws' key
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "Laws" in data:
                return data["Laws"]
            return [data]
        except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.ConnectError) as e:
            last_error = e
            if attempt < max_retries - 1:
                wait = 5 * (2 ** attempt)  # 5s, 10s, 20s
                logger.warning("下載失敗 (attempt %d/%d)，%ds 後重試: %s",
                               attempt + 1, max_retries, wait, e)
                time.sleep(wait)
    raise last_error  # type: ignore[misc]


def update_pcode_all(output_path: Path | None = None) -> dict:
    """從官方 API 下載所有法規，生成 pcode_all.json v2

    Returns:
        生成的 JSON 資料（同時寫入檔案）
    """
    output_path = output_path or PCODE_ALL_PATH

    pcode_map: dict[str, str] = {}
    abolished_set: list[str] = []
    history_map: dict[str, str] = {}  # pcode → 修法沿革文字
    law_count = 0
    order_count = 0

    # SSL verification via OS-native trust store (truststore injected at
    # config.py import time). pcode_all.json is written to disk and
    # trusted by the next startup, so strict verification matters here;
    # with truststore we now get full verification on macOS / Windows /
    # OpenSSL <3.6 Linux. On OpenSSL 3.6+ Linux this update will fail
    # but the server still starts on the existing pcode_all.json shipped
    # in the repo.
    with httpx.Client(follow_redirects=True, verify=True) as client:
        # 下載法律
        laws = _fetch_and_parse(LAW_API_URL, client)
        logger.info("法律: %d 部", len(laws))
        for item in laws:
            name = item.get("LawName", "")
            url = item.get("LawURL", "")
            pcode = _extract_pcode_from_url(url)
            if not name or not pcode:
                continue
            pcode_map[name] = pcode
            law_count += 1
            if item.get("LawAbandonNote", "") == "廢":
                abolished_set.append(pcode)
            hist = item.get("LawHistories", "").strip()
            if hist:
                history_map[pcode] = hist

        # 下載命令
        orders = _fetch_and_parse(ORDER_API_URL, client)
        logger.info("命令: %d 部", len(orders))
        for item in orders:
            name = item.get("LawName", "")
            url = item.get("LawURL", "")
            pcode = _extract_pcode_from_url(url)
            if not name or not pcode:
                continue
            pcode_map[name] = pcode
            order_count += 1
            if item.get("LawAbandonNote", "") == "廢":
                abolished_set.append(pcode)
            hist = item.get("LawHistories", "").strip()
            if hist:
                history_map[pcode] = hist

    # 資料驗證（防止 API 異常時空資料覆蓋好資料）
    abolished_unique = sorted(set(abolished_set))
    if len(pcode_map) < MIN_EXPECTED_COUNT:
        raise ValueError(
            f"pcode_map 只有 {len(pcode_map)} 筆（預期 >= {MIN_EXPECTED_COUNT}），"
            f"可能 API 異常，拒絕覆蓋"
        )
    if len(abolished_unique) > len(pcode_map) * 0.5:
        raise ValueError(
            f"已廢止 {len(abolished_unique)}/{len(pcode_map)} 超過 50%，可能 API 異常，拒絕覆蓋"
        )

    result = {
        "version": 2,
        "update_date": datetime.now(TW_TZ).strftime("%Y-%m-%d"),
        "law_count": law_count,
        "order_count": order_count,
        "abolished_count": len(abolished_unique),
        "total": len(pcode_map),
        "pcode_map": pcode_map,
        "abolished_set": abolished_unique,
    }

    # Atomic write：寫入 temp 檔再 os.replace（中途 crash 不損壞原檔）
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(output_path.parent), suffix=".tmp", prefix="pcode_all_",
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False)
        os.replace(tmp_path, str(output_path))
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    logger.info(
        "pcode_all.json v2 已更新: %d 部法規（法律 %d + 命令 %d，廢止 %d）",
        result["total"], law_count, order_count, result["abolished_count"],
    )

    # 寫出 law_histories.json（修法沿革）
    if history_map:
        hist_path = output_path.parent / "law_histories.json"
        tmp_fd2, tmp_path2 = tempfile.mkstemp(
            dir=str(hist_path.parent), suffix=".tmp", prefix="law_histories_",
        )
        try:
            with os.fdopen(tmp_fd2, "w", encoding="utf-8") as f:
                json.dump(history_map, f, ensure_ascii=False)
            os.replace(tmp_path2, str(hist_path))
        except Exception:
            try:
                os.unlink(tmp_path2)
            except OSError:
                pass
            raise
        logger.info("law_histories.json 已更新: %d 部法規有沿革", len(history_map))

    return result


def should_update_saturday(path: Path | None = None) -> tuple[bool, str]:
    """Saturday-aware 過期判斷。

    政府每週五晚更新法規。判斷 update_date 是否在最近一個
    「週六 06:00 台北時間」之前，是的話就需要更新。

    Returns:
        (should_update, reason)
    """
    path = path or PCODE_ALL_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        return True, f"檔案不存在或損壞: {e}"

    update_date_str = data.get("update_date", "")
    if not update_date_str:
        return True, "無 update_date"

    # 支援 "2026-03-12" 和 "2026/3/12 上午 12:00:00" 兩種格式
    try:
        update_date = datetime.strptime(update_date_str, "%Y-%m-%d").date()
    except ValueError:
        try:
            update_date = datetime.strptime(update_date_str.split()[0], "%Y/%m/%d").date()
        except ValueError:
            return True, f"無法解析 update_date: {update_date_str}"

    now_tw = datetime.now(TW_TZ)

    # 找最近一個「週六 06:00 台北時間」
    # weekday(): Monday=0 ... Saturday=5, Sunday=6
    days_since_saturday = (now_tw.weekday() - 5) % 7
    last_saturday = now_tw.date() - timedelta(days=days_since_saturday)
    last_saturday_6am = datetime.combine(last_saturday, dt_time(6, 0), tzinfo=TW_TZ)

    # 如果現在還沒到本週六 06:00（例如週六 03:00），退回上一個週六
    if now_tw < last_saturday_6am:
        last_saturday = last_saturday - timedelta(days=7)

    if update_date < last_saturday:
        age = (now_tw.date() - update_date).days
        return True, f"已過期（{age} 天前更新，上個週六: {last_saturday}）"

    age = (now_tw.date() - update_date).days
    return False, f"尚未過期（{age} 天前更新）"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    result = update_pcode_all()
    print(f"完成: {result['total']} 部法規, {result['abolished_count']} 部已廢止")
