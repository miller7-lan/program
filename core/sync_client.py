"""
core/sync_client.py
云同步客户端 — 与 server/profit_sync_server.py 对接，不依赖 Qt，可单独测试。

同步流程（一次请求同时完成上传与下载）：
  1. 取出本机待上传的记录和设置（每批最多 200 条）
  2. POST /api/v1/sync，附带上次已拉取到的修订号 since_rev
  3. 服务器逐条裁决：接受 → 记下新修订号；拒绝 → 用服务器版本覆盖本机
  4. 写入服务器返回的增量数据（本机有未上传修改的日期不覆盖，留给下一轮由服务器裁决）
  5. 还有更多增量或待上传数据时继续下一轮

连接码：把“服务器地址 + 同步密钥 + 备用 IP”打包成一串文本，桌面端和安卓端都能粘贴识别。
"""

from __future__ import annotations

import base64
import gzip
import http.client
import json
import platform
import socket
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from core.database import DatabaseManager, now_ms

PROTOCOL_VERSION = 1
PUSH_BATCH = 200
PULL_LIMIT = 500
MAX_ROUNDS = 200
CONNECTION_CODE_PREFIX = "PH1-"


class SyncError(Exception):
    """同步失败，message 可直接展示给用户。"""


class SyncAuthError(SyncError):
    """同步密钥错误。"""


@dataclass
class SyncConfig:
    server_url: str
    token: str
    fallback_ip: str = ""

    @property
    def is_complete(self) -> bool:
        return bool(self.server_url and self.token)


@dataclass
class SyncResult:
    pushed: int = 0
    pulled: int = 0
    records_changed: bool = False
    settings_changed: bool = False
    invalid: list[tuple[str, str]] = field(default_factory=list)
    server_rev: int = 0
    resynced: bool = False

    @property
    def summary(self) -> str:
        parts = []
        if self.pushed:
            parts.append(f"上传 {self.pushed} 条")
        if self.pulled:
            parts.append(f"更新 {self.pulled} 条")
        if self.settings_changed:
            parts.append("设置已更新")
        if self.invalid:
            parts.append(f"{len(self.invalid)} 条未能上传")
        return "，".join(parts) if parts else "已是最新"


# ─────────────────────────────────────────────
# 连接码 / 地址
# ─────────────────────────────────────────────

def normalize_server_url(url: str) -> str:
    """补全协议、去掉末尾斜杠，只允许 https（本机调试允许 http://127.0.0.1）。"""
    url = (url or "").strip()
    if not url:
        return ""
    if "://" not in url:
        url = "https://" + url
    parts = urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise SyncError("服务器地址格式不正确，例如：https://example.com:28443")
    if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "10.0.2.2"):
        raise SyncError("为了保护账本数据，服务器地址必须使用 https://")
    netloc = parts.hostname if parts.port is None else f"{parts.hostname}:{parts.port}"
    if ":" in parts.hostname:  # IPv6
        netloc = f"[{parts.hostname}]" + ("" if parts.port is None else f":{parts.port}")
    return f"{parts.scheme}://{netloc}{parts.path.rstrip('/')}"


def make_connection_code(server_url: str, token: str, fallback_ip: str = "") -> str:
    payload = {"u": normalize_server_url(server_url), "k": token.strip()}
    if fallback_ip:
        payload["ip"] = fallback_ip.strip()
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return CONNECTION_CODE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def parse_connection_code(code: str) -> SyncConfig:
    text = "".join((code or "").split())
    if not text.upper().startswith(CONNECTION_CODE_PREFIX):
        raise SyncError("连接码应以 PH1- 开头，请完整复制")
    body = text[len(CONNECTION_CODE_PREFIX):]
    try:
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        payload = json.loads(raw.decode("utf-8"))
        return SyncConfig(
            server_url=normalize_server_url(payload["u"]),
            token=str(payload["k"]).strip(),
            fallback_ip=str(payload.get("ip", "")).strip(),
        )
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise SyncError("连接码无法识别，请检查是否复制完整") from None


# ─────────────────────────────────────────────
# HTTP 传输
# ─────────────────────────────────────────────

def _build_ssl_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    try:  # 打包时附带 certifi 根证书，避免 Win10 系统根证书不全导致校验失败
        import certifi

        context.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return context


class _FallbackHTTPSConnection(http.client.HTTPSConnection):
    """先尝试备用 IP，再尝试 DNS 解析结果；证书始终按域名校验。"""

    def __init__(self, host: str, port: int | None, fallback_ip: str, **kwargs):
        super().__init__(host, port, **kwargs)
        self._fallback_ip = fallback_ip

    def connect(self):
        targets = [t for t in (self._fallback_ip, self.host) if t]
        last_error: OSError | None = None
        sock = None
        for target in dict.fromkeys(targets):
            try:
                sock = socket.create_connection((target, self.port), self.timeout, self.source_address)
                break
            except OSError as exc:
                last_error = exc
        if sock is None:
            raise last_error or OSError("无法连接服务器")
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class SyncTransport:
    def __init__(self, config: SyncConfig, timeout: float = 20.0):
        self.config = config
        self.timeout = timeout
        parts = urlsplit(normalize_server_url(config.server_url))
        self._scheme = parts.scheme
        self._host = parts.hostname or ""
        self._port = parts.port or (443 if parts.scheme == "https" else 80)
        self._prefix = parts.path.rstrip("/")
        self._ssl_context = _build_ssl_context() if parts.scheme == "https" else None

    def _connection(self) -> http.client.HTTPConnection:
        if self._scheme == "https":
            return _FallbackHTTPSConnection(
                self._host, self._port, self.config.fallback_ip,
                timeout=self.timeout, context=self._ssl_context,
            )
        return http.client.HTTPConnection(self._host, self._port, timeout=self.timeout)

    def request(self, method: str, path: str, body: dict | None = None, auth: bool = True) -> dict:
        headers = {"Accept": "application/json", "Accept-Encoding": "gzip", "User-Agent": "ProfitHelper-Desktop"}
        if auth:
            headers["Authorization"] = f"Bearer {self.config.token}"
        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
            if len(data) > 2048:
                data = gzip.compress(data, compresslevel=6)
                headers["Content-Encoding"] = "gzip"

        conn = self._connection()
        try:
            conn.request(method, self._prefix + path, body=data, headers=headers)
            response = conn.getresponse()
            raw = response.read()
            if response.getheader("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
        except ssl.SSLCertVerificationError:
            raise SyncError("服务器证书校验失败，请确认服务器地址是否正确") from None
        except (socket.timeout, TimeoutError):
            raise SyncError("连接服务器超时，请检查网络") from None
        except socket.gaierror:
            raise SyncError("无法解析服务器地址，请检查网络或在连接码中提供备用 IP") from None
        except ConnectionRefusedError:
            raise SyncError("服务器拒绝连接，同步服务可能未启动") from None
        except (OSError, http.client.HTTPException) as exc:
            raise SyncError(f"网络连接失败：{exc}") from None
        finally:
            conn.close()

        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SyncError(f"服务器返回了无法识别的内容（HTTP {response.status}）") from None

        if response.status == 401:
            raise SyncAuthError(payload.get("message") or "同步密钥不正确")
        if response.status != 200 or not payload.get("ok", False):
            raise SyncError(payload.get("message") or f"服务器返回错误（HTTP {response.status}）")
        return payload


# ─────────────────────────────────────────────
# 同步引擎
# ─────────────────────────────────────────────

class SyncEngine:
    def __init__(self, db: DatabaseManager, app_version: str, platform_name: str = "windows",
                 transport_factory=SyncTransport):
        self.db = db
        self.app_version = app_version
        self.platform_name = platform_name
        self._transport_factory = transport_factory

    # ── 配置 ────────────────────────────────

    def load_config(self) -> SyncConfig:
        return SyncConfig(
            server_url=self.db.get_sync_meta("server_url"),
            token=self.db.get_sync_meta("token"),
            fallback_ip=self.db.get_sync_meta("fallback_ip"),
        )

    def save_config(self, config: SyncConfig) -> None:
        url = normalize_server_url(config.server_url)
        previous = self.load_config()
        if previous.server_url and previous.server_url != url:
            # 换了服务器：重新标记全部数据待上传，避免沿用旧服务器的修订号
            self.db.reset_sync_state()
        self.db.set_sync_metas({
            "server_url": url,
            "token": config.token.strip(),
            "fallback_ip": config.fallback_ip.strip(),
        })

    def is_configured(self) -> bool:
        return self.load_config().is_complete

    def device_name(self) -> str:
        return self.db.get_sync_meta("device_name") or platform.node() or "Windows 电脑"

    # ── 操作 ────────────────────────────────

    def test_connection(self, config: SyncConfig | None = None) -> dict:
        config = config or self.load_config()
        if not config.is_complete:
            raise SyncError("请先填写服务器地址和同步密钥")
        return self._transport_factory(config).request("GET", "/api/v1/status")

    def sync(self) -> SyncResult:
        config = self.load_config()
        if not config.is_complete:
            raise SyncError("尚未设置云同步")
        transport = self._transport_factory(config)
        self.db.backup_before_first_sync()

        result = SyncResult()
        invalid_dates: set[str] = set()
        settings_invalid = False

        for _ in range(MAX_ROUNDS):
            records = self.db.collect_dirty_records(PUSH_BATCH, exclude=invalid_dates)
            settings = None if settings_invalid else self.db.collect_dirty_settings()
            body = {
                "protocol": PROTOCOL_VERSION,
                "device": {
                    "id": self.db.get_sync_meta("device_id"),
                    "name": self.device_name(),
                    "platform": self.platform_name,
                    "app_version": self.app_version,
                },
                "ledger_id": self.db.get_sync_meta("ledger_id"),
                "since_rev": int(self.db.get_sync_meta("last_rev", "0") or 0),
                "client_time": now_ms(),
                "push": {"records": records, "settings": settings},
                "pull_limit": PULL_LIMIT,
            }
            try:
                response = transport.request("POST", "/api/v1/sync", body)
            except SyncError as exc:
                self.db.set_sync_metas({"last_error": str(exc), "last_error_at": str(now_ms())})
                raise

            if response.get("resync_required"):
                if result.resynced:
                    raise SyncError("服务器数据已重建，重新同步失败，请稍后再试")
                self.db.reset_sync_state()
                self.db.set_sync_meta("ledger_id", response.get("ledger_id", ""))
                result.resynced = True
                continue

            self.db.set_sync_meta("ledger_id", response.get("ledger_id", ""))
            pushed_by_date = {item["date"]: item for item in records}
            results = response.get("results") or {}

            for item in results.get("records") or []:
                pushed = pushed_by_date.get(item.get("date"))
                if pushed is None:
                    continue
                status = item.get("status")
                if status == "accepted":
                    self.db.mark_record_synced(pushed["date"], int(item["rev"]), pushed["modified_at"])
                    result.pushed += 1
                elif status == "rejected" and item.get("current"):
                    if self.db.apply_remote_record(item["current"], force_if_modified_at=pushed["modified_at"]):
                        result.records_changed = True
                        result.pulled += 1
                else:
                    invalid_dates.add(pushed["date"])
                    result.invalid.append((pushed["date"], item.get("message", "数据格式不正确")))

            settings_result = results.get("settings")
            if settings is not None and settings_result:
                if settings_result.get("status") == "accepted":
                    self.db.mark_settings_synced(int(settings_result["rev"]), settings["modified_at"])
                elif settings_result.get("status") == "rejected" and settings_result.get("current"):
                    if self.db.apply_remote_settings(settings_result["current"],
                                                     force_if_modified_at=settings["modified_at"]):
                        result.settings_changed = True
                else:
                    settings_invalid = True

            pull = response.get("pull") or {}
            for remote in pull.get("records") or []:
                if self.db.apply_remote_record(remote):
                    result.records_changed = True
                    result.pulled += 1
            if pull.get("settings"):
                if self.db.apply_remote_settings(pull["settings"]):
                    result.settings_changed = True

            self.db.set_sync_meta("last_rev", str(int(pull.get("next_since", body["since_rev"]))))
            result.server_rev = int(response.get("server_rev", 0))

            more_to_push = bool(self.db.collect_dirty_records(1, exclude=invalid_dates))
            more_settings = not settings_invalid and self.db.collect_dirty_settings() is not None
            if not pull.get("has_more") and not more_to_push and not more_settings:
                break
        else:
            raise SyncError("同步轮次过多，请稍后重试")

        self.db.set_sync_metas({
            "last_sync_at": str(now_ms()),
            "last_error": "",
            "last_result": result.summary,
        })
        return result


def describe_time(ms_text: str) -> str:
    """把毫秒时间戳格式化为“今天 18:32 / 10-28 09:10”。"""
    try:
        ms = int(ms_text)
    except (TypeError, ValueError):
        return ""
    if ms <= 0:
        return ""
    moment = time.localtime(ms / 1000)
    today = time.localtime()
    if (moment.tm_year, moment.tm_yday) == (today.tm_year, today.tm_yday):
        return time.strftime("今天 %H:%M", moment)
    return time.strftime("%m-%d %H:%M", moment)
