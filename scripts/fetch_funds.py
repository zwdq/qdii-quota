#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_funds.py —— 每日抓取 QDII 基金申购状态 / 限额 / 净值，写入 data.json

数据源：
  状态/限额/净值：东方财富移动端 FundMNNBasicInformation（MAXSG 已验证可信，含紧限购）
  公告交叉校验（路径 B）：东方财富公告列表 jjgg(type=5) + 正文 np-cnotice-stock

运行：python3 scripts/fetch_funds.py [data.json]   无第三方依赖（仅标准库）

【关于"限额"精度的最终结论】
经公告正文交叉验证：MAXSG 字段是**可信的、按份额代码返回的当前限额**。
  - 040046：公告正文"不超过 10 元"，MAXSG=10 ✅ 一致
  - 因此即使 MAXSG 很小（如 10），也是真实限额，直接使用，不再标"见公告"。
路径 B（公告解析）作为**透明度补充**：
  - 附上最近一期限购公告的日期 / 标题 / 从正文抠出的金额。
  - 当公告金额与 MAXSG 不一致时打 review=true，提示人工核对。
  - 仍属 best-effort（标题措辞多变、多份额基金会有多个数字、需取最新公告）。

字段说明（来源 API -> 本文件字段）：
  SGZT 申购状态 -> status/statusText
  MAXSG 单日上限(元) -> limit（limited 时直接用，可信）
  DWJZ/RZDF/FSRQ -> nav/navChangePct/navDate
  公告 jjgg+正文 -> announcement{date,title,limits,review}
"""

import json
import re
import sys
import time
import datetime
import urllib.request

# ── 关注清单（代码, 分组）────────────────────────────────
WATCHLIST = [
    ("159941", "纳斯达克100"), ("513100", "纳斯达克100"), ("513300", "纳斯达克100"),
    ("161130", "纳斯达克100"), ("270042", "纳斯达克100"), ("006327", "纳斯达克100"),
    ("040046", "纳斯达克100"), ("160213", "纳斯达克100"), ("000834", "纳斯达克100"),
    ("513500", "标普500"), ("050025", "标普500"), ("161125", "标普500"), ("007721", "标普500"),
    ("513050", "中概互联"), ("513330", "恒生互联"), ("513180", "恒生科技"), ("164906", "中概互联"),
    ("118001", "亚洲精选"), ("000041", "全球精选"), ("470888", "全球互联"),
]

# ── 手动覆盖（最高优先级，看到公告填真实额度，单位元，0=实质暂停）──
OVERRIDES = {
    # "270042": 1000,
}

API = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
JJGG = "https://api.fund.eastmoney.com/f10/jjgg"
CONTENT = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
HDR = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/96.0 Mobile Safari/537.36",
    "Referer": "http://fundf10.eastmoney.com/",
}

# 限购公告标题：含这些词算限购相关
ANN_INCLUDE = re.compile(r"(限制|暂停|调整|恢复).{0,6}(大额)?(申购|定期定额|转换转入)|大额申购|限额")
# 排除纯运营类公告
ANN_EXCLUDE = re.compile(r"节假日|休市|境外.{0,4}(休市|节假日)|清盘|分红|费率|销售(机构|渠道)|代销|"
                         r"基金经理|变更|估值|托管|成立|生效|招募|分红|转托管|终止")
# 从正文抠金额：不超过/上限/限额 ... X 元
AMOUNT_RE = re.compile(
    r"(?:不超过|上限[为是]?|上限金额[为是]?|金额[为是]?|限额[为调是]?|限制金额[为是]?|"
    r"单日[^。；]{0,10}为|将[^。；]{0,10}为)\s*0*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*元")


def beijing_now():
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def http_json(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def http_text(url):
    req = urllib.request.Request(url, headers=HDR)
    with urllib.request.urlopen(req, timeout=20) as r:
        return r.read().decode("utf-8", "ignore")


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
        return "etf"
    if "LOF" in name:
        return "lof"
    return "qdii"


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
    """OVERRIDES > MAXSG（已验证可信）。返回 (limit, limitText, reliable, source)"""
    if code in OVERRIDES:
        n = OVERRIDES[code]
        return n, fmt_amount(n), True, "manual"
    if status == "suspended":
        return None, "—", False, "api"
    if status == "traded":
        return None, "场内", False, "api"
    if status == "normal":
        return None, "不限", False, "api"
    # limited：直接信任 MAXSG（按份额代码返回的当前值）
    maxsg = to_int(maxsg_raw, None)
    if maxsg is not None:
        return maxsg, fmt_amount(maxsg), True, "api"
    return None, "限大额", False, "api"


# ───────────── 路径 B：公告解析（交叉校验）─────────────
def latest_limit_announcement(code):
    """返回最近一期限购公告记录，或 None。"""
    url = ("%s?callback=j&fundCode=%s&pageIndex=1&pageSize=120&type=5&_=%d000"
           % (JJGG, code, int(time.time())))
    try:
        raw = http_text(url)
        m = re.search(r"\{.*\}", raw, re.S)
        data = json.loads(m.group(0)).get("Data") or []
    except Exception:
        return None
    cands = []
    for r in data:
        title = r.get("TITLE", "") or ""
        if ANN_INCLUDE.search(title) and not ANN_EXCLUDE.search(title):
            cands.append(r)
    if not cands:
        return None
    # 取公告日期最新
    cands.sort(key=lambda r: r.get("PUBLISHDATE", ""), reverse=True)
    return cands[0]


def extract_amounts(text):
    nums = []
    for m in AMOUNT_RE.finditer(text):
        raw = m.group(1).replace(",", "")
        try:
            n = int(float(raw))
        except ValueError:
            continue
        if 1 <= n < 100_000_000:  # 过滤掉费率/份额噪音
            nums.append(n)
    return sorted(set(nums))


def announcement_supplement(code, current_limit):
    """抓最近限购公告，抠金额，与 MAXSG 比对。返回 dict 或 None。"""
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
    review = bool(limits and current_limit is not None and current_limit not in limits)
    return {
        "date": ann.get("PUBLISHDATEDesc") or "",
        "title": (ann.get("TITLE") or "")[:60],
        "artId": art_id,
        "limits": limits,            # 正文抠出的全部金额（多份额会有多个）
        "review": review,            # 与 MAXSG 不符时提示人工核对
    }


def fetch_one(code, do_announcement):
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

    # 路径 B：仅对 limited/suspended 抓公告做交叉校验
    if do_announcement and status in ("limited", "suspended"):
        try:
            ann = announcement_supplement(code, limit)
            if ann:
                item["announcement"] = ann
        except Exception as e:
            item["announcementError"] = str(e)
    return item


def main():
    out_path = sys.argv[1] if len(sys.argv) > 1 else "data.json"
    do_ann = "--no-ann" not in sys.argv
    funds, errors = [], []
    for code, group in WATCHLIST:
        try:
            item = fetch_one(code, do_ann)
            item["group"] = group
            funds.append(item)
            ann = item.get("announcement")
            annstr = ""
            if ann:
                flag = " ⚠️与MAXSG不符" if ann.get("review") else ""
                annstr = " | 公告%s 抠出%s%s" % (ann["date"], ann["limits"], flag)
            print("[OK] %s %-24s %-10s %s%s"
                  % (code, item["name"][:24], item["status"], item["limitText"], annstr))
        except Exception as e:
            errors.append((code, str(e)))
            print("[FAIL] %s %s" % (code, e))
        time.sleep(0.3)

    result = {
        "updatedAt": beijing_now(),
        "source": "eastmoney FundMNNBasicInformation + jjgg 公告交叉校验",
        "note": "MAXSG 为可信当前限额；announcement 为公告交叉校验，review=true 表示与 MAXSG 不符需人工核对。",
        "count": len(funds),
        "failed": len(errors),
        "funds": funds,
    }
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print("\n写入 %s：成功 %d，失败 %d" % (out_path, len(funds), len(errors)))
    if errors:
        print("失败：", errors)


if __name__ == "__main__":
    main()
