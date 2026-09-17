# -*- coding: utf-8 -*-
"""CET-6 全流程学习 · 引擎与密钥配置面板（默认端口 5562）

职责：
  1. 可视化配置 DeepSeek / MiniMax：默认引擎、默认模型、接口地址、认证方式、
     JSON 模式、以及两家的 API 密钥；
  2. 密钥「只写不回显」：页面永远拿不到密钥原文，只显示是否已配置与来源；
  3. 配置落盘：非机密项写 <仓库根>/settings.json，密钥写 <仓库根>/.env ——
     与 7 个训练应用、Docker 部署共用同一份（各应用每次请求都重读，改完即生效）；
  4. 写密钥的安全闸：见 _write_allowed()。

运行：python app.py  （然后自动打开 http://127.0.0.1:5562）
"""

import io
import os
import threading
import webbrowser
from pathlib import Path

import requests

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file

import providers
import auth  # 访问控制：登录页 + 会话（账号密码在根目录 .env 配置）

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
DEFAULT_PORT = 5562

app = Flask(__name__)

# 访问控制：登录页 + 会话（AUTH_USER / AUTH_PASSWORD 在仓库根目录 .env 配置）
auth.register(app)


# ----------------------------------------------------------------------
# .env 加载（与其它应用同款最小实现）
# ----------------------------------------------------------------------
def load_env(path):
    if not path.exists():
        return
    with io.open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env(ENV_FILE)


# ----------------------------------------------------------------------
# 写密钥的安全闸（按顺序判断，任一条命中即允许）
#   ① 显式开启 ALLOW_REMOTE_KEY_WRITE=1（用户自担风险）；
#   ② 「本机部署」：BIND_ADDR 为 127.0.0.1 / localhost / 未设置 —— 此时端口只绑
#      本机，外部连不上。注意 Docker 下浏览器请求的源地址是 Docker 网关，不能只看
#      remote_addr，所以用「绑定地址」判断部署形态；
#   ③ 请求确实来自回环地址（裸机运行时的常见情况）。
#   否则拒绝：端口对外暴露（BIND_ADDR=0.0.0.0）时，不让局域网里任何人改密钥。
# ----------------------------------------------------------------------
def _runtime_bind():
    bind = (os.environ.get("BIND_ADDR") or "").strip()
    if not bind:
        bind = (providers.read_env_file(providers.ROOT_ENV_FILE).get("BIND_ADDR") or "").strip()
    return bind or "127.0.0.1"


def _deployment_is_local_only():
    return _runtime_bind() in ("127.0.0.1", "localhost", "::1")


def _is_loopback(addr):
    a = (addr or "").strip()
    return a in ("127.0.0.1", "::1", "localhost", "") or a.startswith("127.")


def _write_allowed():
    if providers.available_config()["allow_remote_key_write"]:
        return True, ""
    if _deployment_is_local_only():
        return True, ""
    if _is_loopback(request.remote_addr):
        return True, ""
    return False, ("出于安全考虑，密钥只能在服务器本机修改（当前部署监听 %s，对局域网开放）。"
                   "如确需远程修改，请在 .env 里设 ALLOW_REMOTE_KEY_WRITE=1 后重启。" % _runtime_bind())


# ----------------------------------------------------------------------
# 路由
# ----------------------------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    return jsonify(providers.health_payload())


@app.route("/api/config")
def api_config():
    data = providers.available_config()
    data["is_local"] = _is_loopback(request.remote_addr)
    data["bind_addr"] = _runtime_bind()
    data["can_write_keys"], data["write_hint"] = _write_allowed()
    data["settings_file"] = str(providers.SETTINGS_FILE)
    data["env_file"] = str(providers.ROOT_ENV_FILE)
    return jsonify(data)


@app.route("/api/settings", methods=["POST"])
def api_settings():
    data = request.get_json(silent=True) or {}
    patch = {}
    if data.get("provider"):
        patch["provider"] = data["provider"]
    if "model" in data:
        patch["model"] = data["model"]
    for k in ("endpoints", "models", "json_mode", "auth_style"):
        v = data.get(k)
        if isinstance(v, dict):
            patch[k] = v
    try:
        out = providers.save_settings(patch)
    except OSError as e:
        return jsonify({"error": "写入 settings.json 失败：%s" % e}), 500
    return jsonify({"ok": True, "settings": out, "config": providers.available_config()})


@app.route("/api/keys", methods=["POST"])
def api_keys():
    allowed, why = _write_allowed()
    if not allowed:
        return jsonify({"error": why, "can_write_keys": False}), 403
    data = request.get_json(silent=True) or {}
    updated, cleared = [], []
    for pid, val in (data.get("keys") or {}).items():
        if pid not in providers.PROVIDERS or val is None:
            continue
        try:
            providers.set_key(pid, val)
        except (OSError, providers.ProviderError) as e:
            return jsonify({"error": "写入 .env 失败：%s" % e}), 500
        (cleared if not (val or "").strip() else updated).append(pid)
    return jsonify({"ok": True, "updated": updated, "cleared": cleared,
                    "config": providers.available_config()})


@app.route("/api/test", methods=["POST"])
def api_test():
    data = request.get_json(silent=True) or {}
    return jsonify(providers.test(provider=data.get("provider"), model=data.get("model")))


# ----------------------------------------------------------------------
# 裸机「总入口」：/portal 直接把门户页面跑起来
#   Docker 部署时这些路由由 nginx 承担（见 docker/nginx/default.conf.template）；
#   裸机没有 nginx，所以在这里用 Flask 实现一份等价实现，页面文件完全共用
#   （docker/portal/index.html）—— 两种部署方式得到同一个总入口体验。
# ----------------------------------------------------------------------
_PORT_VARS = (
    ("listening",   "LISTENING_PORT",   5555),
    ("cloze",       "CLOZE_PORT",       5556),
    ("longread",    "LONGREAD_PORT",    5557),
    ("carefulread", "CAREFULREAD_PORT", 5558),
    ("translate",   "TRANSLATE_PORT",   5559),
    ("writing",     "WRITING_PORT",     5560),
    ("intensive",   "INTENSIVE_PORT",   5561),
    ("config",      "CONFIG_PORT",      5562),
)


def _ports():
    """端口来源与 Docker 一致：仓库根目录 .env 的 *_PORT（缺省用默认值）。"""
    env_file = providers.read_env_file(providers.ROOT_ENV_FILE)
    out = {}
    for key, var, default in _PORT_VARS:
        raw = (env_file.get(var) or os.environ.get(var) or "").strip()
        try:
            out[key] = int(raw) if raw else default
        except ValueError:
            out[key] = default
    return out


@app.route("/portal")
def portal():
    """门户导航面板（与 Docker 部署共用同一份页面文件）。"""
    page = providers.ROOT_DIR / "docker" / "portal" / "index.html"
    if not page.is_file():
        return "门户页面缺失：docker/portal/index.html", 404
    return send_file(str(page), mimetype="text/html")


@app.route("/go/<app_key>/")
def portal_go(app_key):
    """门户卡片的跳转：302 到对应应用的宿主机端口。"""
    port = _ports().get(app_key)
    if not port:
        abort(404)
    host = (request.host or "").split(":")[0] or "127.0.0.1"
    return redirect("http://%s:%d/" % (host, port), code=302)


@app.route("/health/<app_key>/")
def portal_health(app_key):
    """门户状态灯：服务端代探各应用的 /api/health（浏览器同源，无 CORS 问题）。"""
    port = _ports().get(app_key)
    if not port:
        return jsonify({"ok": False, "error": "未知应用：%s" % app_key}), 404
    try:
        r = requests.get("http://127.0.0.1:%d/api/health" % port, timeout=4)
        return r.text, r.status_code, {"Content-Type": "application/json"}
    except requests.RequestException as e:
        return jsonify({"ok": False, "error": "无法连接 %s：%s" % (app_key, e)}), 502


@app.route("/settings-api/config")
def settings_api_config():
    """门户的「全局默认引擎」开关读接口（Docker 下由 nginx 转发到这里）。"""
    return api_config()


@app.route("/settings-api/settings", methods=["POST"])
def settings_api_settings():
    """门户的「全局默认引擎」开关写接口。"""
    return api_settings()


@app.route("/offline/<path:name>")
def portal_offline(name):
    """门户里的「自由学习」离线工具（静态文件）。"""
    base = (providers.ROOT_DIR / "自由学习").resolve()
    target = (base / name).resolve()
    if not str(target).startswith(str(base)) or not target.is_file():
        abort(404)
    return send_file(str(target))


# ----------------------------------------------------------------------
# 启动
# ----------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", DEFAULT_PORT))
    host = os.environ.get("HOST", "127.0.0.1")
    if not os.environ.get("NO_OPEN_BROWSER"):
        threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:%d" % port)).start()
    print("=" * 56)
    print("  CET-6 引擎与密钥配置  ->  http://%s:%d" % (host, port))
    for pid, cfg in providers.PROVIDERS.items():
        print("  %-9s 密钥：%s" % (cfg["label"], "已配置 [OK]" if providers.resolve_key(pid) else "未配置 [X]"))
    print("  总入口：http://%s:%d/portal  （导航面板 + 状态灯 + 全局默认引擎开关）" % (host, port))
    print("  配置落盘：%s" % providers.SETTINGS_FILE)
    print("           %s" % providers.ROOT_ENV_FILE)
    print("  按 Ctrl+C 退出")
    print("=" * 56)
    app.run(host=host, port=port, debug=False)