# ============================================================
#  CET-6 全流程学习 · 训练应用镜像（7 个 Flask 应用共用一份）
#
#  设计：镜像只负责提供 Python 运行环境 + 两个容器脚本；
#        应用代码由 docker-compose 以卷的方式挂载到 /apps，因此：
#          · 改 prompts/*.txt   → 即时生效（应用每次请求都重读）
#          · 改 .env            → docker compose up -d（重建容器才会读入新值）
#          · 改 app.py          → docker compose restart（重启进程重载代码）
#          · 生成的练习          → 直接落到宿主机的 my/ 目录
#
#  构建：docker compose up -d --build
# ============================================================
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOST=0.0.0.0 \
    NO_OPEN_BROWSER=1 \
    TZ=Asia/Shanghai

WORKDIR /apps

# 依赖清单单独一份 —— 只改应用代码时不会重复安装依赖（吃层缓存）
COPY docker/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# 容器入口脚本 + 健康检查脚本
COPY docker/start.sh /opt/cet6/start.sh
COPY docker/healthcheck.py /opt/cet6/healthcheck.py
RUN chmod +x /opt/cet6/start.sh

# 应用代码（.dockerignore 已排除 .env / my/ / 材料/ / .git，密钥不会烤进镜像）
COPY . /apps/

# 七个应用各自的默认端口
EXPOSE 5555 5556 5557 5558 5559 5560 5561

ENTRYPOINT ["/opt/cet6/start.sh"]