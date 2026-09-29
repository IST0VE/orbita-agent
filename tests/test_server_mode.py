"""
Серверный режим: docker-compose.server.yml поверх штатного Compose.

Здесь расходится то, чего на машине разработчика не видно: адрес realm в
токенах и в OIDC_ISSUER, путь Keycloak в nginx и в KC_HTTP_RELATIVE_PATH,
тестовые пользователи, доехавшие до сервера. Ничего из этого не проявится,
пока кто-то не попробует войти на сервере.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parent.parent
SERVER_FILE = "docker-compose.server.yml"
ENABLED = "docker-compose.yml:docker-compose.server.yml"


class Tagged:
    """Значение с тегом слияния Compose: `!reset` или `!override`."""

    def __init__(self, tag: str, value):
        self.tag = tag
        self.value = value


class ComposeLoader(yaml.SafeLoader):
    pass


def _tagged(loader: ComposeLoader, node: yaml.Node) -> Tagged:
    if isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    elif isinstance(node, yaml.MappingNode):
        value = loader.construct_mapping(node, deep=True)
    else:
        value = loader.construct_scalar(node)
    return Tagged(node.tag, value)


for _tag in ("!reset", "!override"):
    ComposeLoader.add_constructor(_tag, _tagged)


def load(name: str) -> dict:
    return yaml.load((ROOT / name).read_text(encoding="utf-8"), Loader=ComposeLoader)["services"]


BASE = load("docker-compose.yml")
SERVER = load(SERVER_FILE)
KEYCLOAK = SERVER["keycloak"]["environment"]
NGINX = (ROOT / "web" / "nginx" / "server.conf").read_text(encoding="utf-8")
LOCAL_REALM = json.loads(
    (ROOT / "config" / "keycloak" / "import" / "orbita-realm.json").read_text(encoding="utf-8")
)
SERVER_REALM = json.loads(
    (ROOT / "config" / "keycloak" / "server" / "orbita-realm.json").read_text(encoding="utf-8")
)
#: Чем серверный realm строже локального — и больше ничем: защита от подбора,
#: закрытая регистрация и журнал входов и действий администратора.
SERVER_ONLY = {
    "bruteForceProtected", "failureFactor", "registrationAllowed", "resetPasswordAllowed",
    "eventsEnabled", "eventsExpiration", "adminEventsEnabled", "adminEventsDetailsEnabled",
}


def public(value: str) -> str:
    """`${ORBITA_PUBLIC_URL:?сообщение}` → `${ORBITA_PUBLIC_URL}`: сообщение не часть адреса."""
    return re.sub(r"\$\{ORBITA_PUBLIC_URL(:[-?][^}]*)?\}", "${ORBITA_PUBLIC_URL}", value)


def without_redirects(realm: dict) -> dict:
    realm = json.loads(json.dumps(realm))
    for client in realm["clients"]:
        client.pop("redirectUris")
    return realm


def test_the_server_realm_is_the_local_realm_without_test_users():
    """Клиент, роли и тема одни и те же; разница — пользователи и адреса возврата."""
    local = without_redirects(LOCAL_REALM)
    local.pop("users")
    server = {k: v for k, v in without_redirects(SERVER_REALM).items() if k not in SERVER_ONLY}

    assert "users" not in SERVER_REALM
    assert server == local
    assert all(name in SERVER_REALM for name in SERVER_ONLY)


def test_the_server_realm_keeps_a_login_journal():
    """Кто входил, кто ошибался паролем, что правили в консоли — для разбора инцидента."""
    assert SERVER_REALM["bruteForceProtected"] is True
    assert SERVER_REALM["eventsEnabled"] is True
    assert SERVER_REALM["adminEventsEnabled"] is True
    # Тело правки в консоли не пишется: в нём бывает пароль привязки LDAP.
    assert SERVER_REALM["adminEventsDetailsEnabled"] is False


def test_the_server_realm_returns_only_to_the_public_address():
    (client,) = SERVER_REALM["clients"]

    assert client["redirectUris"] == ["${ORBITA_PUBLIC_URL}/*"]
    # Плейсхолдер Keycloak берёт из своего окружения при импорте.
    assert public(KEYCLOAK["ORBITA_PUBLIC_URL"]) == "${ORBITA_PUBLIC_URL}"


def test_the_test_users_do_not_reach_the_server():
    """Том сервера встаёт на место локального: тот же путь в контейнере."""
    local = next(v for v in BASE["keycloak"]["volumes"] if v.endswith("/data/import:ro"))
    server = next(v for v in SERVER["keycloak"]["volumes"] if v.endswith("/data/import:ro"))

    assert local.split(":")[1] == server.split(":")[1]
    assert server.split(":")[0] == "./config/keycloak/server"


def test_the_agent_trusts_the_issuer_keycloak_signs_with():
    """`iss` токена — KC_HOSTNAME плюс realm; с OIDC_ISSUER агента он обязан совпасть."""
    agent = SERVER["agent"]["environment"]
    realm = "/realms/" + SERVER_REALM["realm"]
    relative = KEYCLOAK["KC_HTTP_RELATIVE_PATH"]

    assert public(KEYCLOAK["KC_HOSTNAME"]) == "${ORBITA_PUBLIC_URL}" + relative
    assert public(agent["OIDC_ISSUER"]) == public(KEYCLOAK["KC_HOSTNAME"]) + realm
    assert agent["OIDC_JWKS_URL"] == (
        "http://keycloak:8080" + relative + realm + "/protocol/openid-connect/certs"
    )
    assert agent["OIDC_CLIENT_ID"] == SERVER_REALM["clients"][0]["clientId"]
    assert "orbita-user" in agent["OIDC_REQUIRED_ROLE"]


def test_nginx_serves_keycloak_under_its_relative_path():
    relative = KEYCLOAK["KC_HTTP_RELATIVE_PATH"]

    assert re.search(r"location \^~ " + re.escape(relative) + r"/ \{", NGINX)
    assert "set $keycloak http://keycloak:8080;" in NGINX
    # Схему Keycloak берёт из заголовка, которому верит, — его ставит nginx.
    # Сюда запрос приходит по HTTP от прокси, а снаружи адрес всегда https.
    assert KEYCLOAK["KC_PROXY_HEADERS"] == "xforwarded"
    assert "proxy_set_header X-Forwarded-Proto https;" in NGINX


def test_the_server_serves_the_same_application_as_the_local_one():
    """Тело сервера одно: путь API, добавленный в app.conf, есть и на сервере."""
    default = (ROOT / "web" / "nginx" / "default.conf").read_text(encoding="utf-8")
    dockerfile = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")
    include = "include /etc/nginx/orbita/app.conf;"

    assert include in default
    assert include in NGINX
    assert "COPY nginx/app.conf /etc/nginx/orbita/app.conf" in dockerfile
    assert "COPY nginx/default.conf /etc/nginx/conf.d/default.conf" in dockerfile
    # На сервере server.conf встаёт на место default.conf, а не рядом с ним:
    # два сервера на одном порту nginx не поднял бы.
    assert "./web/nginx/server.conf:/etc/nginx/conf.d/default.conf:ro" in SERVER["web"]["volumes"]


def test_every_page_nginx_serves_refuses_to_be_framed():
    """
    `frame-ancestors` из <meta> браузер не читает, а `add_header` в location
    отменяет унаследованные: заголовки обязаны стоять в каждом location страниц.
    """
    app = (ROOT / "web" / "nginx" / "app.conf").read_text(encoding="utf-8")
    headers = (ROOT / "web" / "nginx" / "headers.conf").read_text(encoding="utf-8")
    default = (ROOT / "web" / "nginx" / "default.conf").read_text(encoding="utf-8")
    dockerfile = (ROOT / "web" / "Dockerfile").read_text(encoding="utf-8")

    assert "frame-ancestors 'none'" in headers
    assert 'X-Frame-Options "DENY"' in headers
    assert "COPY nginx/headers.conf /etc/nginx/orbita/headers.conf" in dockerfile
    static = re.findall(r"location\s+(?:=\s*)?(/[^\s{]*)\s*\{([^}]*)\}", app)
    assert {path for path, _ in static} >= {"/", "/assets/", "/index.html"}
    for path, body in static:
        assert "include /etc/nginx/orbita/headers.conf;" in body, path
    # Переменная HSTS нужна обоим серверам: без неё nginx не запустится.
    assert 'set $orbita_hsts "";' in default
    assert 'set $orbita_hsts "max-age=31536000";' in NGINX


def test_tls_belongs_to_the_corporate_proxy():
    """Сертификата у Orbita нет: nginx слушает тот же HTTP-порт, что проверяет healthcheck."""
    ports = SERVER["web"]["ports"]

    assert isinstance(ports, Tagged) and ports.tag == "!override"
    assert ports.value == ["${ORBITA_WEB_BIND:-0.0.0.0}:${ORBITA_WEB_PORT:-8080}:8080"]
    assert re.search(r"listen 8080;", NGINX)
    assert "ssl" not in re.sub(r"#.*", "", NGINX)
    assert "http://127.0.0.1:8080/" in BASE["web"]["healthcheck"]["test"]
    # Keycloak — только через nginx, и без профиля: на сервере вход обязателен.
    for key in ("ports", "profiles"):
        assert isinstance(SERVER["keycloak"][key], Tagged)
        assert SERVER["keycloak"][key].tag == "!reset"


def test_keycloak_health_is_checked_where_the_healthcheck_asks():
    """Без явного `/` порт управления унаследовал бы /auth, и проверка не нашла бы /health."""
    assert KEYCLOAK["KC_HTTP_MANAGEMENT_RELATIVE_PATH"] == "/"
    assert "GET /health/ready" in " ".join(BASE["keycloak"]["healthcheck"]["test"])


def test_keycloak_keeps_its_data_in_its_own_database():
    database = SERVER["keycloak-db"]

    assert SERVER["keycloak"]["command"][0] == "start"
    assert KEYCLOAK["KC_DB"] == "postgres"
    assert KEYCLOAK["KC_DB_URL"].endswith("/keycloak")
    assert "CREATE DATABASE keycloak" in " ".join(database["command"])
    assert database["depends_on"]["postgres"]["condition"] == "service_healthy"
    assert SERVER["keycloak"]["depends_on"]["keycloak-db"]["condition"] == (
        "service_completed_successfully"
    )


def test_the_fixed_list_covers_what_the_server_file_pins():
    """То же правило, что в test_settings_in_docker, для окружения после слияния."""
    environment = {**BASE["agent"]["environment"], **SERVER["agent"]["environment"]}
    pinned = {
        name for name, value in environment.items()
        if not re.fullmatch(r"\$\{" + name + r"(:[-?][^}]*)?\}", str(value))
        and not name.startswith("SETTINGS_")
    }

    assert set(environment["SETTINGS_FIXED"].split(",")) == pinned


def test_keycloak_trusts_the_company_root_for_ldaps():
    """conf/truststores Keycloak читает сам; с отдельной настройкой он брал бы файлы дважды."""
    assert "./config/ca:/opt/keycloak/conf/truststores:ro" in SERVER["keycloak"]["volumes"]
    assert "KC_TRUSTSTORE_PATHS" not in KEYCLOAK


def test_company_certificates_stay_out_of_git():
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    assert "/config/ca/*" in ignored
    assert "!/config/ca/README.md" in ignored


# --------------------------------------------------------------------------
# Запускалки: настоящий блок серверного режима из up.sh и up.ps1
# --------------------------------------------------------------------------
def _bash() -> str | None:
    if os.name == "nt":
        path = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Git/bin/bash.exe"
        return str(path) if path.is_file() else None
    return shutil.which("bash")


def _server_block(shell: str) -> tuple[list[str], str]:
    """Помощники скрипта и его блок серверного режима — без Docker и без модели."""
    if shell == "bash":
        executable = _bash()
        source = (ROOT / "up.sh").read_text(encoding="utf-8")
        helpers = source[source.index("fail() {"):source.index("# -----")]
        block = source[source.index("# Серверный режим"):source.index("provider=$(")]
        return ([executable, "probe.sh"] if executable else []), (
            "set -euo pipefail\n" + helpers + "\n" + block
        )
    executable = shutil.which("powershell")
    source = (ROOT / "scripts" / "up.ps1").read_text(encoding="utf-8-sig")
    helpers = source[source.index("function Fail"):source.index("# -----")]
    block = source[source.index("# Серверный режим"):source.index('$provider = "deepseek"')]
    script = (
        "$ErrorActionPreference = 'Stop'\n$root = $PWD.Path\n"
        "$envPath = Join-Path $root '.env'\n"
        "$utf8 = New-Object System.Text.UTF8Encoding($false)\n"
        "function Wait-IfDoubleClicked {}\n" + helpers
        + "\n$settings = Read-DotEnv\n" + block
    )
    command = [executable, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "probe.ps1"]
    return (command if executable else []), script


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize(
    ("env", "ok"),
    [
        ("ORBITA_PUBLIC_URL=https://orbita.example.ru\n", True),
        ("ORBITA_PUBLIC_URL=https://orbita.example.ru/\n", False),
        ("ORBITA_PUBLIC_URL=http://orbita.example.ru\n", False),
        ("ORBITA_PUBLIC_URL=https://orbita.example.ru:8443\n", False),
        ("ORBITA_PUBLIC_URL=https://orbita.example.ru\nCOMPOSE_FILE=docker-compose.yml\n", False),
        ("ORBITA_PUBLIC_URL=https://orbita.example.ru\nKEYCLOAK_ADMIN_PASSWORD=kept\n", True),
        (f"ORBITA_PUBLIC_URL=https://orbita.example.ru\nCOMPOSE_FILE={ENABLED}\n", True),
        # Без адреса — локальный режим: скрипт ничего не дописывает.
        ("ORBITA_PUBLIC_URL=\n", True),
    ],
)
def test_the_launchers_switch_the_server_mode_on(tmp_path, shell, env, ok):
    from dotenv import dotenv_values

    command, script = _server_block(shell)
    if not command:
        pytest.skip(f"{shell} unavailable")
    probe = tmp_path / ("probe.sh" if shell == "bash" else "probe.ps1")
    probe.write_text(script, encoding="utf-8" if shell == "bash" else "utf-8-sig", newline="\n")
    (tmp_path / ".env").write_bytes(env.encode())
    environ = {k: v for k, v in os.environ.items() if not k.startswith("COMPOSE_")}

    result = subprocess.run(command, cwd=tmp_path, capture_output=True, timeout=30, env=environ)
    written = dotenv_values(tmp_path / ".env")

    assert (result.returncode == 0) == ok, result.stdout.decode(errors="replace") + result.stderr.decode(errors="replace")
    if ok and written["ORBITA_PUBLIC_URL"]:
        assert written["COMPOSE_FILE"] == ENABLED
        assert written["KEYCLOAK_ADMIN_PASSWORD"] == "kept" or len(written["KEYCLOAK_ADMIN_PASSWORD"]) >= 32
    if ok and "COMPOSE_FILE" not in env:
        assert written.get("COMPOSE_PATH_SEPARATOR") == (":" if written["ORBITA_PUBLIC_URL"] else None)
