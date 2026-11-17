# Yuketang Evan

雨课堂课堂监听与答题辅助工具，基于 Python、FastAPI 和 React。**本项目由 Evan 开发和维护**，在既有项目的代码基础上进行界面与功能扩展。

当前版本：**1.0**。项目面向 Windows 11；本版本保留了以下尚未完成的功能限制，请在使用前阅读。

> **实际答题提交尚未启用。** 由于尚无法可靠核验雨课堂题目在提交时是否仍然开放，助手的真实提交入口会拒绝请求。题目提醒和 AI 答案仅供参考，请在雨课堂手动作答。AI 失败时不会随机或空白提交。

## 功能

- 监听课堂事件，展示课堂连接、最近轮询、重连等状态，并通过浏览器提示、语音或 PushDeer 提醒。
- 按账号与课堂记录待处理题目，支持展示 AI 建议、编辑本地答案草稿和忽略题目。
- 添加 OpenAI Chat Completions 兼容供应商、自定义 Base URL 和模型 ID；支持配置多个模型、默认模型、模型发现及连接、文本和合成图片视觉测试。
- 保留原有 Google Gemini 与 ModelScope 的接入路径；实际兼容性取决于服务配置和模型能力。
- 支持课程设置、多账号、中英文界面和系统浅色／深色主题；原有自动签到、弹幕和红包功能可按已有设置使用。

AI 建议可能不准确。视觉测试通过仅表示模型识别了内置测试图片，不代表它能正确理解所有课堂题目。模型测试和 AI 请求可能产生服务商费用，使用第三方服务时题目图片可能被发送给该服务商。

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

1. 登录后打开设置页。在“自定义 AI 供应商”中填写服务商名称、**HTTPS Base URL** 和 API Key。
2. 添加实际模型 ID，按模型能力选择视觉输入和图片传输方式，然后设为默认模型。
3. 使用内置测试检查连接、文本或视觉能力；错误信息以页面测试结果为准。
4. 按需配置 PushDeer 和课程提醒，并在相应课程设置中启用所需选项。

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
