package com.homeapp.profithelper.data

import android.content.ContentValues
import android.content.Context
import android.database.Cursor
import android.database.sqlite.SQLiteDatabase
import android.database.sqlite.SQLiteOpenHelper
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FieldNode
import com.homeapp.profithelper.core.RawData
import java.io.File
import java.io.InputStream
import java.io.OutputStream
import java.util.UUID
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray
import org.json.JSONObject

data class DailyRecord(
    val date: String,
    val totalProfit: Double,
    val rawData: RawData,
    val note: String,
)

/** 本机账本。表结构与同步规则和桌面端 core/database.py 保持一致（删除的记录保留墓碑以便同步）。 */
class Ledger(private val context: Context, dbName: String = "ledger.db") {

    companion object {
        val SYNCED_CONFIG_KEYS = listOf("custom_field_tree", "custom_fields", "profit_formula")
        const val LEGACY_MODIFIED_AT = 1L
        /** 与同步服务器的限制一致，超出的数据无法上传。 */
        const val NOTE_MAX_CHARS = 4000
        private val DATE_RE = Regex("^\\d{4}-\\d{2}-\\d{2}$")
        fun nowMs(): Long = System.currentTimeMillis()

        fun isValidDate(text: String): Boolean =
            DATE_RE.matches(text) && runCatching { java.time.LocalDate.parse(text) }.isSuccess
    }

    private val helper = object : SQLiteOpenHelper(context, dbName, null, 1) {
        override fun onCreate(db: SQLiteDatabase) {
            db.execSQL(
                """CREATE TABLE records (
                    date TEXT PRIMARY KEY,
                    total_profit REAL NOT NULL DEFAULT 0,
                    raw_data TEXT NOT NULL DEFAULT '{}',
                    note TEXT NOT NULL DEFAULT '',
                    deleted INTEGER NOT NULL DEFAULT 0,
                    modified_at INTEGER NOT NULL DEFAULT 0,
                    base_rev INTEGER NOT NULL DEFAULT 0,
                    dirty INTEGER NOT NULL DEFAULT 0)"""
            )
            db.execSQL("CREATE TABLE config (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            db.execSQL("CREATE TABLE sync_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            putConfig(db, "custom_field_tree", FieldConfig.treeToJson(FieldConfig.DEFAULT_TREE))
            putConfig(db, "custom_fields", FieldConfig.fieldsToJson(FieldConfig.DEFAULT_TREE))
            putConfig(db, "profit_formula", FieldConfig.DEFAULT_FORMULA)
            for ((key, value) in mapOf(
                "device_id" to UUID.randomUUID().toString().replace("-", ""),
                "settings_dirty" to "0",
                "settings_modified_at" to "0",
                "settings_base_rev" to "0",
                "last_rev" to "0",
                "auto_sync" to "1",
            )) putMeta(db, key, value)
        }

        override fun onUpgrade(db: SQLiteDatabase, oldVersion: Int, newVersion: Int) = Unit
    }

    private val db: SQLiteDatabase get() = helper.writableDatabase

    private val _version = MutableStateFlow(0L)

    /** 数据版本号：本机或同步修改了数据后递增，界面据此重新读取。 */
    val version: StateFlow<Long> = _version.asStateFlow()

    private val localChangeListeners = mutableListOf<() -> Unit>()

    fun addLocalChangeListener(listener: () -> Unit) {
        localChangeListeners += listener
    }

    fun bumpVersion() {
        _version.value = _version.value + 1
    }

    private fun localChanged() {
        bumpVersion()
        localChangeListeners.forEach { it() }
    }

    // ─────────────────────────────────────────
    // 记录
    // ─────────────────────────────────────────

    private fun Cursor.toRecord() = DailyRecord(
        date = getString(0),
        totalProfit = getDouble(1),
        rawData = FieldConfig.parseRawData(getString(2)),
        note = getString(3) ?: "",
    )

    private fun queryRecords(where: String, args: Array<String>, suffix: String = "ORDER BY date ASC"): List<DailyRecord> {
        val result = mutableListOf<DailyRecord>()
        db.rawQuery("SELECT date, total_profit, raw_data, note FROM records WHERE deleted = 0 AND $where $suffix", args).use {
            while (it.moveToNext()) result += it.toRecord()
        }
        return result
    }

    fun getRecord(date: String): DailyRecord? = queryRecords("date = ?", arrayOf(date)).firstOrNull()

    fun getRange(start: String, end: String): List<DailyRecord> = queryRecords("date BETWEEN ? AND ?", arrayOf(start, end))

    fun getAll(): List<DailyRecord> = queryRecords("1 = 1", emptyArray())

    fun getRecent(limit: Int): List<DailyRecord> = queryRecords("1 = 1", emptyArray(), "ORDER BY date DESC LIMIT $limit")

    fun dateBounds(): Pair<String?, String?> {
        db.rawQuery("SELECT MIN(date), MAX(date) FROM records WHERE deleted = 0", null).use {
            it.moveToFirst()
            return (if (it.isNull(0)) null else it.getString(0)) to (if (it.isNull(1)) null else it.getString(1))
        }
    }

    private fun nextModifiedAt(date: String): Long {
        db.rawQuery("SELECT modified_at FROM records WHERE date = ?", arrayOf(date)).use {
            val previous = if (it.moveToFirst()) it.getLong(0) else 0L
            return maxOf(nowMs(), previous + 1)
        }
    }

    /** 新增或更新一天的记录。备注留空时保留这一天原有的备注（与桌面端一致）。 */
    fun upsertRecord(date: String, totalProfit: Double, rawData: RawData, note: String) {
        db.beginTransaction()
        try {
            val existingNote = getRecord(date)?.note ?: ""
            val finalNote = if (note.isBlank()) existingNote else note
            val modifiedAt = nextModifiedAt(date)
            val values = ContentValues().apply {
                put("total_profit", totalProfit)
                put("raw_data", FieldConfig.rawDataToJson(rawData))
                put("note", finalNote)
                put("deleted", 0)
                put("modified_at", modifiedAt)
                put("dirty", 1)
            }
            if (db.update("records", values, "date = ?", arrayOf(date)) == 0) {
                values.put("date", date)
                values.put("base_rev", 0)
                db.insertOrThrow("records", null, values)
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
        localChanged()
    }

    fun deleteRecords(dates: Collection<String>): Int {
        var count = 0
        db.beginTransaction()
        try {
            for (date in dates) {
                val values = ContentValues().apply {
                    put("deleted", 1)
                    put("dirty", 1)
                    put("modified_at", nextModifiedAt(date))
                    put("raw_data", "{}")
                    put("note", "")
                    put("total_profit", 0.0)
                }
                count += db.update("records", values, "date = ? AND deleted = 0", arrayOf(date))
            }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
        if (count > 0) localChanged()
        return count
    }

    // ─────────────────────────────────────────
    // 配置
    // ─────────────────────────────────────────

    private fun putConfig(target: SQLiteDatabase, key: String, value: String) {
        target.insertWithOnConflict("config", null, ContentValues().apply {
            put("key", key)
            put("value", value)
        }, SQLiteDatabase.CONFLICT_REPLACE)
    }

    private fun putMeta(target: SQLiteDatabase, key: String, value: String) {
        target.insertWithOnConflict("sync_meta", null, ContentValues().apply {
            put("key", key)
            put("value", value)
        }, SQLiteDatabase.CONFLICT_REPLACE)
    }

    fun getConfig(key: String, default: String = ""): String {
        db.rawQuery("SELECT value FROM config WHERE key = ?", arrayOf(key)).use {
            return if (it.moveToFirst()) it.getString(0) else default
        }
    }

    fun fieldTree(): List<FieldNode> = FieldConfig.loadTree(getConfig("custom_field_tree").ifBlank { getConfig("custom_fields") })

    fun profitFormula(): String = getConfig("profit_formula", "营业额 - 成本")

    fun setConfigs(values: Map<String, String>) {
        var syncedChanged = false
        db.beginTransaction()
        try {
            for ((key, value) in values) {
                val old = db.rawQuery("SELECT value FROM config WHERE key = ?", arrayOf(key)).use {
                    if (it.moveToFirst()) it.getString(0) else null
                }
                putConfig(db, key, value)
                if (key in SYNCED_CONFIG_KEYS && old != value) syncedChanged = true
            }
            if (syncedChanged) markSettingsDirty()
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
        if (syncedChanged) localChanged() else bumpVersion()
    }

    private fun markSettingsDirty() {
        val previous = getMeta("settings_modified_at").toLongOrNull() ?: 0L
        putMeta(db, "settings_dirty", "1")
        putMeta(db, "settings_modified_at", maxOf(nowMs(), previous + 1).toString())
    }

    // ─────────────────────────────────────────
    // 同步元数据与读写（与桌面端 DatabaseManager 对应）
    // ─────────────────────────────────────────

    fun getMeta(key: String, default: String = ""): String {
        db.rawQuery("SELECT value FROM sync_meta WHERE key = ?", arrayOf(key)).use {
            return if (it.moveToFirst()) it.getString(0).ifEmpty { default } else default
        }
    }

    fun setMetas(values: Map<String, String>) {
        db.beginTransaction()
        try {
            values.forEach { (key, value) -> putMeta(db, key, value) }
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun countPendingChanges(): Int {
        var count = db.rawQuery("SELECT COUNT(*) FROM records WHERE dirty = 1", null).use { it.moveToFirst(); it.getInt(0) }
        if (getMeta("settings_dirty") == "1") count += 1
        return count
    }

    /** 待上传记录中日期格式不正确的（只可能来自外部导入），这些记录不能上传。 */
    fun invalidDirtyDates(): List<String> {
        db.rawQuery("SELECT date FROM records WHERE dirty = 1", null).use {
            return buildList {
                while (it.moveToNext()) {
                    val date = it.getString(0)
                    if (!isValidDate(date)) add(date)
                }
            }
        }
    }

    fun collectDirtyRecords(limit: Int, exclude: Set<String> = emptySet()): List<JSONObject> {
        val result = mutableListOf<JSONObject>()
        db.rawQuery(
            "SELECT date, total_profit, raw_data, note, deleted, modified_at, base_rev FROM records WHERE dirty = 1 ORDER BY modified_at ASC",
            null,
        ).use {
            while (it.moveToNext() && result.size < limit) {
                val date = it.getString(0)
                if (date in exclude) continue
                val deleted = it.getInt(4) == 1
                val change = JSONObject()
                    .put("date", date)
                    .put("modified_at", it.getLong(5))
                    .put("base_rev", it.getLong(6))
                    .put("deleted", deleted)
                if (!deleted) {
                    change.put("total_profit", FieldConfig.toDouble(it.getDouble(1)))
                    change.put("raw_data", FieldConfig.rawDataToJsonObject(FieldConfig.parseRawData(it.getString(2))))
                    change.put("note", it.getString(3) ?: "")
                }
                result += change
            }
        }
        return result
    }

    fun collectDirtySettings(): JSONObject? {
        if (getMeta("settings_dirty") != "1") return null
        val tree = fieldTree()
        val data = JSONObject()
            .put("custom_field_tree", JSONArray(FieldConfig.treeToJson(tree)))
            .put("custom_fields", JSONArray(FieldConfig.fieldsToJson(tree)))
            .put("profit_formula", profitFormula())
        return JSONObject()
            .put("data", data)
            .put("modified_at", getMeta("settings_modified_at").toLongOrNull() ?: 0L)
            .put("base_rev", getMeta("settings_base_rev").toLongOrNull() ?: 0L)
    }

    fun markRecordSynced(date: String, rev: Long, pushedModifiedAt: Long) {
        db.execSQL(
            "UPDATE records SET base_rev = ?, dirty = CASE WHEN modified_at = ? THEN 0 ELSE dirty END WHERE date = ?",
            arrayOf<Any>(rev, pushedModifiedAt, date),
        )
    }

    fun markSettingsSynced(rev: Long, pushedModifiedAt: Long) {
        val values = mutableMapOf("settings_base_rev" to rev.toString())
        if ((getMeta("settings_modified_at").toLongOrNull() ?: 0L) == pushedModifiedAt) values["settings_dirty"] = "0"
        setMetas(values)
    }

    /** 写入服务器版本；本机有未上传修改时不覆盖，除非服务器明确拒绝了这次上传。返回本机数据是否变化。 */
    fun applyRemoteRecord(record: JSONObject, forceIfModifiedAt: Long? = null): Boolean {
        val date = record.getString("date")
        db.beginTransaction()
        try {
            val state = db.rawQuery("SELECT modified_at, dirty FROM records WHERE date = ?", arrayOf(date)).use {
                if (it.moveToFirst()) it.getLong(0) to (it.getInt(1) == 1) else null
            }
            if (state != null && state.second && (forceIfModifiedAt == null || state.first != forceIfModifiedAt)) {
                return false
            }
            val before = getRecord(date)
            val deleted = record.optBoolean("deleted", false)
            val raw = if (deleted) emptyMap() else parseRemoteRaw(record.optJSONObject("raw_data"))
            val profit = if (deleted) 0.0 else FieldConfig.toDouble(record.opt("total_profit"))
            val note = if (deleted) "" else record.optString("note", "")
            val values = ContentValues().apply {
                put("date", date)
                put("total_profit", profit)
                put("raw_data", FieldConfig.rawDataToJson(raw))
                put("note", note)
                put("deleted", if (deleted) 1 else 0)
                put("modified_at", record.optLong("modified_at", 0L))
                put("base_rev", record.getLong("rev"))
                put("dirty", 0)
            }
            db.insertWithOnConflict("records", null, values, SQLiteDatabase.CONFLICT_REPLACE)
            db.setTransactionSuccessful()
            return if (deleted) before != null else before == null ||
                before.totalProfit != profit || before.note != note || before.rawData != raw
        } finally {
            db.endTransaction()
        }
    }

    private fun parseRemoteRaw(obj: JSONObject?): RawData = FieldConfig.parseRawData(obj?.toString())

    fun applyRemoteSettings(settings: JSONObject, forceIfModifiedAt: Long? = null): Boolean {
        db.beginTransaction()
        try {
            if (getMeta("settings_dirty") == "1") {
                val local = getMeta("settings_modified_at").toLongOrNull() ?: 0L
                if (forceIfModifiedAt == null || local != forceIfModifiedAt) return false
            }
            val data = settings.optJSONObject("data") ?: JSONObject()
            val newValues = mapOf(
                "custom_field_tree" to (data.optJSONArray("custom_field_tree") ?: JSONArray()).toString(),
                "custom_fields" to (data.optJSONArray("custom_fields") ?: JSONArray()).toString(),
                "profit_formula" to data.optString("profit_formula", ""),
            )
            var changed = false
            for ((key, value) in newValues) {
                if (getConfig(key, "\u0000") != value) changed = true
                putConfig(db, key, value)
            }
            putMeta(db, "settings_dirty", "0")
            putMeta(db, "settings_base_rev", settings.optLong("rev", 0L).toString())
            putMeta(db, "settings_modified_at", settings.optLong("modified_at", 0L).toString())
            db.setTransactionSuccessful()
            return changed
        } finally {
            db.endTransaction()
        }
    }

    /** 服务器被重建或恢复旧备份：把本机全部数据重新标记为待上传。 */
    fun resetSyncState() {
        db.beginTransaction()
        try {
            db.execSQL("UPDATE records SET base_rev = 0, dirty = 1")
            val settingsModified = getMeta("settings_modified_at").toLongOrNull() ?: 0L
            putMeta(db, "last_rev", "0")
            putMeta(db, "ledger_id", "")
            putMeta(db, "settings_base_rev", "0")
            putMeta(db, "settings_dirty", if (settingsModified > 0) "1" else "0")
            db.setTransactionSuccessful()
        } finally {
            db.endTransaction()
        }
    }

    fun forgetServer() {
        resetSyncState()
        db.execSQL(
            "DELETE FROM sync_meta WHERE key IN ('server_url', 'token', 'fallback_ip', 'last_sync_at', 'last_error', 'last_result')"
        )
    }

    // ─────────────────────────────────────────
    // 导入 / 导出（与桌面端 family_ledger.db 兼容）
    // ─────────────────────────────────────────

    fun exportDesktopDb(output: OutputStream): Int {
        val file = File(context.cacheDir, "export-${nowMs()}.db")
        file.delete()
        val records = getAll()
        val target = SQLiteDatabase.openOrCreateDatabase(file, null)
        try {
            target.execSQL("CREATE TABLE DailyRecords (date TEXT PRIMARY KEY, total_profit REAL DEFAULT 0.0, raw_data TEXT DEFAULT '{}', note TEXT DEFAULT '')")
            target.execSQL("CREATE TABLE Config (key TEXT PRIMARY KEY, value TEXT DEFAULT '')")
            target.beginTransaction()
            try {
                for (record in records) {
                    target.insert("DailyRecords", null, ContentValues().apply {
                        put("date", record.date)
                        put("total_profit", record.totalProfit)
                        put("raw_data", FieldConfig.rawDataToJson(record.rawData))
                        put("note", record.note)
                    })
                }
                db.rawQuery("SELECT key, value FROM config", null).use {
                    while (it.moveToNext()) {
                        target.insert("Config", null, ContentValues().apply {
                            put("key", it.getString(0))
                            put("value", it.getString(1))
                        })
                    }
                }
                target.setTransactionSuccessful()
            } finally {
                target.endTransaction()
            }
        } finally {
            target.close()
        }
        file.inputStream().use { it.copyTo(output) }
        file.delete()
        File(file.path + "-journal").delete()
        return records.size
    }

    /** 导入桌面端或安卓端导出的账本，替换本机全部记录和设置，并同步到其他设备。 */
    fun importDesktopDb(input: InputStream): Pair<Int, Int> {
        val file = File(context.cacheDir, "import-${nowMs()}.db")
        file.outputStream().use { input.copyTo(it) }
        try {
            val source = SQLiteDatabase.openDatabase(
                file.path, null, SQLiteDatabase.OPEN_READONLY or SQLiteDatabase.NO_LOCALIZED_COLLATORS,
            )
            val records = mutableListOf<DailyRecord>()
            val configs = mutableListOf<Pair<String, String>>()
            source.use { src ->
                val tables = src.rawQuery("SELECT name FROM sqlite_master WHERE type = 'table'", null).use {
                    buildSet { while (it.moveToNext()) add(it.getString(0)) }
                }
                val missing = listOf("DailyRecords", "Config").filter { it !in tables }
                require(missing.isEmpty()) { "导入文件缺少表：${missing.joinToString()}" }
                val columns = src.rawQuery("PRAGMA table_info(DailyRecords)", null).use {
                    buildSet { while (it.moveToNext()) add(it.getString(1)) }
                }
                val missingColumns = listOf("date", "total_profit", "raw_data", "note").filter { it !in columns }
                require(missingColumns.isEmpty()) { "DailyRecords 缺少字段：${missingColumns.joinToString()}" }
                src.rawQuery("SELECT date, total_profit, raw_data, note FROM DailyRecords ORDER BY date", null).use {
                    while (it.moveToNext()) {
                        val date = it.getString(0) ?: ""
                        require(isValidDate(date)) { "导入文件中有无效日期：$date（应为 YYYY-MM-DD），未做任何修改。" }
                        val rawText = it.getString(2) ?: "{}"
                        try {
                            JSONObject(rawText)
                        } catch (e: Exception) {
                            throw IllegalArgumentException("${it.getString(0)} 的 raw_data 不是合法 JSON。")
                        }
                        records += DailyRecord(it.getString(0), it.getDouble(1), FieldConfig.parseRawData(rawText), it.getString(3) ?: "")
                    }
                }
                src.rawQuery("SELECT key, value FROM Config", null).use {
                    while (it.moveToNext()) configs += it.getString(0) to (it.getString(1) ?: "")
                }
            }

            db.beginTransaction()
            try {
                val now = nowMs()
                val imported = records.map { it.date }.toSet()
                val previous = db.rawQuery("SELECT date FROM records WHERE deleted = 0", null).use {
                    buildSet { while (it.moveToNext()) add(it.getString(0)) }
                }
                for (record in records) {
                    val values = ContentValues().apply {
                        put("total_profit", record.totalProfit)
                        put("raw_data", FieldConfig.rawDataToJson(record.rawData))
                        put("note", record.note)
                        put("deleted", 0)
                        put("dirty", 1)
                        put("modified_at", maxOf(now, nextModifiedAt(record.date)))
                    }
                    if (db.update("records", values, "date = ?", arrayOf(record.date)) == 0) {
                        values.put("date", record.date)
                        values.put("base_rev", 0)
                        db.insertOrThrow("records", null, values)
                    }
                }
                for (date in previous - imported) {
                    db.update("records", ContentValues().apply {
                        put("deleted", 1)
                        put("dirty", 1)
                        put("modified_at", maxOf(now, nextModifiedAt(date)))
                        put("raw_data", "{}")
                        put("note", "")
                        put("total_profit", 0.0)
                    }, "date = ?", arrayOf(date))
                }
                db.delete("config", null, null)
                configs.forEach { (key, value) -> putConfig(db, key, value) }
                if (configs.none { it.first == "custom_field_tree" } && configs.none { it.first == "custom_fields" }) {
                    putConfig(db, "custom_field_tree", FieldConfig.treeToJson(FieldConfig.DEFAULT_TREE))
                }
                if (configs.none { it.first == "profit_formula" }) putConfig(db, "profit_formula", FieldConfig.DEFAULT_FORMULA)
                markSettingsDirty()
                db.setTransactionSuccessful()
            } finally {
                db.endTransaction()
            }
            localChanged()
            return records.size to configs.size
        } finally {
            file.delete()
        }
    }
}
