"""
Django settings for config project.

架構調整(2026-08-27)：拿掉 Channels/Redis/Postgres 即時推播那條線，
改成 worker 把 tick 快照直接寫成靜態 JSON 檔到 FLOWDATA_DIR，
backend 只負責：(1) 把那個目錄的檔案伺服出去 (2) 簡易帳密登入/session
(3) 之後補的價格 API (sec_chart/daily_kbars)。DB 只剩 Django 內建的
session/auth 表，用 SQLite 就夠，不用再跑 Postgres 容器。
"""
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True))

BASE_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = BASE_DIR.parent  # backend/ 的上一層，也就是 stock/ 專案根目錄

SECRET_KEY = os.environ.get(
    "DJANGO_SECRET_KEY",
    "django-insecure-(@*7bt^s)*@zyje_tvv5ld6sv=!gr316ney5y)6ctbsgb71**2",
)

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# 純本機個人使用的 SQLite，檔案放在 backend/db.sqlite3
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "zh-hant"
TIME_ZONE = "Asia/Taipei"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------- 這個專案自己的設定 ----------

# worker 寫快照檔、backend 讀快照檔的共用目錄。跟 worker/worker_common.py
# 的 FLOWDATA_DIR 要指向同一個地方 —— 本機開發時兩邊都吃專案根目錄的 .env。
_flowdata_env = os.environ.get("FLOWDATA_DIR", "flowdata")
FLOWDATA_DIR = (
    _flowdata_env if os.path.isabs(_flowdata_env)
    else str((PROJECT_ROOT / _flowdata_env).resolve())
)

# 簡易帳密登入（不是 Django User，只是單一組帳密比對 + session）
APP_USERNAME = os.environ.get("APP_USERNAME", "alen")
APP_PASSWORD = os.environ.get("APP_PASSWORD", "alen")

# 本機開發用 SPA 直接呼叫 API，登入用的 POST 先不強制 CSRF token
# （純本機個人工具，沒有對外開放；之後如果要部署到外網要另外處理）
