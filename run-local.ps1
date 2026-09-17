# ============================================================
#  CET-6 全流程学习 · Windows 裸机一键运行（不需要 Docker）
#
#  用法（在仓库根目录执行）：
#     powershell -ExecutionPolicy Bypass -File run-local.ps1           # 建环境 + 启动 8 个服务
#     powershell -ExecutionPolicy Bypass -File run-local.ps1 status    # 查看端口/进程/健康状态
#     powershell -ExecutionPolicy Bypass -File run-local.ps1 logs      # 看各服务日志尾部
#     powershell -ExecutionPolicy Bypass -File run-local.ps1 down      # 停止（只停本脚本启动的进程）
#     powershell -ExecutionPolicy Bypass -File run-local.ps1 install-autostart    # 注册开机自启（计划任务）
#     powershell -ExecutionPolicy Bypass -File run-local.ps1 uninstall-autostart  # 移除开机自启
#
#  与 Docker 版共用同一份配置：仓库根目录 .env（密钥/端口）、settings.json（默认引擎）、my/（练习）
#  两种部署方式二选一即可（同时开会抢端口）。
#  总入口：http://127.0.0.1:5562/portal
#
#  国内网络若 pip 慢，可先设置： $env:HTTPS_PROXY='http://127.0.0.1:7897'
# ============================================================
param(
    [ValidateSet('up', 'down', 'status', 'logs', 'install-autostart', 'uninstall-autostart')]
    [string]$Action = 'up'
)

$ErrorActionPreference = 'Continue'
if ($PSScriptRoot) { Set-Location -Path $PSScriptRoot }

$root      = (Get-Location).Path
$venv      = Join-Path $root '.venv'
$py        = Join-Path $venv 'Scripts\python.exe'
$logs      = Join-Path $root 'logs'
$pidFile   = Join-Path $logs 'baremetal-pids.json'
$portalUrl = 'http://127.0.0.1:5562/portal'
$taskName  = 'CET6-Full-Process-Learning'

function Write-Step($m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok($m)   { Write-Host "[OK] $m" -ForegroundColor Green }
function Write-Warn2($m){ Write-Host "[!] $m" -ForegroundColor Yellow }
function Write-Err2($m) { Write-Host "[X] $m" -ForegroundColor Red }

# 端口以仓库根目录 .env 为准（与 Docker 部署同一套变量），缺省用默认值
function Get-EnvPort([string]$Var, [int]$Default) {
    $f = Join-Path $root '.env'
    if (Test-Path -LiteralPath $f) {
        foreach ($line in (Get-Content -LiteralPath $f -Encoding UTF8)) {
            if ($line -match ('^\s*' + [regex]::Escape($Var) + '\s*=\s*(\d+)\s*$')) { return [int]$Matches[1] }
        }
    }
    return $Default
}

$services = @(
    @{ slug = 'listening';   dir = '听力专项训练'; port = (Get-EnvPort 'LISTENING_PORT'   5555) },
    @{ slug = 'cloze';       dir = '选词填空';     port = (Get-EnvPort 'CLOZE_PORT'       5556) },
    @{ slug = 'longread';    dir = '长篇阅读';     port = (Get-EnvPort 'LONGREAD_PORT'    5557) },
    @{ slug = 'carefulread'; dir = '仔细阅读';     port = (Get-EnvPort 'CAREFULREAD_PORT' 5558) },
    @{ slug = 'translate';   dir = '翻译';         port = (Get-EnvPort 'TRANSLATE_PORT'   5559) },
    @{ slug = 'writing';     dir = '写作';         port = (Get-EnvPort 'WRITING_PORT'     5560) },
    @{ slug = 'intensive';   dir = '精读训练';     port = (Get-EnvPort 'INTENSIVE_PORT'   5561) },
    @{ slug = 'config';      dir = 'config';       port = (Get-EnvPort 'CONFIG_PORT'      5562) }
)

function Test-PortOpen([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return ($task.Wait(600) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Start-Services {
    if (-not (Test-Path -LiteralPath $py)) {
        Write-Step '首次运行：创建虚拟环境 .venv（约 1 分钟）'
        & python -m venv $venv
        if (-not (Test-Path -LiteralPath $py)) { Write-Err2 '创建虚拟环境失败（需要系统 Python 3.8+）'; exit 1 }
        & $py -m pip install --quiet --upgrade pip
        foreach ($s in $services) {
            $req = Join-Path (Join-Path $root $s.dir) 'requirements.txt'
            if (Test-Path -LiteralPath $req) {
                Write-Step ("安装依赖：" + $s.dir)
                & $py -m pip install --quiet -r $req
            }
        }
        Write-Ok '依赖安装完成'
    }

    New-Item -ItemType Directory -Path $logs -Force | Out-Null
    $started = @()
    foreach ($s in $services) {
        if (Test-PortOpen $s.port) { Write-Warn2 ($s.dir + " 端口 " + $s.port + " 已被占用，跳过"); continue }
        $dir = Join-Path $root $s.dir
        $env:HOST = '127.0.0.1'
        $env:PORT = "$($s.port)"
        $env:NO_OPEN_BROWSER = '1'
        $out = Join-Path $logs ($s.slug + '.out.log')
        $errLog = Join-Path $logs ($s.slug + '.err.log')
        $p = Start-Process -FilePath $py -ArgumentList 'app.py' -WorkingDirectory $dir -WindowStyle Hidden `
             -RedirectStandardOutput $out -RedirectStandardError $errLog -PassThru
        $started += @{ slug = $s.slug; dir = $s.dir; port = $s.port; pid = $p.Id }
        Write-Host ("  启动 " + $s.dir.PadRight(10) + " http://127.0.0.1:" + $s.port + "   (PID " + $p.Id + ")")
    }
    Remove-Item Env:HOST, Env:PORT, Env:NO_OPEN_BROWSER -ErrorAction SilentlyContinue
    $started | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $pidFile -Encoding UTF8

    Write-Step '等待服务就绪（最多 20 秒）'
    for ($i = 0; $i -lt 20; $i++) {
        Start-Sleep -Seconds 1
        $ready = 0
        foreach ($s in $services) { if (Test-PortOpen $s.port) { $ready++ } }
        if ($ready -eq $services.Count) { break }
    }
    Write-Step '自检'
    foreach ($s in $services) {
        if (Test-PortOpen $s.port) {
            try {
                $h = Invoke-RestMethod -Uri ("http://127.0.0.1:" + $s.port + "/api/health") -TimeoutSec 5
                $keyState = '未配置'
                if ($h.hasKey) { $keyState = '已配置' }
                Write-Ok ($s.dir.PadRight(10) + " :" + $s.port + "  已就绪  引擎=" + $h.model + "  Key=" + $keyState)
            } catch {
                Write-Ok ($s.dir.PadRight(10) + " :" + $s.port + "  端口已开（健康接口未响应）")
            }
        } else {
            Write-Err2 ($s.dir.PadRight(10) + " :" + $s.port + "  未启动，看日志：logs\" + $s.slug + ".err.log")
        }
    }
    Write-Host ''
    Write-Ok '裸机部署完成 —— 打开总入口：'
    Write-Host ("     " + $portalUrl) -ForegroundColor White
    Write-Host '   （引擎与密钥配置卡片就在该页面上；也可直接 http://127.0.0.1:5562）' -ForegroundColor DarkGray
}

switch ($Action) {
    'up' { Start-Services }

    'down' {
        if (-not (Test-Path -LiteralPath $pidFile)) { Write-Warn2 '没有找到启动记录（logs\baremetal-pids.json）'; exit 0 }
        $list = Get-Content -LiteralPath $pidFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $stopped = 0
        foreach ($p in $list) {
            $proc = Get-Process -Id $p.pid -ErrorAction SilentlyContinue
            if ($proc -and $proc.Path -like "$venv*") {
                Stop-Process -Id $p.pid -Force -ErrorAction SilentlyContinue
                Write-Ok ("已停止 " + $p.dir + " (PID " + $p.pid + ")")
                $stopped++
            } elseif ($proc) {
                Write-Warn2 ("PID " + $p.pid + " 不是本仓库 .venv 的进程，跳过（避免误杀）")
            }
        }
        if ($stopped -eq 0) { Write-Warn2 '没有需要停止的进程' }
    }

    'status' {
        Write-Step '服务状态'
        foreach ($s in $services) {
            $up = Test-PortOpen $s.port
            if ($up) {
                $line = $s.dir.PadRight(10) + " :" + $s.port + "  运行中"
                try {
                    $h = Invoke-RestMethod -Uri ("http://127.0.0.1:" + $s.port + "/api/health") -TimeoutSec 5
                    $keyState = '未配置'
                    if ($h.hasKey) { $keyState = '已配置' }
                    $line = $line + "  引擎=" + $h.model + "  Key=" + $keyState
                } catch { }
                Write-Ok $line
            } else {
                Write-Warn2 ($s.dir.PadRight(10) + " :" + $s.port + "  未运行")
            }
        }
        Write-Host ("  总入口：" + $portalUrl)
    }

    'logs' {
        foreach ($s in $services) {
            $errLog = Join-Path $logs ($s.slug + '.err.log')
            $outLog = Join-Path $logs ($s.slug + '.out.log')
            Write-Host ("===== " + $s.dir + " (:" + $s.port + ") =====") -ForegroundColor Cyan
            if (Test-Path -LiteralPath $errLog) { Get-Content -LiteralPath $errLog -Tail 8 -Encoding UTF8 | ForEach-Object { "  " + $_ } }
            if (Test-Path -LiteralPath $outLog) { Get-Content -LiteralPath $outLog -Tail 8 -Encoding UTF8 | ForEach-Object { "  " + $_ } }
        }
    }

    'install-autostart' {
        $scriptPath = Join-Path $root 'run-local.ps1'
        $taskCmd = 'powershell -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $scriptPath + '" up'
        Write-Step ('注册开机自启计划任务：' + $taskName)
        schtasks /create /tn $taskName /tr $taskCmd /sc onlogon /rl highest /f | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Err2 '注册计划任务失败（需要管理员权限）'; exit 1 }
        Write-Ok '已注册：登录 Windows 时自动启动这 8 个服务'
        Write-Step '立即启动一次'
        schtasks /run /tn $taskName | Out-Null
        Write-Output '  已触发；约 20 秒后就绪，可用 run-local.ps1 status 查看'
        Write-Output '  移除：run-local.ps1 uninstall-autostart'
    }

    'uninstall-autostart' {
        schtasks /end /tn $taskName 2>$null | Out-Null
        schtasks /delete /tn $taskName /f | Out-Null
        Write-Ok '已移除开机自启计划任务（已启动的服务进程请用 run-local.ps1 down 停止）'
    }
}
