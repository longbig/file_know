# 学术评论句提取工具 — Windows 部署文档

## 1. 项目概述

输入学术论文 PDF，自动识别其中引用他人研究成果的"学术评论句"（含标志词 + 被评作者 + 年份三要素），输出高亮标注 PDF、Excel 汇总表、Word 登记表。

提供 Web 界面（FastAPI），支持单篇上传和批量文件夹处理。

## 2. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Windows 10 / 11 / Windows Server 2019+ |
| Python | **3.10 ~ 3.12**（推荐 3.12，不支持 3.9 及以下） |
| 内存 | 最低 2GB，推荐 4GB+（MinerU PDF 解析较消耗内存） |
| 磁盘 | 项目本身约 50MB，venv + MinerU 模型约 3~5GB |
| 网络 | 需能访问 LLM API 端点 + CrossRef API（机构查询） |

## 3. Python 安装

### 3.1 下载安装 Python

从 [python.org](https://www.python.org/downloads/) 下载 Python 3.12 安装包。

安装时 **务必勾选**：
- ✅ `Add Python to PATH`
- ✅ `Install pip`

安装完成后验证：

```powershell
python --version
# 应输出 Python 3.12.x

pip --version
# 应输出 pip xx.x
```

## 4. Python 依赖包

### 4.1 核心依赖（requirements.txt）

| 包名 | 最低版本 | 已验证版本 | 用途 |
|------|---------|-----------|------|
| `PyMuPDF` | >=1.24.0 | 1.24.14 | PDF 文本提取、元数据解析、高亮标注 |
| `magic-pdf[full]` | >=0.9.0 | 1.3.12 | MinerU PDF 解析（高质量 Markdown 输出） |
| `openpyxl` | >=3.1.0 | 3.1.5 | Excel 汇总表生成 |
| `python-docx` | >=1.1.0 | 1.2.0 | Word 登记表生成 |
| `anthropic` | >=0.40.0 | 0.109.0 | LLM API 调用（OpenAI 兼容格式） |
| `pydantic` | >=2.0.0 | 2.10.6 | 数据校验 |
| `httpx` | >=0.27.0 | 0.28.1 | HTTP 客户端 |
| `fastapi` | >=0.115.0 | 0.136.3 | Web 服务框架 |
| `uvicorn` | >=0.30.0 | 0.49.0 | ASGI 服务器 |
| `python-multipart` | >=0.0.9 | 0.0.32 | FastAPI 文件上传支持 |

### 4.2 可选依赖

| 包名 | 用途 | 说明 |
|------|------|------|
| `rapidocr-onnxruntime` | 扫描版 PDF OCR | 文字层为空的 PDF 自动降级使用，不安装则跳过扫描版 |

## 5. 安装步骤

### 5.1 放置项目文件

将项目文件夹放在任意目录，例如：

```
C:\file_know\
```

> **注意**：路径中不要包含中文或空格，否则 MinerU 可能出错。

### 5.2 创建虚拟环境并安装依赖

以 **管理员身份** 打开 PowerShell 或 CMD，执行：

```powershell
cd C:\file_know

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# PowerShell:
.\venv\Scripts\Activate.ps1
# 如果报执行策略错误，先运行:
# Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# CMD:
# .\venv\Scripts\activate.bat

# 升级 pip
pip install --upgrade pip

# 安装核心依赖
pip install -r requirements.txt

# （可选）安装扫描版 PDF OCR 支持
pip install rapidocr-onnxruntime
```

### 5.3 下载 MinerU 模型

`magic-pdf` 首次运行前需下载模型文件（约 2~3GB）：

```powershell
# 如果 HuggingFace 访问不通，先设置镜像
$env:HF_ENDPOINT = "https://hf-mirror.com"

# 下载模型
magic-pdf --download-models
```

CMD 设置环境变量的写法：

```cmd
set HF_ENDPOINT=https://hf-mirror.com
magic-pdf --download-models
```

### 5.4 创建输出目录

```powershell
mkdir C:\file_know\output -Force
mkdir C:\file_know\logs -Force
```

## 6. 配置文件

### 6.1 `models.json` — LLM 模型与 API 配置

**路径**：`C:\file_know\models.json`

**必须修改**：`api_key` 改为你自己的 API Key。

```json
{
  "providers": [
    {
      "name": "provider_name",
      "base_url": "https://your-api-endpoint.com",
      "api_key": "sk-your-api-key-here",
      "models": [
        "claude-sonnet-4-6",
        "claude-opus-4-6",
        "gemini-2.5-pro"
      ]
    }
  ],
  "default_model": "claude-sonnet-4-6"
}
```

**字段说明**：

| 字段 | 说明 |
|------|------|
| `providers` | 数组，可配置多个 API 供应商 |
| `providers[].name` | 供应商标识名（仅用于内部识别） |
| `providers[].base_url` | API 端点 URL（OpenAI 兼容格式，程序会拼接 `/v1/chat/completions`） |
| `providers[].api_key` | API 密钥 |
| `providers[].models` | 该供应商支持的模型列表 |
| `providers[].auth_type` | 认证方式，可选 `"bearer"`（默认）或 `"api-key"` |
| `providers[].max_tokens_field` | 最大 token 字段名，可选 `"max_tokens"`（默认）或 `"max_completion_tokens"` |
| `providers[].extra_payload` | 额外请求参数（dict），会合并到 API 请求体中 |
| `default_model` | 默认使用的模型名（必须在某个 provider 的 models 列表中） |

**多供应商示例**（同时配置两个 API 源）：

```json
{
  "providers": [
    {
      "name": "primary",
      "base_url": "https://api.example.com",
      "api_key": "sk-xxx",
      "models": ["claude-sonnet-4-6", "claude-opus-4-6"]
    },
    {
      "name": "secondary",
      "base_url": "https://api.another.com",
      "api_key": "sk-yyy",
      "auth_type": "api-key",
      "max_tokens_field": "max_completion_tokens",
      "extra_payload": {
        "stream": false,
        "thinking": {"type": "disabled"},
        "response_format": {"type": "json_object"}
      },
      "models": ["mimo-v2.5-pro"]
    }
  ],
  "default_model": "claude-sonnet-4-6"
}
```

### 6.2 `markers.json` — 标志词配置

**路径**：`C:\file_know\markers.json`

**通常无需修改**。包含中英文标志词列表、裸词列表（单独出现不构成标志词）、自引检测词。

### 6.3 环境变量（备选配置方式）

如果不使用 `models.json`，也可通过环境变量配置 API。

PowerShell（当前会话生效）：

```powershell
$env:ANTHROPIC_API_KEY = "sk-your-api-key"
$env:ANTHROPIC_BASE_URL = "https://your-api-endpoint.com"
```

永久设置（系统级）：

```powershell
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-your-api-key", "Machine")
[System.Environment]::SetEnvironmentVariable("ANTHROPIC_BASE_URL", "https://your-api-endpoint.com", "Machine")
```

**优先级**：`models.json` > 环境变量 > 代码默认值

## 7. 启动方式

### 7.1 直接启动（开发/测试）

```powershell
cd C:\file_know
.\venv\Scripts\Activate.ps1

python app.py
# 服务监听在 http://0.0.0.0:7860
# 浏览器访问 http://localhost:7860
```

### 7.2 创建启动脚本

创建 `C:\file_know\start.bat`：

```bat
@echo off
cd /d C:\file_know
call venv\Scripts\activate.bat
python app.py
pause
```

双击 `start.bat` 即可启动服务。

### 7.3 创建 Windows 服务（生产部署）

使用 [NSSM](https://nssm.cc/download)（Non-Sucking Service Manager）将应用注册为 Windows 服务：

```powershell
# 1. 下载 nssm，将 nssm.exe 放到 C:\file_know\ 或系统 PATH 中

# 2. 安装为服务
nssm install FileKnow C:\file_know\venv\Scripts\python.exe app.py
nssm set FileKnow AppDirectory C:\file_know
nssm set FileKnow DisplayName "学术评论句提取工具"
nssm set FileKnow Description "学术评论句自动提取 Web 服务"
nssm set FileKnow Start SERVICE_AUTO_START
nssm set FileKnow AppStdout C:\file_know\logs\service_stdout.log
nssm set FileKnow AppStderr C:\file_know\logs\service_stderr.log

# 3. 启动服务
nssm start FileKnow

# 其他管理命令
nssm stop FileKnow       # 停止
nssm restart FileKnow    # 重启
nssm status FileKnow     # 查看状态
nssm remove FileKnow     # 卸载服务
```

### 7.4 开机自启（不用 NSSM 的简易方案）

1. 按 `Win + R`，输入 `shell:startup`，打开"启动"文件夹
2. 将 `start.bat` 的快捷方式放入该文件夹

## 8. Windows 防火墙配置

首次启动时 Windows 防火墙可能弹窗询问，选择 **允许访问**。

如果需要手动配置：

```powershell
# 允许 7860 端口入站（以管理员身份运行）
New-NetFirewallRule -DisplayName "FileKnow Web Service" -Direction Inbound -Protocol TCP -LocalPort 7860 -Action Allow
```

同一局域网的其他电脑访问：`http://<本机IP>:7860`

查看本机 IP：

```powershell
ipconfig
# 查看 "IPv4 地址" 行
```

## 9. CLI 批量处理

```powershell
cd C:\file_know
.\venv\Scripts\Activate.ps1

# 基本用法
python batch_run.py D:\papers\pdf_folder

# 指定输出目录和模型
python batch_run.py D:\papers\pdf_folder --output D:\results --model claude-opus-4-6

# 全部参数
python batch_run.py <input_dir> `
  --output <output_dir> `
  --model <model_name> `
  --api-key <api_key> `
  --base-url <base_url> `
  --provider <provider_name>
```

## 10. 输出文件结构

每篇 PDF 处理后生成以下文件：

```
output\
└── {pdf_name}\
    ├── {pdf_name}.md              # MinerU 解析的 Markdown 全文
    ├── {pdf_name}_高亮标注.pdf     # 评论句高亮标注的 PDF
    ├── {pdf_name}_汇总表.xlsx      # Excel 汇总表（26 列）
    ├── {pdf_name}_登记表_1.docx    # Word 登记表（每条评论句一份）
    ├── {pdf_name}_登记表_N.docx
    └── {pdf_name}_全部结果.zip     # 以上全部文件的打包
```

批量处理还会在 output 根目录生成 `批量结果报告.html`。

## 11. 项目文件结构

```
file_know\
├── app.py                    # FastAPI Web 服务入口
├── batch_run.py              # CLI 批量处理入口
├── config.py                 # 全局配置（自动读取 models.json）
├── models.json               # 【需配置】LLM API 供应商与模型
├── markers.json              # 标志词列表（通常不需修改）
├── requirements.txt          # Python 依赖
├── start.bat                 # 【需创建】Windows 启动脚本
├── templates\
│   └── index.html            # Web 前端页面
├── core\
│   ├── pipeline.py           # 5 阶段主流程编排
│   ├── pdf_parser.py         # PyMuPDF PDF 解析
│   ├── mineru_parser.py      # MinerU PDF 解析（高质量 Markdown）
│   ├── ref_parser.py         # 参考文献列表解析
│   ├── sentence_splitter.py  # 句子分割
│   ├── marker_matcher.py     # 标志词匹配
│   ├── author_extractor.py   # 作者名提取
│   ├── year_extractor.py     # 年份提取
│   ├── veto_rules.py         # 一票否决规则
│   ├── rule_engine.py        # 规则引擎总调度
│   ├── llm_analyzer.py       # LLM API 调用层
│   ├── prompts.py            # LLM Prompt 模板
│   ├── result_assembler.py   # 结果组装
│   ├── record_splitter.py    # 多文献记录拆分
│   ├── institution_lookup.py # CrossRef 机构查询
│   ├── pdf_highlighter.py    # PDF 高亮标注
│   ├── excel_writer.py       # Excel 汇总表生成
│   ├── word_writer.py        # Word 登记表生成
│   └── html_reporter.py      # 批量结果 HTML 报告
├── deploy\
│   └── setup.sh              # Linux 一键部署脚本（Windows 不适用）
├── output\                   # 运行时输出（自动创建）
└── logs\                     # 日志目录（自动创建）
```

## 12. 处理流程概览

```
PDF 文件输入
    │
    ▼
[阶段 1] 文本预处理（纯 Python）
    ├─ MinerU/PyMuPDF 解析 → 全文 Markdown + 元数据
    ├─ 参考文献列表解析 → 结构化 Reference
    └─ 句子分割 → 句子列表
    │
    ▼
[阶段 2] 规则引擎（纯 Python，零 LLM 调用）
    ├─ 标志词匹配
    ├─ 三要素校验（标志词 + 作者 + 年份）
    └─ 一票否决规则过滤
    │
    ▼
[阶段 3] LLM 语义判定（仅对候选句调用 LLM）
    ├─ 学术评价性判定
    └─ 标志词语义验证
    │
    ▼
[阶段 4] 结果组装
    ├─ 参考文献信息补全
    ├─ CrossRef 机构查询
    └─ 原文复检（防编造）
    │
    ▼
[阶段 5] 输出生成
    ├─ 高亮标注 PDF
    ├─ Excel 汇总表
    └─ Word 登记表 × N
```

## 13. 网络要求

| 方向 | 端口/地址 | 用途 |
|------|----------|------|
| 入站 | TCP 7860 | Web 服务访问端口（需在防火墙放行） |
| 出站 | LLM API 端点 | 模型调用（models.json 中配置的 base_url） |
| 出站 | api.crossref.org (443) | 被评文献机构查询（失败不影响主流程） |
| 出站 | hf-mirror.com (443) | MinerU 模型下载（仅首次安装） |

## 14. 常见问题

### Q: PowerShell 执行 Activate.ps1 报错 "无法加载文件，因为在此系统上禁止运行脚本"

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### Q: pip install 报错编译失败

部分包（如 `PyMuPDF`）需要 C++ 编译环境。安装 [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)，勾选 "C++ 桌面开发"。

或者直接使用预编译的 wheel（pip 默认优先使用 wheel，通常不需要手动编译）。

### Q: magic-pdf 命令找不到

确认虚拟环境已激活（命令行前缀显示 `(venv)`）。如果已激活仍找不到：

```powershell
# 检查安装位置
pip show magic-pdf

# 直接用完整路径执行
C:\file_know\venv\Scripts\magic-pdf.exe --version
```

### Q: magic-pdf 报错找不到模型

```powershell
$env:HF_ENDPOINT = "https://hf-mirror.com"
magic-pdf --download-models
```

### Q: MinerU 解析超时

当前超时设置为 1200 秒（20 分钟）。超大 PDF（>30MB）可能超时，程序会自动降级使用 PyMuPDF 解析，不会中断流程。

### Q: 扫描版 PDF 无法识别

```powershell
pip install rapidocr-onnxruntime
```

### Q: API 调用报 401/403

检查 `models.json` 中的 `api_key` 是否正确，`base_url` 是否可达。

### Q: 中文路径导致 PDF 解析失败

建议将项目和 PDF 文件放在纯英文路径下。如果必须使用中文路径，确保 Python 和系统的编码设置为 UTF-8：

```powershell
# 设置 Python UTF-8 模式
$env:PYTHONUTF8 = "1"
python app.py
```

### Q: 局域网其他电脑无法访问

1. 确认防火墙已放行 7860 端口（见第 8 节）
2. 确认使用本机 IP 而非 localhost 访问
3. 检查是否在同一子网内

## 15. 快速部署检查清单

- [ ] Python 3.10~3.12 已安装，`python --version` 正常
- [ ] 虚拟环境已创建并激活，`pip list` 能看到已安装的包
- [ ] `pip install -r requirements.txt` 全部成功
- [ ] `magic-pdf --version` 正常输出版本号
- [ ] `magic-pdf --download-models` 模型已下载
- [ ] `models.json` 已配置正确的 API Key 和 Base URL
- [ ] `python app.py` 启动无报错
- [ ] 浏览器访问 `http://localhost:7860` 能看到页面
- [ ] 上传测试 PDF 能正常处理并输出结果
