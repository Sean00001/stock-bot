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
]
