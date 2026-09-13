"""
一次性修補腳本：幫已經 finalize 過、但還沒有 open_price/high_price 欄位的
{date}.json 整日檔，把這兩個欄位補上去。

背景：2026-09-13 這次改版讓 worker finalize() 收盤時開始順便算好每檔股票
「當天開盤價(open_price)/當天最高價(high_price)」，跟本來就有的 last_price
放在一起存進整日檔頂層，給「族群/個股淨流入排行」表的「隔天開/收/最高%」
這幾欄用。但這次改版之前就已經 finalize 過的舊日期(當時 finalize() 還沒有
這段邏輯)不會自動生出這兩個欄位，跑這支腳本可以直接從檔案裡已經存的 price
序列(整包已經解碼得到的逐筆股價)當場算出這兩個數字、補寫回檔案——純粹是
本地 JSON 讀取+轉換，不需要重新打一次 Shioaji 歷史 tick API。

用法：
  python backfill_open_high.py                 # 掃 FLOWDATA_DIR 底下所有缺欄位的 {date}.json
  python backfill_open_high.py 2026-09-11       # 只處理指定日期(即使已經有欄位也強制重算覆蓋)
  python backfill_open_high.py 2026-09-11 2026-09-08
"""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(__file__))
from flow_codec import decode  # noqa: E402
from worker_common import FLOWDATA_DIR, atomic_write_json  # noqa: E402


def compute_open_high(full: dict) -> tuple[dict, dict]:
    price_scale = full.get("price_scale") or 100
    price = full.get("price") or {}
    stocks = full.get("stocks") or {}
    open_out: dict[str, float] = {}
    high_out: dict[str, float] = {}
    for sector, price_row in price.items():
        stock_list = stocks.get(sector, [])
        for idx, bucket in enumerate(price_row):
            if idx >= len(stock_list):
                continue  # 理論上不該發生(price/stocks 應該同長度)，保底跳過
            code = stock_list[idx]["code"]
            pts = decode(bucket)
            if not pts:
                continue
            vals = [v for _, v in pts]
            open_out[code] = vals[0] / price_scale
            high_out[code] = max(vals) / price_scale
    return open_out, high_out


def process_file(path: str, force: bool) -> bool:
    with open(path, encoding="utf-8") as f:
        full = json.load(f)

    if not full.get("final"):
        print(f"[backfill_open_high] {path} 不是已收盤的整日檔(沒有 final=true)，略過")
        return False
    if not force and "open_price" in full and "high_price" in full:
        return False

    open_price, high_price = compute_open_high(full)
    full["open_price"] = open_price
    full["high_price"] = high_price
    atomic_write_json(path, full)
    return True


def main():
    args = sys.argv[1:]
    if args:
        targets = [os.path.join(FLOWDATA_DIR, f"{d}.json") for d in args]
        force = True  # 明確指定日期時，即使已經有欄位也強制重算覆蓋一次
    else:
        if not os.path.isdir(FLOWDATA_DIR):
            print(f"[backfill_open_high] 找不到 FLOWDATA_DIR: {FLOWDATA_DIR}")
            return
        targets = sorted(
            os.path.join(FLOWDATA_DIR, fn)
            for fn in os.listdir(FLOWDATA_DIR)
            if fn.endswith(".json") and not fn.endswith(".t.json") and not fn.endswith(".b.json")
        )
        force = False

    done = 0
    for path in targets:
        if not os.path.exists(path):
            print(f"[backfill_open_high] 找不到 {path}，略過")
            continue
        if process_file(path, force):
            print(f"[backfill_open_high] 已補上 {path}")
            done += 1
    print(f"[backfill_open_high] 完成，共處理 {done} 個檔案")


if __name__ == "__main__":
    main()
