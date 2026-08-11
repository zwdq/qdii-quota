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

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field


# ════════════════════════════════════════════════════════════════════════════
# 常量定义
# ════════════════════════════════════════════════════════════════════════════

SCRIPT_DIR: str = os.path.dirname(os.path.abspath(__file__))

# ── 接口 URL 统一管理 ──────────────────────────────────────────────────────
class Endpoints:
    """东方财富 / 腾讯行情接口 URL 集中管理"""

    # 基金基本信息（状态/限额/净值/费率/规模）
    FUND_INFO = "https://fundmobapi.eastmoney.com/FundMNewApi/FundMNNBasicInformation"
    # 全量基金名单（天天基金官方，含全部公募基金，无榜单缺漏）
    FUND_ALL = "https://fund.eastmoney.com/js/fundcode_search.js"
    # 基金公告列表
    ANNOUNCEMENT = "https://api.fund.eastmoney.com/f10/jjgg"
    # 公告正文
    ANNOUNCEMENT_CONTENT = "https://np-cnotice-stock.eastmoney.com/api/content/ann"
    # 全量 QDII 基金排名（自动发现）
    RANK = "http://fund.eastmoney.com/data/rankhandler.aspx"
    # 基金搜索
    SEARCH = "https://fundsuggest.eastmoney.com/FundSearch/api/FundSearchPageAPI.ashx"
    # 腾讯行情（ETF 溢价率）
    QT = "https://qt.gtimg.cn/q="


# ── HTTP 请求头 ─────────────────────────────────────────────────────────────
HTTP_HEADERS: dict[str, str] = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 10) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/96.0 Mobile Safari/537.36",
    "Referer": "http://fundf10.eastmoney.com/",
}

# ── 兜底清单文件路径 ────────────────────────────────────────────────────────
FALLBACK_FILE: str = os.path.join(SCRIPT_DIR, "fallback_codes.json")

# ── CORE 基金：精确分组 + 公告交叉校验 ─────────────────────────────────────
# 注意：只含跟踪纳斯达克100指数的基金，排除纳斯达克生物科技/科技/精选等
# 以下不是纳斯达克100，不纳入该分组：
#   017436/017437 华宝纳斯达克精选（主动选股，非跟踪100指数）
#   513290/017894/017895/017951/017952 纳斯达克生物科技
#   019118/017092 纳斯达克科技
CORE_FUNDS: dict[str, str] = {
    # 纳斯达克100（场内ETF + 场外联接/指数，含各份额）
    "159941": "纳斯达克100", "513100": "纳斯达克100", "513300": "纳斯达克100", "161130": "纳斯达克100",
    "270042": "纳斯达克100", "040046": "纳斯达克100", "160213": "纳斯达克100", "000834": "纳斯达克100",
    "159513": "纳斯达克100", "159659": "纳斯达克100", "159632": "纳斯达克100", "513870": "纳斯达克100",
    "159660": "纳斯达克100", "159696": "纳斯达克100", "513110": "纳斯达克100", "513390": "纳斯达克100",
    "008971": "纳斯达克100", "012870": "纳斯达克100", "012871": "纳斯达克100", "006480": "纳斯达克100",
    "000055": "纳斯达克100", "016055": "纳斯达克100", "016057": "纳斯达克100", "015299": "纳斯达克100",
    "015300": "纳斯达克100", "015518": "纳斯达克100", "018966": "纳斯达克100", "018967": "纳斯达克100",
    "018968": "纳斯达克100", "019524": "纳斯达克100", "019525": "纳斯达克100", "019547": "纳斯达克100",
    "019548": "纳斯达克100", "019441": "纳斯达克100", "019442": "纳斯达克100", "019736": "纳斯达克100",
    "019737": "纳斯达克100", "022525": "纳斯达克100", "022664": "纳斯达克100", "021000": "纳斯达克100",
    "021773": "纳斯达克100", "021838": "纳斯达克100", "024237": "纳斯达克100", "016534": "纳斯达克100",
    "016535": "纳斯达克100", "012751": "纳斯达克100", "012753": "纳斯达克100", "023422": "纳斯达克100",
    "019173": "纳斯达克100", "019174": "纳斯达克100", "019175": "纳斯达克100", "019172": "纳斯达克100",
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

# 仅 CORE 基金做公告交叉校验
ANN_CORE: set[str] = set(CORE_FUNDS.keys())

# 手动覆盖额度（最高优先级，单位元，0=实质暂停）
OVERRIDES: dict[str, int] = {
    # "270042": 1000,
}

# ── 名称分类规则（按优先级；不使用裸"美"，避免误匹配"美元"份额）──
# 注意：纳斯达克100规则排除"纳斯达克生物科技""纳斯达克科技"等非100指数
CAT_RULES: list[tuple[str, str]] = [
    (r"纳斯达克生物科技|纳斯达克科技", "全球科技"),
    (r"纳斯达克100|纳斯达克.*ETF|纳指(?!.*生物)(?!.*科技)", "纳斯达克100"),
    (r"标普500", "标普500"),
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

# 搜索关键词 → 分组映射（用于发现 CORE 和 rankhandler 遗漏的 ETF 联接/指数基金）
SEARCH_KEYWORDS: dict[str, str] = {
    "标普500": "标普500",
    "纳斯达克": "纳斯达克100",
}

# 搜索结果过滤：只保留名称真正匹配的基金
SEARCH_FILTERS: dict[str, re.Pattern] = {
    "标普500": re.compile(r"标普500|标普.*500|S&P.?500", re.I),
    "纳斯达克100": re.compile(r"纳斯达克100|纳斯达克.*ETF|纳指(?!.*生物)(?!.*科技)", re.I),
}

# ── 公告交叉校验正则 ────────────────────────────────────────────────────────
ANN_INCLUDE: re.Pattern = re.compile(
    r"(限制|暂停|调整|恢复).{0,6}(大额)?(申购|定期定额|转换转入)|大额申购|限额|金额限制"
)
ANN_EXCLUDE: re.Pattern = re.compile(
    r"节假日|休市|境外.{0,4}(休市|节假日)|清盘|分红|费率|销售(机构|渠道)|代销|"
    r"基金经理|变更|估值|托管|成立|生效|招募|转托管|终止"
)
AMOUNT_RE: re.Pattern = re.compile(
    r"(?:不超过|上限[为是]?|上限金额[为是]?|金额[为是]?|限额[为调是]?|限制金额[为是]?|"
    r"单日[^。；]{0,10}为|将[^。；]{0,10}为)\s*0*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)\s*元"
)

# ── 网络重试配置 ────────────────────────────────────────────────────────────
HTTP_MAX_RETRIES: int = 3
HTTP_RETRY_DELAY: float = 1.0


# ════════════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════════════

def beijing_now() -> str:
    """当前北京时间（UTC+8）"""
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")


def to_float(v: object, d: float | None = None) -> float | None:
    """安全转 float，失败返回默认值"""
    try:
        return float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return d


def to_int(v: object, d: int | None = None) -> int | None:
    """安全转 int，失败返回默认值"""
    f = to_float(v, None)
    return int(f) if f is not None else d


def parse_fee(s: str | None) -> float | None:
    """'1.10%' → 1.10, '--' → None"""
    if not s or s == "--":
        return None
    m = re.search(r"(\d+\.?\d*)\s*%", str(s))
    return to_float(m.group(1)) if m else None


def fmt_scale(n: float | None) -> float | None:
    """规模格式化：元 → 亿（小于1亿不显示）"""
    if n is None or n == 0:
        return None
    if n >= 1_0000_0000:
        return round(n / 1_0000_0000, 2)
    return None


def fmt_amount(n: int | None) -> str:
    """限额金额格式化"""
    if n is None:
        return "—"
    if n <= 0:
        return "0 元（实质暂停）"
    if n >= 100_000_000:
        return "%g 亿" % (n / 100_000_000)
    if n >= 10_000:
        return "%g 万" % (n / 10_000)
    return "%d 元" % n


def classify(name: str) -> str:
    """基金类型分类：ETF / LOF / QDII"""
    if "ETF" in name and "联接" not in name:
        return "etf"
    if "LOF" in name:
        return "lof"
    return "qdii"


def categorize(name: str) -> str:
    """按名称关键词自动分类（CAT_RULES 按优先级匹配）"""
    for pat, cat in CAT_RULES:
        if re.search(pat, name):
            return cat
    return "其他"


def parse_status(sgzt: str | None) -> tuple[str, str]:
    """解析申购状态，返回 (状态码, 原始文本)"""
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


def build_limit(status: str, maxsg_raw: str | None, code: str) -> tuple[int | None, str, bool, str]:
    """根据状态和接口数据构建限额信息

    返回 (limit, limit_text, reliable, source)
    """
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


def extract_amounts(text: str) -> list[int]:
    """从公告正文中提取金额（1 ~ 1亿元之间）"""
    out: list[int] = []
    for m in AMOUNT_RE.finditer(text):
        try:
            n = int(float(m.group(1).replace(",", "")))
        except ValueError:
            continue
        if 1 <= n < 100_000_000:
            out.append(n)
    return sorted(set(out))


# ════════════════════════════════════════════════════════════════════════════
# HTTP 客户端（带重试）
# ════════════════════════════════════════════════════════════════════════════

class HttpClient:
    """HTTP 请求封装，支持自动重试"""

    def __init__(self, max_retries: int = HTTP_MAX_RETRIES,
                 retry_delay: float = HTTP_RETRY_DELAY) -> None:
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def _request(self, url: str) -> str:
        """发起 HTTP GET 请求（带重试），返回响应文本"""
        last_err: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                req = urllib.request.Request(url, headers=HTTP_HEADERS)
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.read().decode("utf-8", "ignore")
            except Exception as e:
                last_err = e
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay * attempt)
        raise last_err  # type: ignore[misc]

    def get_text(self, url: str) -> str:
        """GET 请求，返回纯文本"""
        return self._request(url)

    def get_json(self, url: str) -> dict:
        """GET 请求，返回解析后的 JSON dict"""
        return json.loads(self._request(url))


# ════════════════════════════════════════════════════════════════════════════
# 基金清单构建器
# ════════════════════════════════════════════════════════════════════════════

class FundUniverse:
    """基金清单构建：CORE + 搜索补充 + rankhandler 自动发现 / 兜底"""

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def build(self) -> tuple[list[str], str]:
        """构建基金代码清单，返回 (codes, 来源描述)"""
        codes: list[str] = list(CORE_FUNDS.keys())
        seen: set[str] = set(codes)
        sources: list[str] = []

        # 1. 搜索 API 补充标普500、纳斯达克等
        for keyword, group in SEARCH_KEYWORDS.items():
            try:
                results = self._search_funds(keyword)
                filt = SEARCH_FILTERS.get(group)
                added = 0
                for code, name in results:
                    if code and code not in seen and filt and filt.search(name):
                        codes.append(code)
                        seen.add(code)
                        added += 1
                sources.append('搜索"%s"发现 %d 只（过滤后新增 %d）' % (keyword, len(results), added))
            except Exception as e:
                sources.append('搜索"%s"失败: %s' % (keyword, e))

        # 2. rankhandler 全量 QDII
        univ = self._discover_universe()
        if univ:
            sources.append("自动发现(rankhandler) %d 只" % len(univ))
            for c in univ:
                if c not in seen:
                    codes.append(c)
                    seen.add(c)
        else:
            fb = self._load_fallback()
            sources.append("兜底清单 fallback_codes.json %d 只" % len(fb))
            for c in fb:
                if c not in seen:
                    codes.append(c)
                    seen.add(c)

        return codes, " + ".join(sources)

    def _search_funds(self, keyword: str, max_pages: int = 8) -> list[tuple[str, str]]:
        """通过东方财富基金搜索 API 搜索基金，返回 [(code, name), ...]"""
        results: list[tuple[str, str]] = []
        for page in range(1, max_pages + 1):
            try:
                url = ("%s?m=1&key=%s&pageindex=%d&pagesize=30&_=%d"
                       % (Endpoints.SEARCH, urllib.parse.quote(keyword), page, int(time.time())))
                raw = self.http.get_text(url)
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

    def _discover_universe(self) -> list[str] | None:
        """天天基金全量名单筛 QDII/海外型（含指数型-海外股票等非标分类）。
        替代 rankhandler 榜单——实测 rankhandler 缺整个摩根基金（2026-08-11 019172 事件）。"""
        try:
            raw = self.http.get_text(Endpoints.FUND_ALL)
            rows = re.findall(r'\["(\d{6})","[^"]*","([^"]*)","([^"]*)"', raw)
            codes = [c for c, name, typ in rows
                     if 'QDII' in typ.upper() or 'QDII' in name.upper() or '海外' in typ]
            return codes if len(codes) > 100 else None
        except Exception as e:
            print("[discover] fundcode_search 失败(%s)，退回 rankhandler" % e)
        # 兜底1：rankhandler 榜单（已知缺摩根等公司）
        try:
            url = ("%s?op=ph&dt=kf&ft=qdii&rs=&gs=0&sc=zzf&st=desc&pi=1&pn=800"
                   "&sd=2026-05-20&ed=2026-07-20&v=%d" % (Endpoints.RANK, int(time.time())))
            raw = self.http.get_text(url)
            pairs = re.findall(r'"(\d{6}),[^,",]+,', raw)
            return pairs if len(pairs) > 50 else None
        except Exception as e:
            print("[discover] 失败，将用兜底清单：%s" % e)
            return None

    @staticmethod
    def _load_fallback() -> list[str]:
        """加载兜底清单"""
        try:
            with open(FALLBACK_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []


# ════════════════════════════════════════════════════════════════════════════
# 公告校验器
# ════════════════════════════════════════════════════════════════════════════

class AnnouncementChecker:
    """公告交叉校验：拉取公告列表 → 提取正文 → 校验额度是否一致"""

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def _latest_limit_announcement(self, code: str, fund_name: str = "") -> dict | None:
        """获取最近一条限额相关公告；I类/E类等份额优先匹配带类标的公告"""
        url = ("%s?callback=j&fundCode=%s&pageIndex=1&pageSize=120&type=5&_=%d000"
               % (Endpoints.ANNOUNCEMENT, code, int(time.time())))
        try:
            raw = self.http.get_text(url)
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)).get("Data") or []
        except Exception:
            return None
        cands = [r for r in data
                 if ANN_INCLUDE.search(r.get("TITLE", "") or "")
                 and not ANN_EXCLUDE.search(r.get("TITLE", "") or "")]
        if not cands:
            return None
        # PUBLISHDATE 常为空，改用公告 ID 内嵌时间戳排序（AN2026072018... 字典序=时间序）
        cands.sort(key=lambda r: r.get("ID", "") or "", reverse=True)
        # 份额类标优先（如 021000 是 I 类，优先选标题含"I类"的公告——金额和 A/C 类不同）
        mcls = re.search(r"\(?([A-Z])\)?$", fund_name.strip())
        if mcls:
            tag = mcls.group(1) + "类"
            specific = [r for r in cands if tag in (r.get("TITLE") or "")]
            if specific:
                return specific[0]
        return cands[0]

    def supplement(self, code: str, current_limit: int | None, fund_name: str = "") -> dict | None:
        """获取公告并构建校验结果

        返回 {"date", "title", "artId", "limits", "review"} 或 None
        """
        ann = self._latest_limit_announcement(code, fund_name)
        if not ann:
            return None
        art_id = ann.get("ID") or ""
        body = ""
        if art_id:
            try:
                d = self.http.get_json(
                    "%s?art_code=%s&client_source=web&page_index=1"
                    % (Endpoints.ANNOUNCEMENT_CONTENT, art_id)
                )
                body = re.sub(r"<[^>]+>", "",
                              d.get("data", {}).get("notice_content", "") or "")
            except Exception:
                body = ""
        limits = extract_amounts(body) if body else []
        return {
            "date": ann.get("PUBLISHDATEDesc") or "",
            "title": (ann.get("TITLE") or "")[:60],
            "artId": art_id,
            "limits": limits,
            "review": bool(limits and current_limit is not None
                           and current_limit not in limits),
        }


# ════════════════════════════════════════════════════════════════════════════
# ETF 溢价率获取
# ════════════════════════════════════════════════════════════════════════════

class EtfPremiumFetcher:
    """ETF 场内品种溢价率（腾讯行情接口）"""

    def __init__(self, http: HttpClient) -> None:
        self.http = http

    def fetch(self, code: str) -> dict | None:
        """返回 {"price", "iopvNav", "premium"} 或 None"""
        # 0/1 前缀：深交所=sz, 上交所=sh
        prefix = "sh" if code.startswith(("5", "11", "13")) else "sz"
        try:
            raw = self.http.get_text("%s%s%s" % (Endpoints.QT, prefix, code))
            m = re.search(r'"([^"]+)"', raw)
            if not m:
                return None
            parts = m.group(1).split("~")
            if len(parts) < 79:
                return None
            price = to_float(parts[3])
            nav = to_float(parts[78])  # 基金净值
            if not price or not nav or nav <= 0:
                return None
            premium = round((price - nav) / nav * 100, 2)
            return {"price": price, "iopvNav": nav, "premium": premium}
        except Exception:
            return None


# ════════════════════════════════════════════════════════════════════════════
# 基金数据抓取器
# ════════════════════════════════════════════════════════════════════════════

class FundFetcher:
    """单只基金数据抓取：获取基本信息 → 字段映射 → 溢价率 → 公告校验"""

    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.ann_checker = AnnouncementChecker(http)
        self.etf_premium = EtfPremiumFetcher(http)

    # ── 数据获取 ──────────────────────────────────────────────────────────

    def _fetch_raw_data(self, code: str) -> dict:
        """从东方财富接口获取基金基本信息原始数据"""
        url = ("%s?FCODE=%s&deviceid=1&plat=Android&appType=ttjj&product=EFund&version=6.2.6"
               % (Endpoints.FUND_INFO, code))
        datas = self.http.get_json(url).get("Datas") or {}
        if not datas:
            raise ValueError("无数据")
        return datas

    # ── 字段映射 ──────────────────────────────────────────────────────────

    @staticmethod
    def _map_fields(code: str, datas: dict) -> dict:
        """将接口原始数据映射为前端所需的输出字段"""
        name = datas.get("SHORTNAME") or ""
        status, status_text = parse_status(datas.get("SGZT"))
        limit, limit_text, reliable, source = build_limit(status, datas.get("MAXSG"), code)
        fund_type = classify(name)

        return {
            "code": code,
            "name": name,
            "type": fund_type,
            "group": CORE_FUNDS.get(code) or categorize(name),  # 分组即展示分类
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
            # 费率
            "mgmtFee": parse_fee(datas.get("HRGRT")),        # 管理费率 %
            "custodyFee": parse_fee(datas.get("HSGRT")),      # 托管费率 %
            "purchaseFee": parse_fee(datas.get("RATE")),      # 申购费率(折后) %
            "purchaseFeeOrig": parse_fee(datas.get("SOURCERATE")),  # 申购费率(原) %
            # 规模
            "scale": fmt_scale(to_float(datas.get("ENDNAV"))),  # 亿元
            "scaleDate": datas.get("FEGMRQ") or "",              # 规模日期
        }

    # ── 溢价率补充 ────────────────────────────────────────────────────────

    def _add_premium(self, item: dict) -> None:
        """ETF 场内品种：拉溢价率并写入 item"""
        if item["type"] != "etf":
            return
        premium_data = self.etf_premium.fetch(item["code"])
        if premium_data:
            item["etfPrice"] = premium_data["price"]
            item["etfPremium"] = premium_data["premium"]

    # ── 公告校验 ──────────────────────────────────────────────────────────

    def _add_announcement(self, item: dict, do_ann: bool) -> None:
        """CORE 基金且状态为限大额/暂停时：拉公告交叉校验；API 无额度时用公告金额兜底"""
        code = item["code"]
        if not (do_ann and code in ANN_CORE and item["status"] in ("limited", "suspended")):
            return
        try:
            ann = self.ann_checker.supplement(code, item["limit"], item.get("name", ""))
            if ann:
                item["announcement"] = ann
                # API MAXSG='--'（未公布，如 I 类份额）→ 用公告抠出的金额当限额
                if item["limit"] is None and item["status"] == "limited" and ann.get("limits"):
                    amt = max(ann["limits"])
                    item["limit"] = amt
                    item["limitText"] = fmt_amount(amt)
                    item["limitReliable"] = True
                    item["limitSource"] = "ann"
        except Exception as e:
            item["announcementError"] = str(e)

    # ── 费率兜底（F10 费率页）────────────────────────────────────────────

    def _fee_fallback(self, item: dict) -> None:
        """App API 对部分份额（如 I 类）费率返回 '--' 或错误的 0.00%，
        此时刮 F10 费率页补全（管理/托管费率）。mgmtFee 缺失即触发。"""
        if item.get("mgmtFee") is not None:
            return
        try:
            raw = self.http.get_text(
                "https://fundf10.eastmoney.com/jjfl_%s.html" % item["code"])
            text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw))
            m = re.search(r"管理费率[^0-9]{0,20}([0-9.]+)%", text)
            if m:
                item["mgmtFee"] = float(m.group(1))
            m = re.search(r"托管费率[^0-9]{0,20}([0-9.]+)%", text)
            if m:
                item["custodyFee"] = float(m.group(1))
        except Exception:
            pass

    # ── 主入口 ────────────────────────────────────────────────────────────

    def fetch_one(self, code: str, do_ann: bool) -> dict:
        """抓取单只基金完整数据"""
        datas = self._fetch_raw_data(code)
        item = self._map_fields(code, datas)
        self._fee_fallback(item)
        self._add_premium(item)
        self._add_announcement(item, do_ann)
        return item


# ════════════════════════════════════════════════════════════════════════════
# 主程序
# ════════════════════════════════════════════════════════════════════════════

class QdiiQuotaApp:
    """QDII 限额抓取主程序"""

    def __init__(self) -> None:
        self.http = HttpClient()
        self.universe = FundUniverse(self.http)
        self.fetcher = FundFetcher(self.http)

    @staticmethod
    def _parse_args() -> argparse.Namespace:
        """解析命令行参数"""
        parser = argparse.ArgumentParser(
            description="每日抓取 QDII 基金申购状态 / 限额 / 净值，写入 data.json"
        )
        parser.add_argument(
            "output", nargs="?", default="data.json",
            help="输出 JSON 文件路径（默认：data.json）"
        )
        parser.add_argument(
            "--no-ann", action="store_true",
            help="跳过公告交叉校验（加快速度）"
        )
        return parser.parse_args()

    def run(self) -> None:
        """主流程：构建清单 → 逐只抓取 → 写入 JSON"""
        args = self._parse_args()
        out_path: str = args.output
        do_ann: bool = not args.no_ann

        # 1. 构建基金代码清单
        codes, src = self.universe.build()
        print("清单来源：%s → 共 %d 只基金" % (src, len(codes)))

        # 2. 逐只抓取
        funds: list[dict] = []
        errors: list[tuple[str, str]] = []
        for i, code in enumerate(codes, 1):
            try:
                item = self.fetcher.fetch_one(code, do_ann)
                funds.append(item)
                if i <= 5 or i % 50 == 0:
                    print("[%d/%d] %s %-22s %s %s" % (
                        i, len(codes), code, item["name"][:22],
                        item["status"], item["limitText"]
                    ))
            except Exception as e:
                errors.append((code, str(e)))
            time.sleep(0.18)

        # 3. 组装结果并写入
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

        # 4. 输出统计
        print("\n写入 %s：成功 %d，失败 %d" % (out_path, len(funds), len(errors)))
        if errors[:5]:
            print("失败示例：", errors[:5])


# ════════════════════════════════════════════════════════════════════════════
# 入口
# ════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    QdiiQuotaApp().run()
