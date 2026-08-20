#!/usr/bin/env python3
"""
ewave-radar 核心引擎
1. akshare 拉上证指数日K
2. 艾略特波浪 + 斐波那契推演
3. 调智谱 GLM 生成"今日信号"解读
4. 输出 HTML 静态页
"""

import json
import os
import sys
import datetime
import requests

# ============================================================
# 1. 数据获取 — 上证指数日K
# ============================================================

def fetch_sse_index(days=250):
    """
    拉取上证指数日K数据。
    优先腾讯财经接口，失败降级 akshare / 东财。
    返回 list[dict]: {date, open, high, low, close, volume}
    """
    # 腾讯财经（最稳定）
    try:
        records = fetch_sse_tencent(days)
        if records:
            return records
    except Exception as e:
        print(f"[WARN] 腾讯接口失败: {e}")

    # 降级 akshare
    try:
        import akshare as ak
        df = ak.stock_zh_index_daily_em(symbol="sh000001")
        df = df.tail(days).copy()
        records = []
        for _, row in df.iterrows():
            records.append({
                "date": str(row["date"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0)),
            })
        return records
    except Exception as e:
        print(f"[WARN] akshare 失败: {e}，降级东财接口")
        return fetch_sse_eastmoney(days)


def fetch_sse_tencent(days=250):
    """腾讯财经接口 — 沙箱可用"""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    params = {
        "param": f"sh000001,day,,,{days},qfq",
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    data = resp.json()
    day_list = data.get("data", {}).get("sh000001", {}).get("day", [])
    records = []
    for item in day_list:
        # [date, open, close, high, low, volume]
        records.append({
            "date": item[0],
            "open": float(item[1]),
            "close": float(item[2]),
            "high": float(item[3]),
            "low": float(item[4]),
            "volume": float(item[5]) if len(item) > 5 else 0,
        })
    return records


def fetch_sse_eastmoney(days=250):
    """东财直连接口降级方案"""
    url = "http://push2his.eastmoney.com/api/qt/stock/kline/get"
    params = {
        "secid": "1.000001",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "klt": "101",   # 日K
        "fqt": "0",
        "end": "20500101",
        "lmt": str(days),
    }
    headers = {"User-Agent": "Mozilla/5.0"}
    resp = requests.get(url, params=params, headers=headers, timeout=15)
    data = resp.json()
    klines = data.get("data", {}).get("klines", [])
    records = []
    for k in klines:
        parts = k.split(",")
        records.append({
            "date": parts[0],
            "open": float(parts[1]),
            "close": float(parts[2]),
            "high": float(parts[3]),
            "low": float(parts[4]),
            "volume": float(parts[5]),
        })
    return records


# ============================================================
# 2. 艾略特波浪推演 + 斐波那契
# ============================================================

def find_swings(klines, window=5):
    """
    简化版波段识别：用 N 根K线窗口找局部高低点。
    返回 list[dict]: {type: "high"/"low", index, price, date}
    """
    swings = []
    n = len(klines)
    for i in range(window, n - window):
        high = klines[i]["high"]
        low = klines[i]["low"]
        is_high = all(high >= klines[j]["high"] for j in range(i - window, i + window + 1) if j != i)
        is_low = all(low <= klines[j]["low"] for j in range(i - window, i + window + 1) if j != i)
        if is_high:
            swings.append({"type": "high", "index": i, "price": high, "date": klines[i]["date"]})
        if is_low:
            swings.append({"type": "low", "index": i, "price": low, "date": klines[i]["date"]})
    return swings


def detect_elliott_waves(klines):
    """
    艾略特波浪检测（简化版）。
    基于波段序列匹配 5 浪驱动 + 3 浪调整结构。

    驱动浪标准结构（上升趋势）：
      1(low) → 2(high) → 3(low) → 4(high) → 5(low) → A(high) → B(low) → C(high)
    即：从低点起步，交替标注。

    如果最近波段从高点起步（下降趋势），则调整标签起点。
    返回波浪标注列表。
    """
    swings = find_swings(klines, window=5)
    # 过滤过近的波段（相邻同类型合并取极值）
    filtered = []
    for s in swings:
        if not filtered or filtered[-1]["type"] != s["type"]:
            filtered.append(s)
        else:
            # 同类型相邻，保留更极端的
            if s["type"] == "high" and s["price"] > filtered[-1]["price"]:
                filtered[-1] = s
            elif s["type"] == "low" and s["price"] < filtered[-1]["price"]:
                filtered[-1] = s

    # 取最近 8 个波段
    recent = filtered[-8:] if len(filtered) >= 8 else filtered
    if len(recent) < 5:
        return []

    # 确保波段从 low 开始（驱动浪标准起点）
    # 如果第一个是 high，往后移一位
    start_idx = 0
    if recent[0]["type"] == "high":
        start_idx = 1
    recent = recent[start_idx:]

    if len(recent) < 5:
        return []

    # 标注 1-2-3-4-5-A-B-C
    all_labels = ["1", "2", "3", "4", "5", "A", "B", "C"]
    waves = []
    for i, s in enumerate(recent):
        if i >= len(all_labels):
            break
        waves.append({
            "label": all_labels[i],
            "type": s["type"],
            "price": s["price"],
            "date": s["date"],
            "index": s["index"],
        })

    return waves


def calc_fibonacci(waves, klines):
    """
    基于检测到的波浪计算斐波那契回撤位。
    基准：波浪区间最低点 → 最高点
    回撤位 = 高点 - (高点 - 低点) × ratio
    """
    fib_levels = {}
    if len(waves) < 2:
        return fib_levels

    # 找波浪区间最低点和最高点
    all_prices = [w["price"] for w in waves]
    swing_low = min(all_prices)
    swing_high = max(all_prices)
    diff = swing_high - swing_low

    if diff <= 0:
        return fib_levels

    # 斐波那契回撤：从高点往下回撤
    fib_ratios = {
        "0.000": 0.0,    # 高点本身
        "0.236": 0.236,
        "0.382": 0.382,
        "0.500": 0.500,
        "0.618": 0.618,
        "0.786": 0.786,
        "1.000": 1.0,     # 低点本身
    }
    for name, ratio in fib_ratios.items():
        fib_levels[name] = round(swing_high - diff * ratio, 2)

    return fib_levels


def analyze_current_position(klines, waves, fib_levels):
    """
    分析当前价格在波浪结构中的位置，生成信号。
    """
    if not klines or not waves:
        return {"signal": "数据不足", "detail": "无法完成波浪推演", "action": "观望"}

    latest = klines[-1]
    latest_close = latest["close"]
    latest_date = latest["date"]

    # 判断当前可能处于哪一浪
    last_wave = waves[-1]
    current_wave_guess = "未知"

    if last_wave["label"] in ["1", "2", "3", "4"]:
        next_label = str(int(last_wave["label"]) + 1)
        current_wave_guess = f"可能处于第 {next_label} 浪"
    elif last_wave["label"] == "5":
        current_wave_guess = "5 浪可能结束，进入调整 (A-B-C)"
    elif last_wave["label"] in ["A", "B"]:
        next_label = {"A": "B", "B": "C"}[last_wave["label"]]
        current_wave_guess = f"可能处于 {next_label} 浪"
    elif last_wave["label"] == "C":
        current_wave_guess = "C 浪可能结束，新一轮周期启动"

    # 关键位判断
    nearest_fib = None
    nearest_dist = float("inf")
    for name, level in fib_levels.items():
        dist = abs(latest_close - level) / latest_close
        if dist < nearest_dist:
            nearest_dist = dist
            nearest_fib = name

    # 信号生成
    signal_parts = []
    action = "观望"

    if nearest_fib in ["0.382", "0.500", "0.618"] and nearest_dist < 0.015:
        signal_parts.append(f"价格接近斐波那契 {nearest_fib} 回撤位 ({fib_levels[nearest_fib]})")
        if last_wave["label"] in ["1", "2"]:
            action = "试仓进场"
        elif last_wave["label"] in ["3", "5"]:
            action = "持有/滚仓"
        elif last_wave["label"] in ["A", "B"]:
            action = "轻仓观察"
    elif nearest_fib == "0.786" and nearest_dist < 0.015:
        signal_parts.append(f"价格接近深回撤位 0.786 ({fib_levels[nearest_fib]})")
        action = "谨慎抄底"
    else:
        signal_parts.append(f"价格 {latest_close} 距斐波那契关键位较远")

    return {
        "signal": current_wave_guess,
        "detail": "; ".join(signal_parts),
        "action": action,
        "latest_close": latest_close,
        "latest_date": latest_date,
        "nearest_fib": nearest_fib,
        "nearest_fib_price": fib_levels.get(nearest_fib, 0),
        "last_wave": last_wave["label"],
    }


# ============================================================
# 3. 智谱 GLM 生成"今日信号"解读
# ============================================================

def generate_glm_analysis(klines, waves, fib_levels, position, api_key):
    """
    调用智谱 GLM-4 生成今日信号解读文字。
    """
    if not api_key:
        return "（未配置智谱 API key，使用默认信号）"

    # 准备 prompt
    recent_klines = klines[-20:]
    kline_summary = "\n".join(
        f"  {k['date']}: 开{k['open']} 高{k['high']} 低{k['low']} 收{k['close']}"
        for k in recent_klines
    )
    wave_summary = "\n".join(
        f"  浪{w['label']}: {w['date']} {w['type']}={w['price']}"
        for w in waves
    ) if waves else "  （波浪检测不足）"

    fib_summary = "\n".join(
        f"  {name}: {level}" for name, level in fib_levels.items()
    )

    prompt = f"""你是一位艾略特波浪理论分析师，请基于以下数据生成简洁的"今日信号"解读（200字以内）：

【上证指数近20日K】
{kline_summary}

【波浪标注】
{wave_summary}

【斐波那契关键位】
{fib_summary}

【自动推演结果】
当前: {position.get('signal','')} 
信号: {position.get('detail','')}
建议: {position.get('action','')}

请输出：
1. 今日信号（一句话总结当前波浪位置和关键位）
2. 操作建议（试仓/滚仓/观望，附理由）
3. 风险提示（一句话）

格式要求：每条一行，简洁有力。"""

    url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "glm-4",
        "messages": [
            {"role": "user", "content": prompt}
        ],
        "temperature": 0.7,
        "max_tokens": 500,
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        return content.strip()
    except Exception as e:
        print(f"[WARN] 智谱 API 调用失败: {e}")
        return f"（智谱解读生成失败，使用默认信号）\n{position.get('signal','')} — {position.get('detail','')}"


# ============================================================
# 4. 生成 HTML
# ============================================================

def generate_html(klines, waves, fib_levels, position, glm_text):
    """生成静态 HTML 页面"""

    latest = klines[-1] if klines else {}
    # 统一用中国时间 (UTC+8)，避免沙箱/runner 时区不一致
    cn_now = datetime.datetime.utcnow() + datetime.timedelta(hours=8)
    today_str = cn_now.strftime("%Y-%m-%d")

    # 构建波浪数据给 JS 用
    waves_json = json.dumps(waves, ensure_ascii=False)
    fib_json = json.dumps(fib_levels, ensure_ascii=False)
    klines_json = json.dumps(klines[-60:], ensure_ascii=False)  # 近60日
    position_json = json.dumps(position, ensure_ascii=False)

    # GLM 文本转 HTML 安全
    glm_html = glm_text.replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>\n")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>艾略特波浪推演 · 上证指数</title>
<style>
:root {{
  --bg: #0d1117;
  --card: #161b22;
  --border: #30363d;
  --text: #c9d1d9;
  --text-dim: #8b949e;
  --green: #3fb950;
  --red: #f85149;
  --yellow: #d29922;
  --blue: #58a6ff;
  --accent: #1f6feb;
}}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
  padding: 12px;
  max-width: 100%;
}}
.header {{
  text-align: center;
  padding: 16px 0;
  border-bottom: 1px solid var(--border);
  margin-bottom: 16px;
}}
.header h1 {{ font-size: 1.3rem; color: var(--blue); }}
.header .subtitle {{ font-size: 0.8rem; color: var(--text-dim); margin-top: 4px; }}

/* 信号卡片 */
.signal-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}}
.signal-card h2 {{
  font-size: 1rem;
  color: var(--yellow);
  margin-bottom: 12px;
  display: flex;
  align-items: center;
  gap: 8px;
}}
.signal-card .badge {{
  background: var(--accent);
  color: #fff;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 0.7rem;
}}
.signal-row {{
  display: flex;
  justify-content: space-between;
  padding: 8px 0;
  border-bottom: 1px solid var(--border);
  font-size: 0.85rem;
}}
.signal-row:last-child {{ border-bottom: none; }}
.signal-row .label {{ color: var(--text-dim); }}
.signal-row .value {{ font-weight: bold; }}
.action-tag {{
  display: inline-block;
  padding: 4px 12px;
  border-radius: 4px;
  font-size: 0.8rem;
  font-weight: bold;
}}
.action-试仓 {{ background: rgba(63,185,80,0.15); color: var(--green); border: 1px solid var(--green); }}
.action-滚仓 {{ background: rgba(88,166,255,0.15); color: var(--blue); border: 1px solid var(--blue); }}
.action-观望 {{ background: rgba(139,148,158,0.15); color: var(--text-dim); border: 1px solid var(--border); }}
.action-持有 {{ background: rgba(63,185,80,0.15); color: var(--green); border: 1px solid var(--green); }}
.action-轻仓 {{ background: rgba(210,153,34,0.15); color: var(--yellow); border: 1px solid var(--yellow); }}
.action-谨慎抄底 {{ background: rgba(248,81,73,0.15); color: var(--red); border: 1px solid var(--red); }}

/* GLM 解读 */
.glm-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}}
.glm-card h2 {{ font-size: 1rem; color: var(--blue); margin-bottom: 12px; }}
.glm-text {{ font-size: 0.85rem; line-height: 1.8; color: var(--text); }}

/* 波浪图表 */
.chart-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}}
.chart-card h2 {{ font-size: 1rem; color: var(--green); margin-bottom: 12px; }}
#kline-chart {{ width: 100%; height: 400px; }}

/* 斐波那契位 */
.fib-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}}
.fib-card h2 {{ font-size: 1rem; color: var(--yellow); margin-bottom: 12px; }}
.fib-grid {{
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(100px, 1fr));
  gap: 8px;
}}
.fib-item {{
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 8px;
  text-align: center;
}}
.fib-item .ratio {{ font-size: 0.7rem; color: var(--text-dim); }}
.fib-item .price {{ font-size: 0.9rem; font-weight: bold; color: var(--text); }}
.fib-item.active {{ border-color: var(--yellow); background: rgba(210,153,34,0.1); }}

/* 波浪列表 */
.wave-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}}
.wave-card h2 {{ font-size: 1rem; color: var(--blue); margin-bottom: 12px; }}
.wave-table {{ width: 100%; border-collapse: collapse; font-size: 0.8rem; }}
.wave-table th, .wave-table td {{
  padding: 6px 8px;
  text-align: center;
  border-bottom: 1px solid var(--border);
}}
.wave-table th {{ color: var(--text-dim); font-weight: normal; }}
.wave-label {{
  display: inline-block;
  width: 24px; height: 24px;
  line-height: 24px;
  border-radius: 50%;
  font-weight: bold;
  font-size: 0.75rem;
}}
.wave-high {{ background: rgba(248,81,73,0.2); color: var(--red); }}
.wave-low {{ background: rgba(63,185,80,0.2); color: var(--green); }}

.footer {{
  text-align: center;
  color: var(--text-dim);
  font-size: 0.7rem;
  padding: 16px 0;
  border-top: 1px solid var(--border);
}}
.update-time {{ color: var(--green); }}

/* 教学 */
.edu-card {{
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 16px;
  margin-bottom: 16px;
}}
.edu-card h2 {{ font-size: 1rem; color: var(--blue); margin-bottom: 12px; }}
.edu-card h3 {{ font-size: 0.85rem; color: var(--yellow); margin: 12px 0 6px; }}
.edu-card p {{ font-size: 0.8rem; line-height: 1.7; color: var(--text-dim); margin-bottom: 6px; }}

@media (max-width: 600px) {{
  #kline-chart {{ height: 300px; }}
  .header h1 {{ font-size: 1.1rem; }}
}}
</style>
</head>
<body>

<div class="header">
  <h1>📊 艾略特波浪推演 · 上证指数</h1>
  <div class="subtitle">EWave Radar · 实时数据 + 智谱 GLM 解读</div>
  <div class="subtitle">更新时间: <span class="update-time">{today_str} {cn_now.strftime('%H:%M')} (中国时间)</span></div>
</div>

<!-- 今日信号卡片 -->
<div class="signal-card">
  <h2>🔥 今日关键信号 <span class="badge">SIGNAL</span></h2>
  <div class="signal-row">
    <span class="label">上证收盘</span>
    <span class="value">{latest.get('close', '--')}</span>
  </div>
  <div class="signal-row">
    <span class="label">日期</span>
    <span class="value">{latest.get('date', '--')}</span>
  </div>
  <div class="signal-row">
    <span class="label">波浪推演</span>
    <span class="value">{position.get('signal', '--')}</span>
  </div>
  <div class="signal-row">
    <span class="label">关键信号</span>
    <span class="value">{position.get('detail', '--')}</span>
  </div>
  <div class="signal-row">
    <span class="label">最近斐波那契位</span>
    <span class="value">{position.get('nearest_fib', '--')} @ {position.get('nearest_fib_price', '--')}</span>
  </div>
  <div class="signal-row">
    <span class="label">操作建议</span>
    <span class="value">
      <span class="action-tag action-{position.get('action', '观望')}">{position.get('action', '观望')}</span>
    </span>
  </div>
</div>

<!-- 智谱 GLM 解读 -->
<div class="glm-card">
  <h2>🤖 智谱 GLM 解读</h2>
  <div class="glm-text">{glm_html}</div>
</div>

<!-- 波浪图表 -->
<div class="chart-card">
  <h2>📈 日K线 + 波浪标注</h2>
  <div id="kline-chart"></div>
</div>

<!-- 斐波那契关键位 -->
<div class="fib-card">
  <h2>📐 斐波那契回撤位</h2>
  <div class="fib-grid" id="fib-grid"></div>
</div>

<!-- 波浪列表 -->
<div class="wave-card">
  <h2>🌊 波浪结构</h2>
  <table class="wave-table">
    <thead>
      <tr><th>浪型</th><th>类型</th><th>点位</th><th>日期</th></tr>
    </thead>
    <tbody id="wave-tbody"></tbody>
  </table>
</div>

<!-- 教学 -->
<div class="edu-card">
  <h2>📚 艾略特波浪 · 是什么 / 为什么 / 怎么办</h2>
  <h3>是什么？</h3>
  <p>艾略特波浪理论认为市场以 5 浪驱动 + 3 浪调整的 8 浪周期运行。驱动浪标号 1-2-3-4-5，调整浪标号 A-B-C。</p>
  <h3>为什么？</h3>
  <p>波浪结构反映群体心理周期：乐观→狂热→恐慌→绝望，周而复始。斐波那契比率是波浪间天然数学关系。</p>
  <h3>怎么办？</h3>
  <p>① 确认当前处于哪一浪；② 在回撤位附近寻找进场点（0.382/0.5/0.618）；③ 顺势操作，2 浪底和 4 浪底是常见买点。</p>
</div>

<div class="footer">
  ewave-radar · 数据源: akshare/东财 · 解读: 智谱 GLM-4<br>
  仅供学习参考，不构成投资建议
</div>

<script src="https://cdn.jsdelivr.net/npm/echarts@5/dist/echarts.min.js"></script>
<script>
const klines = {klines_json};
const waves = {waves_json};
const fibLevels = {fib_json};
const position = {position_json};
const nearestFib = position.nearest_fib || '';

// 渲染K线图
const chart = echarts.init(document.getElementById('kline-chart'));
const ohlc = klines.map(k => [k.open, k.close, k.low, k.high]);
const dates = klines.map(k => k.date);
const volumes = klines.map(k => k.volume);

// 波浪标注
const markPoints = waves.map(w => ({{
  name: '浪' + w.label,
  coord: [w.date, w.price],
  itemStyle: {{ color: w.type === 'high' ? '#f85149' : '#3fb950' }},
  label: {{
    show: true,
    formatter: w.label,
    position: w.type === 'high' ? 'top' : 'bottom',
    color: w.type === 'high' ? '#f85149' : '#3fb950',
    fontSize: 14,
    fontWeight: 'bold'
  }}
}}));

// 斐波那契标线
const fibLines = [];
const maxPrice = Math.max(...klines.map(k => k.high));
const minPrice = Math.min(...klines.map(k => k.low));
Object.entries(fibLevels).forEach(([ratio, price]) => {{
  fibLines.push({{
    yAxis: price,
    label: {{
      show: true,
      formatter: ratio + ' → ' + price,
      position: 'end',
      color: '#d29922',
      fontSize: 10
    }},
    lineStyle: {{ color: '#d29922', type: 'dashed', opacity: 0.5 }}
  }});
}});

chart.setOption({{
  backgroundColor: 'transparent',
  grid: [
    {{ left: '8%', right: '12%', top: '6%', height: '60%' }},
    {{ left: '8%', right: '12%', top: '75%', height: '18%' }}
  ],
  xAxis: [
    {{ type: 'category', data: dates, scale: true, boundaryGap: false, axisLine: {{ lineStyle: {{ color: '#30363d' }} }}, axisLabel: {{ color: '#8b949e', fontSize: 9 }} }},
    {{ type: 'category', gridIndex: 1, data: dates, scale: true, boundaryGap: false, axisLabel: {{ show: false }} }}
  ],
  yAxis: [
    {{ scale: true, splitLine: {{ lineStyle: {{ color: '#21262d' }} }}, axisLabel: {{ color: '#8b949e', fontSize: 9 }} }},
    {{ gridIndex: 1, splitNumber: 2, axisLabel: {{ color: '#8b949e', fontSize: 8 }} }},
  ],
  series: [
    {{
      type: 'candlestick',
      data: ohlc,
      itemStyle: {{
        color: '#3fb950',
        color0: '#f85149',
        borderColor: '#3fb950',
        borderColor0: '#f85149'
      }},
      markPoint: {{ data: markPoints, symbolSize: 0 }},
      markLine: {{ data: fibLines, symbol: 'none' }}
    }},
    {{
      name: '成交量',
      type: 'bar',
      xAxisIndex: 1,
      yAxisIndex: 1,
      data: volumes,
      itemStyle: {{ color: function(params) {{
        return klines[params.dataIndex].close >= klines[params.dataIndex].open ? 'rgba(63,185,80,0.4)' : 'rgba(248,81,73,0.4)';
      }} }}
    }}
  ]
}});
chart.resize();

// 斐波那契网格
const fibGrid = document.getElementById('fib-grid');
Object.entries(fibLevels).forEach(([ratio, price]) => {{
  const item = document.createElement('div');
  item.className = 'fib-item' + (ratio === nearestFib ? ' active' : '');
  item.innerHTML = '<div class="ratio">' + ratio + '</div><div class="price">' + price + '</div>';
  fibGrid.appendChild(item);
}});

// 波浪列表
const waveTbody = document.getElementById('wave-tbody');
waves.forEach(w => {{
  const tr = document.createElement('tr');
  const cls = w.type === 'high' ? 'wave-high' : 'wave-low';
  tr.innerHTML = '<td><span class="wave-label ' + cls + '">' + w.label + '</span></td>' +
    '<td>' + (w.type === 'high' ? '高点' : '低点') + '</td>' +
    '<td>' + w.price + '</td><td>' + w.date + '</td>';
  waveTbody.appendChild(tr);
}});

</script>
</body>
</html>"""
    return html


# ============================================================
# 5. 主流程
# ============================================================

def main():
    print("[1/5] 拉取上证指数日K...")
    klines = fetch_sse_index(days=250)
    if not klines:
        print("[ERROR] 数据拉取失败，退出")
        sys.exit(1)
    print(f"  ✓ 获取 {len(klines)} 条日K")

    print("[2/5] 艾略特波浪检测...")
    waves = detect_elliott_waves(klines)
    print(f"  ✓ 检测到 {len(waves)} 个波浪标注")

    print("[3/5] 斐波那契计算...")
    fib_levels = calc_fibonacci(waves, klines)
    print(f"  ✓ 计算完成: {fib_levels}")

    print("[4/5] 当前位置分析...")
    position = analyze_current_position(klines, waves, fib_levels)
    print(f"  ✓ {position}")

    print("[5/5] 智谱 GLM 解读...")
    api_key = os.environ.get("ZHIPU_API_KEY", "")
    glm_text = generate_glm_analysis(klines, waves, fib_levels, position, api_key)
    print(f"  ✓ 解读完成 ({len(glm_text)} 字)")

    print("[生成] HTML...")
    html = generate_html(klines, waves, fib_levels, position, glm_text)

    output_path = os.environ.get("OUTPUT_PATH", "/workspace/ewave-radar/index.html")
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"  ✓ 输出: {output_path}")

    # 同时输出 JSON 供调试
    debug_path = output_path.replace(".html", "_debug.json")
    with open(debug_path, "w", encoding="utf-8") as f:
        json.dump({"waves": waves, "fib": fib_levels, "position": position}, f, ensure_ascii=False, indent=2)
    print(f"  ✓ 调试: {debug_path}")


if __name__ == "__main__":
    main()
