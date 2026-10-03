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

查詢時即時取得各機關官方系統，不建本機資料庫（國土管理署、智慧局著作權函釋官方只提供全量清單，下載到使用者資料目錄後在本機比對；消保處、監察院陽光法令、標準檢驗局的清單只有幾百筆，每天抓一次在記憶體比對）。官網有效力標示的（停止適用、廢止、現行），結果帶 `status` 欄位，詳見 README：

| 機關 / Agency | URL | Coverage |
|---|---|---|
| 法務部 | https://mojlaw.moj.gov.tw | 行政函釋、法規諮詢意見 |
| 勞動部 | https://laws.mol.gov.tw | 行政函釋、解釋令 |
| 衛生福利部 | https://mohwlaw.mohw.gov.tw | 行政函釋（robots.txt 不允許爬蟲；僅使用者觸發的單次查詢） |
| 環境部 | https://oaout.moenv.gov.tw/law/ | 行政函釋 |
| 行政院公共工程委員會 | https://planpe.pcc.gov.tw/prms/explainLetter/readPrmsExplainLetterSearch | 政府採購法規解釋函令 |
| 財政部 | https://ttc.mof.gov.tw | 各稅法令函釋（法令彙編、新頒令釋、函釋免列及節錄理由） |
| 財政部 | https://law-out.mof.gov.tw/ | 主管法規查詢系統：財政部與關務署、國有財產署、國庫署的行政規則（核釋令） |
| 經濟部 | https://law.moea.gov.tw/ | 主管法規查詢系統：經濟部本部與所屬機關的行政規則（解釋令） |
| 經濟部商業發展署 | https://gcis.nat.gov.tw/elaw/ | 公司法、商業登記法、商業會計法、有限合夥法函釋 |
| 經濟部標準檢驗局 | https://www.bsmi.gov.tw/lawVue/ | 解釋函令（取自該系統的 JSON API；只收標示公開且有內容的） |
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
| 大陸委員會 | https://law.mac.gov.tw/ | 行政規則（解釋令） |
| 中央選舉委員會 | https://law.cec.gov.tw/ | 行政函釋、行政規則 |
| 行政院主計總處 | https://law.dgbas.gov.tw/ | 其他令函、行政規則 |
| 外交部 | https://law.mofa.gov.tw/ | 行政規則（指名才查） |
| 國軍退除役官兵輔導委員會 | https://law.vac.gov.tw/vaclaw/ | 行政規則（指名才查） |
| 核能安全委員會 | https://erss.nusc.gov.tw/law/ | 行政指導、行政規則（指名才查） |
| NCC | https://ncclaw.ncc.gov.tw/FINT/ | 個別函釋；指名 `NCC` 才查。HTTP 優先，遇驗證使用瀏覽器；不推論效力 |
| 客家委員會 | https://law.hakka.gov.tw/ | 行政規則；補現有 TWCA 2023 中繼憑證，保留 TLS 驗證；指名才查 |
| 僑務委員會 | https://law.ocac.gov.tw/law/ | 正確公開入口是 `/law/`；行政規則，指名才查 |
| 運動部 | https://law.sports.gov.tw/ | 行政規則；HTTP 優先，必要時 Playwright；指名才查 |
| 財政部關務署 | https://web.customs.gov.tw/multiplehtml/41 | 新頒釋函；單次 CSRF 表單查一頁標題、按需全文；`關務署` 同時查既有財政部主管法規系統 |
| 陸委會主站 | https://www.mac.gov.tw/Content_List.aspx?n=8E8FA34452E8DBC2 | 僅廣告規範類別的函與參考意見；HTTP／瀏覽器；`陸委會` 同時查既有主管法規系統，不代表全部主站函文 |
| 國家發展委員會 | https://theme.ndc.gov.tw/lawout/ | 行政規則（指名才查） |
| 行政院人事行政總處 | https://law.dgpa.gov.tw/ | 人事法令解釋 |
| 行政院消費者保護處 | https://www.ey.gov.tw/Page/B68C1CA8857302A2 | 消費者保護法函釋（只比對標題與摘要） |
| 監察院陽光法令主題網 | https://sunshine.cy.gov.tw/News.aspx?n=24&sms=8862 | 政治獻金法、公職人員利益衝突迴避法、財產申報法的主管機關函釋（只比對標題） |
| 考試院、銓敘部、保訓會、考選部 | https://law.exam.gov.tw/ | 行政函釋（考試院主管法規共用系統） |
| 交通部 | https://motclaw.motc.gov.tw/webMotcLaw2018/ | 行政解釋（令、函、公告）；伺服器未送中繼憑證，本工具附上公開的 TWCA 中繼憑證（`mcp_server/tools/tls.py`） |
| 中央銀行 | https://www.law.cbc.gov.tw/ | 行政令函 |
| 臺北市政府 | https://laws.gov.taipei/Law/Interpretation/ | 臺北市政府解釋令函，及該系統收錄的中央機關函釋 |
| 新北市政府 | https://web.law.ntpc.gov.tw/Scripts/SimpleQ2.aspx?C3=E | 新北市政府與中央機關函釋（依類別逐類查詢） |
| 行政院公報資訊網 | https://gazette.nat.gov.tw | 各機關依行政程序法第 159 條第 2 項第 2 款發布之解釋性規定 |

## 6. 訴願決定與準司法決定 / Administrative Appeals and Quasi-judicial Decisions

官網公開的決定書照原樣提供：多數機關已遮蔽當事人姓名，部分舊案（行政院 108 年以前收辦、法務部約 112 年以前）與原民會的決定書官網未遮蔽，本工具不另外遮蔽。新增來源僅供使用者觸發的公開免登入查詢；必要時以全新 Playwright 工作階段、CSRF 表單或本機 OCR 完成驗證。沒有使用個人登入狀態，也不遍歷所有分頁。驗證未完成回報錯誤，不當成查無資料。內政部與衛福部需安裝 `[captcha]` 額外依賴；需要瀏覽器的來源（文化部訴願等）會在第一次使用時自動安裝 Chromium。

### 預設來源與準司法機關

| 機關 / Agency | URL | Coverage |
|---|---|---|
| 行政院訴願審議委員會 | https://appeal.ey.gov.tw | 訴願決定書（PDF；108 年以前收辦案件為 HTML）。robots.txt 不允許爬蟲；僅使用者觸發的單次查詢 |
| 公平交易委員會 | https://www.ftc.gov.tw/internet/main/decision/decisionList.aspx?mid=11 | 處分書及不處分決議書（PDF） |
| 勞動部不當勞動行為裁決委員會 | https://uflb.mol.gov.tw/front/querydecision | 不當勞動行為裁決 |
| 公務人員保障暨培訓委員會 | https://web13.csptc.gov.tw/index.aspx | 復審、再申訴決定（不含年金改革案件） |
| 金融監督管理委員會 | https://www.fsc.gov.tw/ch/home.jsp?id=131&parentpath=0,2 | 重大裁罰案件 |
| 金管會銀行局 | https://www.banking.gov.tw/ch/home.jsp?id=550&parentpath=0,524,547 | 非重大裁罰案件（只公開摘要） |
| 金管會證券期貨局 | https://www.sfb.gov.tw/ch/home.jsp?id=104&parentpath=0,2,102 | 裁罰案件 |
| 金管會保險局 | https://www.ib.gov.tw/ch/home.jsp?id=42&parentpath=0,2 | 裁罰案件 |
| 行政院公共工程委員會 | https://web.pcc.gov.tw/piat/piaq/index | 採購申訴審議判斷（須指定才查） |
| 監察院 | https://www.cy.gov.tw/CyBsBox.aspx?CSN=1&n=133&sms=0 | 調查報告、糾正案、彈劾案、糾舉案（須指定才查） |
| 衛福部醫事懲戒 | https://ma.mohw.gov.tw/Accessibility/DISSearch/MASearchDIS | 目前上架公告（預設西醫師）；公開表單無須前端驗證碼，只取官方主機 PDF；掃描檔沒有文字全文。須指定 `醫事懲戒` |
| 法務部律師查詢系統 | https://lawyerbc.moj.gov.tw/ | 律師懲戒、懲戒覆審決議（須指定才查） |

### 各部會訴願決定（須指定才查）

| 機關 / Agency | URL |
|---|---|
| 經濟部 | https://eportal2.moea.gov.tw/EE120/page/decision-doc-query （公開 CSRF + JSON API，固定 `isOpen=true`；本年度及前五年度） |
| 農業部 | https://appeal.moa.gov.tw/Mondel/LaKm/LaKmQry.aspx （WebForms；最多前五頁，總數為估計，列表日期是登錄日） |
| 教育部 | https://appeal.moe.gov.tw/hope_search.aspx （WebForms；最近二年、最多前五頁，不支援年度篩選） |
| 文化部 | https://appeal.moc.gov.tw/home/zh-tw/mocappeal （全新 Playwright 工作階段取得前端呼叫 themedata.culture.tw 公開 API 的回應；列表日期是刊登日；官網整串比對，一次只查一個詞） |
| 環境部 | https://aamis-web.moenv.gov.tw/Search/Decision （公開 JSON 查詢；預設本年度，上限 300 筆） |
| 勞動部 | https://appealweb.mol.gov.tw/Appeal/AppealCaseDecision （公開語音驗證回應；只查單一年度，列表日期是發文日） |
| 內政部 | https://aarc.moi.gov.tw/Decision/ （本機 OCR 最多兩次；預設本年度、一次限一年度） |
| 衛福部 | https://service.mohw.gov.tw/AppealSearch/ （本機 OCR 最多兩次；預設本年度、全文 ODT；無法區分驗證失敗的「查無資料」會回報錯誤） |
| 中央選舉委員會 | https://web.cec.gov.tw/api/central/article/list （公開清單 API 分類 156，文章附 PDF；只比對標題，日期為刊登日） |
| 人事行政總處 | https://www.dgpa.gov.tw/informationlist?uid=130 （只在指定一頁比對標題，總數是未篩選筆數；全文 ODT；robots.txt 不作排除，僅使用者觸發） |
| 法務部 | https://www.moj.gov.tw/2204/2645/2686/Lpsimplelist |
| 外交部 | https://www.mofa.gov.tw/News.aspx?n=1013&sms=229 |
| 國防部 | https://law.mnd.gov.tw/ |
| 交通部 | https://nseweb.motc.gov.tw/NSEWEB/WebSite/Sys/Func01 （伺服器未送中繼憑證，本工具附上公開的 TWCA 中繼憑證） |
| 金融監督管理委員會 | https://www.fsc.gov.tw/ch/home.jsp?id=809&parentpath=0,7 |
| 中央銀行 | https://www.law.cbc.gov.tw/ |
| 國軍退除役官兵輔導委員會 | https://www.vac.gov.tw/sp-appeal-CDQS-1.html |
| 國家科學及技術委員會 | https://www.nstc.gov.tw/law/ch/list/120f2d63-fdac-4d36-a6e6-19bac8bc430d |
| 數位發展部 | https://moda.gov.tw/information-service/govinfo/administrative-appeal/1235 |
| 原住民族委員會 | https://law.cip.gov.tw/ （主管法規共用系統「函釋及訴願決定」類） |
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
| 基隆市政府 | https://www.klcg.gov.tw/tw/klcg1/2669.html （標題多為案號，全文 PDF；不做附件全文搜尋） |

## 7. 立法院 / Legislative Yuan

| 系統 / System | URL | Coverage |
|---|---|---|
| 立法院法律系統 | https://lis.ly.gov.tw/lglawc/lglawkm | 各法律之法條沿革：歷次制定、修正之條文與立法理由（民國 59 年以後之修正附理由）；最近一次修正的立法歷程與公報頁 PDF。伺服器需要舊式 TLS 重新協商 |
| 立法院議事暨公報資訊網 | https://ppg.ly.gov.tw/ppg/ | 議案（含審查中草案與關係文書）、立法院公報。robots.txt 不允許爬蟲；僅使用者觸發的單次查詢 |
| 行政院公報資訊網 | https://gazette.nat.gov.tw/egFront/ | 法規命令訂定、修正草案預告 |
| JOIN 公共政策網路參與平臺 | https://join.gov.tw/policies/ | `kind="join"` 補法律草案預告；`pending`／`closed` 是諮詢狀態，不是法律效力。單頁公開 API、預告內文、一份選定的草案／對照表 PDF；其他附件提供連結 |

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
| 國立臺灣大學法律學院 | https://www.law.ntu.edu.tw/center/ | 臺大法學論叢：由國圖書目定位一卷一頁目錄與該期，只取明確標示全文／定稿的 PDF；其餘只回摘要，不遍歷歷年期刊 |

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
| 桃園市 | https://law.tycg.gov.tw/ |
| 基隆市 | https://exlaw.klcg.gov.tw/ （伺服器未送中繼憑證，本工具附上公開的 TWCA 中繼憑證） |
| 宜蘭縣 | https://glrslaw.e-land.gov.tw/ （同上） |
| 南投縣 | https://glrs.nantou.gov.tw/ |
| 花蓮縣 | https://glrs.hl.gov.tw/glrsout/ |
| 連江縣 | https://law.matsu.gov.tw/ |
| 雲林縣 | https://law.yunlin.gov.tw/ |

雲林縣採 HTTP 優先、遇驗證時以全新 Playwright 工作階段讀取；逾時或挑戰頁回報失敗，不當成查無法規。

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


## 目前未收錄 / Not covered

下列來源實測後仍無法即時查詢，或刻意不收。網站改版後可能可以補上，歡迎回報。

| 來源 | 原因 |
|---|---|
| 數位發展部法規系統 law.moda.gov.tw | 瀏覽器也停在驗證等待頁；只能從行政院公報查到依法公告的解釋性規定 |
| 財政部、桃園市訴願 | 查詢頁在表單出現前就中斷連線 |
| 臺南市訴願 | 圖形驗證碼無法穩定辨識 |
| 南投縣訴願 | 依會議刊登、附件多為舊式 DOC，沒有逐案查詢 |
| NCC 處分書 | 尚未實作（NCC 函釋已收） |
| 興大法學全文 | 官網連線不穩；書目與摘要可從國圖期刊索引查 |
| 司法院量刑資訊服務平台 sen.judicial.gov.tw | 連線逾時；量刑統計使用事實型量刑資訊系統的公開彙總 |
| GRB 研究成果報告全文 | 報告頁逾時；書目與摘要已收 |
| 國圖博碩士論文（NDLTD）全文 | 全文需會員登入 |
| 勞動力發展署法規系統 | 需員工帳號 |
| 司法院量刑系統的個案判決清單 | 只開放院內使用者 |
| 證基會 selaw 證券函令 | 網站載明非經授權不得轉載（櫃買、期交所規章僅供查閱並附提醒） |
| 月旦、華藝、法源、Lawsnote 等付費資料庫 | 不使用 |
