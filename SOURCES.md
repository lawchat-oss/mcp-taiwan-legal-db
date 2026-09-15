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

## Copyright Status

上述三個來源的資料均依中華民國《著作權法》第 9 條第 1 項第 1 款不受著作權保護，屬公有領域：

- **裁判書**：法院判決屬「公文」
- **法規**：「憲法、法律、命令」不受著作權保護
- **大法官解釋 / 憲判字**：同屬「公文」

All three data sources are in the public domain under Article 9(1)(1) of the
ROC Copyright Act, which excludes from copyright protection: constitutions,
statutes, regulations, and official documents (including court judgments and
Constitutional Court rulings).

## Structured Dataset

The structured packaging (JSON data files, HTML parsing, field normalization)
is released under [CC0 1.0](DATA_LICENSE) by LawChat.
