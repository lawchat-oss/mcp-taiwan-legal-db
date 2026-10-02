# Data Sources

All data in this project is sourced from Taiwan government public databases.

## 1. 司法院裁判書系統 / Judicial Judgments

- **URL**: https://judgment.judicial.gov.tw
- **Provider**: Judicial Yuan of the Republic of China (Taiwan)
- **Coverage**: 各級法院民事、刑事、行政裁判書

## 2. 全國法規資料庫 / National Regulations

- **URL**: https://law.moj.gov.tw
- **Provider**: Ministry of Justice of the Republic of China (Taiwan)
- **Coverage**: 11,700+ 部法規（法律 + 命令），含修法沿革

## 3. 司法院憲法法庭 / Constitutional Court

- **URL**: https://cons.judicial.gov.tw
- **Provider**: Judicial Yuan of the Republic of China (Taiwan)
- **Coverage**:
  - 舊制大法官解釋（釋字）第 1–813 號（民國 38–110 年，1949–2021）
  - 新制憲法法庭裁判（憲判字）民國 111 年起（2022–）
  - 大法官意見書：釋字 401 號以後與憲判字的意見書取自官網公開的 PDF 附件，擷取文字後打包（`scripts/build_opinions.py`）；無法擷取文字的少數 PDF 以頁面影像逐字轉錄（`scripts/opinion_transcripts/`）

## 4. 司法院法學資料檢索系統 / Judicial Yuan Law Database (FINT)

- **URL**: https://legal.judicial.gov.tw/FINT/
- **Provider**: Judicial Yuan of the Republic of China (Taiwan)
- **Coverage**: 最高法院／最高行政法院決議、法律問題座談、停止適用之判例、大理院／最高法院解釋與司法院院字／院解字、大法庭裁定、跨機關行政函釋
- **Access**: 查詢時即時取得、不建本機資料庫。站方 robots.txt 不允許爬蟲，本工具只做使用者觸發的單次查詢 / live, user-triggered single lookups only (robots.txt disallows crawlers)

## 5. 行政機關函釋 / Administrative Interpretations

查詢時即時取得各機關官方系統，不建本機資料庫（國土管理署、智慧局官方只提供全量清單，下載到使用者資料目錄後在本機比對）：

| 機關 / Agency | URL | Coverage |
|---|---|---|
| 法務部 | https://mojlaw.moj.gov.tw | 行政函釋、法規諮詢意見 |
| 勞動部 | https://laws.mol.gov.tw | 行政函釋、解釋令 |
| 衛生福利部 | https://mohwlaw.mohw.gov.tw | 行政函釋（robots.txt 不允許爬蟲；僅使用者觸發的單次查詢） |
| 行政院公共工程委員會 | https://planpe.pcc.gov.tw/prms/explainLetter/readPrmsExplainLetterSearch | 政府採購法規解釋函令 |
| 財政部 | https://ttc.mof.gov.tw | 各稅法令函釋（法令彙編、新頒令釋） |
| 經濟部商業發展署 | https://gcis.nat.gov.tw/elaw/ | 公司法、商業登記法、商業會計法、有限合夥法函釋 |
| 經濟部智慧財產局 | https://www.tipo.gov.tw/public/Data/data_output_1.xml | 著作權解釋令函（開放資料） |
| 內政部戶政司 | https://www.ris.gov.tw/info-lawsExplained/app/aw0711/toMain | 戶政法令解釋 |
| 內政部國土管理署 | https://www.nlma.gov.tw/ch/titlelist/interpcomp | 解釋函彙編 |
| 行政院公報資訊網 | https://gazette.nat.gov.tw | 各部會依行政程序法第 159 條第 2 項第 2 款發布之解釋性規定 |

## Copyright Status

上述來源的資料均依中華民國《著作權法》第 9 條第 1 項第 1 款不受著作權保護，屬公有領域：

- **裁判書**：法院判決屬「公文」
- **法規**：「憲法、法律、命令」不受著作權保護
- **大法官解釋 / 憲判字**：同屬「公文」
- **決議、法律問題座談、判例、司法院解釋、行政函釋**：同屬「公文」

All of these sources are in the public domain under Article 9(1)(1) of the
ROC Copyright Act, which excludes from copyright protection: constitutions,
statutes, regulations, and official documents (including court judgments,
Constitutional Court rulings, court resolutions and administrative
interpretations).

## Structured Dataset

The structured packaging (JSON data files, HTML parsing, field normalization)
is released under [CC0 1.0](DATA_LICENSE) by LawChat.
