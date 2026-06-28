# 学术评论句提取工具 — 部署文档

## 1. 项目概述

输入学术论文 PDF，自动识别其中引用他人研究成果的"学术评论句"（含标志词 + 被评作者 + 年份三要素），输出高亮标注 PDF、Excel 汇总表、Word 登记表。

提供 Web 界面（FastAPI），支持单篇上传和批量文件夹处理。

## 2. 系统要求

| 项目 | 要求 |
|------|------|
| 操作系统 | Ubuntu 20.04+ / macOS 12+ / 其他 Linux |
| Python | **3.10 ~ 3.12**（推荐 3.12，不支持 3.9 及以下，3.13+ 未验证） |
| 内存 | 最低 2GB，推荐 4GB+（MinerU PDF 解析较消耗内存） |
| 磁盘 | 项目本身约 50MB，venv + MinerU 模型约 3~5GB |
| 网络 | 需能访问 LLM API 端点 + CrossRef API（机构查询） |

## 3. Python 依赖包

### 3.1 核心依赖（requirements.txt）

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

### 3.2 可选依赖

| 包名 | 用途 | 说明 |
|------|------|------|
| `rapidocr-onnxruntime` | 扫描版 PDF OCR | 文字层为空的 PDF 自动降级使用，不安装则跳过扫描版 |
| `onnxruntime` | OCR 推理后端 | `rapidocr-onnxruntime` 的依赖 |

### 3.3 MinerU 模型下载

`magic-pdf` 首次运行前需下载模型文件（约 2~3GB）：

```bash
magic-pdf --download-models
```

下载过程中若 HuggingFace 访问不通，项目已内置 HF 镜像配置（`HF_ENDPOINT=https://hf-mirror.com`），也可手动设置：

```bash
export HF_ENDPOINT=https://hf-mirror.com
magic-pdf --download-models
```

## 4. 安装步骤

### 4.1 克隆/上传项目

```bash
# 将项目文件上传到服务器目标目录
mkdir -p /opt/file_know
# 方式一：rsync 上传
rsync -avz --exclude='.git' --exclude='output' --exclude='__pycache__' \
  --exclude='.DS_Store' --exclude='venv*' --exclude='.claude' \
  ./file_know/ /opt/file_know/
# 方式二：直接解压
# tar xzf file_know.tar.gz -C /opt/
```

### 4.2 创建虚拟环境并安装依赖

```bash
cd /opt/file_know
python3 -m venv venv
source venv/bin/activate

# 安装核心依赖
pip install --upgrade pip
pip install -r requirements.txt

# （可选）安装扫描版 PDF OCR 支持
pip install rapidocr-onnxruntime

# 下载 MinerU 模型
magic-pdf --download-models
```

### 4.3 创建运行目录

```bash
mkdir -p /opt/file_know/output
mkdir -p /opt/file_know/logs
```

## 5. 配置文件

### 5.1 `models.json` — LLM 模型与 API 配置

**路径**：项目根目录 `/opt/file_know/models.json`

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

### 5.2 `markers.json` — 标志词配置

**路径**：项目根目录 `/opt/file_know/markers.json`

**通常无需修改**。包含中英文标志词列表、裸词列表（单独出现不构成标志词）、自引检测词。

```json
{
  "chinese": ["首次成功合成", "首次公开报道", ...],
  "english": ["first described", "first proposed", ...],
  "bare_words": ["reported", "proposed", "described", ...],
  "self_citation_words": ["our group", "our team", ...]
}
```

### 5.3 `.env` — 环境变量（备选配置方式）

**路径**：`/opt/file_know/.env`

如果不使用 `models.json`，也可通过环境变量配置 API：

```bash
ANTHROPIC_API_KEY=sk-your-api-key
ANTHROPIC_BASE_URL=https://your-api-endpoint.com
```

**优先级**：`models.json` > 环境变量 > 代码默认值

### 5.4 `config.py` — 应用配置

**路径**：项目根目录 `/opt/file_know/config.py`

**通常无需修改**。包含 `LLMConfig` 和 `AppConfig` 两个 dataclass，自动从 `models.json` 和环境变量加载配置。

可调参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `LLMConfig.max_retries` | 3 | LLM 调用失败重试次数 |
| `LLMConfig.temperature` | 0.0 | LLM 采样温度 |
| `AppConfig.output_dir` | `"output"` | 输出目录 |

## 6. 启动方式

### 6.1 直接启动（开发/测试）

```bash
cd /opt/file_know
source venv/bin/activate
python app.py
# 服务监听在 http://0.0.0.0:7860
```

或使用 uvicorn：

```bash
uvicorn app:app --host 0.0.0.0 --port 7860
```

### 6.2 systemd 服务（生产部署）

创建 `/etc/systemd/system/file_know.service`：

```ini
[Unit]
Description=学术评论句提取工具
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=/opt/file_know
EnvironmentFile=/opt/file_know/.env
ExecStart=/opt/file_know/venv/bin/python app.py
Restart=on-failure
RestartSec=5
StandardOutput=append:/opt/file_know/logs/service.log
StandardError=append:/opt/file_know/logs/service.log

[Install]
WantedBy=multi-user.target
```

```bash
# 设置目录权限
chown -R www-data:www-data /opt/file_know

# 启用并启动服务
systemctl daemon-reload
systemctl enable file_know
systemctl start file_know

# 查看状态
systemctl status file_know

# 查看日志
tail -f /opt/file_know/logs/service.log
tail -f /opt/file_know/logs/app_$(date +%Y%m%d).log
```

### 6.3 一键部署脚本

项目提供了 `deploy/setup.sh`，在 Ubuntu 上以 root 执行即可自动完成全部部署：

```bash
cd /opt/file_know
bash deploy/setup.sh
```

## 7. CLI 批量处理

除 Web 界面外，也可通过命令行批量处理整个文件夹的 PDF：

```bash
cd /opt/file_know
source venv/bin/activate

# 基本用法
python batch_run.py /path/to/pdf/folder

# 指定输出目录和模型
python batch_run.py /path/to/pdf/folder --output ./results --model claude-opus-4-6

# 全部参数
python batch_run.py <input_dir> \
  --output <output_dir> \
  --model <model_name> \
  --api-key <api_key> \
  --base-url <base_url> \
  --provider <provider_name>
```

## 8. 输出文件结构

每篇 PDF 处理后生成以下文件：

```
output/
└── {pdf_name}/
    ├── {pdf_name}.md              # MinerU 解析的 Markdown 全文
    ├── {pdf_name}_高亮标注.pdf     # 评论句高亮标注的 PDF
    ├── {pdf_name}_汇总表.xlsx      # Excel 汇总表（26 列）
    ├── {pdf_name}_登记表_1.docx    # Word 登记表（每条评论句一份）
    ├── {pdf_name}_登记表_N.docx
    └── {pdf_name}_全部结果.zip     # 以上全部文件的打包
```

批量处理还会在 output 根目录生成 `批量结果报告.html`。

## 9. 项目文件结构

```
file_know/
├── app.py                    # FastAPI Web 服务入口
├── batch_run.py              # CLI 批量处理入口
├── config.py                 # 全局配置（自动读取 models.json）
├── models.json               # 【需配置】LLM API 供应商与模型
├── markers.json              # 标志词列表（通常不需修改）
├── requirements.txt          # Python 依赖
├── templates/
│   └── index.html            # Web 前端页面
├── core/
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
├── deploy/
│   └── setup.sh              # Ubuntu 一键部署脚本
├── output/                   # 运行时输出（自动创建）
└── logs/                     # 日志目录（自动创建）
```

## 10. 处理流程概览

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

## 11. 网络与防火墙

| 方向 | 端口/地址 | 用途 |
|------|----------|------|
| 入站 | TCP 7860 | Web 服务访问端口 |
| 出站 | LLM API 端点 | 模型调用（models.json 中配置的 base_url） |
| 出站 | api.crossref.org (443) | 被评文献机构查询（失败不影响主流程） |
| 出站 | hf-mirror.com (443) | MinerU 模型下载（仅首次安装） |

## 12. 常见问题

### Q: magic-pdf 报错找不到模型

运行 `magic-pdf --download-models` 下载模型。如 HuggingFace 不通：

```bash
export HF_ENDPOINT=https://hf-mirror.com
magic-pdf --download-models
```

### Q: MinerU 解析超时

当前超时设置为 1200 秒（20 分钟）。超大 PDF（>30MB）可能超时，程序会自动降级使用 PyMuPDF 解析，不会中断流程。

### Q: 扫描版 PDF 无法识别

安装 `rapidocr-onnxruntime`：

```bash
pip install rapidocr-onnxruntime
```

### Q: API 调用报 401/403

检查 `models.json` 中的 `api_key` 是否正确，`base_url` 是否可达。

### Q: 输出文件在哪里

默认在项目根目录的 `output/` 下，按 PDF 文件名创建子目录。Web 界面可直接下载 zip 包。
