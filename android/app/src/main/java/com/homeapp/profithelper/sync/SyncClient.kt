package com.homeapp.profithelper.sync

import com.homeapp.profithelper.data.Ledger
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.ConnectException
import java.net.InetAddress
import java.net.SocketTimeoutException
import java.net.URI
import java.net.UnknownHostException
import java.util.Base64
import java.util.concurrent.TimeUnit
import java.util.zip.GZIPOutputStream
import javax.net.ssl.SSLException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException
import okhttp3.Dns
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject

/** 同步失败，message 可直接展示给用户。 */
open class SyncException(message: String) : Exception(message)

class SyncAuthException(message: String) : SyncException(message)

data class SyncConfig(val serverUrl: String, val token: String, val fallbackIp: String = "") {
    val isComplete: Boolean get() = serverUrl.isNotBlank() && token.isNotBlank()
}

data class SyncResult(
    var pushed: Int = 0,
    var pulled: Int = 0,
    var recordsChanged: Boolean = false,
    var settingsChanged: Boolean = false,
    val invalid: MutableList<Pair<String, String>> = mutableListOf(),
    var resynced: Boolean = false,
) {
    val summary: String
        get() {
            val parts = mutableListOf<String>()
            if (pushed > 0) parts += "上传 $pushed 条"
            if (pulled > 0) parts += "更新 $pulled 条"
            if (settingsChanged) parts += "设置已更新"
            if (invalid.isNotEmpty()) parts += "${invalid.size} 条未能上传"
            return if (parts.isEmpty()) "已是最新" else parts.joinToString("，")
        }
}

/** 连接码：PH1- + base64url(JSON {"u": 地址, "k": 密钥, "ip": 备用 IP})，与桌面端完全相同。 */
object ConnectionCode {
    private const val PREFIX = "PH1-"
    private val LOCAL_HOSTS = setOf("127.0.0.1", "localhost", "10.0.2.2")

    fun normalizeServerUrl(input: String): String {
        var url = input.trim()
        if (url.isEmpty()) return ""
        if ("://" !in url) url = "https://$url"
        val uri = try {
            URI(url)
        } catch (e: Exception) {
            throw SyncException("服务器地址格式不正确，例如：https://example.com:28443")
        }
        val scheme = uri.scheme?.lowercase()
        val host = uri.host
        if ((scheme != "https" && scheme != "http") || host.isNullOrEmpty()) {
            throw SyncException("服务器地址格式不正确，例如：https://example.com:28443")
        }
        if (scheme == "http" && host !in LOCAL_HOSTS) throw SyncException("为了保护账本数据，服务器地址必须使用 https://")
        val port = if (uri.port == -1) "" else ":${uri.port}"
        return "$scheme://$host$port${(uri.rawPath ?: "").trimEnd('/')}"
    }

    fun make(serverUrl: String, token: String, fallbackIp: String = ""): String {
        val payload = JSONObject().put("u", normalizeServerUrl(serverUrl)).put("k", token.trim())
        if (fallbackIp.isNotBlank()) payload.put("ip", fallbackIp.trim())
        val raw = payload.toString().replace("\\/", "/").toByteArray(Charsets.UTF_8)
        return PREFIX + Base64.getUrlEncoder().withoutPadding().encodeToString(raw)
    }

    fun parse(code: String): SyncConfig {
        val text = code.filterNot { it.isWhitespace() }
        if (!text.uppercase().startsWith(PREFIX)) throw SyncException("连接码应以 PH1- 开头，请完整复制")
        return try {
            val body = text.substring(PREFIX.length)
            val json = JSONObject(String(Base64.getUrlDecoder().decode(body), Charsets.UTF_8))
            SyncConfig(
                serverUrl = normalizeServerUrl(json.getString("u")),
                token = json.getString("k").trim(),
                fallbackIp = json.optString("ip", "").trim(),
            )
        } catch (e: SyncException) {
            throw e
        } catch (e: Exception) {
            throw SyncException("连接码无法识别，请检查是否复制完整")
        }
    }
}

/** 先尝试备用 IP，再尝试系统 DNS 解析结果；证书始终按域名校验（与桌面端一致）。 */
private class FallbackDns(private val fallbackIp: String) : Dns {
    override fun lookup(hostname: String): List<InetAddress> {
        val result = mutableListOf<InetAddress>()
        if (fallbackIp.isNotBlank()) runCatching { InetAddress.getByName(fallbackIp) }.getOrNull()?.let { result += it }
        runCatching { Dns.SYSTEM.lookup(hostname) }.getOrNull()?.forEach { if (it !in result) result += it }
        if (result.isEmpty()) throw UnknownHostException(hostname)
        return result
    }
}

class SyncTransport(private val config: SyncConfig) {
    private val baseUrl = ConnectionCode.normalizeServerUrl(config.serverUrl)
    private val client = OkHttpClient.Builder()
        .connectTimeout(10, TimeUnit.SECONDS)
        .readTimeout(30, TimeUnit.SECONDS)
        .writeTimeout(30, TimeUnit.SECONDS)
        .dns(FallbackDns(config.fallbackIp))
        .retryOnConnectionFailure(true)
        .build()

    fun request(method: String, path: String, body: JSONObject? = null): JSONObject {
        val builder = Request.Builder()
            .url(baseUrl + path)
            .header("Authorization", "Bearer ${config.token}")
            .header("Accept", "application/json")
            .header("User-Agent", "ProfitHelper-Android")
        if (body != null) {
            var bytes = body.toString().toByteArray(Charsets.UTF_8)
            if (bytes.size > 2048) {
                val buffer = ByteArrayOutputStream()
                GZIPOutputStream(buffer).use { it.write(bytes) }
                bytes = buffer.toByteArray()
                builder.header("Content-Encoding", "gzip")
            }
            builder.method(method, bytes.toRequestBody("application/json; charset=utf-8".toMediaType()))
        } else {
            builder.method(method, null)
        }

        val (status, text) = try {
            client.newCall(builder.build()).execute().use { it.code to (it.body?.string() ?: "") }
        } catch (e: SSLPeerUnverifiedException) {
            throw SyncException("服务器证书校验失败，请确认服务器地址是否正确")
        } catch (e: SSLHandshakeException) {
            throw SyncException("服务器证书校验失败，请确认服务器地址是否正确")
        } catch (e: SocketTimeoutException) {
            throw SyncException("连接服务器超时，请检查网络")
        } catch (e: UnknownHostException) {
            throw SyncException("无法解析服务器地址，请检查网络或在连接码中提供备用 IP")
        } catch (e: ConnectException) {
            throw SyncException("无法连接服务器，请检查网络")
        } catch (e: SSLException) {
            throw SyncException("安全连接失败：${e.message ?: "未知原因"}")
        } catch (e: IOException) {
            throw SyncException("网络连接失败：${e.message ?: "未知原因"}")
        }

        val payload = try {
            if (text.isEmpty()) JSONObject() else JSONObject(text)
        } catch (e: Exception) {
            throw SyncException("服务器返回了无法识别的内容（HTTP $status）")
        }
        if (status == 401) throw SyncAuthException(payload.optString("message", "同步密钥不正确"))
        if (status != 200 || !payload.optBoolean("ok", false)) {
            throw SyncException(payload.optString("message").ifEmpty { "服务器返回错误（HTTP $status）" })
        }
        return payload
    }
}

/** 同步引擎 —— 与桌面端 core/sync_client.py 的 SyncEngine 逐步对应。 */
class SyncEngine(
    private val ledger: Ledger,
    private val appVersion: String,
    private val deviceName: () -> String,
) {
    companion object {
        const val PROTOCOL_VERSION = 1
        const val PUSH_BATCH = 200
        const val PULL_LIMIT = 500
        const val MAX_ROUNDS = 200
    }

    fun loadConfig() = SyncConfig(
        serverUrl = ledger.getMeta("server_url"),
        token = ledger.getMeta("token"),
        fallbackIp = ledger.getMeta("fallback_ip"),
    )

    fun saveConfig(config: SyncConfig) {
        val url = ConnectionCode.normalizeServerUrl(config.serverUrl)
        val previous = loadConfig()
        if (previous.serverUrl.isNotEmpty() && previous.serverUrl != url) ledger.resetSyncState()
        ledger.setMetas(mapOf("server_url" to url, "token" to config.token.trim(), "fallback_ip" to config.fallbackIp.trim()))
    }

    fun isConfigured() = loadConfig().isComplete

    fun testConnection(config: SyncConfig = loadConfig()): JSONObject {
        if (!config.isComplete) throw SyncException("请先填写服务器地址和同步密钥")
        return SyncTransport(config).request("GET", "/api/v1/status")
    }

    fun sync(): SyncResult {
        val config = loadConfig()
        if (!config.isComplete) throw SyncException("尚未设置云同步")
        val transport = SyncTransport(config)
        val result = SyncResult()
        val invalidDates = mutableSetOf<String>()
        var settingsInvalid = false
        for (badDate in ledger.invalidDirtyDates()) {
            invalidDates += badDate
            result.invalid += badDate to "日期格式不正确，无法同步"
        }

        for (round in 0 until MAX_ROUNDS) {
            val records = ledger.collectDirtyRecords(PUSH_BATCH, invalidDates)
            val settings = if (settingsInvalid) null else ledger.collectDirtySettings()
            val sinceRev = ledger.getMeta("last_rev", "0").toLongOrNull() ?: 0L
            val body = JSONObject()
                .put("protocol", PROTOCOL_VERSION)
                .put("device", JSONObject()
                    .put("id", ledger.getMeta("device_id"))
                    .put("name", deviceName())
                    .put("platform", "android")
                    .put("app_version", appVersion))
                .put("ledger_id", ledger.getMeta("ledger_id"))
                .put("since_rev", sinceRev)
                .put("client_time", Ledger.nowMs())
                .put("push", JSONObject().put("records", JSONArray(records)).put("settings", settings ?: JSONObject.NULL))
                .put("pull_limit", PULL_LIMIT)

            val response = try {
                transport.request("POST", "/api/v1/sync", body)
            } catch (e: SyncException) {
                ledger.setMetas(mapOf("last_error" to (e.message ?: ""), "last_error_at" to Ledger.nowMs().toString()))
                throw e
            }

            if (response.optBoolean("resync_required", false)) {
                if (result.resynced) throw SyncException("服务器数据已重建，重新同步失败，请稍后再试")
                ledger.resetSyncState()
                ledger.setMetas(mapOf("ledger_id" to response.optString("ledger_id", "")))
                result.resynced = true
                continue
            }
            ledger.setMetas(mapOf("ledger_id" to response.optString("ledger_id", "")))

            val pushedByDate = records.associateBy { it.getString("date") }
            val results = response.optJSONObject("results") ?: JSONObject()
            val recordResults = results.optJSONArray("records") ?: JSONArray()
            for (index in 0 until recordResults.length()) {
                val item = recordResults.getJSONObject(index)
                val pushed = pushedByDate[item.optString("date")] ?: continue
                when (item.optString("status")) {
                    "accepted" -> {
                        ledger.markRecordSynced(pushed.getString("date"), item.getLong("rev"), pushed.getLong("modified_at"))
                        result.pushed++
                    }
                    "rejected" -> {
                        val current = item.optJSONObject("current")
                        if (current != null && ledger.applyRemoteRecord(current, pushed.getLong("modified_at"))) {
                            result.recordsChanged = true
                            result.pulled++
                        }
                    }
                    else -> {
                        invalidDates += pushed.getString("date")
                        result.invalid += pushed.getString("date") to item.optString("message", "数据格式不正确")
                    }
                }
            }

            val settingsResult = results.optJSONObject("settings")
            if (settings != null && settingsResult != null) {
                when (settingsResult.optString("status")) {
                    "accepted" -> ledger.markSettingsSynced(settingsResult.getLong("rev"), settings.getLong("modified_at"))
                    "rejected" -> settingsResult.optJSONObject("current")?.let {
                        if (ledger.applyRemoteSettings(it, settings.getLong("modified_at"))) result.settingsChanged = true
                    }
                    else -> settingsInvalid = true
                }
            }

            val pull = response.optJSONObject("pull") ?: JSONObject()
            val pulled = pull.optJSONArray("records") ?: JSONArray()
            for (index in 0 until pulled.length()) {
                if (ledger.applyRemoteRecord(pulled.getJSONObject(index))) {
                    result.recordsChanged = true
                    result.pulled++
                }
            }
            pull.optJSONObject("settings")?.let {
                if (ledger.applyRemoteSettings(it)) result.settingsChanged = true
            }
            ledger.setMetas(mapOf("last_rev" to pull.optLong("next_since", sinceRev).toString()))

            val morePush = ledger.collectDirtyRecords(1, invalidDates).isNotEmpty()
            val moreSettings = !settingsInvalid && ledger.collectDirtySettings() != null
            if (!pull.optBoolean("has_more", false) && !morePush && !moreSettings) {
                ledger.setMetas(mapOf(
                    "last_sync_at" to Ledger.nowMs().toString(),
                    "last_error" to "",
                    "last_result" to result.summary,
                ))
                if (result.recordsChanged || result.settingsChanged) ledger.bumpVersion()
                return result
            }
        }
        throw SyncException("同步轮次过多，请稍后重试")
    }
}
