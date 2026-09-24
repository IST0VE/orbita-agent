"""
Запуск одной командой: `up.cmd` / `up.sh` и штатный Compose со всеми тремя
частями — агентом, веб-интерфейсом и runner НТ с k6.

Проверяются места, которые расходятся молча. Путь API, забытый в nginx,
отдаёт браузеру index.html вместо JSON; up.ps1, пересохранённый без BOM,
печатает в Windows PowerShell кракозябры; up.cmd с LF ломается в cmd.exe на
первой же метке. Ни одно из этого не видно, пока не запустишь.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
NGINX = (ROOT / "web" / "nginx.conf").read_text(encoding="utf-8")
VITE = (ROOT / "web" / "vite.config.ts").read_text(encoding="utf-8")
SERVICES = COMPOSE["services"]


def vite_proxied() -> set[str]:
    listed = re.search(r"const PROXIED = \[(.*?)\];", VITE, re.S)
    assert listed, "в vite.config.ts не найден список PROXIED"
    return {path.strip("/") for path in re.findall(r'"(/[^"]*)"', listed.group(1))}


def nginx_proxied() -> set[str]:
    location = re.search(r"location ~ \^/\(([^)]*)\)", NGINX)
    assert location, "в nginx.conf не найдено проксирование API"
    return set(location.group(1).split("|"))


def test_the_built_frontend_proxies_the_same_paths_as_the_dev_server():
    assert nginx_proxied() == vite_proxied()


def test_the_proxy_does_not_buffer_run_streams():
    assert "proxy_buffering off" in NGINX
    assert re.search(r"proxy_read_timeout\s+\d+[hm]", NGINX)


def test_compose_starts_the_frontend_after_a_healthy_agent():
    web = SERVICES["web"]

    assert web["depends_on"]["agent"]["condition"] == "service_healthy"
    assert "healthcheck" in SERVICES["agent"]
    # Веб-порт закрыт так же, как порт агента: только петля.
    assert web["ports"][0].startswith("127.0.0.1:")
    assert web["ports"][0].endswith(":8080")
    assert web["cap_drop"] == ["ALL"]


def test_the_agent_healthcheck_authenticates():
    """`/ok` закрыт токеном: проверка без него не станет здоровой никогда."""
    test = " ".join(SERVICES["agent"]["healthcheck"]["test"])

    assert "/ok" in test
    assert "API_ADMIN_TOKEN" in test
    assert "Bearer" in test


def test_a_personal_web_env_stays_out_of_the_image():
    """VITE_API_URL из личного web/.env встроился бы в собранный код."""
    ignored = (ROOT / "web" / ".dockerignore").read_text(encoding="utf-8").split()

    assert ".env" in ignored
    assert ".env.*" in ignored
    assert "node_modules/" in ignored


def test_the_powershell_script_keeps_its_bom():
    assert (ROOT / "scripts" / "up.ps1").read_bytes().startswith(b"\xef\xbb\xbf")


def test_the_batch_launcher_is_checked_out_with_crlf():
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")

    assert re.search(r"^\*\.cmd\s+text\s+eol=crlf$", attributes, re.M)
    assert r"scripts\up.ps1" in (ROOT / "up.cmd").read_text(encoding="utf-8")


def test_the_default_stack_needs_no_database():
    """Postgres нужен только демо; обычный запуск не должен его ни ждать, ни требовать."""
    assert SERVICES["postgres"]["profiles"] == ["demo"]
    assert "postgres" not in SERVICES["agent"].get("depends_on", {})
    # Compose подставляет переменные во весь файл до выбора профилей: `:?` у
    # пароля остановил бы запуск, в котором базы нет.
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert not re.search(r"\$\{POSTGRES_PASSWORD:\?", compose)


def test_the_runner_is_part_of_the_stack_but_not_published():
    runner = SERVICES["runner"]
    command = runner["command"]

    assert "profiles" not in runner
    # Управляющий API запускает нагрузку: наружу его не открываем.
    assert "ports" not in runner
    assert command[command.index("--host") + 1] == "0.0.0.0"
    assert command[command.index("--k6-binary") + 1] == "/usr/local/bin/k6"
    assert command[command.index("--loopback-alias") + 1] == "host.docker.internal"
    assert runner["init"] is True
    assert runner["cap_drop"] == ["ALL"]
    assert "NT_RUNNER_TOKEN" in " ".join(runner["healthcheck"]["test"])
    assert "nt-runs" in COMPOSE["volumes"]


def test_the_shared_image_is_built_once():
    """Две одинаковые сборки параллельно роняли BuildKit на экспорте (EOF)."""
    image = SERVICES["agent"]["image"]

    assert "build" in SERVICES["agent"]
    for name in ("runner",):
        assert SERVICES[name]["image"] == image
        assert "build" not in SERVICES[name], f"{name} собирает образ второй раз"
        # Иначе на чистой машине Compose сперва пытается скачать локальное имя.
        assert SERVICES[name]["pull_policy"] == "never"


def test_demo_can_build_without_starting_the_agent():
    demo = SERVICES["demo"]
    assert demo["build"] == "."
    assert demo["pull_policy"] == "build"
    assert demo["image"] != SERVICES["agent"]["image"]
    assert "agent" not in demo["depends_on"]


def test_the_agent_reaches_the_runner_over_the_compose_network():
    environment = SERVICES["agent"]["environment"]

    assert environment["NT_RUNNER_URL"] == "http://runner:8077"
    assert environment["NT_RUNNER_TOKEN"] == SERVICES["runner"]["environment"]["NT_RUNNER_TOKEN"]


def test_the_image_ships_k6_and_the_runner_entrypoint():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "/usr/local/bin/k6" in dockerfile
    assert "COPY scripts/serve_nt_runner.py" in dockerfile


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:8087", "http://host.docker.internal:8087"),
        ("http://127.0.0.1:8088/", "http://host.docker.internal:8088/"),
        ("https://[::1]", "https://host.docker.internal"),
        ("http://192.168.0.158:8087", "http://192.168.0.158:8087"),
        ("https://stand.example.test", "https://stand.example.test"),
    ],
)
def test_loopback_targets_are_sent_to_the_host(url: str, expected: str):
    from scripts.serve_nt_runner import alias_loopback_targets

    config = {"targets": {"t": {"url": url}}}
    changed = alias_loopback_targets(config, "host.docker.internal")

    assert config["targets"]["t"]["url"] == expected
    assert changed == (["t"] if url != expected else [])


@pytest.mark.parametrize("script", ["up.sh", "scripts/up.ps1"])
def test_both_launchers_do_the_same_checks(script: str):
    text = (ROOT / script).read_text(encoding="utf-8-sig")

    for name in ("API_ADMIN_TOKEN", "NT_RUNNER_TOKEN", "LLM_API_KEY", "ORBITA_WEB_PORT",
                 "nt-runner.example.json", "host.docker.internal"):
        assert name in text, f"{script} не знает про {name}"
    # Пароль демо-базы обычному запуску не нужен.
    assert "POSTGRES_PASSWORD" not in text
    assert "anthropic" in text
    assert "docker compose up -d --build --wait" in text
    # Без неё каждая сборка даёт новый ID образа, и повторный запуск
    # пересоздаёт агента посреди прогона.
    assert "BUILDX_NO_DEFAULT_ATTESTATIONS" in text


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("initial", [
    "API_ADMIN_TOKEN=\nAPI_ADMIN_TOKEN=\n",
    "API_ADMIN_TOKEN=old\nAPI_ADMIN_TOKEN=\"\" # reset\n",
    "API_ADMIN_TOKEN=\r\nAPI_ADMIN_TOKEN=\r\n",
    "API_ADMIN_TOKEN=",
    "UNRELATED=value",
    'API_ADMIN_TOKEN="existing-token-with-32-characters" # keep\n',
])
def test_launcher_token_roundtrip(tmp_path, shell, initial):
    """Execute the real helpers against isolated files, without invoking Docker."""
    from dotenv import dotenv_values

    executable = shutil.which(shell)
    if shell == "bash" and os.name == "nt":
        executable = str(Path(os.environ.get("ProgramFiles", "C:/Program Files"))
                         / "Git/bin/bash.exe")
        if not Path(executable).is_file():
            executable = None
    if not executable:
        pytest.skip(f"{shell} unavailable")
    env_file = tmp_path / ".env"
    env_file.write_bytes(initial.encode())
    expected = dotenv_values(env_file).get("API_ADMIN_TOKEN") or "A" * 43
    if shell == "bash":
        source = (ROOT / "up.sh").read_text(encoding="utf-8")
        helpers = source[source.index("env_get() {"):source.index("# -----")]
        probe = tmp_path / "probe.sh"
        probe.write_text(
            "set -euo pipefail\n" + helpers
            + '\n[ -n "$(env_get API_ADMIN_TOKEN)" ] || env_set API_ADMIN_TOKEN '
            + "A" * 43 + '\n[ "$(env_get API_ADMIN_TOKEN)" = "' + expected + '" ]\n',
            encoding="utf-8", newline="\n",
        )
        command = [executable, "probe.sh"]
    else:
        source = (ROOT / "scripts/up.ps1").read_text(encoding="utf-8-sig")
        helpers = source[source.index("function Read-DotEnv"):source.index("# -----")]
        probe = tmp_path / "probe.ps1"
        probe.write_text(
            "$ErrorActionPreference = 'Stop'\n$envPath = Join-Path $PWD '.env'\n"
            "$utf8 = New-Object System.Text.UTF8Encoding($false)\n" + helpers
            + "\nif (-not (Read-DotEnv)['API_ADMIN_TOKEN']) { Set-DotEnvValue API_ADMIN_TOKEN ('A' * 43) }\n"
            + f"if ((Read-DotEnv)['API_ADMIN_TOKEN'] -ne '{expected}') {{ throw 'token mismatch' }}\n",
            encoding="utf-8-sig",
        )
        command = [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(probe)]
    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert dotenv_values(env_file)["API_ADMIN_TOKEN"] == expected
    if initial.startswith("UNRELATED"):
        assert dotenv_values(env_file)["UNRELATED"] == "value"
    if "# keep" in initial:
        assert env_file.read_bytes() == initial.encode()
