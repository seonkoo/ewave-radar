# EWave Radar · 上证指数艾略特波浪推演

实时拉取上证指数日K → 艾略特波浪检测 + 斐波那契回撤位 → 智谱 GLM 生成今日信号解读 → 部署 GitHub Pages。

## 架构

```
腾讯财经API → 日K数据 → 波浪检测 → 斐波那契计算 → 智谱GLM解读 → 静态HTML → GitHub Pages
```

## 本地运行

```bash
export ZHIPU_API_KEY="your_key_here"
python scripts/ewave_engine.py
```

## GitHub Actions 自动更新

- 每个交易日 15:30 CST 自动运行
- 需要在仓库 Settings → Secrets 中配置 `ZHIPU_API_KEY`
- 部署到 `https://seonkoo.github.io/ewave-radar/`

## 配置

| 环境变量 | 说明 |
|---|---|
| `ZHIPU_API_KEY` | 智谱 API key（必填，用于 GLM 解读） |
| `OUTPUT_PATH` | HTML 输出路径（默认 `index.html`） |

## 数据源

| 优先级 | 源 | 接口 |
|---|---|---|
| 1 | 腾讯财经 | `web.ifzq.gtimg.cn` |
| 2 | akshare | `stock_zh_index_daily_em` |
| 3 | 东方财富 | `push2his.eastmoney.com` |

## 免责声明

仅供学习参考，不构成投资建议。
