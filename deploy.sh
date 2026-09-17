#!/usr/bin/env bash
# ============================================================
#  CET-6 全流程学习 · macOS / Linux 一键部署脚本
#
#  用法（在仓库根目录执行）：
#     ./deploy.sh            # 启动 / 更新（首次自动建镜像）
#     ./deploy.sh ps         # 查看容器状态
#     ./deploy.sh logs       # 跟踪应用日志
#     ./deploy.sh restart    # 重建容器（改了 .env / app.py 之后用它）
#     ./deploy.sh down       # 停止并移除容器
#
#  脚本只做三件事：检查 Docker、准备 .env、调用 docker compose。
#  不会删除任何数据（my/ 与 .env 原样保留）。
# ============================================================
set -e

ACTION="${1:-up}"
case "$ACTION" in
  up|down|restart|logs|ps) ;;
  *) echo "用法：$0 [up|down|restart|logs|ps]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")"

step() { printf '\033[36m==> %s\033[0m\n' "$1"; }
ok()   { printf '\033[32m[OK] %s\033[0m\n' "$1"; }
warn() { printf '\033[33m[!] %s\033[0m\n' "$1"; }
err()  { printf '\033[31m[X] %s\033[0m\n' "$1"; }

# 读取 .env 里的某个键（不存在或为空则返回默认值）
env_value() {
  local name="$1" def="$2" line val
  if [ -f .env ]; then
    line=$(grep -E "^[[:space:]]*${name}[[:space:]]*=" .env | tail -n 1 || true)
    if [ -n "$line" ]; then
      val="${line#*=}"
      val="$(printf '%s' "$val" | tr -d '\r' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//")"
      [ -n "$val" ] && { printf '%s' "$val"; return; }
    fi
  fi
  printf '%s' "$def"
}

# 各应用目录里是否已有配好的 Key
app_env_key() {
  local d f
  for d in 听力专项训练 选词填空 长篇阅读 仔细阅读 翻译 写作 精读训练; do
    f="$d/.env"
    [ -f "$f" ] || continue
    if grep -E '^[[:space:]]*DEEPSEEK_API_KEY[[:space:]]*=[[:space:]]*[^[:space:]]' "$f" | grep -qv '在此填入'; then
      return 0
    fi
  done
  return 1
}

# ---------- 1. 检查 Docker ----------
step '检查 Docker 环境'
if ! command -v docker >/dev/null 2>&1; then
  err '未找到 docker 命令。请先安装 Docker Engine / Docker Desktop。'
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  err 'Docker 引擎未运行（或当前用户无权限访问 docker，可尝试 sudo）。'
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  err 'docker compose 不可用 —— 需要 Docker Compose v2。'
  exit 1
fi
ok 'Docker 与 Compose 均可用'

# ---------- 2. 准备 .env ----------
if [ ! -f .env ]; then
  cp .env.example .env
  ok '已从 .env.example 生成 .env'
  warn '请编辑 .env 填入 DEEPSEEK_API_KEY（不填也能启动，但无法出题/评分）'
fi

if [ -n "$(env_value DEEPSEEK_API_KEY '')" ]; then
  ok 'DEEPSEEK_API_KEY 已配置'
elif app_env_key; then
  ok '根目录 .env 未填 Key，将使用各应用目录自己的 .env'
else
  warn '还没有配置 DEEPSEEK_API_KEY —— 应用能启动，但出题/评分会提示「Key 未配置」'
  warn '填好后执行：./deploy.sh restart'
fi

BIND_ADDR="$(env_value BIND_ADDR '127.0.0.1')"
PORTAL_PORT="$(env_value PORTAL_PORT '8080')"

# ---------- 3. 调用 docker compose ----------
case "$ACTION" in
  up)
    step '构建镜像并启动容器（首次约 1 分钟）'
    docker compose up -d --build
    step '当前容器状态'
    docker compose ps
    echo
    ok '部署完成 —— 打开总入口（一个页面直达七个应用）：'
    echo "     http://${BIND_ADDR}:${PORTAL_PORT}"
    echo "   各应用直连端口：$(env_value LISTENING_PORT 5555) / $(env_value CLOZE_PORT 5556) / $(env_value LONGREAD_PORT 5557) / $(env_value CAREFULREAD_PORT 5558) / $(env_value TRANSLATE_PORT 5559) / $(env_value WRITING_PORT 5560) / $(env_value INTENSIVE_PORT 5561)"
    if [ "$BIND_ADDR" = '127.0.0.1' ]; then
      echo '   仅本机可访问；要让手机/平板也能用，把 .env 的 BIND_ADDR 改成 0.0.0.0 后 restart（重建容器）'
    fi
    ;;
  down)    step '停止并移除容器（数据保留）'; docker compose down ;;
  restart) step '重建容器（.env 与代码改动都会生效）'; docker compose up -d --force-recreate ;;
  logs)    step '跟踪日志（Ctrl+C 退出）';      docker compose logs -f --tail 50 ;;
  ps)      step '容器状态';                    docker compose ps ;;
esac