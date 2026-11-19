# Yuketang Evan

雨课堂课堂监听与答题辅助工具，基于 Python、FastAPI 和 React。**本项目由 Evan 开发和维护**，在既有项目的代码基础上进行界面与功能扩展。

当前版本：**1.21**（Windows 桌面封装，基于 V1.2 功能）。项目面向 Windows 11；本版本保留了以下尚未完成的功能限制，请在使用前阅读。

> **AI 自动答题需在每门课程的题型设置中主动启用。** 启用后无需人工审批，助手会尝试提交 AI 的答案；默认仍为仅提醒，旧设置不会自动改为自动答题。雨课堂可能提前关闭题目，接口也无法完全证明题目仍然开放；提交失败或结果不明确时，请在雨课堂核实。AI 失败时不会随机或空白提交。人工确认提交仍未启用。

## 功能

- 监听课堂事件，展示课堂连接、最近轮询、重连等状态，并通过浏览器提示、语音或 PushDeer 提醒。
- 按账号与课堂记录待处理题目，支持展示 AI 建议、编辑本地答案草稿和忽略题目。
- 可为单选、多选、投票和简答题分别启用 AI 自动答题；需配置自定义供应商的默认模型，答案将直接提交，无需人工审批。
- 添加自定义供应商并选择 OpenAI Chat Completions、OpenAI Responses、Gemini Native 或 Anthropic Messages 协议，配置 Base URL、多个模型、默认模型并进行连接、文本和合成图片视觉测试。
- 保留原有 Google Gemini 与 ModelScope 的接入路径；实际兼容性取决于服务配置和模型能力。
- 仪表盘支持手动刷新监听状态；未启用课程自动折叠，可手动展开。Windows 系统通知独立于浏览器页面，按课程启用。
- 支持课程设置、多账号、中英文界面和系统浅色／深色主题；原有自动签到、弹幕和红包功能可按已有设置使用。

Gemini Native 的课堂图片输入需将模型的图片传输方式设为 Data URL。各服务商 Base URL、模型 ID 和协议兼容性以该服务商要求为准。

自动答题只尝试提交一次；收到不确定的响应或连接中断时不会自动重试，请先在雨课堂核实。启用临近截止作答时，如果最近 30 秒内没有新的题目解锁信息，助手会放弃自动提交并提醒，避免依据过期状态作答。

AI 建议可能不准确。视觉测试通过仅表示模型识别了内置测试图片，不代表它能正确理解所有课堂题目。模型测试和 AI 请求可能产生服务商费用，使用第三方服务时题目图片可能被发送给该服务商。

## Windows 桌面版

解压桌面版文件夹后，双击 `YuketangEvan.exe` 打开独立窗口；请保留文件夹内的全部文件。首次使用需要在桌面版中重新登录并配置服务。关闭窗口会隐藏到系统托盘，监听继续运行；在托盘图标菜单选择“退出并停止监听”可以完全退出。若系统托盘不可用，关闭窗口会退出应用。重复打开同一桌面数据目录时，会唤醒已有窗口。

桌面版使用独立的 `%LOCALAPPDATA%\Yuketang Evan Desktop` 数据目录，不自动导入源码开发版或原版 EXE 的登录 Session 和密钥。桌面版需要 Windows WebView2 Runtime；实际课堂、AI 服务及手机通知仍需要网络。浏览器通知、语音和 Windows 通知在桌面和托盘状态下的可见性取决于系统与运行环境，需在使用时验证。

### 从源码构建桌面版

桌面构建使用独立依赖，不需要修改现有开发环境。请先安装 Python 3.14、Node.js/npm 和 uv；以下命令供开发者重新打包时使用：

```powershell
npm --prefix frontend ci
uv venv --python 3.14 .venv-desktop
uv pip install --python .\.venv-desktop\Scripts\python.exe -r requirements-desktop.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\build-desktop.ps1 -PythonExe .\.venv-desktop\Scripts\python.exe
```

构建结果位于 `desktop-builds/` 下的时间戳目录。构建和运行客户端使用不同目录，打包不会覆盖原版 EXE。

## Windows 源码运行

本地开发环境曾使用 Python 3.14.5 和 Node.js 24.16.0；请先安装 Python、Node.js 和 Git。首次运行时，在 PowerShell 进入项目根目录后执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
npm --prefix frontend ci
.\.venv\Scripts\python.exe start.py
```

启动后访问 <http://127.0.0.1:5174>。默认后端地址为 <http://127.0.0.1:8501>。以后启动时，只需在项目根目录执行：

```powershell
.\.venv\Scripts\python.exe start.py
```

如果提示已有 PID 文件，先检查开发版是否已经运行以及相关端口是否占用，确认记录过期后再处理，避免结束其他程序。

## 模型与提醒配置

1. 登录后打开设置页。在“自定义 AI 供应商”中选择服务商支持的 API 格式，并填写名称、**HTTPS Base URL** 和 API Key。
2. 添加实际模型 ID，按模型能力选择视觉输入和图片传输方式，然后设为默认模型。
3. 使用内置测试检查连接、文本或视觉能力；错误信息以页面测试结果为准。供应商和模型添加表单保存后自动收起，可再次展开。
4. 按需配置 PushDeer 和课程提醒；PushDeer 添加表单保存后自动收起。Windows 系统通知需在每门课程的设置中启用，可在监听设置里发送测试通知。系统通知由 Windows PowerShell 身份发送，实际显示受 Windows 通知设置、专注模式及当前用户会话影响。
5. 需要自动答题时，进入课程设置，分别为需要的题型选择“AI 自动答题（无需审批）”并应用。请先确认默认模型适用于相应题目；提交行为应在可控课堂环境中自行验证。

不要将 API Key、PushDeer 密钥或登录 Session 提交到仓库。Windows 下新 AI 密钥使用系统 DPAPI 保护；旧配置迁移前的备份可能仍包含原始密钥，应妥善保管。源码运行的数据默认存于项目根目录的 `store/`，可通过 `YUKETANG_STORE_DIR` 指定独立数据目录。

## 构建

在项目根目录安装 PyInstaller 后构建 Windows 可执行文件：

```powershell
npm --prefix frontend ci
npm --prefix frontend run build
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\python.exe -m PyInstaller YuketangHelper.spec
```

构建结果位于 `dist/`。构建成功不等于在目标机器上的功能验证；运行前请确认端口与数据目录设置。当前版本尚未完成受控课堂提交验证。

## 项目来源与许可

本项目由 Evan 基于 [dvdsanyi/Yuketang-Helper-Web](https://github.com/dvdsanyi/Yuketang-Helper-Web) 开发；该项目注明基于 [TrickyDeath/RainClassroomAssitant](https://github.com/TrickyDeath/RainClassroomAssitant)。感谢原作者的工作。

当前源码中未附明确的上游开源许可证；相关版权及使用授权以权利人的实际授权为准。本仓库不代表原作者或雨课堂官方。
