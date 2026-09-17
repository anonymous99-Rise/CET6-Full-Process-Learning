#!/bin/sh
# ============================================================
#  训练应用容器入口脚本（7 个应用共用）
#
#  工作目录由 compose 的 working_dir 指定（如 /apps/听力专项训练），
#  脚本做两件事：
#
#  1) 清掉「空值」环境变量。
#     app.py 用 os.environ.setdefault 读取同目录下的 .env；如果容器里已经
#     存在同名但为空的值（例如 DEEPSEEK_API_KEY=""），setdefault 不会覆盖
#     它，于是 .env 里的真实配置被顶掉，页面就显示「Key 未配置」。
#     先把空值 unset 掉，配置加载才符合预期：
#       ① 仓库根目录 .env → compose 注入容器环境变量（优先级最高，始终有效）
#       ② 各应用目录 .env → app.py 启动时读取（DEEPSEEK_API_KEY / CET_VOCAB_CHECK 有效）
#     注：DEEPSEEK_MODEL 由各 app.py 在 load_env() 之后读取（v3.4.0 起），
#     所以根目录 .env 与本应用目录 .env 两条路径都能生效。
#
#  2) 以 HOST=0.0.0.0 启动（应用默认只绑 127.0.0.1，那样容器外访问不到）。
#
#  想换生产级 WSGI 服务器？改最后一行即可，各应用都是标准 Flask 对象
#  （app:app）：exec gunicorn -b 0.0.0.0:$PORT app:app
# ============================================================
set -e

[ -n "${DEEPSEEK_API_KEY:-}" ] || unset DEEPSEEK_API_KEY
[ -n "${DEEPSEEK_MODEL:-}" ]   || unset DEEPSEEK_MODEL
[ -n "${CET_VOCAB_CHECK:-}" ]  || unset CET_VOCAB_CHECK

PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
export PORT HOST

echo "[cet6] workdir=$(pwd)  port=${PORT}  bind=${HOST}"

# app.py 自带启动横幅（含 API Key 状态），docker compose logs 里能直接看到
exec python app.py