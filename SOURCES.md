# Data Sources

All data in this project is sourced from public databases run by Taiwan government agencies and public institutions. Everything except the bundled Constitutional Court corpus is fetched live, only when a user asks; nothing is bulk-downloaded.

## 1. 司法院裁判書系統 / Judicial Judgments

- **URL**: https://judgment.judicial.gov.tw
- **Provider**: Judicial Yuan of the Republic of China (Taiwan)
- **Coverage**: 各級法院民事、刑事、行政裁判書；歷審清單（`controls/GetJudHistory.ashx`，同一案件各審級裁判）

## 2. 全國法規資料庫 / National Regulations

- **URL**: https://law.moj.gov.tw
- **Provider**: Ministry of Justice of the Republic of China (Taiwan)
- **Coverage**: 11,700+ 部法規（法律 + 命令），含修法沿革；每週由開放資料 API（https://law.moj.gov.tw/api/Ch/Law/JSON 等）更新法規清單、修法沿革與最新公布日、施行日註記、主管機關分類（`law_meta.json`）
- **English**: 官方英譯本（https://law.moj.gov.tw/api/En/Law/JSON、https://law.moj.gov.tw/api/En/Order/JSON），第一次查詢時下載到使用者資料目錄、每週更新；英譯僅供參考，法律效力以中文為準

## 3. 司法院憲法法庭 / Constitutional Court

- **URL**: https://cons.judicial.gov.tw
- **Provider**: Judicial Yuan of the Republic of China (Taiwan)
- **Coverage**:
  - 舊制大法官解釋（釋字）第 1–813 號（民國 38–110 年，1949–2021）
  - 新制憲法法庭裁判（憲判字）民國 111 年起（2022–）
  - 大法官意見書：釋字 401 號以後與憲判字的意見書取自官網公開的 PDF 附件，擷取文字後打包（`scripts/build_opinions.py`）；無法擷取文字的少數 PDF 以頁面影像逐字轉錄（`scripts/opinion_transcripts/`）
  - 案件進度與卷內文書（查詢時即時取得，不打包）：受理案件 https://cons.judicial.gov.tw/docdata.aspx?fid=52 、言詞辯論 https://cons.judicial.gov.tw/docdata.aspx?fid=2204 、徵求法庭之友 https://cons.judicial.gov.tw/docdata.aspx?fid=5504 ，以及各案聲請書、答辯書、鑑定意見、法庭之友意見書、言詞辯論筆錄等附件（PDF 擷取文字）

## 4. 司法院法學資料檢索系統 / Judicial Yuan Law Database (FINT)

- **URL**: https://legal.judicial.gov.tw/FINT/
- **Provider**: Judicial Yuan of the Republic of China (Taiwan)
- **Coverage**: 最高法院／最高行政法院決議、法律問題座談、停止適用之判例、大理院／最高法院解釋與司法院院字／院解字、大法庭裁定、精選裁判（含具參考價值裁判）、跨機關行政函釋
- **Access**: 查詢時即時取得、不建本機資料庫。站方 robots.txt 不允許爬蟲，本工具只做使用者觸發的單次查詢 / live, user-triggered single lookups only (robots.txt disallows crawlers)

## 5. 行政機關函釋與審查基準 / Administrative Interpretations and Examination Guidelines

查詢時即時取得各機關官方系統，不建本機資料庫（國土管理署、智慧局著作權函釋官方只提供全量清單，下載到使用者資料目錄後在本機比對）：

| 機關 / Agency | URL | Coverage |
|---|---|---|
| 法務部 | https://mojlaw.moj.gov.tw | 行政函釋、法規諮詢意見 |
| 勞動部 | https://laws.mol.gov.tw | 行政函釋、解釋令 |
| 衛生福利部 | https://mohwlaw.mohw.gov.tw | 行政函釋（robots.txt 不允許爬蟲；僅使用者觸發的單次查詢） |
| 環境部 | https://oaout.moenv.gov.tw/law/ | 行政函釋 |
| 行政院公共工程委員會 | https://planpe.pcc.gov.tw/prms/explainLetter/readPrmsExplainLetterSearch | 政府採購法規解釋函令 |
| 財政部 | https://ttc.mof.gov.tw | 各稅法令函釋（法令彙編、新頒令釋） |
| 經濟部商業發展署 | https://gcis.nat.gov.tw/elaw/ | 公司法、商業登記法、商業會計法、有限合夥法函釋 |
| 經濟部智慧財產局 | https://www.tipo.gov.tw/public/Data/data_output_1.xml | 著作權解釋令函（開放資料） |
| 經濟部智慧財產局 | https://www.tipo.gov.tw/tw/patents/997.html 、https://www.tipo.gov.tw/tw/trademarks/576.html | 專利審查基準（網頁版全文）、商標審查基準（PDF）；只比對標題 |
| 內政部戶政司 | https://www.ris.gov.tw/info-lawsExplained/app/aw0711/toMain | 戶政法令解釋 |
| 內政部國土管理署 | https://www.nlma.gov.tw/ch/titlelist/interpcomp | 解釋函彙編 |
| 內政部地政司 | https://www.land.moi.gov.tw/law/ | 地政法令解釋（含已停止適用；robots.txt 全站不允許爬蟲，僅使用者觸發的單次查詢） |
| 內政部消防署 | https://law.nfa.gov.tw/GNFA/ | 消防法令解釋（只有摘要；函文為掃描 PDF） |
| 內政部 | https://glrs.moi.gov.tw/ | 部本部行政規則（解釋令、函） |
| 金融監督管理委員會 | https://law.fsc.gov.tw | 行政規則（解釋令、函收在此類） |
| 教育部 | https://edu.law.moe.gov.tw/ | 行政規則（解釋令、函） |
| 農業部 | https://law.moa.gov.tw/ | 行政規則（解釋令、函） |
| 文化部 | https://law.moc.gov.tw/ | 行政規則（解釋令、函） |
| 國家科學及技術委員會 | https://law.nstc.gov.tw/ | 行政規則（解釋令、函） |
| 原住民族委員會 | https://law.cip.gov.tw/ | 行政規則（解釋令、函） |
| 海洋委員會 | https://law.oac.gov.tw/ | 行政規則（解釋令、函） |
| 公平交易委員會 | https://law.ftc.gov.tw/law/ | 行政規則、行政解釋 |
| 考試院、銓敘部、保訓會、考選部 | https://law.exam.gov.tw/ | 行政函釋（考試院主管法規共用系統） |
| 交通部 | https://motclaw.motc.gov.tw/webMotcLaw2018/ | 行政解釋（令、函、公告）；伺服器未送中繼憑證，本工具附上公開的 TWCA 中繼憑證（`mcp_server/tools/tls.py`） |
| 中央銀行 | https://www.law.cbc.gov.tw/ | 行政令函 |
| 臺北市政府 | https://laws.gov.taipei/Law/Interpretation/ | 臺北市政府解釋令函，及該系統收錄的中央機關函釋 |
| 行政院公報資訊網 | https://gazette.nat.gov.tw | 各機關依行政程序法第 159 條第 2 項第 2 款發布之解釋性規定 |

## 6. 訴願決定與準司法決定 / Administrative Appeals and Quasi-judicial Decisions

只收錄官網已遮蔽當事人姓名的來源。行政院 108 年以前收辦、法務部約 112 年以前標題未遮蔽姓名的決定書不列出；經濟部、農業部、原民會、教育部的訴願網站未遮蔽姓名，未收錄。需要驗證碼或 Cloudflare 驗證的網站（勞動部、財政部、內政部、衛福部、臺南市的訴願查詢，醫事懲戒、NCC）未收錄，也不嘗試繞過。

### 預設來源與準司法機關

| 機關 / Agency | URL | Coverage |
|---|---|---|
| 行政院訴願審議委員會 | https://appeal.ey.gov.tw | 近 10 年訴願決定書（PDF）。robots.txt 不允許爬蟲；僅使用者觸發的單次查詢。108 年以前收辦案件官網未遮蔽姓名，本工具不列出 |
| 公平交易委員會 | https://www.ftc.gov.tw/internet/main/decision/decisionList.aspx?mid=11 | 處分書及不處分決議書（PDF） |
| 勞動部不當勞動行為裁決委員會 | https://uflb.mol.gov.tw/front/querydecision | 不當勞動行為裁決 |
| 公務人員保障暨培訓委員會 | https://web13.csptc.gov.tw/index.aspx | 復審、再申訴決定（不含年金改革案件） |
| 金融監督管理委員會 | https://www.fsc.gov.tw/ch/home.jsp?id=131&parentpath=0,2 | 重大裁罰案件 |
| 金管會銀行局 | https://www.banking.gov.tw/ch/home.jsp?id=550&parentpath=0,524,547 | 非重大裁罰案件（只公開摘要） |
| 金管會證券期貨局 | https://www.sfb.gov.tw/ch/home.jsp?id=104&parentpath=0,2,102 | 裁罰案件 |
| 金管會保險局 | https://www.ib.gov.tw/ch/home.jsp?id=42&parentpath=0,2 | 裁罰案件 |
| 行政院公共工程委員會 | https://web.pcc.gov.tw/piat/piaq/index | 採購申訴審議判斷（須指定才查） |
| 監察院 | https://www.cy.gov.tw/CyBsBox.aspx?CSN=1&n=133&sms=0 | 調查報告、糾正案、彈劾案、糾舉案（須指定才查） |
| 法務部律師查詢系統 | https://lawyerbc.moj.gov.tw/ | 律師懲戒、懲戒覆審決議（須指定才查） |

### 各部會訴願決定（須指定才查）

| 機關 / Agency | URL |
|---|---|
| 法務部 | https://www.moj.gov.tw/2204/2645/2686/Lpsimplelist |
| 外交部 | https://www.mofa.gov.tw/News.aspx?n=1013&sms=229 |
| 國防部 | https://law.mnd.gov.tw/ |
| 交通部 | https://nseweb.motc.gov.tw/NSEWEB/WebSite/Sys/Func01 （伺服器未送中繼憑證，本工具附上公開的 TWCA 中繼憑證） |
| 金融監督管理委員會 | https://www.fsc.gov.tw/ch/home.jsp?id=809&parentpath=0,7 |
| 中央銀行 | https://www.law.cbc.gov.tw/ |
| 國軍退除役官兵輔導委員會 | https://www.vac.gov.tw/sp-appeal-CDQS-1.html |
| 國家科學及技術委員會 | https://www.nstc.gov.tw/law/ch/list/120f2d63-fdac-4d36-a6e6-19bac8bc430d |
| 數位發展部 | https://moda.gov.tw/information-service/govinfo/administrative-appeal/1235 |
| 行政院公共工程委員會 | https://www.pcc.gov.tw/content/index?eid=10166&type=C |

### 縣市政府訴願決定（須指定才查）

| 機關 / Agency | URL |
|---|---|
| 臺北市政府 | https://laws.gov.taipei/Law/LawDecision/ （robots.txt 不允許爬取決定書全文路徑；僅使用者觸發的單次查詢） |
| 新北市政府 | https://web.law.ntpc.gov.tw/Scripts/Su_list02.aspx |
| 臺中市政府 | https://appeal.taichung.gov.tw/Home/FN0601 |
| 高雄市政府 | https://law.kcg.gov.tw/plead3.aspx |
| 彰化縣政府 | https://www.chcg.gov.tw/DTO/general/06service/service04.aspx |
| 花蓮縣政府 | https://glrs.hl.gov.tw/glrsout/ |
| 金門縣政府 | https://law.kinmen.gov.tw/ |
| 苗栗縣政府 | https://www.miaoli.gov.tw/general_affairs/News.aspx?n=872&sms=9710 |
| 臺東縣政府 | https://www.taitung.gov.tw/News.aspx?n=13382&sms=12660 |
| 嘉義市政府 | https://general.chiayi.gov.tw/News.aspx?n=3813&sms=11872 |
| 嘉義縣政府 | https://www.cyhg.gov.tw/News.aspx?n=1220&sms=12642 （伺服器使用過短的 DH 金鑰，本工具對此站放寬 OpenSSL 安全等級） |
| 宜蘭縣政府 | https://www.e-land.gov.tw/OpenData_Default.aspx?n=9929 |
| 新竹縣政府 | https://gdd.hsinchu.gov.tw/News.aspx?n=520&sms=8965 |

## 7. 立法院 / Legislative Yuan

| 系統 / System | URL | Coverage |
|---|---|---|
| 立法院法律系統 | https://lis.ly.gov.tw/lglawc/lglawkm | 各法律之法條沿革：歷次制定、修正之條文與立法理由（民國 59 年以後之修正附理由）；最近一次修正的立法歷程與公報頁 PDF。伺服器需要舊式 TLS 重新協商 |
| 立法院議事暨公報資訊網 | https://ppg.ly.gov.tw/ppg/ | 議案（含審查中草案與關係文書）、立法院公報。robots.txt 不允許爬蟲；僅使用者觸發的單次查詢 |
| 行政院公報資訊網 | https://gazette.nat.gov.tw/egFront/ | 法規命令訂定、修正草案預告 |

## 8. 統計與量刑 / Statistics and Sentencing

| 機關 / Agency | URL | Coverage |
|---|---|---|
| 司法院 | https://www.judicial.gov.tw/tw/np-1260-1.html | 司法統計年報（ODS／PDF） |
| 司法院 | https://www.judicial.gov.tw/tw/np-1259-1.html | 司法統計月報 |
| 法務部 | https://www.rjsd.moj.gov.tw/RJSDWeb/ | 法務統計常用統計表 |
| 法務部司法官學院 | https://www.cprc.moj.gov.tw/1563/1590/1592/Lpsimplelist | 《犯罪狀況及其分析》年度報告（篇章 PDF＋數據 XLSX） |
| 司法院事實型量刑資訊系統 | https://intellisen.judicial.gov.tw/ | 10 類案件的量刑統計。只用公開頁面的彙總統計；官網只開放給院內使用者的個案清單與判決明細不呼叫 |

## 9. 法學文獻 / Legal Literature

只用官方與開放取用來源，不使用付費資料庫。

| 來源 / Source | URL | Coverage |
|---|---|---|
| 司法院電子出版品 | https://jirs.judicial.gov.tw/JudLib/ | 專題研究報告（含司法研究年報），全文 PDF 按章分檔 |
| 國家圖書館 臺灣期刊論文索引 | https://tpl.ncl.edu.tw/NclService/ | 期刊論文書目、摘要；作者授權者附全文。授權全文僅供個人查閱，本工具不寫入快取 |
| 政府研究資訊系統 GRB | https://www.grb.gov.tw/ | 政府補助研究計畫書目與摘要（成果報告下載需在官網完成驗證，只給連結） |
| 中央研究院法律學研究所 | https://www.iias.sinica.edu.tw/publication_list/9 | 中研院法學期刊全文 |
| 國立政治大學法學院 | http://review.law.nccu.edu.tw/zh_tw/articles | 政大法學評論全文（站方 HTTPS 憑證過期，以 HTTP 連線） |

## 10. 其他規範 / Local Regulations, Treaties and Exchange Rules

### 地方自治法規（只收現行法規）

| 縣市 / Local government | URL |
|---|---|
| 臺北市 | https://laws.gov.taipei/Law/LawSearch/ |
| 新北市 | https://web.law.ntpc.gov.tw/ |
| 臺中市 | https://law.taichung.gov.tw/ |
| 高雄市 | https://outlaw.kcg.gov.tw/ |
| 臺南市 | https://law01.tainan.gov.tw/glrsnewsout/ |
| 新竹縣 | https://hclaw.hsinchu.gov.tw/law/ |
| 新竹市 | https://law.hccg.gov.tw/ |
| 苗栗縣 | https://law.miaoli.gov.tw/glrsnewsout/ |
| 彰化縣 | https://lawsearch.chcg.gov.tw/GLRSNEWSOUT/ |
| 嘉義縣 | https://law.cyhg.gov.tw/ |
| 嘉義市 | https://law.chiayi.gov.tw/ |
| 屏東縣 | https://ptlaw.pthg.gov.tw/ |
| 臺東縣 | https://law.taitung.gov.tw/ |
| 澎湖縣 | https://law.penghu.gov.tw/glrsnewsout/ |
| 金門縣 | https://law.kinmen.gov.tw/ |

桃園市（連線遭重設）、雲林縣（Cloudflare 驗證頁）、基隆市與宜蘭縣（憑證鏈不完整）的法規系統目前無法自動連線，未收錄。

### 條約協定與交易所規章

| 來源 / Source | URL | Coverage |
|---|---|---|
| 全國法規資料庫 條約協定 | https://law.moj.gov.tw | 我國簽署之條約協定（以名稱查詢）。條約查詢路徑 robots.txt 不允許爬蟲；僅使用者觸發的單次查詢 |
| 外交部 中華民國條約協定資料庫 | https://no06.mofa.gov.tw/mofatreatys/ | 條約協定 PDF（不少為掃描檔，無文字層） |
| 財政部 我國所得稅協定網絡 | https://www.mof.gov.tw/singlehtml/191?cntId=82769 | 全面性及海空運輸所得稅協定 PDF。`/download/` 路徑 robots.txt 不允許爬蟲；只在使用者指定時單次下載 |
| 臺灣證券交易所 法規分享知識庫 | https://twse-regulation.twse.com.tw/m/ | 證交所規章 |
| 證券暨期貨法令判解查詢系統 | https://www.selaw.com.tw/ | 證券櫃檯買賣中心、臺灣期貨交易所規章。站方載明非經授權不得轉載：僅供查閱，結果一律附提醒 |

## Copyright Status

下列資料依中華民國《著作權法》第 9 條第 1 項第 1 款不受著作權保護，屬公有領域：

- **裁判書**：法院判決屬「公文」
- **法規**：「憲法、法律、命令」不受著作權保護（含地方自治法規）
- **大法官解釋 / 憲判字**：同屬「公文」
- **決議、法律問題座談、判例、司法院解釋、行政函釋、審查基準、訴願決定、處分書、準司法機關決定、立法理由**：同屬「公文」

期刊論文、研究報告、研究計畫摘要、統計分析報告與交易所規章不在此列，著作權屬各作者或機構：本工具只在使用者查詢時回傳，請依各來源的使用規定引用；國家圖書館授權全文僅供個人查閱、不寫入快取；證基會法規系統的內容不得轉載。

Judgments, statutes and regulations (including local ones), Constitutional Court rulings, court resolutions, administrative interpretations, appeal and quasi-judicial decisions and legislative reasons are in the public domain under Article 9(1)(1) of the ROC Copyright Act, which excludes from copyright protection: constitutions, statutes, regulations, and official documents. Journal articles, research reports, research project abstracts, analytical statistical reports and exchange rules are not covered: they are returned only on a user's request, should be cited according to each source's terms, NCL-licensed full texts are for personal reading and never cached, and content from the SFI regulation system (selaw.com.tw) may not be republished.

## Structured Dataset

The structured packaging (JSON data files, HTML parsing, field normalization)
is released under [CC0 1.0](DATA_LICENSE) by LawChat.
