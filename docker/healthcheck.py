#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""容器健康检查：请求应用自带的 /api/health 接口。

由 docker-compose 的 healthcheck 在容器内调用，只用标准库、无额外依赖。
退出码：0 = 健康，1 = 不健康（docker compose ps 会显示 healthy / unhealthy）。

注意：只判断「服务是否活着」，不判断 API Key 是否配置 ——
没配 Key 属于配置问题而非故障，导航面板会用黄灯单独提示。
"""

import json
import os
import sys
import urllib.request

port = os.environ.get("PORT", "8000")
url = "http://127.0.0.1:%s/api/health" % port

try:
    with urllib.request.urlopen(url, timeout=5) as resp:
        ok = resp.status == 200 and json.load(resp).get("ok") is True
except Exception as exc:  # noqa: BLE001 - 健康检查里任何异常都算不健康
    print("unhealthy: %s (%s)" % (url, exc))
    sys.exit(1)

sys.exit(0 if ok else 1)