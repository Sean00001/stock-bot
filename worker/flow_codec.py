"""
盤中資金流向 - tick 級快照編碼格式

設計目標：
  * 每一筆成交(tick)依成交金額分進 4 個量單桶：特大單/大單/中單/小單
  * 同一桶內的多筆 tick 用「時間差分 + 訊號值」壓成兩個平行陣列 (i, v)，
    小額股票整天可能只有幾十筆 tick，大型權值股可能上萬筆，
    差分編碼可以讓大部分 i 值落在個位數/十位數，JSON 體積小很多。
  * i[0] = 該筆 tick 距離開盤(09:00:00)的「秒數」；i[k>0] = 與前一筆的秒數差(>=0)。
  * v[k] = 該筆 tick 的「成交金額」，買方成交為正、賣方成交為負(股票主動買賣，
    tick_type=1 內盤/2 外盤，依 Shioaji 定義換算)。
  * 同一結構也拿來記 amt（未帶正負號的成交金額，用來算總成交值），此時 v 恆為正。
  * 同一份差分編碼結構也拿來記每檔股票「未分桶」的原始成交價序列(price)，
    給下鑽到某個族群後、個股卡片上的股價走勢小圖用；v 是價格 * PRICE_SCALE
    的四捨五入整數(存分，不存元)，解碼時記得除回去。

一個(淨額/成交值)桶的資料結構：{"i": [...], "v": [...]}
一檔股票的資料結構：[bucket_xl, bucket_l, bucket_m, bucket_s]  (net 與 amt 各一份)
股價序列是「單一」{"i":[...],"v":[...]}，不分桶(每筆 tick 不分大小單都會記一筆價格)。
一份快照：
{
  "date": "2026-08-27",
  "to": 12345,              # 目前已處理的 tick 總數 (checkpoint，供 .t.json 增量比對)
  "asof": "2026-08-27T13:24:59+08:00",
  "market_open_ts": 1787871600.0,   # 09:00:00 當天的 epoch秒，供還原 i[0] 用
  "sectors": ["半導體", "PCB板廠", ...],           # 只在 .b/.full 出現，順序固定
  "stocks": {"半導體": [{"code":"2330","name":"台積電"}, ...], ...},  # 同上，只在 .b/.full 出現
  "net": {"半導體": [ [bucket_xl,bucket_l,bucket_m,bucket_s], ... ] , ...},  # 依 stocks 陣列同順序
  "amt": {"半導體": [ [bucket_xl,bucket_l,bucket_m,bucket_s], ... ] , ...},
  "price": {"半導體": [ price_bucket, ... ] , ...},     # 依 stocks 陣列同順序，每檔股票一條序列
  "price_scale": 100,        # price.v 除以這個數字才是實際股價(元)
  "prev_close": {"2330": 950.0, ...},   # 前一交易日收盤價，直接內嵌在每次快照(不只 finalize 時)
}

.t.json（增量檔）：跟上面格式一樣，但 i/v 陣列只包含「自上次 checkpoint 之後」的新 tick，
                    且沒有 sectors/stocks（前端沿用上一次拿到的 metadata）。
                    有 "from" 欄位標示這批增量的起始 checkpoint，前端核對
                    from === 本地目前的 to 才能直接疊加，否則要整包重抓 .b.json。
.b.json（全量校正檔）：完整重算一次到目前為止的全部 tick，含 metadata，
                    供第一次載入或 checkpoint 兜不起來時 resync 用。
{date}.json（收盤後 finalize 檔）：跟 .b.json 同格式，但多一個 "final": true 與
                    "last_price": {code: price}（今日最後一筆成交價，等同於
                    price 序列解碼後的最後一點，這裡多存一份方便不解碼就能用）
"""
from __future__ import annotations
import bisect

# 量單分桶門檻（新台幣元）
THRESH_XL = 10_000_000   # >= 1000萬 -> 特大單
THRESH_L = 3_000_000     # >= 300萬  -> 大單
THRESH_M = 1_000_000     # >= 100萬  -> 中單
BUCKET_NAMES = ["xl", "l", "m", "s"]


def classify_bucket(amount: float) -> int:
    """回傳 0=特大單 1=大單 2=中單 3=小單"""
    a = abs(amount)
    if a >= THRESH_XL:
        return 0
    if a >= THRESH_L:
        return 1
    if a >= THRESH_M:
        return 2
    return 3


class TickSeries:
    """單一(股票, 桶)的 tick 序列。內部存原始 (t_offset_sec, value)，
    輸出時才做差分編碼，讀取增量時用 index 切片即可，不用重新掃描全部資料。"""

    __slots__ = ("t", "v")

    def __init__(self):
        self.t: list[int] = []   # 秒數 offset (相對開盤)，遞增
        self.v: list[float] = []

    def append(self, t_offset: int, value: float):
        # 保底：極少數情況下(多執行緒/交易所回補)tick 可能微幅亂序，
        # 差分編碼與 bisect 都假設 t 是不遞減序列，這裡強制 clamp 避免資料損毀。
        if self.t and t_offset < self.t[-1]:
            t_offset = self.t[-1]
        self.t.append(t_offset)
        self.v.append(value)

    def __len__(self):
        return len(self.t)

    def encode_full(self) -> dict:
        return self._encode(0)

    def encode_from(self, start_idx: int) -> dict:
        return self._encode(start_idx)

    def _encode(self, start_idx: int) -> dict:
        ts = self.t[start_idx:]
        vs = self.v[start_idx:]
        if not ts:
            return {"i": [], "v": []}
        i_out = [ts[0]]
        for k in range(1, len(ts)):
            i_out.append(ts[k] - ts[k - 1])
        # value 用整數即可，四捨五入避免浮點誤差累積(價格序列存的是「分」，同樣道理)
        v_out = [round(x) for x in vs]
        return {"i": i_out, "v": v_out}


def decode(bucket: dict) -> list[tuple[int, int]]:
    """把 {"i":[...],"v":[...]} 還原成 [(t_offset, value), ...]"""
    i = bucket.get("i") or []
    v = bucket.get("v") or []
    out = []
    running = 0
    for k, (di, val) in enumerate(zip(i, v)):
        running = di if k == 0 else running + di
        out.append((running, val))
    return out


def sum_window(bucket: dict, t_from: int | None, t_to: int | None) -> float:
    """對一個已解碼(或先解碼)的桶，加總落在 [t_from, t_to) 秒數區間內的 value。
    t_from=None 代表從最早開始，t_to=None 代表到最新為止。"""
    pts = decode(bucket) if isinstance(bucket, dict) else bucket
    if not pts:
        return 0.0
    times = [p[0] for p in pts]
    lo = 0 if t_from is None else bisect.bisect_left(times, t_from)
    hi = len(times) if t_to is None else bisect.bisect_left(times, t_to)
    return sum(p[1] for p in pts[lo:hi])


class StockAggregator:
    """整個 worker 進程內，全市場即時累積用的聚合器。
    key = 股票代號；每檔股票底下有 net[4 桶]、amt[4 桶] 與 price(單一序列)。"""

    # 股價序列用整數(分)存，除以 PRICE_SCALE 還原成元，避免浮點誤差/JSON體積膨脹。
    PRICE_SCALE = 100

    def __init__(self, market_open_epoch: float):
        self.market_open_epoch = market_open_epoch
        self.sector_of: dict[str, str] = {}
        self.name_of: dict[str, str] = {}
        self.net: dict[str, list[TickSeries]] = {}
        self.amt: dict[str, list[TickSeries]] = {}
        self.price: dict[str, TickSeries] = {}  # 每檔股票一條「原始成交價」序列(不分桶)
        self.tick_count = 0

    def _ensure(self, code: str, name: str, sector: str):
        if code not in self.net:
            self.net[code] = [TickSeries() for _ in range(4)]
            self.amt[code] = [TickSeries() for _ in range(4)]
            self.price[code] = TickSeries()
            self.sector_of[code] = sector
            self.name_of[code] = name

    def add_tick(self, code: str, name: str, sector: str, epoch_ts: float,
                 price: float, volume: int, is_buy: bool):
        self._ensure(code, name, sector)
        amount = price * volume * 1000  # 1張=1000股
        signed = amount if is_buy else -amount
        b = classify_bucket(amount)
        t_off = int(round(epoch_ts - self.market_open_epoch))
        if t_off < 0:
            t_off = 0
        self.net[code][b].append(t_off, signed)
        self.amt[code][b].append(t_off, amount)
        self.price[code].append(t_off, round(price * self.PRICE_SCALE))
        self.tick_count += 1

    # ---- 快照輸出 ----

    def sectors(self) -> list[str]:
        return sorted(set(self.sector_of.values()))

    def stocks_by_sector(self) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for code, sector in self.sector_of.items():
            out.setdefault(sector, []).append({"code": code, "name": self.name_of[code]})
        for sector in out:
            out[sector].sort(key=lambda x: x["code"])
        return out

    def _order(self) -> dict[str, list[str]]:
        """每個 sector 底下股票代號的固定順序（跟 stocks_by_sector 一致），
        net/amt/price 的增量檔就靠這個順序對齊，不用每次都附代號。"""
        by_sector = self.stocks_by_sector()
        return {sec: [s["code"] for s in lst] for sec, lst in by_sector.items()}

    def build_full_snapshot(self, date_str: str, asof_iso: str,
                             prev_close: dict | None = None) -> dict:
        order = self._order()
        net_out, amt_out, price_out = {}, {}, {}
        for sector, codes in order.items():
            net_out[sector] = [[bucket.encode_full() for bucket in self.net[c]] for c in codes]
            amt_out[sector] = [[bucket.encode_full() for bucket in self.amt[c]] for c in codes]
            price_out[sector] = [self.price[c].encode_full() for c in codes]
        return {
            "date": date_str,
            "to": self.tick_count,
            "asof": asof_iso,
            "market_open_ts": self.market_open_epoch,
            "sectors": self.sectors(),
            "stocks": self.stocks_by_sector(),
            "net": net_out,
            "amt": amt_out,
            "price": price_out,
            "price_scale": self.PRICE_SCALE,
            "prev_close": prev_close or {},
        }

    # ---- 接續既有快照(盤中重啟用) ----

    def current_cursor(self) -> dict[str, list[int]]:
        """回傳目前每檔股票 4 桶 + 價格序列各自的長度，格式跟
        build_incremental_snapshot() 回傳的 next_idx 一致，可以直接拿來
        當下一次呼叫 build_incremental_snapshot() 的 from_idx 起點 —— 用在
        worker 重啟後接續舊資料時，讓接續後第一次寫 .t.json 只含真正新增的
        tick，不會把已經接續回來的整天資料當成新增量重送一次。"""
        return {code: [len(self.net[code][b]) for b in range(4)] + [len(self.price[code])]
                for code in self.net}

    def load_from_snapshot(self, snapshot: dict,
                            sector_override: dict[str, str] | None = None) -> dict[str, float]:
        """從既有的 full snapshot(.b.json 或 {date}.json 格式)重建聚合器內部
        狀態，讓 worker 重啟後可以接續先前已經寫入的資料繼續累積，而不是
        從 0 開始(盤中因為改程式/當機重啟 worker，記憶體裡的資料會不見，
        但快照檔已經幫忙保留了重啟前的所有 tick，讀回來接著算就好)。

        sector_override 可傳目前最新的族群分類(例如 watchlist.txt 剛改過)，
        代號有對到新分類就優先用新的，沒對到才沿用快照裡記錄的舊分類 ——
        這樣中途改了族群分組再重啟，接續回來的舊資料也會盡量套用新分類，
        不會停留在改版前的舊族群名稱。

        回傳 {代號: 最後成交價}，供呼叫端拿去補 Worker.last_price（收盤
        finalize 時要用，沒有的話重啟前有成交、重啟後沒再成交的股票就會漏掉）。"""
        stocks_by_sector = snapshot.get("stocks") or {}
        net_in = snapshot.get("net") or {}
        amt_in = snapshot.get("amt") or {}
        price_in = snapshot.get("price") or {}
        price_scale = snapshot.get("price_scale") or self.PRICE_SCALE
        sector_override = sector_override or {}

        restored_last_price: dict[str, float] = {}

        for old_sector, stock_list in stocks_by_sector.items():
            net_row = net_in.get(old_sector) or []
            amt_row = amt_in.get(old_sector) or []
            price_row = price_in.get(old_sector) or []
            for idx, s in enumerate(stock_list):
                code, name = s["code"], s["name"]
                sector = sector_override.get(code) or old_sector
                self._ensure(code, name, sector)

                if idx < len(net_row):
                    for b in range(4):
                        pts = decode(net_row[idx][b])
                        self.net[code][b].t = [p[0] for p in pts]
                        self.net[code][b].v = [p[1] for p in pts]
                if idx < len(amt_row):
                    for b in range(4):
                        pts = decode(amt_row[idx][b])
                        self.amt[code][b].t = [p[0] for p in pts]
                        self.amt[code][b].v = [p[1] for p in pts]
                if idx < len(price_row):
                    pts = decode(price_row[idx])
                    scale_ratio = (self.PRICE_SCALE / price_scale) if price_scale else 1
                    self.price[code].t = [p[0] for p in pts]
                    self.price[code].v = (
                        [p[1] for p in pts] if scale_ratio == 1
                        else [round(p[1] * scale_ratio) for p in pts]
                    )
                    if pts:
                        restored_last_price[code] = pts[-1][1] / self.PRICE_SCALE

        self.tick_count = snapshot.get("to", 0)
        return restored_last_price

    def build_incremental_snapshot(self, date_str: str, asof_iso: str,
                                    from_idx: dict[str, list[int]]) -> dict:
        """from_idx: {code: [xl_start, l_start, m_start, s_start, price_start]} 上次已編碼到的位置。
        回傳的快照只含新增的部分，並附上新的 cursor 供下次呼叫。"""
        order = self._order()
        net_out, amt_out, price_out = {}, {}, {}
        next_idx: dict[str, list[int]] = {}
        for sector, codes in order.items():
            net_row, amt_row, price_row = [], [], []
            for code in codes:
                starts = from_idx.get(code, [0, 0, 0, 0, 0])
                net_row.append([self.net[code][b].encode_from(starts[b]) for b in range(4)])
                amt_row.append([self.amt[code][b].encode_from(starts[b]) for b in range(4)])
                price_row.append(self.price[code].encode_from(starts[4] if len(starts) > 4 else 0))
                next_idx[code] = [len(self.net[code][b]) for b in range(4)] + [len(self.price[code])]
            net_out[sector] = net_row
            amt_out[sector] = amt_row
            price_out[sector] = price_row
        return {
            "date": date_str,
            "to": self.tick_count,
            "asof": asof_iso,
            "net": net_out,
            "amt": amt_out,
            "price": price_out,
            "price_scale": self.PRICE_SCALE,
        }, next_idx
