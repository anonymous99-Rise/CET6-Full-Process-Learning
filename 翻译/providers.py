# -*- coding: utf-8 -*-
"""AI 引擎抽象层：DeepSeek / MiniMax 可选，外加「密钥与参数可视化配置」的后端。

本文件在 7 个训练应用目录与 config 配置应用目录里各存一份（沿用「每个应用自包含」
的既有约定，与 vocab.py 同样的做法），内容完全一致 —— 改动时请一起改。

职责：
  1. 引擎预设：接口地址 / 认证方式 / 可用模型 / 密钥环境变量名 / 是否支持 JSON 模式；
  2. 「引擎 + 模型」解析：支持 "provider:model" 标签、配置文件默认值、环境变量兜底；
  3. 密钥解析：根目录 .env → 应用目录 .env → 进程环境变量（取第一个非空）；
  4. 调用收口：各家端点/认证/JSON 模式差异都在这一个函数里处理，错误统一成 ProviderError；
  5. 配置读写：根目录 settings.json（非机密）+ 根目录 .env（密钥，只写不回显）。

优先级：
  密钥     ：根 .env  >  应用目录 .env  >  进程环境变量（容器 / 系统注入）
  引擎/模型：单次请求参数  >  根 settings.json  >  环境变量  >  预设默认值

关于认证方式的实测记录（2026-06）：
  用无效密钥请求 https://api.minimaxi.com/v1/text/chatcompletion_v2 时，MiniMax 返回
  base_resp.status_msg = "login fail: Please carry the API secret key in the 'Authorization' field"，
  说明该端点与路径存在、可直连；但仅凭该提示无法确定它要 `Authorization: Bearer <key>`
  还是 `Authorization: <key>`。因此 chat() 会在「认证类失败」时自动换另一种头重试一次，
  配置页也能显式指定；两家的默认预设都是最主流的 Bearer。
"""

import io
import json
import os
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent      # 本文件所在的应用目录
ROOT_DIR = BASE_DIR.parent                      # 仓库根目录（.env / settings.json 在这）
ROOT_ENV_FILE = ROOT_DIR / ".env"
APP_ENV_FILE = BASE_DIR / ".env"
SETTINGS_FILE = ROOT_DIR / "settings.json"

DEFAULT_PROVIDER = "deepseek"

PROVIDERS = {
    "deepseek": {
        "label": "DeepSeek",
        "endpoint": "https://api.deepseek.com/v1/chat/completions",
        "auth_style": "bearer",
        "key_env": "DEEPSEEK_API_KEY",
        # 官方公开 API 的模型名。实测（2026-06，api.deepseek.com）：deepseek-chat 长生成正常；
        # deepseek-reasoner 与仓库旧预设 deepseek-v4-flash / -pro 会「只返回思考、正文为空」。
        # 若你的账号或网关暴露别的模型名，直接在配置页改「默认模型」即可。
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "default_model": "deepseek-chat",
        "json_mode": True,   # 实测 deepseek-chat + JSON 模式可正常生成
        "hint": "中文命题/评分稳定；chat = 通用（推荐），reasoner = 推理型（更慢、思考会占输出预算）",
    },
    "minimax": {
        "label": "MiniMax",
        "endpoint": "https://api.minimaxi.com/v1/text/chatcompletion_v2",
        "auth_style": "bearer",
        "key_env": "MINIMAX_API_KEY",
        "models": ["MiniMax-M2.7", "MiniMax-M3"],
        "default_model": "MiniMax-M2.7",
        "json_mode": False,
        "hint": "单价更低，适合长文批量生成；端点已实测可达，JSON 模式默认关闭（需用真实密钥确认）",
    },
}

AUTH_STYLES = ["bearer", "raw"]


class ProviderError(Exception):
    """统一的调用错误：message + HTTP 语义状态码（应用会把它转成 ApiError）。"""

    def __init__(self, message, status=500):
        super().__init__(message)
        self.message = message
        self.status = status


# ----------------------------------------------------------------------
# .env 读写（根 .env 是密钥与全局开关的唯一控制面）
# ----------------------------------------------------------------------
def read_env_file(path):
    """读取 .env，返回 {KEY: value}（去掉引号与首尾空白；不展开变量）。"""
    data = {}
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                data[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        return {}
    return data


def _atomic_write(path, text):
    tmp = str(path) + ".tmp"
    with io.open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, str(path))


def set_env_value(path, key, value):
    """就地更新（或追加）一行 KEY=value，其余行原样保留；写失败抛 OSError。"""
    lines = []
    try:
        with io.open(path, "r", encoding="utf-8") as f:
            lines = f.read().split("\n")
    except OSError:
        lines = []
    hit = False
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("#") or "=" not in s:
            continue
        if s.split("=", 1)[0].strip() == key:
            lines[i] = "%s=%s" % (key, value)
            hit = True
    if not hit:
        if lines and lines[-1].strip() != "":
            lines.append("")
        lines.append("%s=%s" % (key, value))
    _atomic_write(path, "\n".join(lines))


# ----------------------------------------------------------------------
# 密钥解析：根 .env → 应用目录 .env → 进程环境变量
# ----------------------------------------------------------------------
def resolve_key(provider):
    cfg = PROVIDERS.get(provider) or {}
    env_name = cfg.get("key_env", "")
    for path in (ROOT_ENV_FILE, APP_ENV_FILE):
        v = (read_env_file(path).get(env_name) or "").strip()
        if v:
            return v
    return (os.environ.get(env_name) or "").strip()


def key_source(provider):
    """返回密钥来源描述（用于界面显示，绝不返回密钥本身）。"""
    cfg = PROVIDERS.get(provider) or {}
    env_name = cfg.get("key_env", "")
    for path, label in ((ROOT_ENV_FILE, "根目录 .env"), (APP_ENV_FILE, "应用目录 .env")):
        if (read_env_file(path).get(env_name) or "").strip():
            return label
    if (os.environ.get(env_name) or "").strip():
        return "容器/系统环境变量"
    return ""


def set_key(provider, value):
    """把密钥写入根目录 .env（空值等于清空该行）。只写不回显。

    注意：清空根 .env 后，若应用目录 .env 里仍有同名密钥，会继续沿用（界面会显示来源）。
    """
    cfg = PROVIDERS.get(provider)
    if not cfg:
        raise ProviderError("未知引擎：%s" % provider, 400)
    v = (value or "").strip().strip('"').strip("'").strip()
    # 常见粘贴事故：把「Bearer 」一起粘进密钥框 → 发出去会变成 "Bearer Bearer sk-..."
    if v.lower().startswith("bearer "):
        v = v[7:].strip()
    set_env_value(ROOT_ENV_FILE, cfg["key_env"], v)


# ----------------------------------------------------------------------
# 配置（settings.json：非机密，可安全回显给前端）
# ----------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "provider": DEFAULT_PROVIDER,   # 全局默认引擎
    "model": "",                    # 留空 = 用该引擎的默认模型
    "endpoints": {},                # {provider: 自定义接口地址}
    "models": {},                   # {provider: 自定义默认模型}
    "json_mode": {},                # {provider: bool} 是否带 response_format
    "auth_style": {},               # {provider: "bearer" | "raw"}
}

_MERGE_KEYS = ("endpoints", "models", "json_mode", "auth_style")


def load_settings():
    data = dict(DEFAULT_SETTINGS)
    try:
        with io.open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict):
            for k in DEFAULT_SETTINGS:
                if k in raw:
                    data[k] = raw[k]
    except (OSError, ValueError):
        pass
    if data.get("provider") not in PROVIDERS:
        data["provider"] = DEFAULT_PROVIDER
    for k in _MERGE_KEYS:
        if not isinstance(data.get(k), dict):
            data[k] = {}
    return data


def save_settings(patch):
    data = load_settings()
    for k, v in (patch or {}).items():
        if k in ("provider", "model"):
            data[k] = v
        elif k in _MERGE_KEYS and isinstance(v, dict):
            merged = dict(data.get(k) or {})
            for kk, vv in v.items():
                if kk in PROVIDERS:
                    merged[kk] = vv
            data[k] = merged
    if data.get("provider") not in PROVIDERS:
        data["provider"] = DEFAULT_PROVIDER
    _atomic_write(SETTINGS_FILE, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


# ----------------------------------------------------------------------
# 解析：引擎 / 模型 / 接口地址 / 认证方式
# ----------------------------------------------------------------------
def _env_name_for(provider, suffix):
    cfg = PROVIDERS.get(provider) or {}
    return cfg.get("key_env", "").replace("_API_KEY", "_" + suffix)


def get_endpoint(provider, settings=None):
    st = settings or load_settings()
    cfg = PROVIDERS.get(provider) or {}
    return (st.get("endpoints", {}).get(provider)
            or os.environ.get(_env_name_for(provider, "ENDPOINT"))
            or cfg.get("endpoint", "")).strip()


def get_auth_style(provider, settings=None):
    st = settings or load_settings()
    cfg = PROVIDERS.get(provider) or {}
    style = (st.get("auth_style", {}).get(provider)
             or os.environ.get(_env_name_for(provider, "AUTH_STYLE"))
             or cfg.get("auth_style", "bearer")).strip().lower()
    return style if style in AUTH_STYLES else "bearer"


def get_models(provider, settings=None):
    st = settings or load_settings()
    preset = list((PROVIDERS.get(provider) or {}).get("models", []))
    custom = st.get("models", {}).get(provider)
    if custom and custom not in preset:
        preset.append(custom)
    return preset


def parse_model_tag(tag):
    """把 "provider:model" 拆成 (provider, model)；无前缀则 provider=None（用默认）。"""
    t = (tag or "").strip()
    if not t:
        return None, ""
    if ":" in t:
        p, _, m = t.partition(":")
        p = p.strip().lower()
        if p in PROVIDERS:
            return p, m.strip()
    return None, t


def active_provider(override=None, settings=None):
    if override and override in PROVIDERS:
        return override
    st = settings or load_settings()
    env_p = (os.environ.get("AI_PROVIDER") or "").strip().lower()
    if st.get("provider") in PROVIDERS:
        return st["provider"]
    if env_p in PROVIDERS:
        return env_p
    return DEFAULT_PROVIDER


def active_model(provider=None, override=None, settings=None):
    st = settings or load_settings()
    prov = provider or active_provider(settings=st)
    return (override or st.get("models", {}).get(prov) or st.get("model")
            or os.environ.get("AI_MODEL")
            or (PROVIDERS.get(prov) or {}).get("default_model", "")).strip()


def active_model_tag(model=None, settings=None):
    """当前生效的 "provider:model"（供界面 / 健康检查显示）。"""
    st = settings or load_settings()
    p_override, m = parse_model_tag(model)
    prov = active_provider(p_override, st)
    return "%s:%s" % (prov, active_model(prov, m or None, st))


def available_config():
    """给配置页用的完整预设（不含任何密钥）。"""
    st = load_settings()
    out = []
    for pid, cfg in PROVIDERS.items():
        out.append({
            "id": pid,
            "label": cfg["label"],
            "endpoint": get_endpoint(pid, st),
            "endpoint_preset": cfg["endpoint"],
            "auth_style": get_auth_style(pid, st),
            "models": get_models(pid, st),
            "model": active_model(pid, None, st),
            "key_env": cfg["key_env"],
            "has_key": bool(resolve_key(pid)),
            "key_source": key_source(pid),
            "key_hint": mask_key(resolve_key(pid)),
            "json_mode": bool(st.get("json_mode", {}).get(pid, cfg["json_mode"])),
            "hint": cfg["hint"],
        })
    return {
        "settings": st,
        "active": {"provider": active_provider(settings=st), "model": active_model(settings=st),
                   "tag": active_model_tag(settings=st)},
        "providers": out,
        "allow_remote_key_write": os.environ.get("ALLOW_REMOTE_KEY_WRITE", "") not in ("", "0", "false", "False"),
    }


# ----------------------------------------------------------------------
# 调用
# ----------------------------------------------------------------------
def mask_key(key):
    """把密钥转成可安全展示的形态：前 3 位 + 后 4 位 + 长度（绝不回传原文）。"""
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "（长度 %d）" % len(k)
    return "%s…%s（长度 %d）" % (k[:3], k[-4:], len(k))


def _hint_from_message(msg):
    """根据上游报错给出「下一步怎么办」，避免用户面对 401 无从下手。"""
    low = (msg or "").lower()
    if "auth header format" in low or "carry the api secret key" in low or "authorization" in low:
        return ("上游要求 Authorization: Bearer sk-... 形式 —— 请到配置页把该引擎的「认证方式」"
                "改为 Bearer（主流）；若密钥是从别处复制来的，确认没有把「Bearer 」一起粘进密钥框。")
    if "invalid" in low and ("api key" in low or "key" in low or "token" in low):
        return "密钥本身无效：请确认复制完整、没有多余空格或换行，或在控制台重新生成一个。"
    if "login fail" in low:
        return "上游拒绝鉴权：确认密钥与端点匹配（国内站 / 国际站端点不同），并点「测试连通性」看原始报错。"
    if "response_format" in low or ("json" in low and "support" in low):
        return "上游不支持 JSON 模式：在配置页取消勾选「要求 JSON 模式」即可（后端也会自动去掉重试）。"
    return ""


def _classify(data, status_code):
    """把返回体判成 ("ok", content) / ("auth", 原因) / ("error", 原因)。"""
    if not isinstance(data, dict):
        return "error", "返回不是 JSON 对象"
    base = data.get("base_resp")
    if isinstance(base, dict) and base.get("status_code") not in (0, None):
        msg = str(base.get("status_msg") or base.get("status_code"))
        low = msg.lower()
        if "login fail" in low or "authorization" in low or "api key" in low or "api_key" in low:
            return "auth", msg
        return "error", "接口返回错误：%s" % msg
    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        ch = choices[0] or {}
        msg = ch.get("message") or {}
        content = (msg.get("content") or "").strip()
        reasoning = (msg.get("reasoning_content") or "").strip()
        finish = str(ch.get("finish_reason") or "").strip()
        usage = json.dumps(data.get("usage") or {}, ensure_ascii=False)
        if content:
            return "ok", content
        # 注意：绝不能用 reasoning_content（思考过程）当答案 —— 它不是最终回答，
        # 拿去解析 JSON 只会得到「Expecting value: line 1 column 1」这种误导性报错。
        if finish == "length":
            return "truncated", "输出被 max_tokens 截断（finish_reason=length，用量 %s）" % usage
        if reasoning:
            return "reasoning_only", (
                "模型只返回了思考内容、正文为空（reasoning_content %d 字，finish_reason=%s，用量 %s）"
                % (len(reasoning), finish or "?", usage))
        return "error", "choices[0].message.content 为空（finish_reason=%s，用量 %s）" % (finish or "?", usage)
    err = data.get("error")
    if err:
        msg = err.get("message") if isinstance(err, dict) else str(err)
        if status_code in (401, 403):
            return "auth", str(msg)[:300]
        return "error", "接口返回错误：%s" % str(msg)[:300]
    return "error", "返回结构异常：%s" % json.dumps(data, ensure_ascii=False)[:300]


def _default_max_tokens():
    """输出预算：推理型模型的「思考」也会占额度，所以默认给宽一点，可用 AI_MAX_TOKENS 覆盖。"""
    try:
        v = int(os.environ.get("AI_MAX_TOKENS") or 0)
    except ValueError:
        v = 0
    return v if v > 0 else 8192


def chat(messages, model=None, temperature=0.7, max_tokens=None, timeout=120):
    """发一次对话请求，返回文本内容。失败抛 ProviderError。

    重试策略（最多 3 次请求，都在同一次调用内完成，对上层透明）：
      1) 首选认证方式（默认 Bearer）+ JSON 模式（若该引擎开启）；
      2) 认证类失败 → 换另一种认证头（原始密钥）再试；
      3) JSON 模式导致 400/422 → 去掉 response_format 再试。
    """
    import requests  # 延迟导入：只读配置（如配置页首屏）时不需要网络库

    st = load_settings()
    max_tokens = max_tokens or _default_max_tokens()
    prov, m = parse_model_tag(model)
    prov = active_provider(prov, st)
    model_name = active_model(prov, m or None, st)
    cfg = PROVIDERS.get(prov) or {}
    label = cfg.get("label", prov)
    endpoint = get_endpoint(prov, st)
    key = resolve_key(prov)
    if not key:
        raise ProviderError("服务端未配置 %s 密钥（%s）。请到「配置」页填写，或写入根目录 .env。"
                            % (label, cfg.get("key_env", "")), 503)
    if not endpoint:
        raise ProviderError("%s 的接口地址为空，请到「配置」页填写。" % label, 500)

    use_json = bool(st.get("json_mode", {}).get(prov, cfg.get("json_mode", False)))
    style = get_auth_style(prov, st)

    def headers_for(s):
        auth = key if s == "raw" else ("Bearer " + key)
        return {"Authorization": auth, "Content-Type": "application/json"}

    def attempt(s, with_json, budget):
        payload = {"model": model_name, "messages": messages, "temperature": temperature,
                   "max_tokens": budget, "stream": False}
        if with_json:
            payload["response_format"] = {"type": "json_object"}
        try:
            resp = requests.post(endpoint, headers=headers_for(s), json=payload, timeout=timeout)
        except requests.RequestException as e:
            raise ProviderError("无法连接 %s（网络问题）：%s" % (label, e), 502)
        if resp.status_code == 429:
            raise ProviderError("%s 请求过于频繁或额度不足 (429)，请稍后重试或换引擎。" % label, 429)
        if resp.status_code >= 500:
            raise ProviderError("%s 服务端错误 (%s)：%s" % (label, resp.status_code, resp.text[:200]), 502)
        try:
            data = resp.json()
        except ValueError:
            body = (resp.text or "").strip()[:200]
            low = body.lower()
            # 有的上游对鉴权失败返回「纯文本 401/403」，例如 DeepSeek 的
            # "Authentication Fails (auth header format should be Bearer sk-...)"。
            # 这类响应必须归为「认证失败」，否则不会触发换一种认证头重试。
            if resp.status_code in (401, 403) or "bearer" in low or "auth" in low or "api key" in low:
                return "auth", "HTTP %s：%s" % (resp.status_code, body)
            raise ProviderError("%s 返回非 JSON (%s)：%s" % (label, resp.status_code, body), 502)
        kind, payload_or_msg = _classify(data, resp.status_code)
        if kind == "ok":
            return "ok", payload_or_msg
        if kind == "error" and resp.status_code in (400, 422):
            return "json", payload_or_msg      # response_format 不被支持 → 去掉它重试
        # auth / truncated / reasoning_only / error 一律原样上抛给重试循环判断
        return kind, payload_or_msg

    plan = [(style, use_json)]
    other = "raw" if style != "raw" else "bearer"
    plan.append((other, use_json))
    if use_json:
        plan.append((style, False))
        plan.append((other, False))

    last = ""
    seen = set()
    tried = []
    auth_failed = False
    budget = max_tokens
    bumped = False          # 是否已经因「输出被截断 / 只有思考」加大过预算
    idx = 0
    while idx < len(plan):
        s, wj = plan[idx]
        idx += 1
        if (s, wj, budget) in seen:
            continue
        seen.add((s, wj, budget))
        tried.append(s)
        kind, msg = attempt(s, wj, budget)
        if kind == "ok":
            return msg
        last = msg
        if kind in ("truncated", "reasoning_only") and not bumped and budget < 32000:
            # 常见于推理型模型：思考把预算吃光 / 输出被截断 → 翻倍预算把整套计划重跑一遍
            bumped = True
            budget = min(budget * 2, 32000)
            idx = 0
            seen = set()
            continue
        if kind == "auth":
            auth_failed = True
            continue          # 认证失败 → 换另一种认证头 / 去掉 JSON 模式再试
        if kind == "error":
            raise ProviderError("%s 调用失败：%s" % (label, msg), 502)

    styles = "、".join(dict.fromkeys(tried))
    detail = "%s 调用失败（已尝试认证方式：%s）：%s" % (label, styles, last)
    hint = _hint_from_message(last)
    if hint:
        detail += "\n提示：" + hint
    raise ProviderError(detail, 401 if auth_failed else 502)


def test(provider=None, model=None, timeout=30):
    """配置页的「测试连通性」：发一个最小请求，返回结构化结果（绝不包含密钥）。"""
    st = load_settings()
    prov = active_provider(provider, st)
    model_name = active_model(prov, model or None, st)
    started = time.time()
    out = {"provider": prov, "model": model_name, "endpoint": get_endpoint(prov, st),
           "auth_style": get_auth_style(prov, st),
           "has_key": bool(resolve_key(prov)), "key_source": key_source(prov),
           "key_hint": mask_key(resolve_key(prov)), "ok": False}
    if not out["has_key"]:
        out["error"] = "未配置密钥（%s）" % (PROVIDERS.get(prov) or {}).get("key_env", "")
        out["elapsed_ms"] = int((time.time() - started) * 1000)
        return out
    try:
        content = chat([{"role": "user", "content": "只回复两个字：收到"}],
                       model="%s:%s" % (prov, model_name), temperature=0, max_tokens=512, timeout=timeout)
        out["ok"] = True
        out["reply"] = content[:80]
    except ProviderError as e:
        out["error"] = e.message
        out["status"] = e.status
    except Exception as e:  # noqa: BLE001 - 测试接口不该把异常抛给页面
        out["error"] = "未预期错误：%s" % e
    out["elapsed_ms"] = int((time.time() - started) * 1000)
    return out


def health_payload():
    """/api/health 的返回体（保留 ok/hasKey/model 三个旧字段，兼容总入口面板）。"""
    st = load_settings()
    prov = active_provider(settings=st)
    return {
        "ok": True,
        "provider": prov,
        "model": active_model_tag(settings=st),
        "hasKey": bool(resolve_key(prov)),
        "providers": {
            pid: {"label": cfg["label"], "hasKey": bool(resolve_key(pid)),
                  "key_source": key_source(pid), "model": active_model(pid, None, st)}
            for pid, cfg in PROVIDERS.items()
        },
    }


# ---------------- 自检 ----------------
if __name__ == "__main__":
    print("可用引擎：", list(PROVIDERS))
    print("当前默认：", active_model_tag())
    for pid in PROVIDERS:
        print("  %-9s 密钥=%-5s 来源=%-12s 认证=%-6s 地址=%s"
              % (pid, bool(resolve_key(pid)), key_source(pid) or "-", get_auth_style(pid), get_endpoint(pid)))
    print("标签解析自检：", parse_model_tag("minimax:MiniMax-M2.7"), parse_model_tag("deepseek-v4-pro"), parse_model_tag(""))