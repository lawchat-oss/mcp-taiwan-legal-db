"""MCP Server 設定管理"""

# ──────────────────────────────────────────────────────────
# OS-native SSL trust store (must run before any `import ssl` / `import httpx`)
# ──────────────────────────────────────────────────────────
# Background: judgment.judicial.gov.tw and law.moj.gov.tw chain up to
# TWCA Global Root CA, a 2012 root that lacks the X509v3 Subject Key
# Identifier extension. OpenSSL 3.6+ (shipped by Homebrew Python 3.13,
# Fedora 40+, upcoming Linux LTS) enforces RFC 5280 strict CA validation
# and rejects this chain with "Missing Subject Key Identifier". OpenSSL
# <3.6 (Ubuntu 22.04, Debian 12, etc.) still accepts it.
#
# `truststore` (PyPA project) makes Python use the OS native trust store
# for SSL verification instead of the certifi Mozilla bundle:
#   - macOS → Security framework (LibreSSL-family, lenient)
#   - Windows → CryptoAPI / schannel (lenient)
#   - Linux → still OpenSSL-based; 3.6+ still rejects, but this gives us
#             a uniform code path and can be complemented by Playwright
#             fallback (already present in JudgmentDocClient).
#
# This preserves *full* SSL verification (verify=True) on macOS + Windows
# and the majority of Linux installations. No `verify=False` is needed
# anywhere in the codebase.
import truststore
truststore.inject_into_ssl()

from pathlib import Path

# 專案根目錄（相對於此檔案自動解析，不硬編碼路徑）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 允許的域名（資安 allow-list，validate_url_domain 會拒絕其他所有 host）
ALLOWED_DOMAINS = [
    "judgment.judicial.gov.tw",
    "data.judicial.gov.tw",
    "law.moj.gov.tw",
]

# 快取設定
CACHE_DB_PATH = PROJECT_ROOT / "data" / "cache" / "legal_mcp.db"
CACHE_JUDGMENT_TTL = 2592000   # 30 天（判決書少有變動，但 parser 更新後需要刷新快取）
CACHE_SEARCH_TTL = 86400       # 24 小時
CACHE_REGULATION_TTL = 604800  # 7 天
CACHE_PCODE_TTL = 2592000      # 30 天

# Playwright 設定
PLAYWRIGHT_HEADLESS = True
PLAYWRIGHT_TIMEOUT = 60000     # 60 秒（法院網站高負載時段可能較慢）
SEARCH_RATE_LIMIT = 5          # 每分鐘最多 5 次
SEARCH_DELAY_MIN = 1.0         # 秒
SEARCH_DELAY_MAX = 3.0         # 秒

# 法規 API
REGULATION_API_BASE = "https://law.moj.gov.tw"
REGULATION_SINGLE_URL = REGULATION_API_BASE + "/LawClass/LawSingle.aspx"
REGULATION_ALL_URL = REGULATION_API_BASE + "/LawClass/LawAll.aspx"
REGULATION_HISTORY_URL = REGULATION_API_BASE + "/LawClass/LawHistory.aspx"

# 司法院
JUDICIAL_SEARCH_URL = "https://judgment.judicial.gov.tw/FJUD/Default_AD.aspx"
JUDICIAL_PRINT_URL = "https://judgment.judicial.gov.tw/FJUD/printData.aspx"
JUDICIAL_DATA_URL = "https://judgment.judicial.gov.tw/FJUD/data.aspx"
# 已廢棄：JDoc API 完全回傳 405，改用 JUDICIAL_DATA_URL (data.aspx)
JDOC_API_BASE = "http://data.judicial.gov.tw/jdg/api"

# 常用法規 pcode 對照表
PCODE_MAP = {
    "民法": "B0000001",
    "民事訴訟法": "B0010001",
    "刑法": "C0000001",
    "刑事訴訟法": "C0010001",
    "勞動基準法": "N0030001",
    "消費者保護法": "J0170001",
    "公平交易法": "J0150002",
    "個人資料保護法": "I0050021",
    "公司法": "J0080001",
    "強制執行法": "B0010004",
    "行政訴訟法": "A0030154",
    "訴願法": "A0030020",
    "國家賠償法": "I0020004",
    "著作權法": "J0070017",
    "專利法": "J0070007",
    "商標法": "J0070001",
    "營業秘密法": "J0080028",
    "保險法": "G0390002",
    "證券交易法": "G0400001",
    "銀行法": "G0380001",
    "勞工退休金條例": "N0030020",
    "性別平等工作法": "N0030014",
    "智慧財產案件審理法": "A0030215",
    "商業事件審理法": "B0010071",
    "土地法": "D0060001",
    "租賃住宅市場發展及管理條例": "D0060125",
    "行政程序法": "A0030055",
    "行政罰法": "A0030210",
    "政府採購法": "A0030057",
    "中華民國憲法": "A0000001",
    "憲法訴訟法": "A0030159",
    "家事事件法": "B0010048",
    "仲裁法": "I0020001",
    "鄉鎮市調解條例": "I0020003",
    "勞動事件法": "B0010064",
    "國民法官法": "A0030320",
    "洗錢防制法": "G0380131",
    "稅捐稽徵法": "G0340001",
    "所得稅法": "G0340003",
    "營業稅法": "G0340080",
    "票據法": "G0380028",
    "海商法": "K0070002",
    "破產法": "B0010006",
    "信託法": "I0020024",
    "民法總則施行法": "B0000002",
    "民法債編施行法": "B0000003",
    "民法物權編施行法": "B0000004",
    "民法親屬編施行法": "B0000005",
    "民法繼承編施行法": "B0000006",
    "涉外民事法律適用法": "B0000007",
    # 不動產與建築
    "建築法": "D0070109",
    "公寓大廈管理條例": "D0070118",
    "不動產經紀業管理條例": "D0060066",
    # 交通
    "道路交通管理處罰條例": "K0040012",
    # 其他常用
    "少年事件處理法": "C0010011",
    "社會秩序維護法": "D0080067",
    "遺產及贈與稅法": "G0340072",
}

# 法院代碼對照表（用於 Playwright 搜尋，2026-02-14 自動抓取自 judicial.gov.tw）
COURT_CODES = {
    # 最高法院 / 特殊法院
    "憲法法庭": "JCC",
    "司法院刑事補償法庭": "TPC",
    "最高法院": "TPS",
    "最高行政法院": "TPA",
    "懲戒法院": "TPP",
    "懲戒法院懲戒法庭": "TPPD",  # TPP Disciplinary
    "懲戒法院職務法庭": "TPJ",
    "智慧財產及商業法院": "IPC",
    # 高等法院
    "臺灣高等法院": "TPH",
    "臺灣高等法院臺中分院": "TCH",
    "臺灣高等法院臺南分院": "TNH",
    "臺灣高等法院高雄分院": "KSH",
    "臺灣高等法院花蓮分院": "HLH",
    "福建高等法院金門分院": "KMH",
    # 高等行政法院
    "臺北高等行政法院": "TPB",
    "臺中高等行政法院": "TCB",
    "高雄高等行政法院": "KSB",
    # 地方法院
    "臺灣臺北地方法院": "TPD",
    "臺灣士林地方法院": "SLD",
    "臺灣新北地方法院": "PCD",
    "臺灣宜蘭地方法院": "ILD",
    "臺灣基隆地方法院": "KLD",
    "臺灣桃園地方法院": "TYD",
    "臺灣新竹地方法院": "SCD",
    "臺灣苗栗地方法院": "MLD",
    "臺灣臺中地方法院": "TCD",
    "臺灣彰化地方法院": "CHD",
    "臺灣南投地方法院": "NTD",
    "臺灣雲林地方法院": "ULD",
    "臺灣嘉義地方法院": "CYD",
    "臺灣臺南地方法院": "TND",
    "臺灣高雄地方法院": "KSD",
    "臺灣橋頭地方法院": "CTD",
    "臺灣花蓮地方法院": "HLD",
    "臺灣臺東地方法院": "TTD",
    "臺灣屏東地方法院": "PTD",
    "臺灣澎湖地方法院": "PHD",
    "福建金門地方法院": "KMD",
    "福建連江地方法院": "LCD",
    "臺灣高雄少年及家事法院": "KSY",
}

# 案件類型代碼
CASE_TYPE_CODES = {
    "民事": "V",
    "刑事": "M",
    "行政": "A",
    "懲戒": "P",
}

# COURT_CODES 的反轉（code → name）
COURT_CODE_TO_NAME = {v: k for k, v in COURT_CODES.items()}

# 法院層級（數字越小權威越高，用於搜尋結果排序）
COURT_LEVEL = {
    # Level 1: 最高級
    "JCC": 1,   # 憲法法庭
    "TPS": 1,   # 最高法院
    "TPA": 1,   # 最高行政法院
    # Level 2: 高等級 + 專業法院
    "TPH": 2, "TCH": 2, "TNH": 2, "KSH": 2, "HLH": 2, "KMH": 2,
    "TPB": 2, "TCB": 2, "KSB": 2,
    "IPC": 2,   # 智慧財產及商業法院
    "TPP": 2, "TPPD": 2, "TPJ": 2, "TPC": 2,
    # Level 3: 地方級 — 不逐一列出，用 .get(code, 3) fallback
}

# 案件類型反查（code → name）
CASE_TYPE_CODE_TO_NAME = {v: k for k, v in CASE_TYPE_CODES.items()}


def validate_url_domain(url: str) -> bool:
    """驗證 URL 是否在允許的域名清單中"""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    return parsed.hostname in ALLOWED_DOMAINS
