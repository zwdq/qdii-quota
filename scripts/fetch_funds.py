#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_funds.py —— 每日抓取 QDII 基金申购状态 / 限额 / 净值，写入 data.json

数据源：
  基金状态/限额/净值：东方财富 FundMNNBasicInformation（MAXSG 已验证可信）
  基金清单（大幅扩充）：运行时自动发现东方财富全量场外 QDII（rankhandler），失败则用兜底清单
  公告交叉校验：仅对 CORE 热门基金做（控制耗时）
运行：python3 scripts/fetch_funds.py [data.json] [--no-ann]   无第三方依赖

【清单扩充策略】
  - CORE：人工核对的 ~20 只热门基金（分组精确，且做公告交叉校验）。
  - 其余：运行时调用 rankhandler 拉全量场外 QDII（约 350+ 只），按名称自动分类。
  - 若发现接口不可用（如 Actions 网络受限），回退到 scripts/fallback_codes.json 兜底清单。
  - 分组(group)即展示分类：CORE 精确分类优先，否则按基金名称关键词归类。
"""

import json
import os
import re
import sys
import time
import datetime
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# ── 核心：精确分组 + 公告交叉校验 ──────────────────────
CORE = {
    # 纳斯达克100（场内ETF + 场外联接/指数，含各份额）
    "159941": "纳斯达克", "513100": "纳斯达克", "513300": "纳斯达克", "161130": "纳斯达克",
    "270042": "纳斯达克", "040046": "纳斯达克", "160213": "纳斯达克", "000834": "纳斯达克",
    "159513": "纳斯达克", "159659": "纳斯达克", "159632": "纳斯达克", "513870": "纳斯达克",
    "159660": "纳斯达克", "159696": "纳斯达克", "513110": "纳斯达克", "513390": "纳斯达克",
    "008971": "纳斯达克", "012870": "纳斯达克", "012871": "纳斯达克", "006480": "纳斯达克",
    "000055": "纳斯达克", "016055": "纳斯达克", "016057": "纳斯达克", "015299": "纳斯达克",
    "015300": "纳斯达克", "015518": "纳斯达克", "018966": "纳斯达克", "018967": "纳斯达克",
    "018968": "纳斯达克", "019524": "纳斯达克", "019525": "纳斯达克", "019547": "纳斯达克",
    "019548": "纳斯达克", "019441": "纳斯达克", "019442": "纳斯达克", "019736": "纳斯达克",
    "019737": "纳斯达克", "022525": "纳斯达克", "022664": "纳斯达克", "021000": "纳斯达克",
    "021773": "纳斯达克", "021838": "纳斯达克", "024237": "纳斯达克", "016534": "纳斯达克",
    "016535": "纳斯达克", "012751": "纳斯达克", "012753": "纳斯达克", "023422": "纳斯达克",
    "019173": "纳斯达克", "019174": "纳斯达克", "019175": "纳斯达克", "017436": "纳斯达克",
    "017437": "纳斯达克",
    # 标普500（场内ETF + 场外联接/指数，含各份额）
    "513500": "标普500", "050025": "标普500", "161125": "标普500", "007721": "标普500",
    "159612": "标普500", "159655": "标普500", "513650": "标普500", "006075": "标普500",
    "003718": "标普500", "012860": "标普500", "012861": "标普500", "019305": "标普500",
    "017028": "标普500", "017030": "标普500", "018064": "标普500", "018065": "标普500",
    "018066": "标普500", "018738": "标普500", "013425": "标普500", "013499": "标普500",
    "017642": "标普500", "017643": "标普500", "008401": "标普500", "013404": "标普500",
    "015545": "标普500", "007722": "标普500", "022523": "标普500",
    # 中概互联
    "513050": "中概互联", "164906": "中概互联", "006327": "中概互联",
    # 恒生
    "513330": "恒生互联", "513180": "恒生科技",
    # 主动
    "118001": "主动基金", "000041": "主动基金", "470888": "主动基金",
}
ANN_CORE = set(CORE.keys())  # 仅这些做公告交叉校验

# 手动覆盖额度（最高优先级，单位元，0=实质暂停）
OVERRIDES = {
    # "270042": 1000,
}

API = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
JJGG = "https://api.fund.eastmoney.com/f10/jjgg"
CONTENT = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
RANK = "http://fund.eastmoney.com/data/rankhandler.aspx"
SEARCH = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchPageAPI.ashx"
FALLBACK_FILE = os.path.join(SCRIPT_DIR, "fallback_codes.json")
HDR = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0 Mobile Safari/537.36",
    "Referer": "http://fundf10.eastmoney.com/",
}

# ── 名称分类规则（按优先级；不使用裸"美"，避免误匹配"美元"份额）──
# 注意：标普500规则必须排除"标普油气""标普消费""标普生物"等非500指数
CAT_RULES = [
    (r"纳斯达克100|纳斯达克.*ETF|纳指", "纳斯达克"), (r"标普500", "标普500"),
    (r"道琼斯", "道琼斯"),
    (r"日经", "日经225"), (r"德国|DAX", "德国"), (r"法国|CAC", "法国"),
    (r"越南", "越南"), (r"印度", "印度"), (r"日本", "日本"),
    (r"恒生科技", "恒生科技"), (r"恒生互联网|恒生互联", "恒生互联"),
    (r"恒生医疗|港股医疗", "恒生医疗"), (r"恒生消费", "恒生消费"),
    (r"恒生国企|H股", "恒生国企"), (r"恒生", "恒生"),
    (r"中概|中国互联|海外中国互联网|海外互联", "中概互联"),
    (r"原油|石油|油气", "油气"), (r"黄金|贵金属", "黄金"),
    (r"REIT|不动产|地产|房地产", "REITs"), (r"半导体|芯片", "半导体"),
    (r"医药|医疗|生物|健康", "医药"), (r"科技", "全球科技"), (r"互联网", "互联网"),
    (r"消费", "消费"), (r"新能源|光伏|电池|碳中和", "新能源"),
    (r"美股|美国", "美国"), (r"欧洲", "欧洲"), (r"亚太|亚洲", "亚太"),
    (r"新兴", "新兴市场"), (r"全球|世界", "全球"), (r"债|票息", "债券"),
]


def beijing_now():
    return (datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def http_text(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


def http_json(url):
    return json.loads(http_text(url))


def to_float(v, d=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def to_int(v, d=None):
    f = to_float(v, None)
    return int(f) if f is not None else d


def classify(name):
    if "ETF" in name and "联接" not in name:
        t = "etf"
    elif "LOF" in name:
        t = "lof"
    else:
        t = "qdii"
    return t


def categorize(name):
    for pat, cat in CAT_RULES:
        if re.search(pat, name):
            return cat
    return "其他"


def parse_status(sgzt):
    s = (sgzt or "").strip()
    if "暂停" in s:
        return "suspended", s
    if "限" in s:
        return "limited", s
    if "场内" in s:
        return "traded", s
    if "开放" in s or "正常" in s:
        return "normal", s
    return "normal", s or "未知"


def fmt_amount(n):
    if n is None:
        return "—"
    if n <= 0:
        return "0 元（实质暂停）"
    if n >= 100_000_000:
        return "%g 亿" % (n / 100_000_000)
    if n >= 10_000:
        return "%g 万" % (n / 10_000)
    return "%d 元" % n


def build_limit(status, maxsg_raw, code):
    if code in OVERRIDES:
        n = OVERRIDES[code]
        return n, fmt_amount(n), True, "manual"
    if status == "suspended":
        return None, "—", False, "api"
    if status == "traded":
        return None, "场内", False, "api"
    if status == "normal":
        return None, "不限", False, "api"
    maxsg = to_int(maxsg_raw, None)
    if maxsg is not None:
        return maxsg, fmt_amount(maxsg), True, "api"
    return None, "限大额", False, "api"


# ── 清单：自动发现 + 搜索补充 + 兜底 ──

# 搜索关键词 → 分组映射（用于发现 CORE 和 rankhandler 遗漏的基金）
SEARCH_KEYWORDS = {
    "标普500": "标普500",
    "纳斯达克": "纳斯达克",
}

# 搜索结果过滤：只保留名称真正匹配的基金
SEARCH_FILTERS = {
    "标普500": re.compile(r"标普500|标普.*500|S&P.?500", re.I),
    "纳斯达克": re.compile(r"纳斯达克|纳指|NASDAQ", re.I),
}


def search_funds(keyword, max_pages=8):
    """通过东方财富基金搜索 API 搜索基金，返回 [(code, name), ...]"""
    import urllib.parse
    results = []
    for page in range(1, max_pages + 1):
        try:
            url = ("%s?m=1&key=%s&pageindex=%d&pagesize=30&_=%d"
                   % (SEARCH, urllib.parse.quote(keyword), page, int(time.time())))
            raw = http_text(url)
            m = re.search(r"\{.*\}", raw, re.S)
            if not m:
                break
            datas = json.loads(m.group(0)).get("Datas") or []
            if not datas:
                break
            for d in datas:
                results.append((d.get("CODE", ""), d.get("NAME", "")))
        except Exception:
            break
        time.sleep(0.15)
    return results


def discover_universe():
    """返回 [code, ...]（成功且数量足够）或 None"""
    try:
        url = ("%s?op=ph&dt=kf&ft=qdii&rs=&gs=0&sc=zzf&st=desc&pi=1&pn=800"
               "&sd=2026-05-20&ed=2026-07-20&v=%d" % (RANK, int(time.time())))
        raw = http_text(url)
        pairs = re.findall(r'"(\d{6}),[^,"]+,', raw)
        return pairs if len(pairs) > 50 else None
    except Exception as e:
        print("[discover] 失败，将用兜底清单：%s" % e)
        return None


def load_fallback():
    try:
        with open(FALLBACK_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def build_codes():
    codes = list(CORE.keys())
    seen = set(codes)
    sources = []

    # 1. 搜索 API 补充标普500、纳斯达克等（发现 rankhandler 遗漏的 ETF 联接/指数基金）
    for keyword, group in SEARCH_KEYWORDS.items():
        try:
            results = search_funds(keyword)
            filt = SEARCH_FILTERS.get(group)
            added = 0
            for code, name in results:
                if code and code not in seen and filt and filt.search(name):
                    codes.append(code)
                    seen.add(code)
                    added += 1
            sources.append("搜索\"%s\"发现 %d 只（过滤后新增 %d）" % (keyword, len(results), added))
        except Exception as e:
            sources.append("搜索\"%s\"失败: %s" % (keyword, e))

    # 2. rankhandler 全量 QDII
    univ = discover_universe()
    if univ:
        sources.append("自动发现(rankhandler) %d 只" % len(univ))
        for c in univ:
            if c not in seen:
                codes.append(c)
                seen.add(c)
    else:
        fb = load_fallback()
        sources.append("兜底清单 fallback_codes.json %d 只" % len(fb))
        for c in fb:
            if c not in seen:
                codes.append(c)
                seen.add(c)

    src = " + ".join(sources)
    return codes, src


# ── 公告交叉校验（仅 CORE）──
ANN_INCLUDE = re.compile(r"(限制|暂停|调整|恢复).{0,6}(大额)?(申购|定期定额|转换转入)|大额申购|限额")
ANN_EXCLUDE = re.compile(r"节假日|休市|境外.{0,4}(休市|节假日)|清盘|分红|费率|销售(机构|渠道)|代销|"
                         r"基金经理|变更|估值|托管|成立|生效|招募|转托管|终止")
AMOUNT_RE = re.compile(
    r"(?:不超过|上限[为是]?|上限金额[为是]?|金额[为是]?|限额[为调是]?|限制金额[为是]?|"
    r"单日[^。；]{0,10}为|将[^。；]{0,10}为)\s*0*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*元")


def latest_limit_announcement(code):
    url = ("%s?callback=j&fundCode=%s&pageIndex=1&pageSize=120&type=5&_=%d000"
           % (JJGG, code, int(time.time())))
    try:
        raw = http_text(url)
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)).get("Data") or []
    except Exception:
        return None
    cands = [r for r in data if ANN_INCLUDE.search(r.get("TITLE", "") or "")
             and not ANN_EXCLUDE.search(r.get("TITLE", "") or "")]
    if not cands:
        return None
    cands.sort(key=lambda r: r.get("PUBLISHDATE", ""), reverse=True)
    return cands[0]


def extract_amounts(text):
    out = []
    for m in AMOUNT_RE.finditer(text):
        try:
            n = int(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
        if 1 <= n < 100_000_000:
            out.append(n)
    return sorted(set(out))


def announcement_supplement(code, current_limit):
    ann = latest_limit_announcement(code)
    if not ann:
        return None
    art_id = ann.get("ID") or ""
    body = ""
    if art_id:
        try:
            d = http_json("%s?art_code=%s&client_source=web&page_index=1" % (CONTENT, art_id))
            body = re.sub(r"<[^>]+>", "", d.get("data", {}).get("notice_content", "") or "")
        except Exception:
            body = ""
    limits = extract_amounts(body) if body else []
    return {
        "date": ann.get("PUBLISHDATEDesc") or "",
        "title": (ann.get("TITLE") or "")[:60],
        "artId": art_id,
        "limits": limits,
        "review": bool(limits and current_limit is not None and current_limit not in limits),
    }


def fetch_one(code, do_ann):
    url = ("%s?FCODE=%s&deviceid=1&plat=Android&appType=ttjj&product=EFund&version=6.2.6"
           % (API, code))
    datas = http_json(url).get("Datas") or {}
    if not datas:
        raise ValueError("无数据")

    name = datas.get("SHORTNAME") or ""
    status, status_text = parse_status(datas.get("SGZT"))
    limit, limit_text, reliable, source = build_limit(status, datas.get("MAXSG"), code)

    item = {
        "code": code,
        "name": name,
        "type": classify(name),
        "group": CORE.get(code) or categorize(name),   # 分组即展示分类
        "index": datas.get("INDEXNAME") or "",
        "company": datas.get("JJGS") or "",
        "status": status,
        "statusText": status_text,
        "limit": limit,
        "limitText": limit_text,
        "limitReliable": reliable,
        "limitSource": source,
        "minPurchase": to_int(datas.get("MINSG")),
        "nav": to_float(datas.get("DWJZ")),
        "navChangePct": to_float(datas.get("RZDF")),
        "navDate": datas.get("FSRQ") or "",
    }
    if do_ann and code in ANN_CORE and status in ("limited", "suspended"):
        try:
            ann = announcement_supplement(code, limit)
            if ann:
                item["announcement"] = ann
        except Exception as e:
            item["announcementError"] = str(e)
    return item


def main():
    out_path = "data.json"
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if args:
        out_path = args[0]
    do_ann = "--no-ann" not in sys.argv

    codes, src = build_codes()
    print("清单来源：%s → 共 %d 只基金" % (src, len(codes)))
    funds, errors = [], []
    for i, code in enumerate(codes, 1):
        try:
            item = fetch_one(code, do_ann)
            funds.append(item)
            if i <= 5 or i % 50 == 0:
                print("[%d/%d] %s %-22s %s %s" % (i, len(codes), code, item["name"][:22], item["status"], item["limitText"]))
        except Exception as e:
            errors.append((code, str(e)))
        time.sleep(0.18)

    result = {
        "updatedAt": beijing_now(),
        "source": "eastmoney FundMNNBasicInformation + rankhandler 发现",
        "universeSource": src,
        "note": "MAXSG 为可信当前限额；CORE 热门基金附公告交叉校验；分组按名称自动分类。",
        "count": len(funds),
        "failed": len(errors),
        "funds": funds,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n写入 %s：成功 %d，失败 %d" % (out_path, len(funds), len(errors)))
    if errors[:5]:
        print("失败示例：", errors[:5])


if __name__ == "__main__":
    main()
