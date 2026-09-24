<#
Orbita одной командой: проверяет .env, дописывает недостающие секреты,
собирает и поднимает Compose (агент, веб-интерфейс, runner НТ с k6), ждёт,
пока всё ответит, и открывает интерфейс в браузере.

    .\up.cmd               из корня репозитория или двойным щелчком
    .\up.cmd -NoBrowser    не открывать браузер

Файл сохранён в UTF-8 с BOM: без него Windows PowerShell 5.1 читает скрипт
в кодировке ANSI, и русские сообщения превращаются в кракозябры.
#>
param([switch]$NoBrowser)

$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root
$envPath = Join-Path $root ".env"
$utf8 = New-Object System.Text.UTF8Encoding($false)

# Двойной щелчок по up.cmd открывает окно, которое закрывается вместе со
# скриптом, — и сообщение об ошибке исчезает раньше, чем его прочтут.
# Признак щелчка — cmd, запущенный проводником ради самого up.cmd: набранный
# в открытой консоли up.cmd выполняется в её cmd, в строке которого его нет.
function Wait-IfDoubleClicked {
    try {
        $self = Get-CimInstance Win32_Process -Filter "ProcessId=$PID"
        $shell = Get-CimInstance Win32_Process -Filter "ProcessId=$($self.ParentProcessId)"
        $launcher = Get-Process -Id $shell.ParentProcessId -ErrorAction Stop
        if ($shell.Name -eq "cmd.exe" -and $shell.CommandLine -match 'up\.cmd' -and $launcher.ProcessName -eq "explorer") {
            Read-Host "Нажмите Enter, чтобы закрыть окно" | Out-Null
        }
    } catch {}
}

function Fail([string]$message) {
    Write-Host ""
    Write-Host $message -ForegroundColor Red
    Wait-IfDoubleClicked
    exit 1
}

function Read-DotEnv {
    $values = @{}
    foreach ($line in [IO.File]::ReadAllLines($envPath, $utf8)) {
        if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value -match '^([''"])(.*?)\1\s*(?:#.*)?$') {
                $value = $Matches[2]
            } else {
                $value = ($value -replace '\s+#.*$', '').TrimEnd()
            }
            # Как у Compose и python-dotenv: повтор ключа перекрывает прежний.
            $values[$name] = $value
        }
    }
    return $values
}

function New-Secret {
    $bytes = New-Object byte[] 32
    $rng = [Security.Cryptography.RandomNumberGenerator]::Create()
    $rng.GetBytes($bytes)
    $rng.Dispose()
    # То же, что secrets.token_urlsafe(32): 43 символа без знаков, которые
    # пришлось бы экранировать в .env или в адресе.
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

# Обновляется последнее вхождение ключа: именно его читает Compose.
# Окончания строк файла сохраняются: замена не трогает соседние строки.
function Set-DotEnvValue([string]$name, [string]$value) {
    $text = [IO.File]::ReadAllText($envPath, $utf8)
    $assignments = [regex]::Matches($text, "(?m)^[ \t]*$name[ \t]*=[^\r\n]*(\r?)$")
    if ($assignments.Count -gt 0) {
        $last = $assignments[$assignments.Count - 1]
        $text = $text.Remove($last.Index, $last.Length).Insert($last.Index, "$name=$value" + $last.Groups[1].Value)
    } else {
        $newline = if ($text.Contains("`r`n")) { "`r`n" } else { "`n" }
        if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $text += $newline }
        $text += "$name=$value$newline"
    }
    [IO.File]::WriteAllText($envPath, $text, $utf8)
}

# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------
if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath (Join-Path $root ".env.example") -Destination $envPath
    Write-Host "Создан .env из .env.example."
}

$settings = Read-DotEnv
foreach ($name in "API_ADMIN_TOKEN", "NT_RUNNER_TOKEN") {
    if (-not $settings[$name]) {
        Set-DotEnvValue $name (New-Secret)
        Write-Host "В .env записан случайный $name."
    }
}
$settings = Read-DotEnv

$provider = "deepseek"
if ($settings["LLM_PROVIDER"]) { $provider = $settings["LLM_PROVIDER"].ToLower() }
# Штатный образ ставит адаптеры deepseek и openai; anthropic в нём нет,
# и прогон упал бы уже в интерфейсе, на первом обращении к модели.
if ($provider -eq "anthropic") {
    Fail "LLM_PROVIDER=anthropic не поддержан штатным образом: в нём нет адаптера langchain-anthropic. Используйте deepseek или openai либо локальный запуск (docs/GETTING_STARTED.md)."
}
$key = $settings["LLM_API_KEY"]
if (-not $key) { $key = $env:LLM_API_KEY }
if (-not $key) { $key = $settings[$provider.ToUpper() + "_API_KEY"] }
if (-not $key) {
    Fail "В .env не задан LLM_API_KEY — ключ модели ($provider). Впишите его и запустите снова."
}

$token = $settings["API_ADMIN_TOKEN"]
if ($token.Length -lt 32 -or $token.Length -gt 256 -or $token -notmatch '^[\x21-\x7E]+$') {
    Fail "API_ADMIN_TOKEN в .env должен состоять из 32–256 ASCII-символов без пробелов. Очистите значение — скрипт сгенерирует новое."
}
if ($settings["NT_RUNNER_TOKEN"].Length -lt 16) {
    Fail "NT_RUNNER_TOKEN в .env должен быть не короче 16 символов. Очистите значение — скрипт сгенерирует новое."
}

# Runner без списка стендов не стартует. Шаблон разрешает только локальную
# демо-цель, поэтому запуск с ним безопасен; свои стенды человек вписывает сам.
$runnerConfig = Join-Path $root "config/nt-runner.json"
if (-not (Test-Path -LiteralPath $runnerConfig)) {
    Copy-Item -LiteralPath (Join-Path $root "config/nt-runner.example.json") -Destination $runnerConfig
    Write-Host "Создан config/nt-runner.json из шаблона: впишите в targets свои стенды для НТ."
}

# 127.0.0.1 из контейнера — это сам контейнер. Адреса runner и базы Compose
# задаёт сам, цели runner переводит на хост флаг --loopback-alias, а остальное
# (Prometheus, Jira, шлюз модели на этой машине) надо поправить в .env.
$loopback = @($settings.Keys | Where-Object {
    $_ -notin @("NT_RUNNER_URL", "POSTGRES_URI") -and
    $settings[$_] -match '^[A-Za-z][A-Za-z0-9+.-]*://(localhost|127\.[0-9.]+|\[::1\])([:/]|$)'
} | Sort-Object)
if ($loopback.Count -gt 0) {
    Write-Host ""
    Write-Host ("Внимание: в .env адреса на 127.0.0.1/localhost: " + ($loopback -join ", ") + ".") -ForegroundColor Yellow
    Write-Host "Из контейнера они ведут в сам контейнер. Используйте host.docker.internal; для модели нужен HTTPS (docs/DEPLOYMENT.md)." -ForegroundColor Yellow
}

# --------------------------------------------------------------------------
# Docker
# --------------------------------------------------------------------------
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Fail "Не найден Docker. Установите Docker Desktop: https://docs.docker.com/desktop/"
}
& docker compose version *> $null
if ($LASTEXITCODE -ne 0) {
    Fail "Не найден Docker Compose v2 (docker compose). Обновите Docker Desktop."
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    Write-Host "Docker не отвечает — запускаю Docker Desktop..."
    & docker desktop start *> $null
    & docker info *> $null
    if ($LASTEXITCODE -ne 0) {
        Fail "Docker не запущен. Запустите Docker Desktop, дождитесь статуса Engine running и повторите."
    }
}

# --------------------------------------------------------------------------
# Запуск
# --------------------------------------------------------------------------
Write-Host ""
Write-Host "Собираю образы и поднимаю контейнеры. Первая сборка занимает несколько минут..."
# Docker Desktop прикладывает к каждой сборке provenance-аттестацию со своим
# digest: даже целиком закешированный образ получает новый ID, и Compose
# пересоздаёт контейнеры — повторный запуск обрывал бы идущие прогоны.
$env:BUILDX_NO_DEFAULT_ATTESTATIONS = "1"
& docker compose up -d --build --wait --wait-timeout 600
if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "Последние строки журналов агента и runner:"
    & docker compose logs --tail 30 agent runner
    Fail "Запуск не удался. Состояние контейнеров: docker compose ps"
}

$port = "8080"
if ($env:ORBITA_WEB_PORT) { $port = $env:ORBITA_WEB_PORT }
elseif ($settings["ORBITA_WEB_PORT"]) { $port = $settings["ORBITA_WEB_PORT"] }
$url = "http://localhost:$port"

Write-Host ""
Write-Host "Orbita запущена: $url" -ForegroundColor Green
Write-Host "При первом входе браузер спросит токен API — это значение API_ADMIN_TOKEN из .env."
Write-Host ""
Write-Host "Журнал агента:  docker compose logs -f agent"
Write-Host "Стенды для НТ:  config/nt-runner.json, после правки — docker compose restart runner"
Write-Host "Остановить:     docker compose down    (документы и треды остаются на томах)"
Write-Host "После правки .env запустите up.cmd снова: контейнеры пересоздадутся."

if (-not $NoBrowser) { Start-Process $url }
Wait-IfDoubleClicked
