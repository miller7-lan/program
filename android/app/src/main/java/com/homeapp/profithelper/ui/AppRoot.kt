package com.homeapp.profithelper.ui

import androidx.activity.compose.BackHandler
import androidx.annotation.DrawableRes
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.consumeWindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.homeapp.profithelper.ProfitApp
import com.homeapp.profithelper.R
import com.homeapp.profithelper.ui.theme.Palette
import java.time.LocalDate
import kotlinx.coroutines.flow.MutableSharedFlow
import kotlinx.coroutines.flow.asSharedFlow

/** 全局提示（底部 Snackbar）。 */
object UiEvents {
    private val _messages = MutableSharedFlow<String>(extraBufferCapacity = 8)
    val messages = _messages.asSharedFlow()
    fun show(message: String) {
        _messages.tryEmit(message)
    }
}

private data class Tab(val label: String, @DrawableRes val icon: Int)

private val TABS = listOf(
    Tab("看板", R.drawable.ic_dashboard),
    Tab("录入", R.drawable.ic_entry),
    Tab("历史", R.drawable.ic_history),
    Tab("设置", R.drawable.ic_settings),
)

@Composable
fun AppRoot(app: ProfitApp) {
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var pendingTab by remember { mutableStateOf<Int?>(null) }
    val snackbar = remember { SnackbarHostState() }
    val entry = remember { EntryController(app.ledger) }

    LaunchedEffect(Unit) { app.sync.messages.collect { snackbar.showSnackbar(it) } }
    LaunchedEffect(Unit) { UiEvents.messages.collect { snackbar.showSnackbar(it) } }

    fun requestTab(target: Int) {
        if (tab == 1 && target != 1 && entry.isDirty) pendingTab = target else tab = target
    }

    fun openEntry(date: LocalDate) {
        tab = 1
        entry.requestDate(date)
    }

    BackHandler(enabled = tab != 0) { requestTab(0) }

    Scaffold(
        containerColor = Palette.Bg,
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            Box {
                NavigationBar(containerColor = Color.White, tonalElevation = 0.dp) {
                    TABS.forEachIndexed { index, item ->
                        NavigationBarItem(
                            selected = tab == index,
                            onClick = { requestTab(index) },
                            icon = { AppIcon(item.icon, if (tab == index) Palette.Accent else Palette.Text2, 22.dp) },
                            label = { Text(item.label) },
                            colors = NavigationBarItemDefaults.colors(
                                selectedTextColor = Palette.Accent,
                                unselectedTextColor = Palette.Text2,
                                indicatorColor = Palette.AccentSoft,
                            ),
                        )
                    }
                }
                HorizontalDivider(color = Palette.Border)
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding)) {
            when (tab) {
                0 -> DashboardScreen(app, onOpenEntry = ::openEntry)
                1 -> EntryScreen(app, entry)
                2 -> HistoryScreen(app, onOpenEntry = ::openEntry)
                else -> SettingsScreen(app)
            }
        }
    }

    pendingTab?.let { target ->
        UnsavedChangesDialog(
            dateLabel = entry.dateTitle(),
            onSave = {
                pendingTab = null
                if (entry.saveAndReport()) tab = target
            },
            onDiscard = {
                pendingTab = null
                entry.discard()
                tab = target
            },
            onCancel = { pendingTab = null },
        )
    }
}

@Composable
fun UnsavedChangesDialog(dateLabel: String, onSave: () -> Unit, onDiscard: () -> Unit, onCancel: () -> Unit) {
    AlertDialog(
        onDismissRequest = onCancel,
        title = { Text("尚未保存") },
        text = { Text("$dateLabel 的修改还没有保存。") },
        confirmButton = { TextButton(onClick = onSave) { Text("保存") } },
        dismissButton = {
            androidx.compose.foundation.layout.Row {
                TextButton(onClick = onCancel) { Text("取消", color = Palette.Text2) }
                TextButton(onClick = onDiscard) { Text("不保存", color = Palette.Danger) }
            }
        },
    )
}
