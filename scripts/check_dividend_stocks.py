#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
收息股每日盯盘（GitHub Actions 版）
-----------------------------------
每个交易日收盘后抓一次行情 → 判断是否触发条件 → 通过 Server酱 推到微信。

只用 Python 标准库，不需要 pip install 任何东西。
行情源：东方财富 push2（主）→ 腾讯 qt（备）。
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(ROOT, "state", "state.json")

# ============================================================
#  这一段是唯一需要你改的地方
# ============================================================
WATCHLIST = [
    # code         : 6 位代码；secid : 上交所 = 1.xxxxxx，深交所 = 0.xxxxxx
    # target_price : 目标买入价（元），跌破就提醒。None 表示不按单一价格判断。
    # tiers        : 阶梯买入位（元），逐档提醒，破一档报一次，破过的不重复报。
    # dps          : 每股年度分红（元），填了才自动算股息率；None 表示不算。
    # target_yield : 该标的自己的股息率门槛（%），不写就用下面的 TARGET_YIELD。

    # 工商银行：跌破 8.00 元提醒
    {"code": "601398", "name": "工商银行", "secid": "1.601398",
     "target_price": 8.00, "dps": None},

    # 长江电力：TTM 股息率 ≥ 4%（你给的换算约等于 25 元以下），先按 25.00 元提醒
    {"code": "600900", "name": "长江电力", "secid": "1.600900",
     "target_price": 25.00, "dps": None, "target_yield": 4.0},

    # 科创50ETF：三档阶梯 1.22 / 1.12 / 1.03，逐档提醒
    {"code": "588000", "name": "科创50ETF", "secid": "1.588000",
     "target_price": None, "dps": None, "tiers": [1.22, 1.12, 1.03]},

    # 加新标的照着上面的格式加一行就行，例如：
    # {"code": "600036", "name": "招商银行", "secid": "1.600036", "target_price": 30.00, "dps": 1.97},
]

TARGET_YIELD = 5.0        # 全局默认股息率门槛（%）；标的自己写了 target_yield 就以它为准
DROP_ALERT = -2.0         # 单日跌幅 <= -2.0% 提醒一次（机会还是风险你自己判断）
MODE = "daily"            # "daily" = 每个交易日都推一条日报；"signal" = 只在触发时推
SIGNAL_COOLDOWN_DAYS = 7  # signal 模式下，同一个信号多少天内不重复推
# ============================================================


def log(msg):
    print(f"[{datetime.now(BEIJING):%H:%M:%S}] {msg}", flush=True)


def http_get(url, headers=None, timeout=15, retries=3):
    hdrs = {"User-Agent": "Mozilla/5.0 (compatible; dividend-watch/1.0)"}
    if headers:
        hdrs.update(headers)
    last_err = None
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:  # noqa: BLE001
            last_err = e
            log(f"  请求失败（第 {i + 1} 次）：{e}")
            time.sleep(2 + i * 2)
    raise RuntimeError(f"请求失败：{url} -> {last_err}")


# ---------- 行情源 1：东方财富 ----------

def quote_eastmoney(item):
    secid = item["secid"]
    fields = "f43,f57,f58,f60,f169,f170,f86"
    url = (
        "https://push2.eastmoney.com/api/qt/stock/get"
        f"?secid={secid}&fields={fields}&invt=2&fltt=2"
    )
    raw = http_get(url).decode("utf-8", "ignore")
    data = (json.loads(raw) or {}).get("data") or {}
    if not data or data.get("f43") in (None, "-", ""):
        raise RuntimeError("东方财富返回空数据")

    price_str = str(data["f43"])
    price = float(price_str)
    prev = float(data["f60"]) if data.get("f60") not in (None, "-", "") else None
    pct = data.get("f170")
    pct = float(pct) if pct not in (None, "-", "") else None
    ts = data.get("f86")
    quote_dt = datetime.fromtimestamp(int(ts), BEIJING) if ts else None
    return {
        "code": item["code"],
        "name": data.get("f58") or item["name"],
        "price": price,
        "price_str": price_str,
        "prev_close": prev,
        "pct": pct,
        "quote_time": quote_dt,
        "source": "东方财富",
    }


# ---------- 行情源 2：腾讯（备用）----------

def quote_tencent(item):
    prefix = "sh" if item["secid"].startswith("1.") else "sz"
    url = f"https://qt.gtimg.cn/q={prefix}{item['code']}"
    raw = http_get(url).decode("gbk", "ignore")
    body = raw.split('"')[1] if '"' in raw else ""
    f = body.split("~")
    if len(f) < 33:
        raise RuntimeError("腾讯行情返回格式异常")
    price_str = f[3]
    price = float(price_str)
    prev = float(f[4]) if f[4] else None
    pct = float(f[32]) if f[32] else None
    try:
        quote_dt = datetime.strptime(f[30], "%Y%m%d%H%M%S").replace(tzinfo=BEIJING)
    except Exception:  # noqa: BLE001
        quote_dt = None
    return {
        "code": item["code"],
        "name": f[1] or item["name"],
        "price": price,
        "price_str": price_str,
        "prev_close": prev,
        "pct": pct,
        "quote_time": quote_dt,
        "source": "腾讯",
    }


def fetch_quote(item):
    for fn in (quote_eastmoney, quote_tencent):
        try:
            q = fn(item)
            if q["price"] and q["price"] > 0:
                return q
        except Exception as e:  # noqa: BLE001
            log(f"  {fn.__name__} 失败：{e}")
    raise RuntimeError(f"{item['code']} 两个行情源都拿不到数据")


# ---------- 判断 ----------

def evaluate(item, q, fired_tiers=None):
    """返回 (是否触发, 原因列表, 标签列表, 展示数据, 本次新跌破的档位)"""
    reasons, tags = [], []
    price = q["price"]
    pct = q["pct"]
    yield_pct = None
    gap = None

    target = item.get("target_price")
    if target:
        gap = (price / float(target) - 1) * 100
        if price <= float(target):
            reasons.append(f"跌破目标价 {target:.2f}")
            tags.append("price_below")

    # ---- 阶梯买入位：逐档提醒 ----
    tiers = [float(t) for t in (item.get("tiers") or [])]
    fired = [float(t) for t in (fired_tiers or [])]
    new_fired = []
    tier_state = None
    if tiers:
        for i, t in enumerate(tiers, 1):
            if price <= t and t not in fired:
                reasons.append(f"跌破第 {i} 档 {t:.2f}")
                tags.append(f"tier{i}")
                new_fired.append(t)
        fired_all = sorted(set(fired) | set(new_fired), reverse=True)
        pending = [t for t in tiers if t not in fired_all]
        # 价格是从上往下走的：下一个会遇到的是"还没破的最高一档"
        upcoming = max(pending) if pending else None
        tier_state = {
            "levels": tiers,
            "fired": fired_all,
            "upcoming": upcoming,
        }
        if gap is None and upcoming is not None:
            # 统一语义：正数 = 现价还要跌这么多才到下一档
            gap = (price / upcoming - 1) * 100

    if item.get("dps"):
        yield_pct = float(item["dps"]) / price * 100
        limit = float(item.get("target_yield") or TARGET_YIELD)
        if yield_pct >= limit:
            reasons.append(f"股息率 {yield_pct:.2f}% ≥ {limit:g}%")
            tags.append("yield_high")

    if pct is not None and pct <= DROP_ALERT:
        reasons.append(f"单日跌 {pct:.2f}%")
        tags.append("drop")

    return bool(reasons), reasons, tags, {
        "price": price,
        "pct": pct,
        "yield": yield_pct,
        "gap": gap,
        "target": target,
        "tiers": tier_state,
    }, new_fired


# ---------- 状态（去重）----------

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)
    log(f"状态已写入 {os.path.relpath(STATE_FILE, ROOT)}")


# ---------- 推送 ----------

def send_serverchan(title, desp, sendkey):
    """兼容 sct / sctp 两种 key。返回 (是否成功, 说明)"""
    key = sendkey.strip()
    if key.startswith("sctp"):
        # 形如 sctp12345tXXXXXXXX -> uid = 12345
        head = key[4:].split("t")[0]
        url = f"https://{head}.push.ft07.com/send/{key}.send"
    else:
        url = f"https://sctapi.ftqq.com/{key}.send"

    body = urllib.parse.urlencode({"title": title.replace("\n", " "), "desp": desp}).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "Mozilla/5.0",
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            resp = r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        log(f"  Server酱 HTTP {e.code}：{body[:300]}")
        return False, f"HTTP {e.code} {body[:200]}"
    log(f"  Server酱返回：{resp[:300]}")
    try:
        j = json.loads(resp)
    except Exception:  # noqa: BLE001
        return False, f"返回非 JSON：{resp[:200]}"
    if j.get("code") == 0:
        return True, "OK"
    return False, f"code={j.get('code')} {j.get('message') or j.get('info')}"


# ---------- 报文 ----------

def build_message(date_str, rows, is_trading_day, quote_time):
    title_bits = []
    triggered = [r for r in rows if r["triggered"]]
    if triggered:
        title_bits.append("、".join(r["name"] for r in triggered) + " 有信号")
    else:
        title_bits.append("无信号")
    title = f"[收息股] {date_str} {title_bits[0]}"

    lines = []
    lines.append(f"**{date_str} 收盘播报**" + (f"（数据时间 {quote_time}）" if quote_time else ""))
    lines.append("")
    lines.append("| 标的 | 现价 | 涨跌 | 股息率 | 距目标 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for r in rows:
        pct = "—" if r["pct"] is None else f"{r['pct']:+.2f}%"
        yld = "—" if r["yield"] is None else f"{r['yield']:.2f}%"
        if r["gap"] is None:
            gap = "—"
        else:
            gap = f"{r['gap']:+.1f}%"
        mark = " 🔴" if r["triggered"] else ""
        px = r.get("price_str") or f"{r['price']:.2f}"
        lines.append(f"| {r['name']}{mark} | {px} | {pct} | {yld} | {gap} |")
    lines.append("")

    # 阶梯档位进度
    ladders = [r for r in rows if r.get("tiers")]
    if ladders:
        for r in ladders:
            t = r["tiers"]
            parts = [f"{lv:.2f}{'✅' if lv in t['fired'] else '⏳'}" for lv in t["levels"]]
            nxt = t.get("upcoming")
            if nxt:
                tail = f"下一档 {nxt:.2f}（还需跌 {(r['price'] / nxt - 1) * 100:.1f}%）"
            else:
                tail = "三档全部已破"
            lines.append(f"**{r['name']} 阶梯**：{' / '.join(parts)}　{tail}")
        lines.append("")

    if triggered:
        lines.append("**触发信号**")
        for r in triggered:
            lines.append(f"- {r['name']}（{r['code']}）：" + "；".join(r["reasons"]))
        lines.append("")
    else:
        lines.append("今日无触发条件。")
        lines.append("")

    if not is_trading_day:
        lines.append("> 提示：行情时间不是今天，可能是休市日，本条仅作确认。")
        lines.append("")

    lines.append("---")
    lines.append("数据来自公开行情接口（东方财富/腾讯），仅供参考，非投资建议。")
    return title, "\n".join(lines)


# ---------- 主流程 ----------

def main():
    today = datetime.now(BEIJING)
    date_str = today.strftime("%Y-%m-%d")
    log(f"开始运行 {date_str}（北京时间）")

    dry_run = os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
    sendkey = os.environ.get("SERVERCHAN_SENDKEY", "")

    rows, errors = [], []
    quote_times = []
    # 先读状态：阶梯档位去重要用历史记录
    state = load_state()
    fired_map = state.get("tiers", {}) or {}
    for item in WATCHLIST:
        log(f"抓取 {item['name']}（{item['code']}）")
        try:
            q = fetch_quote(item)
            triggered, reasons, tags, calc, new_fired = evaluate(item, q, fired_map.get(item["code"]))
            if new_fired:
                fired_map[item["code"]] = sorted(
                    set(fired_map.get(item["code"], [])) | set(new_fired), reverse=True
                )
            if q["quote_time"]:
                quote_times.append(q["quote_time"])
            rows.append({
                "code": q["code"], "name": q["name"],
                "price": calc["price"], "price_str": q.get("price_str"), "pct": calc["pct"],
                "yield": calc["yield"], "gap": calc["gap"], "tiers": calc["tiers"],
                "triggered": triggered, "reasons": reasons, "tags": tags,
            })
            log(f"  {q['name']} {q['price']:.3f} "
                f"{'' if q['pct'] is None else format(q['pct'], '+.2f') + '%'} "
                f"[{q['source']}] {'触发' if triggered else '正常'}"
                + (f" 新破档位 {new_fired}" if new_fired else ""))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{item['name']}（{item['code']}）：{e}")
            log(f"  失败：{e}")

    if not rows:
        msg = "全部标的取数失败：\n" + "\n".join(errors)
        log(msg)
        if sendkey and not dry_run:
            try:
                send_serverchan(f"[收息股] {date_str} 取数失败", msg, sendkey)
            except Exception as e:  # noqa: BLE001
                log(f"  失败通知也没发出去：{e}")
        sys.exit(1)

    quote_time = max(quote_times).strftime("%m-%d %H:%M") if quote_times else None

    is_trading_day = True
    if quote_time and not quote_time.startswith(today.strftime("%m-%d")):
        is_trading_day = False

    title, desp = build_message(date_str, rows, is_trading_day, quote_time)
    if errors:
        desp += "\n\n**部分标的取数失败**\n" + "\n".join(f"- {e}" for e in errors)

    # 是否要发
    should_send = True
    if MODE == "signal":
        fingerprint = "|".join(sorted(
            f"{r['code']}:{t}" for r in rows if r["triggered"] for t in r["tags"]
        ))
        last = state.get("last_fingerprint", "")
        last_date = state.get("last_signal_date", "")
        if fingerprint and fingerprint == last:
            try:
                days = (today.date() - datetime.strptime(last_date, "%Y-%m-%d").date()).days
            except Exception:  # noqa: BLE001
                days = 999
            if days < SIGNAL_COOLDOWN_DAYS:
                should_send = False
                log(f"信号与 {last_date} 相同且未过冷却期（{days} 天），跳过推送")
        if fingerprint:
            state["last_fingerprint"] = fingerprint
            state["last_signal_date"] = date_str

    state["last_run"] = today.strftime("%Y-%m-%d %H:%M:%S")
    state["last_quote_time"] = quote_time
    state["last_trading_day"] = is_trading_day
    if fired_map:
        state["tiers"] = fired_map

    print("\n================ 即将推送的内容 ================")
    print("标题：" + title)
    print(desp)
    print("===============================================\n")

    if dry_run:
        log("DRY_RUN=1，不实际推送")
        save_state(state)
        return

    if not sendkey:
        log("未配置 SERVERCHAN_SENDKEY，只打印不推送")
        save_state(state)
        sys.exit(1)

    if not should_send:
        save_state(state)
        return

    try:
        ok, info = send_serverchan(title, desp, sendkey)
    except Exception as e:  # noqa: BLE001
        log(f"推送异常：{e}")
        save_state(state)
        sys.exit(1)

    if not ok:
        log(f"推送失败：{info}")
        save_state(state)
        sys.exit(1)

    log("推送成功")
    state["last_push"] = today.strftime("%Y-%m-%d %H:%M:%S")
    state["last_push_title"] = title
    save_state(state)


if __name__ == "__main__":
    main()
