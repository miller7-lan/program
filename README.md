# 利润助手 💰

家庭利润记账软件：Win10 桌面端 + 安卓手机端，通过自建的同步服务共用同一份账本，离线也能记账，联网后自动同步。

## 组成

| 部分 | 技术 | 说明 |
|------|------|------|
| Win10 桌面端 | PySide6 + SQLite + openpyxl | 录入、看板、历史、Excel 分级报表、分类与公式设置 |
| 安卓端 | Kotlin + Jetpack Compose | 与桌面端相同的分类、公式（同一套规则，测试逐条比对）、报表与导入导出 |
| 同步服务 | Python 标准库 + SQLite | `server/profit_sync_server.py`，无第三方依赖，HTTPS + 同步密钥 |

## 项目结构

```
├── main.py                 # 桌面端入口（--smoke-test 为打包自检）
├── core/
│   ├── database.py         # SQLite DAO 层（含同步元数据）
│   ├── sync_client.py      # 云同步客户端（不依赖 Qt）
│   ├── formula_engine.py   # 安全公式解析引擎（ast 白名单）
│   ├── field_config.py     # 一级 / 二级分类工具
│   ├── report_engine.py    # 看板统计与 Excel 导出
│   └── event_bus.py        # 全局信号总线
├── ui/                     # 桌面端界面（看板 / 录入 / 历史 / 设置）
├── assets/                 # 样式表、图标（由 tools/make_icons.py 生成）
├── server/                 # 同步服务与部署脚本（见 server/deploy/README.md）
├── android/                # 安卓端 Gradle 工程
├── tests/                  # 服务端与桌面端同步测试
└── tools/                  # 图标生成、公式测试向量、界面截图
```

## 快速开始（桌面端开发）

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python main.py
```

运行测试：

```bash
python -m unittest discover -s tests
```

## 云同步

同步服务器内置在两端程序里，使用人不需要填写任何服务器信息：

1. 在服务器上部署同步服务（见 [server/deploy/README.md](server/deploy/README.md)），得到 `PH1-` 开头的连接码。
2. 桌面端：把连接码保存为 `ProfitHelper-sync.txt`，和 exe 放在同一个文件夹里分发（不要提交到仓库）。
   程序启动时自动连接；文件缺失时设置页会提示，放好后点“重新检测”即可。
3. 安卓端：在 `android/local.properties` 写入 `profitHelper.presetConnectionCode=PH1-…` 后打包正式版。
4. 轮换密钥：替换 exe 旁的 `ProfitHelper-sync.txt`（安卓端安装新打包的版本），重新打开程序或点“重新检测”。

同步规则：

- 启动时、保存后 2.5 秒、使用中每分钟自动同步一次，也可以手动“立即同步”。
- 每天的记录独立同步；同一天在两台设备上都修改过时，以最后修改的为准，被覆盖的版本保留在服务器的历史表中。
- 分类结构和利润公式作为一份设置一起同步；分类改名会同时改写历史记录里的名称并同步到另一端；
  公式保存时自动整理格式（两端规则相同）；“历史起始日期”等界面偏好只保存在本机。
- 首次同步前，桌面端会在数据文件旁自动备份 `family_ledger.before-sync-*.db`。
- 导出的账本不包含同步密钥，可在桌面端和安卓端互相导入。

## 打包为 Windows .exe

推送到 GitHub 后，Actions（`Build Windows EXE`）会自动：运行测试 → 打包 → 用 `--smoke-test` 启动 exe 自检 → 在 Windows 上截取界面图，产物为 `ProfitHelper_Windows` 与 `ProfitHelper_Windows_Screenshots`。

也可以在 Windows 10/11 上双击 `build_windows_exe.bat` 本地打包，或手动执行：

```bash
python -m venv .venv-win
.venv-win\Scripts\activate
python -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn
python -m PyInstaller --noconfirm --clean --windowed --onefile --name "ProfitHelper" --icon assets/app_icon.ico --add-data "assets;assets" main.py
```

生成的 `dist/ProfitHelper.exe` 可直接复制到目标电脑运行；首次运行会在 exe 同级目录生成 `family_ledger.db`，旧版本的账本放在同一目录会自动升级。

## 安卓端

需要 JDK 17+ 与 Android SDK（compileSdk 36）。在 `android/local.properties` 中可选配置（该文件不会提交）：

```properties
sdk.dir=/path/to/Android/sdk
profitHelper.defaultServerUrl=https://example.com:28443   # 设置页默认填好的服务器地址
profitHelper.defaultFallbackIp=203.0.113.10                # 域名解析失败时直接连接的 IP
profitHelper.keystoreProperties=/path/to/keystore.properties # 正式版签名（storeFile/storePassword/keyAlias/keyPassword）
```

```bash
cd android
./gradlew testDebugUnitTest   # 公式引擎与桌面端逐条比对
./gradlew assembleRelease     # 生成 app/build/outputs/apk/release/app-release.apk
```

## 数据说明

- 每日记录以 JSON 存储各分类金额，支持随时增减一级 / 二级分类
- 利润公式可在设置页自由配置，例如 `营业额 - 成本` 或 `(营业额 - 成本) * 0.9`；全角括号和 × ÷ 也能识别
- 录入金额时可以直接输入算式，例如 `120+35`
