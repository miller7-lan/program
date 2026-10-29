# 利润助手同步服务 · 部署与维护

只依赖服务器自带的 Python 3（标准库），无需 pip、Docker 或数据库服务。

## 目录

| 路径 | 内容 |
| --- | --- |
| `/opt/profit-helper/profit_sync_server.py` | 程序（`.prev` 为上一版，用于回滚） |
| `/opt/profit-helper/downloads/` | 可选：放安装包后可在 `https://<域名>:<端口>/download/` 下载 |
| `/etc/profit-helper/config.json` | 配置（只保存同步密钥的 SHA-256，不保存明文） |
| `/var/lib/profit-helper/ledger.db` | 账本数据库 |
| `/var/lib/profit-helper/backups/` | 每日自动备份（保留 30 份） |

## 安装 / 升级

把 `server/` 目录上传到服务器后执行：

```sh
DOMAIN=<域名> PORT=28443 TOKEN_SHA256=<密钥的SHA-256> OPEN_FIREWALL=1 bash server/deploy/install.sh
```

升级程序时省略 `TOKEN_SHA256` 即保留原密钥。生成新密钥：

```sh
python3 server/profit_sync_server.py gen-token
```

## 常用命令

```sh
systemctl status profit-helper --no-pager
journalctl -u profit-helper -n 50 --no-pager
# 立即备份 / 导出为桌面端可导入的 family_ledger.db
runuser -u profit-helper -- python3 -I /opt/profit-helper/profit_sync_server.py backup --config /etc/profit-helper/config.json
runuser -u profit-helper -- python3 -I /opt/profit-helper/profit_sync_server.py export --config /etc/profit-helper/config.json --out /var/lib/profit-helper/export.db
# 导入桌面端账本（覆盖同名日期；--replace 为整本替换）
runuser -u profit-helper -- python3 -I /opt/profit-helper/profit_sync_server.py import --config /etc/profit-helper/config.json --file <文件> --replace
```

## 回滚

```sh
cp /opt/profit-helper/profit_sync_server.py.prev /opt/profit-helper/profit_sync_server.py && systemctl restart profit-helper
```

数据恢复：停止服务，把 `backups/` 中需要的备份复制为 `ledger.db`（删除同目录的 `ledger.db-wal`、`ledger.db-shm`），再启动服务。
客户端会自动检测到服务器回到旧版本并重新上传本机数据。

## 证书

服务启动时由 systemd 读取 `/etc/letsencrypt/live/<域名>/` 下的证书；`profit-helper-reload.timer`
每天凌晨重启一次服务以加载续期后的证书，不修改 certbot 的任何配置。

## 卸载

```sh
systemctl disable --now profit-helper.service profit-helper-reload.timer
rm /etc/systemd/system/profit-helper*.service /etc/systemd/system/profit-helper-reload.timer && systemctl daemon-reload
ufw delete allow <端口>/tcp
```
