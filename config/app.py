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

from flask import Flask, jsonify, render_template, request

import providers

BASE_DIR = Path(__file__).resolve().parent
ENV_FILE = BASE_DIR / ".env"
DEFAULT_PORT = 5562

app = Flask(__name__)


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
    print("  配置落盘：%s" % providers.SETTINGS_FILE)
    print("           %s" % providers.ROOT_ENV_FILE)
    print("  按 Ctrl+C 退出")
    print("=" * 56)
    app.run(host=host, port=port, debug=False)