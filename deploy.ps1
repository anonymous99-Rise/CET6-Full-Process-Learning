# ============================================================
#  CET-6 全流程学习 · Windows 一键部署脚本
#
#  用法（在仓库根目录执行）：
#     powershell -ExecutionPolicy Bypass -File deploy.ps1          # 启动 / 更新（首次自动建镜像）
#     powershell -ExecutionPolicy Bypass -File deploy.ps1 ps       # 查看容器状态
#     powershell -ExecutionPolicy Bypass -File deploy.ps1 logs     # 跟踪应用日志
#     powershell -ExecutionPolicy Bypass -File deploy.ps1 restart  # 重建容器（改了 .env / app.py 后用）
#     powershell -ExecutionPolicy Bypass -File deploy.ps1 down     # 停止并移除容器
#
#  脚本只做三件事：检查 Docker、准备 .env、调用 docker compose。
#  不会删除任何数据（my/ 与 .env 原样保留）。
#
#  注 1：本文件保存为 UTF-8 with BOM。Windows PowerShell 5.1 读取无 BOM 的
#        UTF-8 脚本时会按 ANSI 解码，中文会乱码甚至解析失败 —— 请勿去掉 BOM。
#  注 2：「改了根目录 .env 后要重建容器」：docker compose restart 只重启已存在的
#        容器，不会读入 .env 的新值；所以本脚本的 restart 动作执行的是
#        docker compose up -d --force-recreate（重建容器，配置与代码改动都生效）。
# ============================================================
param(
    [ValidateSet('up', 'down', 'restart', 'logs', 'ps')]
    [string]$Action = 'up'
)

# 用 Continue 而不是 Stop：脚本里有大量原生命令（docker），
# 在 Windows PowerShell 5.1 下 docker 写 stderr（进度条）会被当成错误，
# 配合 Stop 会中断部署。这里改为显式检查 $LASTEXITCODE。
$ErrorActionPreference = 'Continue'
if ($PSScriptRoot) { Set-Location -Path $PSScriptRoot }

function Write-Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK] $msg" -ForegroundColor Green }
function Write-Warn2($msg){ Write-Host "[!] $msg" -ForegroundColor Yellow }
function Write-Err2($msg) { Write-Host "[X] $msg" -ForegroundColor Red }

# 读取 .env 里的某个键（不存在或为空则返回默认值）
function Get-EnvValue([string]$Name, [string]$Default) {
    $file = Join-Path (Get-Location) '.env'
    if (Test-Path -LiteralPath $file) {
        foreach ($line in (Get-Content -LiteralPath $file -Encoding UTF8)) {
            if ($line -match ('^\s*' + [regex]::Escape($Name) + '\s*=\s*(.*)$')) {
                $v = $Matches[1].Trim()
                $v = $v.Trim('"').Trim("'")
                if ($v) { return $v }
            }
        }
    }
    return $Default
}

# 各应用目录里是否已经有配好的 Key（根目录 .env 之外的兜底路径）
function Test-AppEnvKey {
    $apps = @('听力专项训练', '选词填空', '长篇阅读', '仔细阅读', '翻译', '写作', '精读训练')
    foreach ($d in $apps) {
        $f = Join-Path $d '.env'
        if (Test-Path -LiteralPath $f) {
            foreach ($line in (Get-Content -LiteralPath $f -Encoding UTF8)) {
                if ($line -match '^\s*DEEPSEEK_API_KEY\s*=\s*(\S.*)$') {
                    $v = $Matches[1].Trim()
                    $v = $v.Trim('"').Trim("'")
                    if ($v -and ($v -notmatch '在此填入')) { return $true }
                }
            }
        }
    }
    return $false
}

# 执行 docker 命令并返回退出码。
# 关键：docker 的 stdout（构建进度等）必须用 Out-Host 直接送主机 ——
# 否则它会混进函数返回值，$code 变成数组，与 0 比较恒为真，
# 成功也会被判成失败（并让脚本以非 0 退出码结束）。
function Invoke-Docker([string[]]$DockerArgs) {
    & docker @DockerArgs | Out-Host
    return $LASTEXITCODE
}

# ---------- 1. 检查 Docker ----------
Write-Step '检查 Docker 环境'
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Err2 '未找到 docker 命令。请先安装并启动 Docker Desktop：https://www.docker.com/products/docker-desktop/'
    exit 1
}

$engineOk = $false
try { & docker info *> $null; $engineOk = ($LASTEXITCODE -eq 0) } catch { $engineOk = $false }
if (-not $engineOk) {
    Write-Err2 'Docker 引擎未运行 —— 请先启动 Docker Desktop（托盘图标变绿）后重试。'
    exit 1
}

$composeOk = $false
try { & docker compose version *> $null; $composeOk = ($LASTEXITCODE -eq 0) } catch { $composeOk = $false }
if (-not $composeOk) {
    Write-Err2 'docker compose 不可用 —— 需要 Docker Compose v2（随 Docker Desktop 安装）。'
    exit 1
}
Write-Ok 'Docker 与 Compose 均可用'

# ---------- 2. 准备 .env ----------
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
    Write-Ok '已从 .env.example 生成 .env'
    Write-Warn2 '请编辑 .env 填入 DEEPSEEK_API_KEY（不填也能启动，但无法出题 / 评分）'
}

$key = Get-EnvValue 'DEEPSEEK_API_KEY' ''
if (-not $key) {
    if (Test-AppEnvKey) {
        Write-Ok '根目录 .env 未填 Key，将使用各应用目录自己的 .env'
    }
    else {
        Write-Warn2 '还没有配置 DEEPSEEK_API_KEY —— 应用能启动，但出题 / 评分会提示「Key 未配置」'
        Write-Warn2 '填好后执行：deploy.ps1 restart（重建容器，新配置才会生效）'
    }
}
else {
    Write-Ok 'DEEPSEEK_API_KEY 已配置'
}

$bindAddr   = Get-EnvValue 'BIND_ADDR' '127.0.0.1'
$portalPort = Get-EnvValue 'PORTAL_PORT' '8080'

# ---------- 3. 调用 docker compose ----------
switch ($Action) {
    'up' {
        Write-Step '构建镜像并启动容器（首次约 1 分钟）'
        $code = Invoke-Docker @('compose', 'up', '-d', '--build')
        if ($code -ne 0) {
            Write-Err2 'docker compose up 失败，请查看上面的报错'
            exit $code
        }

        Write-Step '当前容器状态'
        Invoke-Docker @('compose', 'ps') | Out-Null

        $p1 = Get-EnvValue 'LISTENING_PORT' '5555'
        $p2 = Get-EnvValue 'CLOZE_PORT' '5556'
        $p3 = Get-EnvValue 'LONGREAD_PORT' '5557'
        $p4 = Get-EnvValue 'CAREFULREAD_PORT' '5558'
        $p5 = Get-EnvValue 'TRANSLATE_PORT' '5559'
        $p6 = Get-EnvValue 'WRITING_PORT' '5560'
        $p7 = Get-EnvValue 'INTENSIVE_PORT' '5561'

        Write-Host ''
        Write-Ok '部署完成 —— 打开总入口（一个页面直达七个应用）：'
        Write-Host ('     http://{0}:{1}' -f $bindAddr, $portalPort) -ForegroundColor White
        Write-Host ('   应用直连端口：{0} / {1} / {2} / {3} / {4} / {5} / {6}' -f $p1, $p2, $p3, $p4, $p5, $p6, $p7)
        if ($bindAddr -eq '127.0.0.1') {
            Write-Host '   仅本机可访问；要让手机 / 平板也能用，把 .env 的 BIND_ADDR 改成 0.0.0.0 后 restart（重建容器）' -ForegroundColor DarkGray
        }
    }
    'down' {
        Write-Step '停止并移除容器（数据保留）'
        $code = Invoke-Docker @('compose', 'down')
        if ($code -ne 0) { Write-Err2 'docker compose down 失败'; exit $code }
    }
    'restart' {
        Write-Step '重建容器（.env 与代码改动都会生效）'
        $code = Invoke-Docker @('compose', 'up', '-d', '--force-recreate')
        if ($code -ne 0) { Write-Err2 'docker compose up --force-recreate 失败'; exit $code }
        Write-Ok '已重建；打开总入口查看状态'
    }
    'logs' {
        Write-Step '跟踪日志（Ctrl+C 退出）'
        Invoke-Docker @('compose', 'logs', '-f', '--tail', '50') | Out-Null
    }
    'ps' {
        Write-Step '容器状态'
        Invoke-Docker @('compose', 'ps') | Out-Null
    }
}