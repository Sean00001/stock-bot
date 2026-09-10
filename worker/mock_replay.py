"""
產生假的盤中資金流向快照檔，供離開盤時間開發/驗證前端使用。

用法：
  python mock_replay.py --finalize-day 2026-08-26
      一次性產生「昨天」的整日檔 {date}.json（final=true），
      讓前端一開啟就至少有一天可以回放/當作 prev_close 依據。

  python mock_replay.py --live 2026-08-27
      模擬今天盤中：每隔 SNAPSHOT_SEC 秒產生新的隨機 tick，
      持續寫 .t.json / .b.json，直到手動中斷 (Ctrl+C)。
      前端可以直接對這個日期輪詢，效果跟真的 worker 一樣。

兩個模式都是用 flow_codec.StockAggregator，所以輸出格式跟真正的
worker/main.py 完全一致(含股價序列、prev_close)，之後直接接真實 Shioaji
tick 不用改前端。
"""
import os
import sys
import time
import random
import argparse
import datetime as dt

sys.path.insert(0, os.path.dirname(__file__))
from flow_codec import StockAggregator  # noqa: E402
from worker_common import (  # noqa: E402
    FLOWDATA_DIR, TAIPEI, atomic_write_json, load_prev_close, market_open_dt,
)

SECTORS = {
    "半導體": [("2330", "台積電"), ("2454", "聯發科"), ("2303", "聯電"), ("3711", "日月光"), ("6488", "環球晶")],
    "記憶體製造": [("2408", "南亞科"), ("8299", "群聯"), ("5289", "宜鼎")],
    "PCB板廠": [("8046", "南電"), ("3037", "欣興"), ("2313", "華通")],
    "PCB載板": [("3006", "晶豪科")],
    "光學鏡頭": [("3406", "玉晶光"), ("6805", "亞光")],
    "被動元件": [("2308", "台達電"), ("2327", "國巨")],
    "軍工航太": [("8033", "雷虎"), ("2634", "漢翔")],
    "貨櫃航運": [("2603", "長榮"), ("2609", "陽明"), ("2615", "萬海")],
    "記憶體模組控制": [("3260", "威剛"), ("2451", "創見")],
    "金融壽險": [("2881", "富邦金"), ("2882", "國泰金"), ("2891", "中信金")],
    "CCL材料": [("1303", "南亞"), ("3081", "聯亞"), ("8358", "金居")],
    "散熱": [("3324", "雙鴻"), ("3653", "健策")],
    "光通訊元件": [("3163", "波若威")],
    "生技製藥": [("4174", "浩鼎"), ("6547", "高端疫苗")],
    "IC設計": [("3529", "力旺"), ("6533", "晶心科")],
    "ASIC設計服務": [("3661", "世芯-KY")],
}

ALL_STOCKS = [(code, name, sector) for sector, lst in SECTORS.items() for code, name in lst]


def gen_ticks_for_window(agg: StockAggregator, t_start: int, t_end: int, n: int, base_prices: dict):
    for _ in range(n):
        code, name, sector = random.choice(ALL_STOCKS)
        t = random.randint(t_start, max(t_start, t_end - 1))
        price = base_prices.setdefault(code, random.uniform(50, 800))
        price *= 1 + random.uniform(-0.003, 0.003)
        base_prices[code] = price
        vol = random.choice([1, 1, 1, 2, 3, 5, 8, 15, 30, 60, 120])
        # 讓少數幾檔權值股/當紅股偏多單量，比較像真實盤面
        if code in ("2330", "2454", "2408", "8046"):
            vol *= random.choice([1, 1, 2, 5, 10])
        is_buy = random.random() > 0.48
        epoch = agg.market_open_epoch + t
        agg.add_tick(code, name, sector, epoch, price, vol, is_buy)
    return base_prices


def _mock_prev_close(date_str: str, base_prices: dict) -> dict:
    """先看看有沒有真的前一日整日檔可以當 prev_close；沒有的話就拿現在模擬出來的
    base_prices 隨機再往前推一點百分比，湊一個看起來合理的「昨收」，
    這樣個股卡片的漲跌幅才不會整天都是 0% 或 N/A。"""
    real = load_prev_close(date_str)
    if real:
        return real
    return {code: round(p / (1 + random.uniform(-0.03, 0.03)), 2) for code, p in base_prices.items()}


def finalize_day(date_str: str):
    d = dt.datetime.strptime(date_str, "%Y-%m-%d")
    if TAIPEI:
        d = d.replace(tzinfo=TAIPEI)
    open_ts = market_open_dt(d).timestamp()
    agg = StockAggregator(open_ts)
    base_prices: dict = {}
    gen_ticks_for_window(agg, 0, 270 * 60, 12000, base_prices)

    asof = (d.replace(hour=13, minute=30)).isoformat()
    prev_close = _mock_prev_close(date_str, base_prices)
    full = agg.build_full_snapshot(date_str, asof, prev_close=prev_close)
    full["final"] = True
    full["last_price"] = {c: round(p, 2) for c, p in base_prices.items()}

    os.makedirs(FLOWDATA_DIR, exist_ok=True)
    path = os.path.join(FLOWDATA_DIR, f"{date_str}.json")
    atomic_write_json(path, full)
    print(f"[mock] 已產生整日檔: {path} ({agg.tick_count} 筆 tick)")


def live_simulate(date_str: str, snapshot_sec: float = 3.0, tick_per_cycle: int = 40):
    d = dt.datetime.strptime(date_str, "%Y-%m-%d")
    if TAIPEI:
        d = d.replace(tzinfo=TAIPEI)
    open_ts = market_open_dt(d).timestamp()
    agg = StockAggregator(open_ts)
    base_prices: dict = {}
    cursor: dict = {}
    prev_close_cache = None

    os.makedirs(FLOWDATA_DIR, exist_ok=True)
    t = 0
    print(f"[mock] 模擬即時盤中資料 -> {FLOWDATA_DIR}/{date_str}.t.json / .b.json (Ctrl+C 結束)")
    try:
        while True:
            gen_ticks_for_window(agg, t, t + int(snapshot_sec), tick_per_cycle, base_prices)
            t += int(snapshot_sec)
            if prev_close_cache is None:
                # 開盤第一輪已經有 base_prices 可以湊 prev_close 了，之後每輪沿用同一份
                # (不然每次都重新隨機一次，漲跌幅會一直亂跳)。
                prev_close_cache = _mock_prev_close(date_str, base_prices)

            asof = dt.datetime.now(TAIPEI).isoformat() if TAIPEI else dt.datetime.now().isoformat()
            full = agg.build_full_snapshot(date_str, asof, prev_close=prev_close_cache)
            prev_cursor = {k: list(v) for k, v in cursor.items()}
            incr, cursor = agg.build_incremental_snapshot(date_str, asof, cursor)
            incr["from"] = prev_cursor
            atomic_write_json(os.path.join(FLOWDATA_DIR, f"{date_str}.b.json"), full)
            atomic_write_json(os.path.join(FLOWDATA_DIR, f"{date_str}.t.json"), incr)
            print(f"[mock] t={t}s ticks={agg.tick_count}", end="\r")
            time.sleep(snapshot_sec)
    except KeyboardInterrupt:
        print("\n[mock] 已停止")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--finalize-day", metavar="YYYY-MM-DD")
    ap.add_argument("--live", metavar="YYYY-MM-DD")
    args = ap.parse_args()

    if args.finalize_day:
        finalize_day(args.finalize_day)
    elif args.live:
        live_simulate(args.live)
    else:
        ap.print_help()
