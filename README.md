# 利润助手 💰

本地极简家庭利润记账软件。本地运行 · 数据不上云 · 操作极简。

## 技术栈

| 模块 | 库 |
|------|----|
| GUI | PySide6 |
| 本地数据库 | SQLite3 (family_ledger.db) |
| 数据处理 | pandas + openpyxl |
| 图表 | matplotlib |
| 安全公式 | ast (内置标准库) |
| 打包 | PyInstaller |

## 项目结构

```
利润助手/
├── main.py              # 程序入口
├── requirements.txt     # 依赖清单
├── family_ledger.db     # 数据库（首次运行自动生成）
├── core/
│   ├── database.py      # SQLite DAO 层
│   ├── event_bus.py     # 全局信号总线
│   ├── formula_engine.py# 安全公式解析引擎
│   └── report_engine.py # pandas 报表生成
├── ui/
│   ├── main_window.py   # 主窗口（导航栏 + 内容区）
│   ├── dashboard_view.py# 看板页
│   ├── entry_view.py    # 录入页
│   ├── history_view.py  # 历史记录页
│   └── settings_view.py # 设置页
└── assets/
    └── style.qss        # 深色主题样式表
```

## 快速开始（Mac 开发环境）

```bash
cd 利润助手

# 1. 创建并激活虚拟环境
python3 -m venv .venv
source .venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 运行
python main.py
```

## 打包为 Windows .exe

> **注意**：必须在 Windows 10/11 机器（或 Mac 虚拟机中的 Windows）上执行以下命令。
> 在 macOS 上 PyInstaller 只能打出 macOS 版本。

```bash
# 在 Windows 机器上：
pip install pyinstaller
pyinstaller --noconsole --onefile main.py
```

生成的 `.exe` 位于 `dist/main.exe`，可直接复制到目标电脑运行，无需安装 Python。

## 数据说明

- 数据存储在 `family_ledger.db`（SQLite 单文件），位于程序同级目录
- 每日记录以 JSON 格式存储自定义字段，支持随时增减账目类型
- 利润计算公式可在设置页自由配置，例如 `营业额 - 成本` 或 `(营业额 - 成本) * 0.9`

## 架构亮点

- **MVC 分层**：UI / DAO / 业务逻辑完全解耦
- **响应式更新**：全局 EventBus 信号总线，数据变更后所有视图自动刷新
- **跨平台路径**：所有路径拼接使用 `pathlib.Path`，Windows/Mac 均兼容
- **安全公式引擎**：基于 `ast` 白名单，拒绝代码注入，支持中文变量名
