"""worker/main.py 與 worker/mock_replay.py 共用的小工具，
拆出來是為了讓 mock_replay.py 不用依賴 shioaji 就能執行。"""
import os
import re
import json
import shutil
import tempfile
import datetime as dt

from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv(usecwd=True))  # 不管從 project 根目錄還是 worker/ 底下執行都找得到 .env

try:
    from zoneinfo import ZoneInfo
    TAIPEI = ZoneInfo("Asia/Taipei")
except Exception:  # pragma: no cover
    TAIPEI = None

_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # worker/ 的上一層
_flowdata_env = os.getenv("FLOWDATA_DIR", "/data/flowdata")
FLOWDATA_DIR = (
    _flowdata_env if os.path.isabs(_flowdata_env)
    else os.path.abspath(os.path.join(_project_root, _flowdata_env))
)
MARKET_OPEN_HM = (9, 0)
MARKET_CLOSE_HM = (13, 30)

# 永豐 Shioaji 官方文件：一條連線 api.subscribe() 最多 200 檔，同一個 person_id
# 最多可開 5 條連線 (https://sinotrade.github.io/zh/tutor/limit/)。留一點安全邊界，
# 不卡在剛好 200 的邊界上，所以每條連線實際訂閱數用這個值。
MAX_SUBSCRIBE = int(os.getenv("MAX_SUBSCRIBE", "190"))
WATCHLIST_FILE = os.getenv("WATCHLIST_FILE", os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.txt"))

# 2026-08-28 新增：多連線支援。全市場上市+上櫃 4 碼股票加起來將近 1900 檔，
# 遠超過單一連線 200 檔的上限，但同一個 person_id 最多可以開到 5 條連線，
# 5 條就有機會涵蓋到全市場(5 x 190 = 950；如果之後又多一組帳號，兩組帳號
# 各開 5 條，理論上就能涵蓋全部約 1900 檔)。每條連線開幾條由
# CONNECTIONS_PER_ACCOUNT 控制，預設 5(官方上限)。
CONNECTIONS_PER_ACCOUNT = int(os.getenv("CONNECTIONS_PER_ACCOUNT", "5"))


def load_accounts() -> list[tuple[str, str, str]]:
    """讀取所有帳號的 Shioaji API key/secret，回傳 [(帳號代稱, api_key, secret_key), ...]。

    帳號 1 沿用既有的 SHIOAJI_API_KEY / SHIOAJI_SECRET_KEY(向下相容，原本只有
    一組帳號時的設定不用改)。之後如果想再加一組帳號(例如請別人也申請一組
    Shioaji API key 一起用)，在 .env 加 SHIOAJI_API_KEY_2 / SHIOAJI_SECRET_KEY_2
    就會自動被抓到，不用改任何程式碼；要加第三組就繼續往下編號 _3、_4...

    ⚠ 用別人的 API key 要注意：這組 key 通常同時具備下單交易的權限，不是只有
    看盤資料而已，等於是把對方帳號的操作能力也交給這支程式在跑。實際使用前
    請先跟對方確認永豐的服務條款是否允許這樣共用/借用，並考慮跟永豐客服確認
    能否申請「只能看資料、不能下單」的權限範圍。"""
    accounts = []
    k1 = os.getenv("SHIOAJI_API_KEY")
    s1 = os.getenv("SHIOAJI_SECRET_KEY")
    if k1 and s1:
        accounts.append(("account1", k1, s1))
    i = 2
    while True:
        k = os.getenv(f"SHIOAJI_API_KEY_{i}")
        s = os.getenv(f"SHIOAJI_SECRET_KEY_{i}")
        if not k or not s:
            break
        accounts.append((f"account{i}", k, s))
        i += 1
    return accounts

os.makedirs(FLOWDATA_DIR, exist_ok=True)


def now_taipei() -> dt.datetime:
    if TAIPEI:
        return dt.datetime.now(TAIPEI)
    return dt.datetime.now()


def today_str(d: dt.datetime) -> str:
    return d.strftime("%Y-%m-%d")


def market_open_dt(d: dt.datetime) -> dt.datetime:
    return d.replace(hour=MARKET_OPEN_HM[0], minute=MARKET_OPEN_HM[1], second=0, microsecond=0)


def market_close_dt(d: dt.datetime) -> dt.datetime:
    return d.replace(hour=MARKET_CLOSE_HM[0], minute=MARKET_CLOSE_HM[1], second=0, microsecond=0)


def atomic_write_json(path: str, payload: dict):
    """先寫暫存檔再 rename，避免前端輪詢時讀到寫一半的檔案。"""
    d = os.path.dirname(path) or "."
    fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, separators=(",", ":"))
        shutil.move(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


def load_prev_close(date_str: str) -> dict:
    """往前找最近一份已經 finalize 的整日檔，取它的 last_price 當作 prev_close。
    最多往前找 10 天（跳過假日/沒開盤的日子）。"""
    d = dt.datetime.strptime(date_str, "%Y-%m-%d")
    for back in range(1, 11):
        cand = (d - dt.timedelta(days=back)).strftime("%Y-%m-%d")
        path = os.path.join(FLOWDATA_DIR, f"{cand}.json")
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data.get("last_price", {})
            except Exception:
                continue
    return {}


def list_available_dates() -> dict:
    """掃 FLOWDATA_DIR，回傳 {"final": [...finalize過的日期...], "live": [...今天正在更新的日期...]}
    供前端日期選單使用。"""
    final_dates, live_dates = set(), set()
    if not os.path.isdir(FLOWDATA_DIR):
        return {"final": [], "live": []}
    for fn in os.listdir(FLOWDATA_DIR):
        if fn.endswith(".b.json"):
            live_dates.add(fn[: -len(".b.json")])
        elif fn.endswith(".json") and not fn.endswith(".t.json"):
            final_dates.add(fn[: -len(".json")])
    live_dates -= final_dates  # 已經 finalize 的就不算 live 了
    return {"final": sorted(final_dates), "live": sorted(live_dates)}


# 上市/上櫃證券「產業別」代號 -> 中文名稱對照表。
# Shioaji 的 contract.category 給的是這種兩碼數字代號字串(例如 "24")，不是
# 中文名稱，直接拿來當族群顯示會變成一堆數字，跟畫面上其他地方期待的中文
# 族群名稱對不起來。對照表來源：台灣證券交易所「證券編碼/分類查詢」
# (https://isin.twse.com.tw/isin/class_i.jsp?kind=1)，"00" 是 ETF 額外補上的
# (該頁沒有把 ETF 算進產業分類表，但實際資料裡 0050/0056 這類 ETF 的
# category 就是 "00")。上櫃(OTC)公司目前觀察到共用同一套代號。
#
# 這份官方分類是「證交所大分類」，顆粒度比參考網站(twstock.xyz)看到的自編
# 主題族群(被動元件/晶圓代工/光學鏡頭/封測...)粗很多 —— 例如台積電、聯電、
# 世界先進都會被歸在同一個「半導體業」底下，沒辦法再拆出「晶圓代工」。
# 所以畫面上顯示的族群名稱改成優先用 load_watchlist_sectors()（來自
# watchlist.txt 裡自己編的「# --- 族群名稱 ---」區段標題），這份官方對照表
# 只當作「watchlist.txt 沒把某代號放進任何區段」時的保底 fallback。
INDUSTRY_NAME_BY_CODE = {
    "00": "ETF",
    "01": "水泥工業",
    "02": "食品工業",
    "03": "塑膠工業",
    "04": "紡織纖維",
    "05": "電機機械",
    "06": "電器電纜",
    "08": "玻璃陶瓷",
    "09": "造紙工業",
    "10": "鋼鐵工業",
    "11": "橡膠工業",
    "12": "汽車工業",
    "13": "電子工業",
    "14": "建材營造業",
    "15": "航運業",
    "16": "觀光餐旅",
    "17": "金融保險業",
    "18": "貿易百貨業",
    "19": "綜合",
    "20": "其他業",
    "21": "化學工業",
    "22": "生技醫療業",
    "23": "油電燃氣業",
    "24": "半導體業",
    "25": "電腦及週邊設備業",
    "26": "光電業",
    "27": "通信網路業",
    "28": "電子零組件業",
    "29": "電子通路業",
    "30": "資訊服務業",
    "31": "其他電子業",
    "32": "文化創意業",
    "33": "農業科技業",
    "35": "綠能環保",
    "36": "數位雲端",
    "37": "運動休閒",
    "38": "居家生活",
}


def industry_name(category_code) -> str:
    """把 Shioaji contract.category 的數字代號轉成中文族群名稱；
    查不到的代號(理論上不該發生，但保底)顯示成「其他(代號)」，
    方便之後發現有漏掉的代號要補進 INDUSTRY_NAME_BY_CODE。
    這是官方大分類，只在 load_watchlist_sectors() 沒有這檔的自編族群時
    當 fallback 用，見上面 INDUSTRY_NAME_BY_CODE 的說明。"""
    if not category_code:
        return "其他"
    code = str(category_code).strip()
    return INDUSTRY_NAME_BY_CODE.get(code, f"其他({code})")


_SECTION_HEADER_RE = re.compile(r"^#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")


def _parse_watchlist():
    """讀 watchlist.txt，一次解析出：
      codes          -- 股票代號清單(依檔案內順序、已去重)
      sector_of_code -- {代號: 該代號所屬「# --- 族群名稱 ---」區段標題}
    watchlist.txt 裡本來就容許同一代號出現在多個區段(例如 2308 同時在
    「PCB / 載板 / 被動元件」跟「車用/汽車」底下)，這種情況用「第一次出現
    時所屬的區段」為準，跟 codes 去重的邏輯一致(先出現先贏)。
    檔案不存在時回傳 (None, {})，呼叫端(load_watchlist/load_watchlist_sectors)
    各自決定退路。"""
    if not os.path.exists(WATCHLIST_FILE):
        return None, {}

    codes = []
    sector_of_code = {}
    seen = set()
    current_sector = None

    with open(WATCHLIST_FILE, encoding="utf-8") as f:
        for raw_line in f:
            stripped = raw_line.strip()
            m = _SECTION_HEADER_RE.match(stripped)
            if m:
                current_sector = m.group(1).strip()
                continue

            line = raw_line.split("#", 1)[0].strip()  # 去掉行內註解，只留代號
            if not line:
                continue
            if line not in seen:
                seen.add(line)
                codes.append(line)
            if current_sector and line not in sector_of_code:
                sector_of_code[line] = current_sector

    return codes, sector_of_code


def load_watchlist():
    """讀 watchlist.txt，回傳股票代號清單(依檔案內順序、已去重)；
    檔案不存在就回傳 None，呼叫端要自己決定退路。"""
    codes, _ = _parse_watchlist()
    return codes


def load_watchlist_sectors() -> dict:
    """讀 watchlist.txt 裡「# --- 族群名稱 ---」這種區段標題，回傳
    {股票代號: 族群名稱}。這份是畫面上顯示族群名稱的主要來源(自編主題族群，
    顆粒度比 industry_name() 那份官方證交所產業分類細)；watchlist.txt 不存在，
    或某代號沒被放進任何區段標題底下，回傳的 dict 就不會有那個代號，呼叫端
    要自己 fallback 回 industry_name()。"""
    _, sector_of_code = _parse_watchlist()
    return sector_of_code
