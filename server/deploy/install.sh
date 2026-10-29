#!/usr/bin/env bash
# 利润助手同步服务 安装 / 升级脚本（在服务器上以 root 运行，可重复执行）
#
#   DOMAIN=example.duckdns.org PORT=28443 TOKEN_SHA256=<hex> OPEN_FIREWALL=1 bash install.sh
#
#   DOMAIN         已有 Let's Encrypt 证书的域名（/etc/letsencrypt/live/<DOMAIN>/）
#   PORT           对外 HTTPS 端口，默认 28443
#   TOKEN_SHA256   同步密钥的 SHA-256；首次安装必填，升级时留空则保留原密钥，填写则替换
#   OPEN_FIREWALL  为 1 时在 ufw 中放行 PORT/tcp（只新增这一条规则）
set -euo pipefail

: "${DOMAIN:?需要 DOMAIN}"
PORT="${PORT:-28443}"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR=/opt/profit-helper
CONF_DIR=/etc/profit-helper
CONF="$CONF_DIR/config.json"
LIVE="/etc/letsencrypt/live/$DOMAIN"

[ "$(id -u)" = 0 ] || { echo "请以 root 运行"; exit 1; }
[ -f "$LIVE/fullchain.pem" ] && [ -f "$LIVE/privkey.pem" ] || { echo "找不到证书：$LIVE"; exit 1; }
[ -f "$SRC_DIR/../profit_sync_server.py" ] || { echo "找不到 profit_sync_server.py"; exit 1; }

echo "[1/6] 系统用户"
id -u profit-helper >/dev/null 2>&1 || \
    useradd --system --home-dir /var/lib/profit-helper --no-create-home --shell /usr/sbin/nologin profit-helper

echo "[2/6] 程序文件（保留上一版用于回滚）"
python3 -I -c "import ast, sys; ast.parse(open(sys.argv[1], encoding='utf-8').read())" "$SRC_DIR/../profit_sync_server.py"
install -d -m 0755 "$APP_DIR" "$APP_DIR/downloads"
[ -f "$APP_DIR/profit_sync_server.py" ] && cp -p "$APP_DIR/profit_sync_server.py" "$APP_DIR/profit_sync_server.py.prev"
install -m 0644 "$SRC_DIR/../profit_sync_server.py" "$APP_DIR/profit_sync_server.py"
install -m 0644 "$SRC_DIR/README.md" "$APP_DIR/README.md"

echo "[3/6] 配置"
install -d -m 0750 -o root -g profit-helper "$CONF_DIR"
if [ ! -f "$CONF" ] && [ -z "${TOKEN_SHA256:-}" ]; then
    echo "首次安装需要提供 TOKEN_SHA256"; exit 1
fi
python3 -I - "$CONF" "$PORT" "${TOKEN_SHA256:-}" <<'PY'
import json, os, re, sys
path, port, digest = sys.argv[1], int(sys.argv[2]), sys.argv[3].strip().lower()
config = {}
if os.path.exists(path):
    with open(path, encoding="utf-8") as handle:
        config = json.load(handle)
config.update({
    "listen_host": "0.0.0.0",
    "listen_port": port,
    "tls_cert": "credential:tls-cert",
    "tls_key": "credential:tls-key",
    "data_dir": "/var/lib/profit-helper",
    "downloads_dir": "/opt/profit-helper/downloads",
    "backup_keep": 30,
    "max_connections": 24,
})
if digest:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        sys.exit("TOKEN_SHA256 格式不正确")
    config["tokens"] = [{"name": "family", "sha256": digest}]
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False, indent=2)
os.replace(tmp, path)
PY
chown root:profit-helper "$CONF"
chmod 0640 "$CONF"

echo "[4/6] systemd 服务"
sed "s#@DOMAIN@#$DOMAIN#g" "$SRC_DIR/profit-helper.service.in" > /etc/systemd/system/profit-helper.service
install -m 0644 "$SRC_DIR/profit-helper-reload.service" /etc/systemd/system/profit-helper-reload.service
install -m 0644 "$SRC_DIR/profit-helper-reload.timer" /etc/systemd/system/profit-helper-reload.timer
systemctl daemon-reload
systemctl enable --quiet profit-helper.service profit-helper-reload.timer
systemctl restart profit-helper.service
systemctl start profit-helper-reload.timer

echo "[5/6] 防火墙"
if [ "${OPEN_FIREWALL:-0}" = 1 ]; then
    ufw allow "$PORT/tcp" comment "profit-helper sync"
else
    echo "未修改防火墙（如需放行请设置 OPEN_FIREWALL=1）"
fi

echo "[6/6] 健康检查"
for _ in $(seq 1 30); do
    if python3 -I - "$PORT" "$DOMAIN" <<'PY'
import http.client, json, socket, ssl, sys
port, domain = int(sys.argv[1]), sys.argv[2]
try:
    context = ssl.create_default_context()
    raw = socket.create_connection(("127.0.0.1", port), timeout=3)
    conn = http.client.HTTPSConnection(domain, port, context=context, timeout=3)
    conn.sock = context.wrap_socket(raw, server_hostname=domain)
    conn.request("GET", "/api/v1/ping")
    response = conn.getresponse()
    sys.exit(0 if response.status == 200 and json.loads(response.read()).get("ok") else 1)
except Exception:
    sys.exit(1)
PY
    then
        echo "同步服务已就绪：https://$DOMAIN:$PORT"
        exit 0
    fi
    sleep 0.5
done
echo "健康检查失败，最近日志："
journalctl -u profit-helper.service -n 30 --no-pager
exit 1
