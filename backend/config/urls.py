from django.contrib import admin
from django.urls import path
from app import views

urlpatterns = [
    path("admin/", admin.site.urls),

    path("api/login/", views.login_view),
    path("api/logout/", views.logout_view),
    path("api/session/", views.session_view),

    path("api/flow/dates/", views.flow_dates_view),
    # kind: t(增量) / b(全量校正) / full(收盤後整日檔)
    path("api/flow/snapshot/<str:date>/<str:kind>/", views.flow_snapshot_view),
    # 只回傳整日檔裡的幾個小欄位(last_price/open_price/high_price/prev_close)，
    # 不含整包 tick 資料——給「族群/個股淨流入排行」表查「隔天」的價格用，
    # 不用為了三個數字下載/解碼一次可能十幾 MB 的完整快照。
    path("api/flow/summary/<str:date>/", views.flow_summary_view),
]
