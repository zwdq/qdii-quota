# QDII 基金每日额度追踪

一个**单 HTML 文件**的网页，统计中国 QDII 基金每天的 **申购状态 / 单日限额 / 净值**，靠 GitHub Actions 每天自动抓数据，**无需自建服务器，完全免费**。

## 它解决什么问题

个人投资者关心：哪些海外基金（纳指、标普、中概互联等）今天**还能不能买**、**每天限购多少钱**。这些数据天天变，纯静态 HTML 抓不到（CORS + 无公开跨域接口）。本项目用一个定时脚本绕开限制：

```
GitHub Actions 每天定时跑  →  scripts/fetch_funds.py 抓东方财富接口
                            →  写入 data.json 并自动 git 提交
                            →  GitHub Pages 托管静态网页，读取同源 data.json 渲染
```

## 文件结构

| 文件 | 作用 |
|---|---|
| [fund-quota.html](fund-quota.html) | 网页本体（单文件，搜索/筛选/排序/统计卡片） |
| [data.json](data.json) | 每天自动生成的数据，网页读取它 |
| [scripts/fetch_funds.py](scripts/fetch_funds.py) | 抓取脚本（仅标准库，无依赖） |
| [.github/workflows/daily.yml](.github/workflows/daily.yml) | 每天定时任务（北京时间 09:15、14:15） |

## 部署（3 步）

1. **建仓库 + 推代码**
   ```bash
   git init && git add . && git commit -m "init"
   git branch -M main
   git remote add origin git@github.com:<你的用户名>/fund-quota.git
   git push -u origin main
   ```

2. **开启 GitHub Pages**：仓库 → Settings → Pages → Source 选 **Deploy from a branch** → 分支选 `main` / `(root)` → Save。
   稍等片刻访问 `https://<你的用户名>.github.io/fund-quota/fund-quota.html`

3. **验证定时任务**：仓库 → Actions → 选「每日更新基金额度」→ Run workflow 手动跑一次，确认 `data.json` 被自动提交。

之后每天自动更新，打开网页即可，无需任何操作。

> 💡 Pages 每天会自动重新发布最新的 `data.json`，无需额外配置 CDN/缓存刷新。

## 加 / 删追踪基金

编辑 [scripts/fetch_funds.py](scripts/fetch_funds.py) 里的 `WATCHLIST`：

```python
WATCHLIST = [
    ("161130", "纳斯达克100"),   # (基金代码, 分组主题)
    ("006327", "纳斯达克100"),
    ...
]
```

提交后下次任务运行即生效。

## 本地预览

直接双击 `fund-quota.html` 会进入「本地模式」（显示示例数据，因为浏览器禁止 `file://` 读取 JSON）。要预览真实数据，起个本地服务：

```bash
python3 -m http.server 8000
# 浏览器打开 http://localhost:8000/fund-quota.html
```

或手动抓一次数据再预览：

```bash
python3 scripts/fetch_funds.py data.json
```

## 数据字段说明

| 字段 | 含义 | 来源 |
|---|---|---|
| `status` | `normal`(正常申购) / `limited`(限大额) / `suspended`(暂停) / `traded`(场内交易) | `SGZT` |
| `limit` | 单日申购上限，单位**元**，`null` = 不限/不适用 | `MAXSG`（已验证可信） |
| `limitText` | **可直接显示的限额文本**（如 `10 元` / `500 万` / `不限`），HTML 直接用它 | 脚本计算 |
| `limitSource` | `api`(接口) / `manual`(手动覆盖) | 脚本判断 |
| `announcement` | **公告交叉校验**（路径 B）：`{date, title, limits[], review}`，仅限购/暂停基金有 | 东方财富公告 jjgg + 正文 |
| `nav` / `navChangePct` / `navDate` | 单位净值 / 日涨跌幅(%) / 净值日期 | `DWJZ` / `RZDF` / `FSRQ` |
| `name` / `index` / `company` | 基金名称 / 跟踪指数 / 基金公司 | `SHORTNAME` / `INDEXNAME` / `JJGS` |

## 关于「限购额度」的精度

经公告正文交叉验证：**东方财富的 `MAXSG` 字段是可信的、按份额代码返回的当前限额**，包括紧限购（如 10 元/日）。例如 040046 公告正文明确"不超过 10 元"，`MAXSG` 正好返回 10。脚本直接采用 `MAXSG`，不再误判为"见公告"。

### 路径 B：公告交叉校验（已内置）

为防止接口与实际不符，脚本会对每只**限购/暂停**基金额外抓最近一期限购公告，从正文里抠出金额，写入 `announcement` 字段：

- `announcement.date` / `title`：最近一期限购公告的日期与标题。
- `announcement.limits`：从正文抠出的全部金额（多份额基金会是数组，如 `[10, 100]`）。
- `announcement.review`：当抠出的金额里**不含** `MAXSG` 时为 `true`——表示接口与公告可能不符，页面限额旁会出现 ⚠️，提示你核对。鼠标悬停限额单元格可见公告详情。

> 路径 B 属 best-effort：公告标题措辞多变（暂停/限制/调整/恢复）、需取最新公告、多份额难精确对应到某一代码。所以它只做**校验与提示**，不替代 `MAXSG`。加 `--no-ann` 参数可跳过公告抓取以提速。

### 手动覆盖（最高优先级）

若某只基金你想强制指定额度（例如路径 B 误报、或你有更准的一手信息），编辑 [scripts/fetch_funds.py](scripts/fetch_funds.py) 的 `OVERRIDES`，看到公告就填一次（单位：元，`0` = 实质暂停）：

```python
OVERRIDES = {
    "270042": 1000,   # 广发纳指联接：强制为 1000 元/日
}
```

填了的基金 `limitSource` 标为 `manual`，优先级高于接口与公告。

## 数据来源

东方财富移动端接口 `FundMNNBasicInformation` + 公告接口 `f10/jjgg` 与正文 `np-cnotice-stock`（天天基金 fund.eastmoney.com）。本仓库仅供个人追踪参考，不构成投资建议。
