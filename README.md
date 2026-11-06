# 收息股每日盯盘

用 GitHub Actions 每个交易日收盘后自动抓行情，判断是否触发条件，通过 Server酱 推到微信。不依赖任何自己的电脑或客户端。

## 这是什么

- **每天 15:10（北京时间，周一到周五）** 自动运行一次
- 抓 601398 / 600900 / 588000 的收盘价
- 判断三条规则（可自己改）：跌破目标价 / 股息率达到阈值 / 单日大跌
- 把结果整理成一张表，推到微信
- 运行状态写回 `state/state.json`，用于去重，同时让定时任务保持存活

## 目录结构

```
.github/workflows/daily-check.yml   定时任务定义（什么时候跑、跑什么）
scripts/check_dividend_stocks.py    主脚本（抓数据 + 判断 + 推送）
state/state.json                    运行状态（自动生成，不用手改）
```

## 我该改哪里

只改 `scripts/check_dividend_stocks.py` 顶部的配置区：

```python
WATCHLIST = [
    {"code": "601398", "name": "工商银行", "secid": "1.601398", "target_price": None, "dps": None},
    ...
]
TARGET_YIELD = 5.0     # 股息率 ≥ 5% 算触发
DROP_ALERT = -2.0      # 单日跌 ≥ 2% 提醒
MODE = "daily"         # daily 每天推一条；signal 只在触发时推
```

- `target_price`：你的目标买入价，填 `None` 表示不按价格判断
- `dps`：每股年度分红（元），填了才算股息率。查法：东方财富个股页 → F10 → 分红融资
- 改完直接提交，下一次运行生效，不需要重新部署

## 手动测一次

仓库页面 → Actions → 左侧「收息股每日盯盘」→ 右侧 Run workflow。

## 数据来源与免责

行情来自公开接口（东方财富 push2 为主，腾讯 qt 为备），可能有 5–15 分钟延迟，仅供参考，不构成投资建议。
