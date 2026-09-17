# -*- coding: utf-8 -*-
"""访问控制：登录页 + 签名会话 Cookie。账号密码在根目录 .env 配置。

配置（来源与 providers 一致：根 .env > 应用目录 .env > 进程环境变量）
  AUTH_ENABLED=1/0        总开关；默认「配了账号密码就开启」
  AUTH_USER               登录账号（例：exam）
  AUTH_PASSWORD           登录密码
  AUTH_SECRET             会话签名密钥；留空则按账号密码派生（改密码即让旧会话失效）
  AUTH_SESSION_DAYS       登录有效期天数（默认 7）

为什么用会话 Cookie 而不是 HTTP Basic：
  Cookie 不区分端口 —— 同一台机器上 8 个应用共用同一个会话，登录一次即可访问全部；
  Basic 认证每个端口都要重新弹一次框。前提是 8 个应用用同一个 AUTH_SECRET（容器里由
  compose 统一注入，裸机下读同一份根目录 .env）。

豁免路径：/login /logout /api/health（门户状态灯要服务端代探它）与 /static/*
API 请求返回 401 JSON；页面请求 302 到登录页（next 只接受站内相对路径，防开放重定向）。
同一来源连续失败 5 次锁定 60 秒（内存节流，够挡脚本撞库）。
"""

import hmac
import html
import time
from datetime import timedelta
from urllib.parse import quote

from flask import jsonify, redirect, request, session

import providers

EXEMPT_PATHS = ("/login", "/logout", "/api/health", "/favicon.ico")
_MAX_FAILS = 5
_LOCK_SECONDS = 60
_fails = {}          # {remote_addr: [失败时间戳...]}


# ----------------------------------------------------------------------
# 配置读取
# ----------------------------------------------------------------------
def _conf():
    env_file = providers.read_env_file(providers.ROOT_ENV_FILE)
    app_file = providers.read_env_file(providers.APP_ENV_FILE)

    def get(name, default=""):
        for src in (env_file, app_file):
            v = (src.get(name) or "").strip()
            if v:
                return v
        return (providers.os.environ.get(name) or "").strip() or default

    user = get("AUTH_USER")
    password = get("AUTH_PASSWORD")
    enabled_raw = get("AUTH_ENABLED")
    if enabled_raw in ("0", "false", "False", "no", "off"):
        enabled = False
    elif enabled_raw in ("1", "true", "True", "yes", "on"):
        enabled = True
    else:
        enabled = bool(user and password)          # 默认：配了账号密码就开启
    try:
        days = int(get("AUTH_SESSION_DAYS", "7"))
    except ValueError:
        days = 7
    secret = get("AUTH_SECRET")
    if not secret:
        # 派生：账号密码变了，旧会话自动失效（安全且零配置）
        secret = "cet6-" + hmac.new(b"cet6-auth", (user + "\n" + password).encode("utf-8"),
                                    "sha256").hexdigest()
    return {"enabled": enabled, "user": user, "password": password,
            "secret": secret, "days": days}


def enabled():
    c = _conf()
    return bool(c["enabled"] and c["user"] and c["password"])


def _app_label():
    name = providers.BASE_DIR.name
    return {"config": "引擎与密钥配置"}.get(name, name or "CET-6 学习工具")


# ----------------------------------------------------------------------
# 节流
# ----------------------------------------------------------------------
def _locked(addr):
    now = time.time()
    hits = [t for t in _fails.get(addr, []) if now - t < _LOCK_SECONDS]
    _fails[addr] = hits
    return len(hits) >= _MAX_FAILS


def _record_fail(addr):
    _fails.setdefault(addr, []).append(time.time())


def _clear_fails(addr):
    _fails.pop(addr, None)


# ----------------------------------------------------------------------
# 登录页
# ----------------------------------------------------------------------
LOGIN_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录 · CET-6 全流程学习</title>
<style>
  :root{--brand:#4f46e5;--ink:#111827;--ink-3:#6b7280;--ink-4:#9ca3af;--line:#e5e7eb;
        --line-2:#d1d5db;--bg:#f7f8fc;--surface:#fff;--danger:#b91c1c;--danger-bg:#fef2f2;--danger-line:#fecaca}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
       background:var(--bg);color:var(--ink);
       font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif}
  .wrap{width:100%;max-width:400px}
  .brand{text-align:center;font-size:13.5px;color:var(--ink-3);margin-bottom:14px;letter-spacing:.3px}
  .card{background:var(--surface);border:1px solid var(--line);border-top:3px solid var(--brand);
        border-radius:16px;padding:28px;box-shadow:0 1px 2px rgba(16,24,40,.04),0 18px 40px -22px rgba(16,24,40,.25)}
  h1{margin:0 0 6px;font-size:20px;font-weight:700}
  .sub{margin:0 0 20px;font-size:13.5px;color:var(--ink-3);line-height:1.6}
  code{background:#eef2ff;color:#4338ca;padding:1px 6px;border-radius:5px;font-size:12.5px}
  label{display:block;font-size:13.5px;font-weight:600;color:#374151;margin-bottom:14px}
  input{width:100%;margin-top:6px;padding:11px 12px;border:1px solid var(--line-2);border-radius:8px;
        font-size:14.5px;font-family:inherit;color:var(--ink);transition:border-color .15s,box-shadow .15s}
  input:focus{border-color:#6366f1;outline:none;box-shadow:0 0 0 3px rgba(99,102,241,.18)}
  button{width:100%;padding:12px;border:none;border-radius:8px;background:var(--brand);color:#fff;
         font-size:15px;font-weight:600;cursor:pointer;font-family:inherit;transition:background .15s}
  button:hover{background:#4338ca}
  .err{background:var(--danger-bg);border:1px solid var(--danger-line);color:var(--danger);
       padding:11px 13px;border-radius:8px;font-size:13.5px;margin-bottom:16px}
  .note{margin:18px 0 0;font-size:12.5px;color:var(--ink-4);line-height:1.6;text-align:center}
</style>
</head>
<body>
  <main class="wrap">
    <div class="brand">🎓 CET-6 全流程学习</div>
    <div class="card">
      <h1>__TITLE__</h1>
      <p class="sub">请输入账号与密码继续。凭据配置在服务器根目录 <code>.env</code>
        （<code>AUTH_USER</code> / <code>AUTH_PASSWORD</code>）。</p>
      __ERROR__
      <form method="post" action="/login">
        <input type="hidden" name="next" value="__NEXT__">
        <label>账号<input name="username" autocomplete="username" autofocus required></label>
        <label>密码<input type="password" name="password" autocomplete="current-password" required></label>
        <button type="submit">登 录</button>
      </form>
      <p class="note">登录一次即可访问全部应用 —— 同一台机器上的 8 个端口共用会话。</p>
    </div>
  </main>
</body>
</html>"""


def _login_page(error="", next_url="/"):
    page = LOGIN_HTML
    page = page.replace("__TITLE__", html.escape(_app_label()))
    page = page.replace("__ERROR__", ('<div class="err">%s</div>' % html.escape(error)) if error else "")
    page = page.replace("__NEXT__", html.escape(next_url or "/"))
    return page


def _safe_next(value):
    """只接受站内相对路径，防开放重定向。"""
    v = (value or "").strip()
    if not v.startswith("/") or v.startswith("//") or "\\" in v:
        return "/"
    return v


# ----------------------------------------------------------------------
# 请求钩子与路由（由 register() 挂到各应用上）
# ----------------------------------------------------------------------
def _login():
    c = _conf()
    addr = request.remote_addr or "?"
    nxt = _safe_next(request.values.get("next") or "/")
    if request.method == "GET":
        if session.get("u") == c["user"]:
            return redirect(nxt)
        return _login_page("", nxt)
    if _locked(addr):
        return _login_page("尝试次数过多，请 60 秒后再试。", nxt), 429
    u = (request.form.get("username") or "").strip()
    p = request.form.get("password") or ""
    if hmac.compare_digest(u, c["user"]) and hmac.compare_digest(p, c["password"]):
        _clear_fails(addr)
        session.permanent = True
        session["u"] = c["user"]
        session["ts"] = int(time.time())
        return redirect(nxt)
    _record_fail(addr)
    return _login_page("账号或密码不正确。", nxt), 401


def _logout():
    session.clear()
    return redirect("/login")


def _guard():
    if not enabled():
        return None
    path = request.path or "/"
    if path in EXEMPT_PATHS or path.startswith("/static/"):
        return None
    c = _conf()
    if session.get("u") == c["user"]:
        return None
    if path.startswith("/api/"):
        return jsonify({"error": "未登录：请先在浏览器登录后再操作", "login": True}), 401
    return redirect("/login?next=" + quote(path, safe="/?=&%"))


def register(app):
    """把访问控制挂到一个 Flask 应用上；返回是否已开启（仅用于日志）。"""
    c = _conf()
    app.secret_key = c["secret"]
    app.config.update(
        SESSION_COOKIE_NAME="cet6_auth",
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        PERMANENT_SESSION_LIFETIME=timedelta(days=c["days"]),
    )
    app.add_url_rule("/login", "cet6_login", _login, methods=["GET", "POST"])
    app.add_url_rule("/logout", "cet6_logout", _logout)
    app.before_request(_guard)
    return enabled()