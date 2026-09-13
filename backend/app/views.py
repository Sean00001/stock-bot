import os
import json
import functools

from django.conf import settings
from django.http import JsonResponse, HttpResponse, HttpResponseNotFound
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods


def require_login(view_func):
    """簡易帳密登入用的裝飾器：沒登入回 401 JSON，而不是 Django 預設的
    「導到登入頁」行為（前端是 SPA，不需要那種導頁）。"""

    @functools.wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if not request.session.get("authenticated"):
            return JsonResponse({"status": "error", "error": "unauthenticated"}, status=401)
        return view_func(request, *args, **kwargs)

    return wrapper


# ---------- 登入 / 登出 / session 狀態 ----------

@csrf_exempt
@require_http_methods(["POST"])
def login_view(request):
    try:
        body = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"status": "error", "error": "invalid json"}, status=400)

    username = body.get("username", "")
    password = body.get("password", "")

    if username == settings.APP_USERNAME and password == settings.APP_PASSWORD:
        request.session["authenticated"] = True
        request.session["username"] = username
        request.session.set_expiry(60 * 60 * 24 * 14)  # 14 天
        return JsonResponse({"status": "ok", "username": username})

    return JsonResponse({"status": "error", "error": "帳號或密碼錯誤"}, status=401)


@csrf_exempt
@require_http_methods(["POST"])
def logout_view(request):
    request.session.flush()
    return JsonResponse({"status": "ok"})


def session_view(request):
    authenticated = bool(request.session.get("authenticated"))
    return JsonResponse({
        "status": "ok",
        "authenticated": authenticated,
        "username": request.session.get("username") if authenticated else None,
    })


# ---------- 快照檔案伺服 ----------

def _list_available_dates() -> dict:
    """掃 FLOWDATA_DIR，回傳 {"final": [...finalize過的日期...], "live": [...今天正在更新的日期...]}
    供前端日期選單使用。跟 worker/worker_common.py 裡同名函式邏輯一致，
    這邊獨立一份是因為 backend 跟 worker 是各自獨立部署的服務，不共用 Python 模組。"""
    final_dates, live_dates = set(), set()
    d = settings.FLOWDATA_DIR
    if not os.path.isdir(d):
        return {"final": [], "live": []}
    for fn in os.listdir(d):
        if fn.endswith(".b.json"):
            live_dates.add(fn[: -len(".b.json")])
        elif fn.endswith(".json") and not fn.endswith(".t.json"):
            final_dates.add(fn[: -len(".json")])
    live_dates -= final_dates
    return {"final": sorted(final_dates), "live": sorted(live_dates)}


@require_login
def flow_dates_view(request):
    return JsonResponse({"status": "ok", **_list_available_dates()})


_KIND_SUFFIX = {
    "t": ".t.json",
    "b": ".b.json",
    "full": ".json",
}


@require_login
def flow_snapshot_view(request, date: str, kind: str):
    suffix = _KIND_SUFFIX.get(kind)
    if suffix is None:
        return JsonResponse({"status": "error", "error": "kind 必須是 t / b / full"}, status=400)

    # date 只允許 YYYY-MM-DD 形狀，避免被拿去做路徑穿越
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        return JsonResponse({"status": "error", "error": "date 格式錯誤"}, status=400)

    path = os.path.join(settings.FLOWDATA_DIR, f"{date}{suffix}")
    path = os.path.normpath(path)
    if not path.startswith(os.path.normpath(settings.FLOWDATA_DIR)):
        return JsonResponse({"status": "error", "error": "非法路徑"}, status=400)

    if not os.path.exists(path):
        return HttpResponseNotFound(json.dumps({"status": "error", "error": "not found"}), content_type="application/json")

    with open(path, "rb") as f:
        data = f.read()
    resp = HttpResponse(data, content_type="application/json")
    resp["Cache-Control"] = "no-store"
    return resp


# 「族群/個股淨流入排行」表的「隔天開/收/最高%」欄位，只需要整日檔裡幾個
# 小的頂層欄位(每檔股票的當天開盤價/最高價/收盤價/前一日收盤價)，不需要
# tick 級的 net/amt/price 序列——那些一天可以有十幾 MB，只為了三個數字整包
# 抓下來、還要在前端解碼一次很浪費。這個 API 直接在 backend 這邊把整日檔
# 讀進來，只挑幾個小欄位回傳，回應通常只有幾十 KB。
_SUMMARY_KEYS = ("date", "asof", "final", "last_price", "open_price", "high_price", "prev_close")


@require_login
def flow_summary_view(request, date: str):
    if len(date) != 10 or date[4] != "-" or date[7] != "-":
        return JsonResponse({"status": "error", "error": "date 格式錯誤"}, status=400)

    path = os.path.join(settings.FLOWDATA_DIR, f"{date}.json")
    path = os.path.normpath(path)
    if not path.startswith(os.path.normpath(settings.FLOWDATA_DIR)):
        return JsonResponse({"status": "error", "error": "非法路徑"}, status=400)

    # 這個 API 只對「已經收盤 finalize」的日期有意義({date}.json 才會有
    # open_price/high_price)，還在盤中的日期沒有這個檔案，直接回 404，
    # 前端看到 404 就知道這天還沒有「隔天」可以查。
    if not os.path.exists(path):
        return HttpResponseNotFound(json.dumps({"status": "error", "error": "not found"}), content_type="application/json")

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    summary = {k: data.get(k) for k in _SUMMARY_KEYS}
    resp = JsonResponse(summary)
    resp["Cache-Control"] = "no-store"
    return resp
