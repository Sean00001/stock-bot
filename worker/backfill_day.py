"""
用 Shioaji 的歷史逐筆 API，回補「過去某一個交易日」的整日快照檔 {date}.json。

跟 main.py(即時 worker) 共用同一份 flow_codec 編碼邏輯，所以回補出來的檔案
跟即時 worker finalize 出來的格式完全一樣，前端不用區分兩者。

用途：
  1. 剛建置好系統時，回補最近幾個交易日，讓「累積淨流入走勢」的起算基準、
     隔日沖候選、族群排行表...等需要「前一交易日」資料的功能一開始就能動。
  2. 如果某天 worker 忘記啟動/中途當機，事後用歷史 tick 補回來。

⚠ 限制與節流：
  - 只會回補 worker/watchlist.txt 裡列出的股票（跟即時 worker 用同一份清單），
    不會抓全市場將近 1900 檔 —— 一來是即時 worker 本來就只追蹤這份清單，回補
    全市場的股票也對不上；二來永豐官方文件(https://sinotrade.github.io/zh/tutor/limit/)
    寫明「行情查詢」類 API（含 api.ticks()）合併上限是 10 秒 50 次，全市場一次
    掃過去一定會超過。
  - 每呼叫一次 api.ticks() 之後會 sleep BACKFILL_SLEEP 秒（預設 0.25 秒，即最快
    約每 10 秒 40 次，留一點安全邊界），避免撞到上面那個 10秒/50次的限制。
  - 找不到 watchlist.txt 時一樣會退回「全市場」，但這樣做很容易觸發限流，
    強烈建議準備好 watchlist.txt 再回補。

用法：
  python backfill_day.py 2026-08-26
  python backfill_day.py 2026-08-25 2026-08-26   # 一次補多天
"""
import os
import sys
import time
import datetime as dt

import shioaji as sj

sys.path.insert(0, os.path.dirname(__file__))
from flow_codec import StockAggregator  # noqa: E402
from worker_common import (  # noqa: E402
    FLOWDATA_DIR, TAIPEI, atomic_write_json, load_prev_close, market_open_dt,
    load_watchlist, load_watchlist_sectors, industry_name,
)

API_KEY = os.environ.get("SHIOAJI_API_KEY")
SECRET_KEY = os.environ.get("SHIOAJI_SECRET_KEY")

# 官方「行情查詢」類 API 合併上限是 10 秒 50 次；每次 api.ticks() 後睡這麼久，
# 留一點安全邊界（0.25 秒 ≈ 每 10 秒 40 次）。
BACKFILL_SLEEP = float(os.getenv("BACKFILL_SLEEP", "0.25"))


def get_target_contracts(api: sj.Shioaji):
    all_contracts = {}
    for exchange in [api.Contracts.Stocks.TSE, api.Contracts.Stocks.OTC]:
        for contract in exchange:
            if len(contract.code) == 4:
                all_contracts[contract.code] = contract

    watchlist = load_watchlist()
    if watchlist is None:
        print("[backfill] 找不到 watchlist.txt，退回全市場清單 —— "
              "很容易撞到「行情查詢 10秒50次」的限流，強烈建議準備一份 watchlist.txt。")
        return list(all_contracts.values())

    contracts = []
    missing = []
    for code in watchlist:
        c = all_contracts.get(code)
        if c is None:
            missing.append(code)
        else:
            contracts.append(c)
    if missing:
        print(f"[backfill] watchlist 裡有 {len(missing)} 檔在契約清單裡找不到(代號打錯或下市?): {missing}")
    return contracts


def backfill(api: sj.Shioaji, date_str: str, contracts: list):
    d = dt.datetime.strptime(date_str, "%Y-%m-%d")
    if TAIPEI:
        d = d.replace(tzinfo=TAIPEI)
    open_ts = market_open_dt(d).timestamp()
    agg = StockAggregator(open_ts)
    last_price: dict[str, float] = {}
    # 族群名稱優先用 watchlist.txt 自編的主題族群(較細)，跟 main.py 即時 worker
    # 一致；沒對到才 fallback 回 Shioaji category 對應的官方大分類(較粗)。
    sector_of_code = load_watchlist_sectors()

    print(f"[backfill] {date_str}: 共 {len(contracts)} 檔股票要抓歷史 tick"
          f"（每次查詢間隔 {BACKFILL_SLEEP} 秒）")

    for idx, contract in enumerate(contracts, 1):
        if idx % 20 == 0:
            print(f"[backfill] {date_str}: {idx}/{len(contracts)}")
        try:
            ticks = api.ticks(contract, date_str)
        except Exception as e:
            print(f"[backfill] {contract.code} 抓取失敗，略過: {e}")
            time.sleep(BACKFILL_SLEEP)
            continue
        time.sleep(BACKFILL_SLEEP)

        if not ticks or len(ticks.ts) == 0:
            continue

        name = getattr(contract, "name", contract.code)
        sector = sector_of_code.get(contract.code) or industry_name(getattr(contract, "category", None))

        for i in range(len(ticks.ts)):
            tick_type = ticks.tick_type[i]
            if tick_type not in (1, 2):
                continue
            price = float(ticks.close[i])
            volume = int(ticks.volume[i])
            epoch_ts = ticks.ts[i] / 1e9
            agg.add_tick(contract.code, name, sector, epoch_ts, price, volume, tick_type == 1)
            last_price[contract.code] = price

    asof = (d.replace(hour=13, minute=30)).isoformat()
    full = agg.build_full_snapshot(date_str, asof)
    full["final"] = True
    full["last_price"] = last_price
    full["prev_close"] = load_prev_close(date_str)
    # 跟 main.py 即時 worker 的 finalize() 一致，順便存開盤價/最高價，
    # 給排行表的「隔天開/收/最高%」欄位用。
    open_price, high_price = agg.open_high_prices()
    full["open_price"] = open_price
    full["high_price"] = high_price

    os.makedirs(FLOWDATA_DIR, exist_ok=True)
    path = os.path.join(FLOWDATA_DIR, f"{date_str}.json")
    atomic_write_json(path, full)
    print(f"[backfill] 已寫入 {path}（{agg.tick_count} 筆 tick）")


def main():
    dates = sys.argv[1:]
    if not dates:
        print("用法: python backfill_day.py YYYY-MM-DD [YYYY-MM-DD ...]")
        return
    if not API_KEY or not SECRET_KEY:
        print("缺少 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY")
        return

    api = sj.Shioaji()
    print("[backfill] 登入 Shioaji...")
    api.login(api_key=API_KEY, secret_key=SECRET_KEY)
    try:
        contracts = get_target_contracts(api)
        for date_str in sorted(dates):
            backfill(api, date_str, contracts)
    finally:
        api.logout()


if __name__ == "__main__":
    main()
