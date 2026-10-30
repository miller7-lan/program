package com.homeapp.profithelper.ui

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.annotation.DrawableRes
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
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
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.homeapp.profithelper.BuildConfig
import com.homeapp.profithelper.ProfitApp
import com.homeapp.profithelper.R
import com.homeapp.profithelper.core.FieldConfig
import com.homeapp.profithelper.sync.SyncTone
import com.homeapp.profithelper.ui.theme.Palette
import com.homeapp.profithelper.ui.theme.TextStyles
import java.text.Normalizer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** 按名称（而不是位置）记录操作对象：对话框打开期间分类被云同步刷新，也不会改错分类。 */
private data class NameDialog(val title: String, val initial: String, val parent: String?, val child: String?, val adding: Boolean)

private data class DeleteTarget(val parent: String, val child: String?)

/**
 * 设置页 —— 与桌面端设置页对应：分类结构、利润公式、云同步、数据备份，底部固定“还原 / 保存设置”。
 *  - 每个分类（含新增的）都有操作按钮，二级分类默认展开
 *  - 公式下方为每个分类提供插入按钮；离开公式框和保存时自动整理格式；格式错误弹窗提醒
 *  - 同步服务器内置在安装包里，这里只显示状态，不需要填写
 */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun SettingsScreen(app: ProfitApp, settings: SettingsController) {
    val ledger = app.ledger
    val sync = app.sync
    val version by ledger.version.collectAsState()
    val status by sync.status.collectAsState()
    val context = LocalContext.current
    val focusManager = LocalFocusManager.current
    val scope = rememberCoroutineScope()
    LaunchedEffect(version) { settings.onDataVersion(version) }

    var autoSync by remember { mutableStateOf(sync.autoEnabled) }
    var nameDialog by remember { mutableStateOf<NameDialog?>(null) }
    var deleteTarget by remember { mutableStateOf<DeleteTarget?>(null) }
    var collapsed by remember { mutableStateOf(setOf<String>()) }
    var importUri by remember { mutableStateOf<Uri?>(null) }
    val tree = settings.tree
    val dirty = settings.isDirty

    val exportLauncher = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/octet-stream")) { uri ->
        if (uri != null) {
            scope.launch(Dispatchers.IO) {
                runCatching { context.contentResolver.openOutputStream(uri)!!.use { ledger.exportDesktopDb(it) } }
                    .onSuccess { UiEvents.show("已导出 $it 天记录，可在 Win10 电脑上导入") }
                    .onFailure { withContext(Dispatchers.Main) { UiEvents.alert("导出失败", it.message ?: "写入文件时出错") } }
            }
        }
    }
    val importLauncher = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri -> importUri = uri }

    Column(Modifier.fillMaxSize().imePadding()) {
        LazyColumn(
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 16.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            modifier = Modifier.weight(1f),
        ) {
            item { Text("设置", style = MaterialTheme.typography.titleLarge, modifier = Modifier.padding(start = 4.dp)) }

            // ── 分类结构 ────────────────────────
            item {
                SectionCard {
                    SectionTitle("分类结构", "一级分类自动汇总其下二级分类；分类名可以直接写进利润公式。改名会同步改写历史记录里的名称，删除分类不会删除已保存的数据。")
                    tree.forEachIndexed { pi, node ->
                        if (pi > 0) HorizontalDivider(color = Palette.Border)
                        val open = node.name !in collapsed
                        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                            Row(
                                Modifier.weight(1f).clip(RoundedCornerShape(8.dp))
                                    .clickable(enabled = node.children.isNotEmpty()) {
                                        collapsed = if (open) collapsed + node.name else collapsed - node.name
                                    }.padding(vertical = 4.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                if (node.children.isEmpty()) {
                                    Row(Modifier.size(18.dp), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
                                        Dot(Palette.Text3, 6.dp)
                                    }
                                } else {
                                    AppIcon(if (open) R.drawable.ic_chevron_down else R.drawable.ic_chevron_right, Palette.Text3, 18.dp)
                                }
                                HSpace(6.dp)
                                Column {
                                    Text(node.name, fontWeight = FontWeight.Bold, fontSize = 15.sp)
                                    Text(if (node.children.isEmpty()) "直接录入" else "${node.children.size} 个二级分类", style = TextStyles.Caption)
                                }
                            }
                            SmallIcon(R.drawable.ic_arrow_up, enabled = pi > 0) { settings.moveParent(pi, -1) }
                            SmallIcon(R.drawable.ic_arrow_down, enabled = pi < tree.size - 1) { settings.moveParent(pi, 1) }
                            SmallIcon(R.drawable.ic_edit) { nameDialog = NameDialog("修改一级分类", node.name, node.name, null, adding = false) }
                            SmallIcon(R.drawable.ic_trash, Palette.Danger) { deleteTarget = DeleteTarget(node.name, null) }
                        }
                        if (open) {
                            node.children.forEachIndexed { ci, child ->
                                Row(Modifier.fillMaxWidth().padding(start = 24.dp), verticalAlignment = Alignment.CenterVertically) {
                                    Text(child, fontSize = 14.sp, modifier = Modifier.weight(1f))
                                    SmallIcon(R.drawable.ic_arrow_up, enabled = ci > 0) { settings.moveChild(pi, ci, -1) }
                                    SmallIcon(R.drawable.ic_arrow_down, enabled = ci < node.children.size - 1) { settings.moveChild(pi, ci, 1) }
                                    SmallIcon(R.drawable.ic_edit) { nameDialog = NameDialog("修改二级分类", child, node.name, child, adding = false) }
                                    SmallIcon(R.drawable.ic_trash, Palette.Danger) { deleteTarget = DeleteTarget(node.name, child) }
                                }
                            }
                        }
                        TextButton(
                            onClick = {
                                collapsed = collapsed - node.name
                                nameDialog = NameDialog("添加到「${node.name}」", "", node.name, null, adding = true)
                            },
                            modifier = Modifier.padding(start = 16.dp),
                        ) {
                            AppIcon(R.drawable.ic_plus, Palette.Accent, 16.dp)
                            HSpace(4.dp)
                            Text("给「${node.name}」添加二级分类", fontSize = 13.sp)
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
                    SectionTitle("利润公式", "支持 + − × ÷ 和括号，例如：营业额 - 成本 + 零钱；离开输入框时会自动整理格式")
                    var hadFocus by remember { mutableStateOf(false) }
                    OutlinedTextField(
                        value = settings.formula,
                        onValueChange = { settings.formula = it.copy(text = it.text.replace('\n', ' ')) },
                        minLines = 3,
                        isError = formulaProblem(settings.formula.text, settings.plainTree).first == Tone.DANGER && settings.formula.text.isNotBlank(),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                        keyboardActions = KeyboardActions(onDone = { focusManager.clearFocus() }),
                        shape = RoundedCornerShape(12.dp),
                        colors = OutlinedTextFieldDefaults.colors(unfocusedBorderColor = Palette.BorderStrong),
                        modifier = Modifier.fillMaxWidth().onFocusChanged {
                            if (hadFocus && !it.isFocused) settings.tidyFormula()
                            hadFocus = it.isFocused
                        },
                    )
                    val (tone, message) = formulaProblem(settings.formula.text, settings.plainTree)
                    val toneColor = when (tone) {
                        Tone.SUCCESS -> Palette.Success
                        Tone.WARNING -> Palette.Warning
                        else -> Palette.Danger
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        AppIcon(if (tone == Tone.SUCCESS) R.drawable.ic_check else R.drawable.ic_alert, toneColor, 16.dp)
                        HSpace(6.dp)
                        Text(message, fontSize = 13.sp, color = toneColor)
                    }
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        listOf("+" to " + ", "−" to " - ", "×" to " * ", "÷" to " / ", "(" to "(", ")" to ")").forEach { (label, insert) ->
                            Chip(label, bold = true, filled = true) { settings.insertFormula(insert) }
                        }
                        Chip("退格") { settings.backspace() }
                        Chip("清空") { settings.clearFormula() }
                    }
                    Text("点击插入分类（每个分类都有按钮，深色为一级分类的合计；新增分类后立即出现）", style = TextStyles.Caption)
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        tree.forEach { node ->
                            Chip(if (node.children.isEmpty()) node.name else "${node.name} 合计", bold = node.children.isNotEmpty(), filled = node.children.isNotEmpty()) {
                                settings.insertFormula(node.name)
                            }
                            node.children.forEach { child -> Chip(child) { settings.insertFormula(child) } }
                        }
                    }
                }
            }

            // ── 云同步 ──────────────────────────
            item {
                SectionCard {
                    SectionTitle("云同步", "同步服务器已内置，不需要填写。与 Win10 电脑自动共用同一份账本；同一天两边都改过时以最后修改的为准，被覆盖的版本会保留在服务器上。")
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
                            Text(status.detail, style = TextStyles.Caption)
                        }
                        if (sync.needsPreset) {
                            TextButton(onClick = {
                                when {
                                    !app.hasPreset -> UiEvents.alert("没有内置同步配置", "这个安装包没有内置同步服务器，请安装正式版利润助手。")
                                    else -> {
                                        app.applyPreset(force = true)
                                        sync.retryWithPreset()
                                    }
                                }
                            }) { Text("重新检测") }
                        } else {
                            TextButton(onClick = { sync.syncNow() }, enabled = status.tone != SyncTone.BUSY) { Text("立即同步") }
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("自动同步", fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                            Text("打开应用时、保存后、使用中每分钟", style = TextStyles.Caption)
                        }
                        Switch(
                            checked = autoSync,
                            onCheckedChange = { autoSync = it; sync.autoEnabled = it },
                            enabled = sync.configured,
                            colors = SwitchDefaults.colors(checkedTrackColor = Palette.Accent),
                        )
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
                    "利润助手 v${BuildConfig.VERSION_NAME} · 数据保存在本机，并通过云同步与 Win10 电脑共用一份账本",
                    style = TextStyles.Caption,
                    modifier = Modifier.padding(horizontal = 4.dp),
                )
            }
        }

        // ── 底部固定操作栏 ──────────────────────
        Surface(color = Color.White) {
            Column {
                HorizontalDivider(color = Palette.Border)
                Row(Modifier.padding(horizontal = 16.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    if (dirty) ToneBadge("有未保存的修改", Tone.WARNING) else Text("分类和公式都已保存", style = TextStyles.Caption)
                    Spacer(Modifier.weight(1f))
                    TextButton(onClick = { settings.load() }, enabled = dirty) { Text("还原") }
                    Button(onClick = {
                        focusManager.clearFocus()
                        settings.save()
                    }, contentPadding = PaddingValues(horizontal = 20.dp, vertical = 10.dp)) {
                        AppIcon(R.drawable.ic_save, Color.White, 18.dp)
                        HSpace(6.dp)
                        Text("保存设置")
                    }
                }
            }
        }
    }

    nameDialog?.let { dialog ->
        var text by remember(dialog) { mutableStateOf(dialog.initial) }
        val current = if (dialog.adding) null else dialog.initial
        val cleaned = Normalizer.normalize(text, Normalizer.Form.NFKC).trim()  // 全角字母、数字转半角，和公式里的写法一致
        val problem = if (cleaned == current) "" else FieldConfig.nameProblem(cleaned, settings.allNames - setOfNotNull(current))
        AlertDialog(
            onDismissRequest = { nameDialog = null },
            title = { Text(dialog.title) },
            text = {
                Column {
                    OutlinedTextField(value = text, onValueChange = { text = it }, singleLine = true, label = { Text("名称") }, modifier = Modifier.fillMaxWidth())
                    if (problem.isNotEmpty() && text.isNotEmpty()) Text(problem, color = Palette.Danger, fontSize = 12.sp, modifier = Modifier.padding(top = 6.dp))
                    if (!dialog.adding) Text("历史记录和公式里的这一项会一起改名", style = TextStyles.Caption, modifier = Modifier.padding(top = 6.dp))
                }
            },
            confirmButton = {
                TextButton(enabled = problem.isEmpty() && cleaned.isNotEmpty(), onClick = {
                    val name = cleaned
                    val pi = tree.indexOfFirst { it.name == dialog.parent }
                    val ci = if (pi >= 0 && dialog.child != null) tree[pi].children.indexOf(dialog.child) else -1
                    when {
                        dialog.adding && dialog.parent == null -> settings.addParent(name)
                        pi < 0 -> UiEvents.show("「${dialog.parent}」已不存在，请重新操作")
                        dialog.adding -> settings.addChild(pi, name)
                        name == dialog.initial -> Unit
                        dialog.child == null -> settings.renameParent(pi, name)
                        ci >= 0 -> settings.renameChild(pi, ci, name)
                        else -> UiEvents.show("「${dialog.child}」已不存在，请重新操作")
                    }
                    nameDialog = null
                }) { Text("确定") }
            },
            dismissButton = { TextButton(onClick = { nameDialog = null }) { Text("取消") } },
        )
    }

    deleteTarget?.let { target ->
        val pi = tree.indexOfFirst { it.name == target.parent }
        val ci = if (pi >= 0 && target.child != null) tree[pi].children.indexOf(target.child) else -1
        if (pi < 0 || (target.child != null && ci < 0)) {
            LaunchedEffect(target) { deleteTarget = null }  // 对象已不存在（例如被云同步刷新）
        } else {
            val node = tree[pi]
            val extra = if (target.child == null && node.children.isNotEmpty()) "\n它下面的 ${node.children.size} 个二级分类也会一起删除。" else ""
            AlertDialog(
                onDismissRequest = { deleteTarget = null },
                title = { Text(if (target.child == null) "删除一级分类" else "删除二级分类") },
                text = { Text("确定删除「${target.child ?: target.parent}」吗？$extra\n已保存的历史数据不会被删除。") },
                confirmButton = {
                    TextButton(onClick = {
                        if (target.child == null) settings.deleteParent(pi) else settings.deleteChild(pi, ci)
                        deleteTarget = null
                    }) { Text("删除", color = Palette.Danger) }
                },
                dismissButton = { TextButton(onClick = { deleteTarget = null }) { Text("取消") } },
            )
        }
    }

    if (settings.conflictPending) {
        AlertDialog(
            onDismissRequest = { settings.resolveConflict(null) },
            title = { Text("设置刚被其他设备修改过") },
            text = { Text("在你编辑期间，分类或公式已通过云同步更新。继续保存会用你现在的设置覆盖它（被覆盖的版本仍保留在服务器上）。") },
            confirmButton = { TextButton(onClick = { settings.resolveConflict(true) }) { Text("仍然保存", color = Palette.Danger) } },
            dismissButton = {
                Row {
                    TextButton(onClick = { settings.resolveConflict(null) }) { Text("取消", color = Palette.Text2) }
                    TextButton(onClick = { settings.resolveConflict(false) }) { Text("载入最新设置") }
                }
            },
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
                        val result = runCatching { context.contentResolver.openInputStream(uri)!!.use { ledger.importDesktopDb(it) } }
                        withContext(Dispatchers.Main) {
                            result.onSuccess { (records, configs) ->
                                settings.load()
                                UiEvents.show("已导入 $records 天记录、$configs 项设置")
                            }.onFailure {
                                UiEvents.alert("导入文件格式错误", "导入文件不符合账本格式，或读取失败：\n${it.message}")
                            }
                        }
                    }
                }) { Text("导入") }
            },
            dismissButton = { TextButton(onClick = { importUri = null }) { Text("取消") } },
        )
    }
}

@Composable
private fun SectionTitle(title: String, hint: String) {
    Column(verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Text(title, style = MaterialTheme.typography.titleMedium)
        Text(hint, style = TextStyles.Caption)
    }
}

@Composable
private fun SmallIcon(@DrawableRes icon: Int, tint: Color = Palette.Text2, enabled: Boolean = true, onClick: () -> Unit) {
    IconButton(onClick = onClick, enabled = enabled, modifier = Modifier.size(36.dp)) {
        AppIcon(icon, if (enabled) tint else Palette.BorderStrong, 18.dp)
    }
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
