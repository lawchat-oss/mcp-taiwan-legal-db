"""司法院「事實型量刑資訊系統」（intellisen.judicial.gov.tw）彙總量刑統計

只用公開頁面呈現的統計：罪名、法條、量刑因子、符合筆數與刑度分布。
個案清單（crimes/list）與判決明細（judgements/{id}）在官網只開放給持票的院內使用者，依政策不呼叫。
"""

from __future__ import annotations

import re
import time

import httpx

BASE = "https://intellisen.judicial.gov.tw/api/frontend/"
_TTL = 86400.0  # 罪名、法院、法條與因子清單很少變動
_memo: dict[str, tuple[float, object]] = {}

NOTE = ("本統計僅涵蓋司法院事實型量刑資訊系統收錄的判決樣本（殺人、強盜搶奪、傷害、不能安全駕駛、肇事逃逸、"
        "詐欺、竊盜、毒品、槍砲、妨害性自主等 10 類案件），反映過去判決的刑度分布，並非量刑基準或量刑建議。")
_UNITS = {"month": "月", "day": "日", "money": "元"}


async def _api(http: httpx.AsyncClient, path: str, body: dict | None = None, cache: bool = True):
    hit = _memo.get(path)
    if cache and hit and time.time() - hit[0] < _TTL:
        return hit[1]
    r = await (http.get(BASE + path) if body is None else http.post(BASE + path, json=body))
    r.raise_for_status()
    data = r.json()
    if cache:
        _memo[path] = (time.time(), data)
    return data


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").replace("台", "臺").replace("地院", "地方法院")


def _pick(query: str, options: dict[str, str], kind: str) -> str:
    """把 id 或中文名稱對到 id：依序比 id、全名、結尾（「臺北地院」不會撞到「臺北地方法院少年法庭」）、部分字串；
    查無或不唯一時丟 ValueError 並列出可選項。"""
    if query in options:
        return query
    q, labels = _norm(query), {i: _norm(label) for i, label in options.items()}
    hits: list[str] = []
    for test in (str.__eq__, str.endswith, str.__contains__):
        hits = [i for i, label in labels.items() if q and test(label, q)]
        if hits:
            break
    if len(hits) == 1:
        return hits[0]
    shown = [f"{i}={label}" for i, label in options.items() if not hits or i in hits]
    raise ValueError(f"{kind}「{query}」{'不明確' if hits else '不存在'}，可選：" + "；".join(shown))


def _laws(rules: list[dict]) -> dict[str, str]:
    out = {}
    for rule in rules:
        for o in rule.get("options") or []:
            name = f"（{o['name']}）" if o.get("name") else ""
            out[o["id"]] = f"{rule['title']} {o.get('title', '')}{name}".strip()
    return out


def _factors(tree: list[dict]) -> list[dict]:
    """因子樹攤平成一列一個可篩選的因子；子選項（如「有具體內容／經濟窘迫」）併入父因子的選項。

    送出時的格式是 {id, value:[選項 id]}；子選項的 id 要掛在父選項底下，所以另記 form_id。
    """
    flat = []
    for group in tree:
        for item in group.get("items") or []:
            for b in item.get("behavior") or []:
                options, form_ids = {}, {}
                for o in b.get("options") or []:
                    if not o.get("id"):  # 「暫不考慮」＝不篩選
                        continue
                    if o.get("child"):
                        for c in o["child"]:
                            options[c["id"]] = f"{o['name']}／{c['name']}"
                            form_ids[c["id"]] = o["id"]
                    else:
                        options[o["id"]] = o["name"]
                        form_ids[o["id"]] = b["id"]
                flat.append({"group": group.get("title", ""), "category": item.get("category", ""),
                             "id": b["id"], "title": re.sub(r"\s+", " ", b.get("title", "")), "type": b.get("type", ""),
                             "options": options, "_form_ids": form_ids})
    return flat


def _factor_form(flat: list[dict], wanted: dict[str, list[str]]) -> list[dict]:
    by_id = {f["id"]: f for f in flat}
    form: dict[str, list[str]] = {}
    for key, values in wanted.items():
        f = by_id[_pick(key, {x["id"]: x["title"] for x in flat}, "量刑因子")]
        values = [values] if isinstance(values, str) else list(values)
        if f["type"] == "input":  # 例如「緩刑：{#} 年」，值是數字
            if not all(re.fullmatch(r"\d+", str(v)) for v in values):
                raise ValueError(f"量刑因子「{f['title']}」要填數字")
            form[f["id"]] = [str(v) for v in values]
        elif not f["options"]:  # 單一勾選框：只能「有」，系統不支援排除條件
            if not all(str(v).strip() in ("是", "有", "1", "true", "True") for v in values):
                raise ValueError(f"量刑因子「{f['title']}」只能指定「是」（系統不支援排除這個條件）")
            form[f["id"]] = [f["id"]]
        else:
            for v in values:
                oid = _pick(str(v), f["options"], f"「{f['title']}」的選項")
                form.setdefault(f["_form_ids"][oid], []).append(oid)
    return [{"id": k, "value": v} for k, v in form.items()]


def _num(x):
    return int(x) if isinstance(x, float) and x.is_integer() else x


def _stats(res: dict) -> dict:
    return {
        "total": res.get("total", 0),
        "average": res.get("punishmentAvg", ""), "min": res.get("punishmentMin", ""),
        "max": res.get("punishmentMax", ""),
        "by_penalty": [{
            "type": p.get("name"), "count": p.get("total"), "avg": p.get("avg"),
            "min": p.get("min"), "min_count": p.get("minCount"), "max": p.get("max"), "max_count": p.get("maxCount"),
            # 每組 [刑度, 件數]；有期徒刑併科罰金會有兩組分布
            "histograms": [{"name": c.get("name"), "unit": _UNITS.get(c.get("unit"), c.get("unit")),
                            "series": [[_num(s["name"]), s["count"]] for s in c.get("series") or []]}
                           for c in p.get("charts") or []],
        } for p in res.get("punishments") or []],
    }


async def sentencing_statistics(
    http: httpx.AsyncClient,
    crime: str = "",
    laws: list[str] | None = None,
    courts: list[str] | None = None,
    factors: dict[str, list[str]] | None = None,
    year_from: int = 0,
    year_to: int = 0,
) -> dict:
    """crime 空白→罪名與法院清單；給 crime→可選法條、量刑因子，以及依條件彙總的刑度統計。

    名稱不符時丟 ValueError（訊息含可選項）；連線錯誤丟 httpx.HTTPError，由呼叫端轉成錯誤回應。
    """
    crimes = {c["id"]: c["title"] for c in await _api(http, "crimes")}
    court_map = {c["id"]: c["name"] for c in await _api(http, "courts")}
    if not crime.strip():
        info = await _api(http, "info")
        return {"crimes": crimes, "courts": court_map, "judgments_in_system": info.get("count"), "note": NOTE}

    cid = _pick(crime.strip(), crimes, "罪名")
    law_data = await _api(http, f"crimes/{cid}/laws")
    tree = await _api(http, f"crimes/{cid}/factors",
                      {"id": cid, "year": {"min": 0, "max": 0}, "laws": [], "courts": [], "factors": []})
    law_opts, flat = _laws(law_data.get("rules") or []), _factors(tree)
    span = law_data.get("year") or {}
    form = {
        "id": cid,
        "year": {"min": year_from or span.get("min", 0), "max": year_to or span.get("max", 0)},
        "laws": [_pick(x, law_opts, "法條") for x in laws or []],
        "courts": [_pick(x, court_map, "法院") for x in courts or []],
        "factors": _factor_form(flat, factors or {}),
    }
    res = await _api(http, "crimes/search", form, cache=False)
    labels = {o: f"{f['title']}：{label}" for f in flat for o, label in f["options"].items()}
    return {
        "crime": {"id": cid, "title": crimes[cid]},
        "query": {"year_from": form["year"]["min"], "year_to": form["year"]["max"],
                  "laws": {i: law_opts[i] for i in form["laws"]},
                  "courts": {i: court_map[i] for i in form["courts"]},
                  "factors": {v: labels.get(v, f"{x['id']}={v}") for x in form["factors"] for v in x["value"]}},
        "statistics": _stats(res),
        "year_range": span,
        "law_options": law_opts,
        "factor_options": [{k: v for k, v in f.items() if k != "_form_ids"} for f in flat],
        "note": NOTE,
    }
