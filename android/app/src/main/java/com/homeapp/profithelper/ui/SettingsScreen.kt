package com.homeapp.profithelper.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.DrawableRes
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.input.VisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.homeapp.profithelper.BuildConfig
import com.homeapp.profithelper.ProfitApp
import com.homeapp.profithelper.R
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.core.FieldNode
import com.homeapp.profithelper.core.FormulaEngine
import com.homeapp.profithelper.sync.ConnectionCode
import com.homeapp.profithelper.sync.SyncConfig
import com.homeapp.profithelper.sync.SyncException
import com.homeapp.profithelper.sync.SyncTone
import com.homeapp.profithelper.ui.theme.Palette
import com.homeapp.profithelper.ui.theme.TextStyles
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private data class NameDialog(val title: String, val initial: String, val parent: Int?, val child: Int?, val adding: Boolean)

private fun signature(tree: List<FieldNode>, formula: String) = FieldConfig.treeToJson(FieldConfig.normalize(tree)) + "\u0000" + formula.trim()

private fun formulaProblem(formula: String, tree: List<FieldNode>): Pair<Tone, String> {
    if (formula.isBlank()) return Tone.DANGER to "公式不能为空"
    FormulaEngine.validate(formula)?.let { return Tone.DANGER to it }
    val used = FormulaEngine.extractVariables(formula).distinct()
    val names = FieldConfig.flatten(tree).toSet()
    val undefined = used.filter { it !in names }
    if (undefined.isNotEmpty()) return Tone.WARNING to "引用了不存在的分类：${undefined.joinToString("、")}，请先添加分类或修改公式"
    return Tone.SUCCESS to "公式有效，引用了 ${used.size} 个分类"
}

private fun clipboardText(context: Context): String {
    val clipboard = context.getSystemService(ClipboardManager::class.java)
    return clipboard?.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString() ?: ""
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SettingsScreen(app: ProfitApp) {
    val ledger = app.ledger
    val sync = app.sync
    val version by ledger.version.collectAsState()
    val status by sync.status.collectAsState()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var tree by remember { mutableStateOf(ledger.fieldTree()) }
    var formula by remember { mutableStateOf(TextFieldValue(ledger.profitFormula())) }
    var savedSignature by remember { mutableStateOf(signature(ledger.fieldTree(), ledger.profitFormula())) }
    val dirty = signature(tree, formula.text) != savedSignature

    fun reloadSettings() {
        tree = ledger.fieldTree()
        formula = TextFieldValue(ledger.profitFormula())
        savedSignature = signature(tree, formula.text)
    }
    LaunchedEffect(version) { if (!dirty) reloadSettings() }

    val saved = remember(version) { sync.engine.loadConfig() }
    var code by remember { mutableStateOf("") }
    var serverUrl by remember { mutableStateOf(saved.serverUrl.ifBlank { BuildConfig.DEFAULT_SERVER_URL }) }
    var token by remember { mutableStateOf(saved.token) }
    var fallbackIp by remember { mutableStateOf(saved.fallbackIp.ifBlank { BuildConfig.DEFAULT_FALLBACK_IP }) }
    var deviceName by remember { mutableStateOf(app.deviceName()) }
    var showManual by remember { mutableStateOf(!sync.configured) }
    var tokenVisible by remember { mutableStateOf(false) }
    var resultMessage by remember { mutableStateOf<Pair<String, Tone>?>(null) }
    var autoSync by remember { mutableStateOf(sync.autoEnabled) }
    var testing by remember { mutableStateOf(false) }
    var confirmFirstSync by remember { mutableStateOf<SyncConfig?>(null) }
    var confirmDisconnect by remember { mutableStateOf(false) }
    var nameDialog by remember { mutableStateOf<NameDialog?>(null) }
    var deleteTarget by remember { mutableStateOf<Pair<Int, Int?>?>(null) }
    var expanded by remember { mutableStateOf(setOf<String>()) }
    var importUri by remember { mutableStateOf<Uri?>(null) }

    fun formConfig(): SyncConfig? = try {
        val url = ConnectionCode.normalizeServerUrl(serverUrl)
        if (url.isEmpty() || token.isBlank()) {
            resultMessage = "请先粘贴连接码并点“识别连接码”，或手动填写服务器信息" to Tone.WARNING
            null
        } else {
            SyncConfig(url, token.trim(), fallbackIp.trim())
        }
    } catch (e: SyncException) {
        resultMessage = (e.message ?: "服务器地址格式不正确") to Tone.DANGER
        null
    }

    fun saveAndSync(config: SyncConfig) {
        sync.saveConfig(config, deviceName)
        sync.syncNow()
        resultMessage = "已保存，正在同步…" to Tone.ACCENT
    }

    fun applyCode(text: String) {
        try {
            val parsed = ConnectionCode.parse(text)
            serverUrl = parsed.serverUrl
            token = parsed.token
            fallbackIp = parsed.fallbackIp
            code = ""
            resultMessage = "已识别：${parsed.serverUrl}，点“保存并同步”即可开始" to Tone.SUCCESS
        } catch (e: SyncException) {
            resultMessage = (e.message ?: "连接码无法识别") to Tone.DANGER
        }
    }

    fun renameInFormula(old: String, new: String) {
        val pattern = Regex("(?<![\\p{L}\\p{N}_])" + Regex.escape(old) + "(?![\\p{L}\\p{N}_])")
        val updated = pattern.replace(formula.text) { new }
        if (updated != formula.text) {
            formula = TextFieldValue(updated, TextRange(updated.length))
            UiEvents.show("公式中的「$old」已同步改为「$new」")
        }
    }

    fun insertFormula(text: String) {
        val selection = formula.selection
        val newText = formula.text.replaceRange(selection.min, selection.max, text)
        formula = TextFieldValue(newText, TextRange(selection.min + text.length))
    }

    val exportLauncher = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        if (uri != null) {
            scope.launch(Dispatchers.IO) {
                runCatching { context.contentResolver.openOutputStream(uri)!!.use { ledger.exportDesktopDb(it) } }
                    .onSuccess { UiEvents.show("已导出 $it 天记录，可在 Win10 电脑上导入") }
                    .onFailure { UiEvents.show("导出失败：${it.message}") }
            }
        }
    }
    val importLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> importUri = uri }

    Box(Modifier.fillMaxSize()) {
        LazyColumn(
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = if (dirty) 96.dp else 24.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            item { Text("设置", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(start = 4.dp)) }

            // ── 云同步 ──────────────────────────
            item {
                SectionCard {
                    SectionTitle("云同步", "已内置同步服务器，与 Win10 电脑自动共用同一份账本。同一天两边都改过时，以最后修改的为准。")
                    val (icon, color) = when (status.tone) {
                        SyncTone.OK -> R.drawable.ic_cloud_check to Palette.Success
                        SyncTone.ERROR -> R.drawable.ic_alert to Palette.Danger
                        SyncTone.BUSY -> R.drawable.ic_sync to Palette.Accent
                        SyncTone.OFF -> R.drawable.ic_cloud_off to Palette.Text3
                    }
                    Row(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(Palette.SurfaceAlt).padding(12.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        AppIcon(icon, color, 22.dp)
                        HSpace(10.dp)
                        Column(Modifier.weight(1f)) {
                            Text(status.title, color = color, fontWeight = FontWeight.SemiBold, fontSize = 14.sp)
                            Text(if (status.tone == SyncTone.OFF) "未连接服务器，展开下方“更换服务器”可重新连接" else status.detail, style = TextStyles.Caption)
                        }
                        if (sync.configured) {
                            TextButton(onClick = { sync.syncNow() }, enabled = status.tone != SyncTone.BUSY) { Text("立即同步") }
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("自动同步", fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                            Text("打开应用时、保存后、使用中每 5 分钟", style = TextStyles.Caption)
                        }
                        Switch(
                            checked = autoSync,
                            onCheckedChange = { autoSync = it; sync.autoEnabled = it },
                            colors = SwitchDefaults.colors(checkedTrackColor = Palette.Accent),
                        )
                    }
                    Row(
                        Modifier.fillMaxWidth().clickable { showManual = !showManual }.padding(vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        AppIcon(if (showManual) R.drawable.ic_chevron_down else R.drawable.ic_chevron_right, Palette.Text2, 18.dp)
                        HSpace(6.dp)
                        Text("更换服务器（一般不需要）", style = TextStyles.Body2)
                    }
                    if (showManual) {
                        OutlinedTextField(
                            value = code,
                            onValueChange = { code = it },
                            placeholder = { Text("粘贴 PH1- 开头的连接码", color = Palette.Text3) },
                            singleLine = true,
                            trailingIcon = {
                                IconButton(onClick = { code = clipboardText(context).trim() }) { AppIcon(R.drawable.ic_paste, Palette.Accent) }
                            },
                            shape = RoundedCornerShape(12.dp),
                            colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Palette.BorderStrong),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            OutlinedButton(onClick = { applyCode(code) }, enabled = code.isNotBlank(), modifier = Modifier.weight(1f)) { Text("识别连接码") }
                            Button(
                                onClick = {
                                    formConfig()?.let { config ->
                                        if (ledger.getMeta("last_sync_at").isEmpty() && ledger.getAll().isNotEmpty()) confirmFirstSync = config else saveAndSync(config)
                                    }
                                },
                                modifier = Modifier.weight(1f),
                            ) { Text("保存并同步") }
                        }
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            TextButton(
                                enabled = !testing,
                                onClick = {
                                    val config = formConfig() ?: return@TextButton
                                    testing = true
                                    resultMessage = "正在连接服务器…" to Tone.ACCENT
                                    scope.launch {
                                        resultMessage = try {
                                            val result = withContext(Dispatchers.IO) { sync.engine.testConnection(config) }
                                            val devices = result.optJSONArray("devices")
                                            val names = (0 until (devices?.length() ?: 0)).mapNotNull { devices?.getJSONObject(it)?.optString("name") }
                                                .filter { it.isNotBlank() }.take(3).joinToString("、").ifEmpty { "暂无" }
                                            "连接成功：服务器上有 ${result.optInt("record_count")} 天记录，最近同步的设备：$names" to Tone.SUCCESS
                                        } catch (e: SyncException) {
                                            "连接失败：${e.message}" to Tone.DANGER
                                        } finally {
                                            testing = false
                                        }
                                    }
                                },
                            ) { Text("测试连接") }
                            TextButton(onClick = {
                                formConfig()?.let { config ->
                                    context.getSystemService(ClipboardManager::class.java)
                                        ?.setPrimaryClip(ClipData.newPlainText("利润助手连接码", ConnectionCode.make(config.serverUrl, config.token, config.fallbackIp)))
                                    UiEvents.show("连接码已复制，只发给自己的设备")
                                }
                            }) { Text("复制连接码") }
                            Spacer(Modifier.weight(1f))
                            if (sync.configured) TextButton(onClick = { confirmDisconnect = true }) { Text("断开", color = Palette.Danger) }
                        }
                        FormField("服务器地址", serverUrl, { serverUrl = it }, "https://example.com:28443")
                        OutlinedTextField(
                            value = token,
                            onValueChange = { token = it },
                            label = { Text("同步密钥") },
                            singleLine = true,
                            visualTransformation = if (tokenVisible) VisualTransformation.None else PasswordVisualTransformation(),
                            trailingIcon = {
                                IconButton(onClick = { tokenVisible = !tokenVisible }) {
                                    AppIcon(if (tokenVisible) R.drawable.ic_eye_off else R.drawable.ic_eye)
                                }
                            },
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.fillMaxWidth(),
                        )
                        FormField("备用 IP（域名解析失败时使用）", fallbackIp, { fallbackIp = it }, "可不填")
                        FormField("本机名称", deviceName, { deviceName = it }, "在服务器上显示的设备名")
                    }
                    resultMessage?.let { (text, tone) ->
                        Text(text, fontSize = 13.sp, color = when (tone) {
                            Tone.SUCCESS -> Palette.Success
                            Tone.DANGER -> Palette.Danger
                            Tone.WARNING -> Palette.Warning
                            else -> Palette.Text2
                        })
                    }
                }
            }

            // ── 分类结构 ────────────────────────
            item {
                SectionCard {
                    SectionTitle("分类结构", "一级分类自动汇总其下二级分类；分类名可以直接写进利润公式。改名不会改动已保存的数据。")
                    tree.forEachIndexed { pi, node ->
                        if (pi > 0) HorizontalDivider(color = Palette.Border)
                        val open = node.name in expanded
                        Row(
                            Modifier.fillMaxWidth().clickable { expanded = if (open) expanded - node.name else expanded + node.name },
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            AppIcon(if (open) R.drawable.ic_chevron_down else R.drawable.ic_chevron_right, Palette.Text3, 18.dp)
                            HSpace(6.dp)
                            Column(Modifier.weight(1f)) {
                                Text(node.name, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                                Text(if (node.children.isEmpty()) "直接录入" else "${node.children.size} 个二级分类", style = TextStyles.Caption)
                            }
                            SmallIcon(R.drawable.ic_arrow_up) { if (pi > 0) tree = tree.toMutableList().apply { add(pi - 1, removeAt(pi)) } }
                            SmallIcon(R.drawable.ic_arrow_down) { if (pi < tree.size - 1) tree = tree.toMutableList().apply { add(pi + 1, removeAt(pi)) } }
                            SmallIcon(R.drawable.ic_edit) { nameDialog = NameDialog("修改一级分类", node.name, pi, null, adding = false) }
                            SmallIcon(R.drawable.ic_trash, Palette.Danger) { deleteTarget = pi to null }
                        }
                        if (open) {
                            node.children.forEachIndexed { ci, child ->
                                Row(Modifier.fillMaxWidth().padding(start = 28.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Text(child, fontSize = 14.sp, modifier = Modifier.weight(1f))
                                    SmallIcon(R.drawable.ic_arrow_up) {
                                        if (ci > 0) tree = tree.updated(pi) { it.copy(children = it.children.toMutableList().apply { add(ci - 1, removeAt(ci)) }) }
                                    }
                                    SmallIcon(R.drawable.ic_arrow_down) {
                                        if (ci < node.children.size - 1) tree = tree.updated(pi) { it.copy(children = it.children.toMutableList().apply { add(ci + 1, removeAt(ci)) }) }
                                    }
                                    SmallIcon(R.drawable.ic_edit) { nameDialog = NameDialog("修改二级分类", child, pi, ci, adding = false) }
                                    SmallIcon(R.drawable.ic_trash, Palette.Danger) { deleteTarget = pi to ci }
                                }
                            }
                            TextButton(onClick = { nameDialog = NameDialog("添加到「${node.name}」", "", pi, null, adding = true) }, modifier = Modifier.padding(start = 20.dp)) {
                                AppIcon(R.drawable.ic_plus, Palette.Accent, 16.dp)
                                HSpace(4.dp)
                                Text("添加二级分类")
                            }
                        }
                    }
                    OutlinedButton(onClick = { nameDialog = NameDialog("添加一级分类", "", null, null, adding = true) }, modifier = Modifier.fillMaxWidth()) {
                        AppIcon(R.drawable.ic_plus, Palette.Accent, 16.dp)
                        HSpace(4.dp)
                        Text("添加一级分类")
                    }
                }
            }

            // ── 利润公式 ────────────────────────
            item {
                SectionCard {
                    SectionTitle("利润公式", "支持 + − × ÷ 和括号，例如：营业额 - 成本 + 零钱")
                    OutlinedTextField(
                        value = formula,
                        onValueChange = { formula = it.copy(text = it.text.replace('\n', ' ')) },
                        minLines = 3,
                        shape = RoundedCornerShape(12.dp),
                        colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Palette.BorderStrong),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    val (tone, message) = formulaProblem(formula.text, tree)
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        AppIcon(if (tone == Tone.SUCCESS) R.drawable.ic_check else R.drawable.ic_alert, if (tone == Tone.SUCCESS) Palette.Success else if (tone == Tone.WARNING) Palette.Warning else Palette.Danger, 16.dp)
                        HSpace(6.dp)
                        Text(message, fontSize = 13.sp, color = if (tone == Tone.SUCCESS) Palette.Success else if (tone == Tone.WARNING) Palette.Warning else Palette.Danger)
                    }
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("+" to " + ", "−" to " - ", "×" to " * ", "÷" to " / ", "(" to "(", ")" to ")").forEach { (label, insert) ->
                            Chip(label, bold = true, filled = true) { insertFormula(insert) }
                        }
                        Chip("退格") {
                            val sel = formula.selection
                            if (sel.collapsed && sel.min > 0) {
                                formula = TextFieldValue(formula.text.removeRange(sel.min - 1, sel.min), TextRange(sel.min - 1))
                            } else if (!sel.collapsed) {
                                formula = TextFieldValue(formula.text.removeRange(sel.min, sel.max), TextRange(sel.min))
                            }
                        }
                        Chip("清空") { formula = TextFieldValue("") }
                    }
                    Text("点击插入分类（深色为一级分类的合计）", style = TextStyles.Caption)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        tree.forEach { node ->
                            Chip(if (node.children.isEmpty()) node.name else "${node.name} 合计", bold = node.children.isNotEmpty(), filled = node.children.isNotEmpty()) {
                                insertFormula(node.name)
                            }
                            node.children.forEach { child -> Chip(child) { insertFormula(child) } }
                        }
                    }
                }
            }

            // ── 数据备份 ────────────────────────
            item {
                SectionCard {
                    SectionTitle("数据备份", "导出为 family_ledger.db（不含同步密钥），可在 Win10 电脑和手机之间互相导入；导入会替换本机全部记录和设置。")
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        OutlinedButton(onClick = { importLauncher.launch(arrayOf("*/*")) }, modifier = Modifier.weight(1f)) {
                            AppIcon(R.drawable.ic_upload, Palette.Accent, 16.dp)
                            HSpace(6.dp)
                            Text("导入账本")
                        }
                        OutlinedButton(onClick = { exportLauncher.launch("family_ledger.db") }, modifier = Modifier.weight(1f)) {
                            AppIcon(R.drawable.ic_download, Palette.Accent, 16.dp)
                            HSpace(6.dp)
                            Text("导出账本")
                        }
                    }
                }
            }
            item {
                Text(
                    "利润助手 v${BuildConfig.VERSION_NAME} · 数据保存在本机，开启云同步后与 Win10 电脑共用一份账本",
                    style = TextStyles.Caption,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
            }
        }

        if (dirty) {
            Surface(
                color = Color.White,
                border = BorderStroke(1.dp, Palette.Border),
                shadowElevation = 6.dp,
                shape = RoundedCornerShape(16.dp),
                modifier = Modifier.align(Alignment.BottomCenter).padding(12.dp).fillMaxWidth(),
            ) {
                Row(Modifier.padding(horizontal = 14.dp, vertical = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                    ToneBadge("有未保存的修改", Tone.WARNING)
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { reloadSettings() }) { Text("还原", color = Palette.Text2) }
                    Button(onClick = {
                        val normalized = FieldConfig.normalize(tree)
                        val (formulaTone, formulaMessage) = formulaProblem(formula.text, normalized)
                        when {
                            normalized.isEmpty() -> UiEvents.show("请至少添加一个一级分类")
                            formulaTone != Tone.SUCCESS -> UiEvents.show(formulaMessage)
                            else -> {
                                val cleanFormula = FormulaEngine.normalizeFormula(formula.text).trim().split(Regex("\\s+")).joinToString(" ")
                                ledger.setConfigs(mapOf(
                                    "custom_field_tree" to FieldConfig.treeToJson(normalized),
                                    "custom_fields" to FieldConfig.fieldsToJson(normalized),
                                    "profit_formula" to cleanFormula,
                                ))
                                reloadSettings()
                                UiEvents.show("设置已保存，录入页会按新分类显示")
                            }
                        }
                    }) { Text("保存设置") }
                }
            }
        }
    }

    nameDialog?.let { dialog ->
        var text by remember(dialog) { mutableStateOf(dialog.initial) }
        val current = if (dialog.adding) null else dialog.initial
        val problem = if (text.trim() == current) "" else FieldConfig.nameProblem(text.trim(), FieldConfig.flatten(tree).toSet() - setOfNotNull(current))
        AlertDialog(
            onDismissRequest = { nameDialog = null },
            title = { Text(dialog.title) },
            text = {
                Column {
                    OutlinedTextField(value = text, onValueChange = { text = it }, singleLine = true, label = { Text("名称") }, modifier = Modifier.fillMaxWidth())
                    if (problem.isNotEmpty() && text.isNotEmpty()) Text(problem, color = Palette.Danger, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                }
            },
            confirmButton = {
                TextButton(enabled = problem.isEmpty() && text.trim().isNotEmpty(), onClick = {
                    val name = text.trim()
                    tree = when {
                        dialog.adding && dialog.parent == null -> tree + FieldNode(name, emptyList())
                        dialog.adding -> tree.updated(dialog.parent!!) { it.copy(children = it.children + name) }.also {
                            expanded = expanded + tree[dialog.parent].name
                        }
                        dialog.child == null -> tree.updated(dialog.parent!!) { it.copy(name = name) }
                        else -> tree.updated(dialog.parent!!) { node -> node.copy(children = node.children.toMutableList().also { it[dialog.child] = name }) }
                    }
                    if (!dialog.adding && name != dialog.initial) renameInFormula(dialog.initial, name)
                    nameDialog = null
                }) { Text("确定") }
            },
            dismissButton = { TextButton(onClick = { nameDialog = null }) { Text("取消") } },
        )
    }

    deleteTarget?.let { (pi, ci) ->
        val node = tree[pi]
        val label = if (ci == null) node.name else node.children[ci]
        val extra = if (ci == null && node.children.isNotEmpty()) "\n它下面的 ${node.children.size} 个二级分类也会一起删除。" else ""
        AlertDialog(
            onDismissRequest = { deleteTarget = null },
            title = { Text("删除分类") },
            text = { Text("确定删除「$label」吗？$extra\n已保存的历史数据不会被删除。") },
            confirmButton = {
                TextButton(onClick = {
                    tree = if (ci == null) tree.filterIndexed { index, _ -> index != pi } else tree.updated(pi) { it.copy(children = it.children.filterIndexed { index, _ -> index != ci }) }
                    deleteTarget = null
                }) { Text("删除", color = Palette.Danger) }
            },
            dismissButton = { TextButton(onClick = { deleteTarget = null }) { Text("取消") } },
        )
    }

    confirmFirstSync?.let { config ->
        AlertDialog(
            onDismissRequest = { confirmFirstSync = null },
            title = { Text("首次同步") },
            text = { Text("手机上已有 ${ledger.getAll().size} 天记录，将与服务器上的账本合并：服务器上没有的日期会上传；同一天两边都有时，以最后修改的为准。确定开始同步吗？") },
            confirmButton = { TextButton(onClick = { confirmFirstSync = null; saveAndSync(config) }) { Text("开始同步") } },
            dismissButton = { TextButton(onClick = { confirmFirstSync = null }) { Text("取消") } },
        )
    }

    if (confirmDisconnect) {
        AlertDialog(
            onDismissRequest = { confirmDisconnect = false },
            title = { Text("断开云同步") },
            text = { Text("断开后手机不再与服务器同步，本机数据会保留；以后重新连接时会再次合并。确定断开吗？") },
            confirmButton = {
                TextButton(onClick = {
                    sync.disconnect()
                    token = ""
                    resultMessage = null
                    confirmDisconnect = false
                }) { Text("断开", color = Palette.Danger) }
            },
            dismissButton = { TextButton(onClick = { confirmDisconnect = false }) { Text("取消") } },
        )
    }

    importUri?.let { uri ->
        AlertDialog(
            onDismissRequest = { importUri = null },
            title = { Text("确认导入") },
            text = { Text("导入后会替换手机上所有历史记录和设置。" + if (sync.configured) "开启了云同步，导入结果也会同步到其他设备。" else "") },
            confirmButton = {
                TextButton(onClick = {
                    importUri = null
                    scope.launch(Dispatchers.IO) {
                        runCatching { context.contentResolver.openInputStream(uri)!!.use { ledger.importDesktopDb(it) } }
                            .onSuccess { (records, configs) ->
                                withContext(Dispatchers.Main) { reloadSettings() }
                                UiEvents.show("已导入 $records 天记录、$configs 项设置")
                            }
                            .onFailure { UiEvents.show("导入失败：${it.message}") }
                    }
                }) { Text("导入") }
            },
            dismissButton = { TextButton(onClick = { importUri = null }) { Text("取消") } },
        )
    }
}

private fun List<FieldNode>.updated(index: Int, transform: (FieldNode) -> FieldNode): List<FieldNode> =
    mapIndexed { i, node -> if (i == index) transform(node) else node }

@Composable
private fun SectionTitle(title: String, hint: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        Text(hint, style = TextStyles.Caption)
    }
}

@Composable
private fun SmallIcon(@DrawableRes icon: Int, tint: Color = Palette.Text2, onClick: () -> Unit) {
    IconButton(onClick = onClick, modifier = Modifier.size(36.dp)) { AppIcon(icon, tint, 18.dp) }
}

@Composable
private fun Chip(label: String, bold: Boolean = false, filled: Boolean = false, onClick: () -> Unit) {
    Text(
        label,
        fontSize = 14.sp,
        fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal,
        color = Palette.AccentDark,
        modifier = Modifier
            .clip(RoundedCornerShape(8.dp))
            .background(if (filled) Palette.AccentSoft else Color.White)
            .border(1.dp, Color(0xFFDCD8FA), RoundedCornerShape(8.dp))
            .clickable(onClick = onClick)
            .padding(horizontal = 12.dp, vertical = 6.dp),
    )
}

@Composable
private fun FormField(label: String, value: String, onChange: (String) -> Unit, placeholder: String) {
    OutlinedTextField(
        value = value,
        onValueChange = onChange,
        label = { Text(label) },
        placeholder = { Text(placeholder, color = Palette.Text3) },
        singleLine = true,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(),
    )
}
