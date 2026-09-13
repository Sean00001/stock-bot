"""
盤中資金流向 worker

⚠ 訂閱數量上限與多連線涵蓋範圍：永豐 Shioaji 官方文件
   (https://sinotrade.github.io/zh/tutor/limit/)寫明 api.subscribe() 一條連線
   最多 200 檔，同一個 person_id(同一個人)最多可以開 5 條連線。全市場上市+
   上櫃 4 碼股票加起來將近 1900 檔，遠超過單一連線的上限，所以這支 worker
   在同一個 Python 行程裡開「多條」Shioaji 連線(同一組帳號最多到 5 條，
   之後如果又多一組帳號可以再疊加，見 worker_common.load_accounts)，把股票
   清單拆成好幾批、每條連線各訂閱一批，所有連線的 tick 都回呼進同一個共用的
   StockAggregator(用 self.lock 保護)，最後合併寫成同一份快照檔——不是每個
   worker 行程各吃一份、事後再合併，是同一個行程內部就先合併好。
   訂閱的股票清單：watchlist.txt 裡列出的(有自編族群名稱、比較好認)一定會
   優先訂閱到；剩下的連線容量會依代號順序盡量塞進全市場其餘的股票，這些
   「沒被 watchlist.txt 分類到」的股票會 fallback 用證交所官方產業分類
   (industry_name)當族群名稱。開的連線數不夠涵蓋全市場時，只會訂閱到前面
   這些，其餘的股票這天就沒有資料(不會報錯，就是量體看起來比全市場少)。

流程：
  1. 依 worker_common.load_accounts()/CONNECTIONS_PER_ACCOUNT 開好所有連線並
     登入，訂閱範圍見上面的說明(找不到/超過總容量的部分會印警告)。
  2. 每一筆 tick(不管從哪條連線來的)依成交金額分進 特大單/大單/中單/小單
     四個桶，用 flow_codec 的 StockAggregator 累積在記憶體裡（不再經過 Redis
     pubsub），同時也記一筆「原始成交價」序列(不分桶)，供下鑽到某族群後的
     個股股價走勢小圖用。每一筆 tick 所屬的「族群」優先用 watchlist.txt 裡
     自己編的「# --- 族群名稱 ---」區段標題(見 worker_common.load_watchlist_sectors)，
     顆粒度比較細；watchlist.txt 沒把該代號放進任何區段時，才 fallback 回
     Shioaji contract.category 對應的證交所官方大分類(industry_name)。
  3. 背景執行緒每隔 SNAPSHOT_SEC 秒，把目前累積的資料寫成兩個檔案到共用的
     FLOWDATA_DIR 目錄：
       {date}.t.json  -- 只含「上次快照之後」新增的 tick（增量，檔案小）
       {date}.b.json  -- 目前累積到現在的「全量」快照（供前端第一次載入或
                          checkpoint 兜不起來時 resync）
     這兩個檔案前端會直接用 fetch 輪詢讀取，不需要另外開 API。
     每次快照都會附上 prev_close(前一交易日收盤價)，不是只有收盤 finalize 才有，
     這樣盤中個股卡片才能算「漲跌幅」。
  4. 收盤(預設 13:30 Asia/Taipei)後，多寫一份不可變的 {date}.json（整日檔），
     內含 final=true、當日每檔股票最後成交價 last_price，以及供隔日計算
     漲跌用的 prev_close（讀取前一個交易日整日檔裡的 last_price）。
     寫完就結束 worker 行程；每個交易日要重新啟動一次 worker
     (可用 Windows 工作排程器排在每天 08:55 執行 python main.py)。
"""
import os
import sys
import json
import time
import threading

import shioaji as sj

sys.path.insert(0, os.path.dirname(__file__))
from flow_codec import StockAggregator  # noqa: E402
from worker_common import (  # noqa: E402
    FLOWDATA_DIR,
    MAX_SUBSCRIBE,
    CONNECTIONS_PER_ACCOUNT,
    TAIPEI,
    now_taipei,
    today_str,
    market_open_dt,
    market_close_dt,
    atomic_write_json,
    load_prev_close,
    load_watchlist,
    load_watchlist_sectors,
    load_accounts,
    industry_name,
)

SNAPSHOT_SEC = float(os.getenv("SNAPSHOT_SEC", "3"))
# 連續登入多條連線時，每條之間先睡一下，避免短時間內連續登入觸發永豐那邊的
# 頻率限制(官方文件沒寫確切門檻，這裡抓保守一點的值)。
LOGIN_STAGGER_SEC = float(os.getenv("LOGIN_STAGGER_SEC", "1.5"))


class ShioajiConnection:
    """包一條 Shioaji 連線(一個 sj.Shioaji() instance)。多條連線各自登入、
    各自訂閱自己那一批股票，但 tick 回呼(見 Worker.on_tick_v1)全部指到
    同一個 Worker 方法，所以資料最後都會匯進同一個 StockAggregator。"""

    def __init__(self, conn_label: str, api_key: str, secret_key: str):
        self.conn_label = conn_label
        self.api_key = api_key
        self.secret_key = secret_key
        self.api = sj.Shioaji()

    def login(self):
        self.api.login(api_key=self.api_key, secret_key=self.secret_key)

    def subscribe_batch(self, contracts: list):
        for idx, contract in enumerate(contracts):
            self.api.subscribe(contract, quote_type=sj.QuoteType.Tick, version=sj.QuoteVersion.v1)
            if idx % 100 == 0:
                time.sleep(0.1)

    def logout(self):
        try:
            self.api.logout()
        except Exception:
            pass


class Worker:
    def __init__(self):
        self.connections: list[ShioajiConnection] = []  # login() 填好之後才有內容
        self.date_str = today_str(now_taipei())
        self.agg = StockAggregator(market_open_dt(now_taipei()).timestamp())
        self.prev_close: dict[str, float] = load_prev_close(self.date_str)
        # watchlist.txt 裡自編的「族群名稱」對照表(代號 -> 較細的主題族群)，
        # 沒對到的代號在 on_tick_v1 裡會 fallback 回官方 industry_name()。
        self.sector_of_code: dict[str, str] = load_watchlist_sectors()
        self.last_price: dict[str, float] = {}
        self.code_to_contract = {}
        self.cursor: dict[str, list[int]] = {}  # 上次寫 .t.json 時每檔股票 4 桶+價格序列的長度
        self.lock = threading.Lock()
        self.finalized = False

        # ⚠ 盤中重啟接續：如果今天已經有 .b.json（代表這個交易日先前已經在跑，
        # 只是因為改程式/當機而重啟），把裡面累積到重啟前的所有 tick 讀回記憶體，
        # 讓圖表接續畫下去，而不是每次重啟就把當天的資料清零重新累積一次。
        self._resume_from_snapshot_if_any()

    def _resume_from_snapshot_if_any(self):
        path = os.path.join(FLOWDATA_DIR, f"{self.date_str}.b.json")
        if not os.path.exists(path):
            return  # 今天還沒有任何快照，正常的第一次啟動，從 0 開始就好
        try:
            with open(path, "r", encoding="utf-8") as f:
                snapshot = json.load(f)
        except Exception as e:
            print(f"[worker] 讀取既有快照 {path} 失敗，改成從 0 開始累積: {e}")
            return
        try:
            restored_last_price = self.agg.load_from_snapshot(snapshot, sector_override=self.sector_of_code)
            self.last_price.update(restored_last_price)
            # cursor 對齊到目前已經累積的長度，重啟後第一次寫 .t.json 才只會包含
            # 「真的新增」的 tick，不會把接續回來的整天資料當成新增量重送一次。
            self.cursor = self.agg.current_cursor()
            print(f"[worker] 已從既有快照接續 {self.agg.tick_count} 筆 tick"
                  f"（{path}，asof={snapshot.get('asof')}），不會從 0 重新累積。")
        except Exception as e:
            print(f"[worker] 接續既有快照失敗，改成從 0 開始累積: {e}")
            # 保底：接續失敗就退回全新的聚合器，不要讓半套失敗的狀態污染資料。
            self.agg = StockAggregator(market_open_dt(now_taipei()).timestamp())
            self.cursor = {}

    # ---------- Shioaji ----------

    def login_all(self):
        accounts = load_accounts()
        if not accounts:
            raise RuntimeError("缺少 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY")
        print(f"[worker] 準備登入 {len(accounts)} 組帳號、每組 {CONNECTIONS_PER_ACCOUNT} 條連線"
              f"(總共最多 {len(accounts) * CONNECTIONS_PER_ACCOUNT} 條)...")
        for label, api_key, secret_key in accounts:
            for slot in range(1, CONNECTIONS_PER_ACCOUNT + 1):
                conn_label = f"{label}-{slot}"
                conn = ShioajiConnection(conn_label, api_key, secret_key)
                try:
                    print(f"[worker] 登入連線 {conn_label}...")
                    conn.login()
                    conn.api.set_on_tick_stk_v1_callback(self.on_tick_v1)
                    self.connections.append(conn)
                except Exception as e:
                    # 單一條連線登入失敗不要讓整支 worker 掛掉，其他條還是能正常收資料，
                    # 只是總涵蓋範圍會少一批(190 檔)。常見原因：帳號本身連線數已經被
                    # 別的程式佔用、或觸發永豐那邊的頻率限制。
                    print(f"[worker] 連線 {conn_label} 登入失敗，略過這條，"
                          f"不影響其他連線: {e}")
                time.sleep(LOGIN_STAGGER_SEC)
        if not self.connections:
            raise RuntimeError("所有連線都登入失敗，worker 無法啟動")
        print(f"[worker] 登入完成，共 {len(self.connections)} 條連線可用")

    def setup_subscriptions(self):
        print("[worker] 取得商品清單...")
        all_contracts = {}
        # 契約清單所有連線內容都一樣，用第一條連線抓一次就好，不用每條都重抓。
        ref_api = self.connections[0].api
        for exchange in [ref_api.Contracts.Stocks.TSE, ref_api.Contracts.Stocks.OTC]:
            for contract in exchange:
                code = contract.code
                if len(code) == 4:
                    all_contracts[code] = contract

        # 排序規則：watchlist.txt 裡的股票(有自編族群名稱)一定優先排進去，
        # 保證訂閱得到；剩下的連線容量依代號順序把全市場其餘股票盡量塞進去
        # (這些補進來的股票會 fallback 用官方 industry_name 當族群名稱)。
        watchlist = load_watchlist() or []
        ordered_codes = []
        seen = set()
        missing = []
        for code in watchlist:
            if code in all_contracts:
                if code not in seen:
                    ordered_codes.append(code)
                    seen.add(code)
            else:
                missing.append(code)
        if missing:
            print(f"[worker] watchlist 裡有 {len(missing)} 檔在契約清單裡找不到(代號打錯或下市?): {missing}")
        for code in sorted(all_contracts.keys()):
            if code not in seen:
                ordered_codes.append(code)
                seen.add(code)

        capacity = len(self.connections) * MAX_SUBSCRIBE
        total_market = len(ordered_codes)
        if total_market > capacity:
            print(f"[worker] 全市場有 {total_market} 檔，目前 {len(self.connections)} 條連線"
                  f"總容量只有 {capacity} 檔(每條 {MAX_SUBSCRIBE} 檔)，只會訂閱前 {capacity} 檔"
                  f"(watchlist.txt 裡的股票保證優先訂閱到)。")
            ordered_codes = ordered_codes[:capacity]
        else:
            print(f"[worker] 全市場共 {total_market} 檔，目前連線總容量 {capacity} 檔，全部都訂閱得到。")

        for i, conn in enumerate(self.connections):
            batch_codes = ordered_codes[i * MAX_SUBSCRIBE:(i + 1) * MAX_SUBSCRIBE]
            batch_contracts = [all_contracts[c] for c in batch_codes]
            for c in batch_codes:
                self.code_to_contract[c] = all_contracts[c]
            conn.subscribe_batch(batch_contracts)
            print(f"[worker] 連線 {conn.conn_label} 訂閱了 {len(batch_contracts)} 檔")

        print(f"[worker] 全部連線加起來共訂閱 {len(self.code_to_contract)} 檔股票")

    def on_tick_v1(self, tick: sj.TickSTKv1):
        code = tick.code
        contract = self.code_to_contract.get(code)
        if contract is None:
            return
        is_buy = tick.tick_type == 1
        is_sell = tick.tick_type == 2
        if not (is_buy or is_sell):
            return

        name = getattr(contract, "name", code)
        # 族群名稱優先用 watchlist.txt 自編的主題族群(較細)，
        # 沒對到才 fallback 回 Shioaji category 對應的官方大分類(較粗)。
        sector = self.sector_of_code.get(code) or industry_name(getattr(contract, "category", None))
        price = float(tick.close)
        volume = int(tick.volume)

        # ⚠ Shioaji 即時 tick 回呼給的 tick.datetime 是「naive」datetime
        # (沒有時區資訊)，但它的值其實就是台北當地時間的牆上時鐘時間。
        # 如果直接對它呼叫 .timestamp()，Python 會拿「執行環境的系統時區」
        # 去解讀這個 naive 時間 —— 這台機器/容器通常是 UTC，於是每一筆 tick
        # 算出來的 epoch 都會比真正時間多了整整 8 小時(台北是 UTC+8)，導致
        # 開盤沒多久，t_offset(距開盤秒數)就被算成快 8 小時，前端累積折線圖
        # 的 X 軸因此被拉到快 17 點、真正的資料全部擠在圖表最左邊一小條，
        # 完全看不清楚。修法：幫這個 naive datetime 補上 Asia/Taipei 時區
        # 再呼叫 .timestamp()，這樣 Python 才會用正確的時區換算成 epoch。
        ts = getattr(tick, "datetime", None)
        if ts is not None:
            if ts.tzinfo is None and TAIPEI is not None:
                ts = ts.replace(tzinfo=TAIPEI)
            epoch_ts = ts.timestamp()
        else:
            epoch_ts = time.time()

        with self.lock:
            self.agg.add_tick(code, name, sector, epoch_ts, price, volume, is_buy)
            self.last_price[code] = price

    # ---------- 快照輸出 ----------

    def snapshot_loop(self):
        while True:
            time.sleep(SNAPSHOT_SEC)
            try:
                self.write_snapshots()
            except Exception as e:
                print(f"[worker] 寫快照失敗: {e}")

            if not self.finalized and now_taipei() >= market_close_dt(now_taipei()):
                try:
                    self.finalize()
                except Exception as e:
                    print(f"[worker] finalize 失敗: {e}")
                break

    def write_snapshots(self):
        with self.lock:
            asof = now_taipei().isoformat()
            full = self.agg.build_full_snapshot(self.date_str, asof, prev_close=self.prev_close)
            incr, next_cursor = self.agg.build_incremental_snapshot(self.date_str, asof, self.cursor)
            incr["from"] = {k: list(v) for k, v in self.cursor.items()}
            self.cursor = next_cursor

        atomic_write_json(os.path.join(FLOWDATA_DIR, f"{self.date_str}.b.json"), full)
        atomic_write_json(os.path.join(FLOWDATA_DIR, f"{self.date_str}.t.json"), incr)

    def finalize(self):
        print("[worker] 收盤，寫入整日檔...")
        with self.lock:
            asof = now_taipei().isoformat()
            full = self.agg.build_full_snapshot(self.date_str, asof, prev_close=self.prev_close)
            full["final"] = True
            full["last_price"] = dict(self.last_price)
            # 順便把當天每檔股票的開盤價/最高價存進整日檔，給「族群/個股淨流入
            # 排行」表的「隔天開/收/最高%」欄位用；隔天要看「這天」的開盤/最高
            # 價時，直接讀這兩個頂層欄位就好，不用整包解碼 price 序列。
            open_price, high_price = self.agg.open_high_prices()
            full["open_price"] = open_price
            full["high_price"] = high_price
        atomic_write_json(os.path.join(FLOWDATA_DIR, f"{self.date_str}.json"), full)
        self.finalized = True
        print("[worker] 整日檔已寫入，worker 即將結束")

    def run(self):
        self.login_all()
        self.setup_subscriptions()

        snap_thread = threading.Thread(target=self.snapshot_loop, daemon=False)
        snap_thread.start()

        print("[worker] 開始接收逐筆成交...")
        try:
            snap_thread.join()
        except KeyboardInterrupt:
            print("[worker] 收到中斷，寫最後一次快照後結束")
            self.write_snapshots()
        finally:
            for conn in self.connections:
                conn.logout()


def main():
    n = now_taipei()
    open_dt = market_open_dt(n)
    close_dt = market_close_dt(n)
    if n < open_dt:
        wait_sec = (open_dt - n).total_seconds()
        print(f"[worker] 現在還沒開盤，{wait_sec:.0f} 秒後({open_dt})開始訂閱")
        time.sleep(min(wait_sec, 6 * 3600))  # 最長先睡 6 小時，避免太早啟動時等太久
    elif n >= close_dt:
        print(f"[worker] 現在({n})已經收盤，今天不會再有新 tick。"
              f" 如果 {today_str(n)}.json 還沒產生，請改用歷史 tick 回補腳本，而不是這支即時 worker。")
        return

    Worker().run()


if __name__ == "__main__":
    main()
