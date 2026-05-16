# CLAUDE.md

## 项目概述

**学术评论句提取工具** — 输入学术论文 PDF，自动识别其中引用他人研究成果的"学术评论句"（含标志词 + 被评作者 + 年份三要素），输出高亮标注 PDF、Excel 汇总表、Word 登记表。

## 技术栈

- Python 3.10+（使用了 `list[str]`、`dict | None` 等类型语法）
- FastAPI + uvicorn（Web 服务）
- PyMuPDF / fitz（PDF 解析与高亮）
- openpyxl（Excel 写入）
- python-docx（Word 写入）
- anthropic / httpx（LLM API 调用，OpenAI 兼容格式）
- pydantic v2（数据校验）

## 运行方式

### 本地 Web 服务
```bash
python app.py
# 或 uvicorn app:app --host 0.0.0.0 --port 7860
# 访问 http://localhost:7860
```

### CLI 批量处理
```bash
python batch_run.py /path/to/pdf/folder
python batch_run.py /path/to/pdf/folder --output ./results --model claude-opus-4-6
```

### 环境变量
```
ANTHROPIC_API_KEY   # 必需
ANTHROPIC_BASE_URL  # 可选，默认 https://timesniper.club
```

## 项目结构

```
file_know/
├── app.py                    # FastAPI Web 服务入口（单篇上传 + 批量文件夹）
├── batch_run.py              # CLI 批量处理入口
├── config.py                 # 全局配置（LLMConfig / AppConfig dataclass）
├── vercel.json               # Vercel 部署配置（目前已不使用，保留备用）
├── requirements.txt
├── templates/
│   └── index.html            # 前端页面（双 Tab：单篇上传 / 批量处理）
├── core/
│   ├── __init__.py           # 空文件
│   ├── pipeline.py           # 主流程编排（7 步）
│   ├── pdf_parser.py         # PDF 文本提取 + 元数据解析
│   ├── ref_parser.py         # 参考文献列表解析
│   ├── llm_analyzer.py       # Claude API 调用 + Pydantic 数据模型
│   ├── prompts.py            # System/User Prompt 模板
│   ├── pdf_highlighter.py    # PDF 高亮标注
│   ├── excel_writer.py       # Excel 汇总表生成（26 列）
│   ├── word_writer.py        # Word 登记表生成（每条评论句一份）
│   └── institution_lookup.py # CrossRef API 机构/国家查询
├── deploy/
│   └── setup.sh              # 腾讯云 Ubuntu 一键部署脚本
└── output/                   # 运行时生成，每篇 PDF 一个子目录
```

## 各文件说明

### `config.py`
全局配置，dataclass 结构。
- `LLMConfig`: api_key, base_url, model, max_retries=3, temperature=0.0
- `AppConfig`: output_dir="output", llm=LLMConfig

### `app.py`
FastAPI Web 服务入口。

路由：
- `GET /` — 返回前端页面
- `POST /api/analyze` — 单篇 PDF 同步分析（接收 pdf_file, model, api_key, base_url, provider）
- `POST /api/batch` — 批量分析（接收 folder_path），后台线程执行，返回 task_id
- `GET /api/batch/status/{task_id}` — 查询批量任务进度
- `GET /download/{path}` — 文件下载（限 output/ 目录内）

批量任务使用 `ThreadPoolExecutor(max_workers=2)` + 内存 `tasks` 字典存储状态。

### `batch_run.py`
CLI 批量入口。argparse 参数：input_dir, --output, --model, --api-key, --base-url, --provider。递归查找 PDF，顺序处理，输出汇总报告。

### `core/pipeline.py`
核心流程编排函数 `process_paper(pdf_path, config, provider, progress_callback) -> dict`。

7 步流程：
1. `pdf_parser.parse_pdf()` — 提取全文 + 元数据
2. `ref_parser.parse_references()` — 解析参考文献列表（含 DOI 提取）
3. `llm_analyzer.call_llm()` — LLM 提取评论句（含机构知识推断）
4. 后处理校验 — 用参考文献过滤非期刊文献，补全卷期页码，保存匹配 Reference（含 DOI）
5. 机构查询（三级回退）— LLM 已填机构 → DOI 精确查询 → 标题搜索，结果写回 record
6. `pdf_highlighter.highlight_sentences()` — 高亮标注
7. `excel_writer.write_excel()` + `word_writer.write_word() × N` — 生成输出文件

返回：`{records, excel_path, word_paths, highlighted_pdf_path, metadata, log}`

### `core/llm_analyzer.py`
LLM 调用层。使用 OpenAI 兼容格式 `POST {base_url}/v1/chat/completions`（非 Anthropic 原生 SDK）。

数据模型（Pydantic）：
- `EvaluatedPaper` — 被评文献字段
- `CommentRecord` — 评论句原文 + 标志词 + 被评文献
- `AnalysisResult` — list[CommentRecord]

关键函数：`call_llm(full_text, authors, config) -> AnalysisResult`

### `core/prompts.py`
LLM 提示词常量。`SYSTEM_PROMPT` 含准入规则、一票否决规则、特殊情形处理、标志词列表（中文约 100 个、英文 600+ 个，完整覆盖附件2汇总表）、JSON 输出格式要求。`USER_PROMPT_TEMPLATE` 含 `{authors}` 和 `{full_text}` 占位符。

关键规则更新（2026-04-21）：
- 年代词（`the 1950s`、`the late 1970s`）算有年份
- `as early as`/`earliest` 等时间定位词算有年份
- 句子中"在文【7,8】中"类编号说明算有年份
- `independently` + 两作者两文献 → 拆分两条记录
- 单独使用不构成标志词的词：`reported/proposed/discovered/discovery/described/published/demonstrated/suggested/provided`
- JSON 新增 `其他被评文献` 字段

关键规则更新（2026-05-15）：
- 强化准入规则三要素：必须在句子正文文本中显式出现，不能仅通过参考文献编号反查补全
- 新增三要素反面示例（缺少作者、缺少年份、自引）
- 自引检测词表扩充：our group/team/lab/previous work、we/us/ourselves、本课题组/我们/笔者等
- 新增 §5.5 被评文献机构信息填写要求：要求 LLM 利用训练知识推断第一作者机构和国家

### `core/pdf_parser.py`
基于 PyMuPDF 的 PDF 解析。提取全文、文本块（带坐标）、元数据（标题/作者/期刊/年份/DOI/机构等）。

### `core/ref_parser.py`
从全文定位参考文献段落，按 `[数字]` 编号分割，区分中英文解析路径，识别文献类型（J/C/D/M）。
- `Reference` 数据类含 `doi` 字段（2026-05-15 新增），中英文参考文献均支持 DOI 提取
- 关键：`find_reference_by_author_year(references, author, year)` 用于后处理校验

### `core/pdf_highlighter.py`
PDF 高亮标注，三级降级定位策略：完整句子搜索 → 去引用标记搜索 → 关键词定位。

### `core/excel_writer.py`
生成 26 列 Excel 汇总表（GB/T 7714 格式）。含施评/被评文献的全部字段、机构国家、标志词、提供者等。

### `core/word_writer.py`
每条评论句生成一份 Word 登记表（16 行 × 5 列表格），严格匹配模板格式。

格式规范（2026-05-15 更新）：
- 页面：A4 纵向，四边距 1.27cm
- 字体：西文 Times New Roman / 中文 宋体 / 12pt（标题 14pt）
- 列宽：1427/2476/4994/702/778 twips（共 5 列）
- 行高：[580,1012,976,648,688,480,615,442,472,1609,2541,648,648,541,494,494] twips
- 标题行："2026年学术评论句登记表"
- D/E 列表头："第1次反馈"/"第2次反馈"

加粗规则（5 条）：
- a. 施评文献作者栏：只加粗第一作者**姓氏**（`_extract_surname()`）
- b. 期刊栏：只加粗年份（`_write_bold_journal()`）
- c. 评论句栏：加粗标志词 + 第一作者姓氏 + 年份（`_write_bold_sentence()`）
- d. 标志词严格按标志词表，去除前后介词/be动词（`_clean_marker()`）
- e. 被评文献栏：加粗第一作者姓氏 + 年份（`_write_bold_ref()`）

### `core/institution_lookup.py`
通过 CrossRef 免费 API 查询被评文献作者机构和国家。超时 15s，失败静默处理不阻断主流程。

机构查询策略（2026-05-15 更新为三级回退）：
1. `lookup_by_doi(doi)` — DOI 精确查询 `api.crossref.org/works/{DOI}`
2. `lookup_institution(title, first_author, year, doi)` — 标题搜索（带 Jaccard 相似度 ≥ 0.3 校验）
3. `_title_similarity()` — 防止误匹配不相关论文
4. `_infer_country()` — 从机构名推断国家（返回中文国家名，覆盖 40+ 国家）

### `templates/index.html`
单页前端。双 Tab 切换：单篇上传（拖拽上传 PDF）/ 批量处理（输入本地文件夹路径）。批量模式通过轮询 `/api/batch/status/{task_id}` 实时显示每个文件的处理状态和汇总统计。

## 数据流

```
PDF 文件
  ↓
[1] pdf_parser.parse_pdf() → full_text + metadata
  ↓
[2] ref_parser.parse_references() → list[Reference]
  ↓
[3] llm_analyzer.call_llm() → list[CommentRecord]
  ↓
[3.5] 后处理校验：过滤非期刊 + 补全字段 + 保存匹配 Reference（含 DOI）
  ↓
[4] 机构查询（三级回退）：LLM 已填 → DOI 查询 → 标题搜索
  ↓
[5] pdf_highlighter → 高亮 PDF
[6] excel_writer → Excel 汇总表
[7] word_writer × N → Word 登记表
  ↓
[打包] → {pdf_name}_全部结果.zip
```

## 输出目录结构

```
output/
└── {pdf_name}/
    ├── {pdf_name}_高亮标注.pdf
    ├── {pdf_name}_汇总表.xlsx
    ├── {pdf_name}_登记表_1.docx
    ├── {pdf_name}_登记表_N.docx
    └── {pdf_name}_全部结果.zip
```

## 服务器部署

### 部署环境
- 腾讯云 2核2G Ubuntu 24.04 LTS（124.221.7.242）
- Python 3.12.3，运行内存约 60MB，资源充裕

### 部署结构
```
/opt/file_know/
├── venv/           # Python 虚拟环境
├── .env            # 环境变量（API Key，权限 600）
├── output/         # 运行时输出
├── logs/           # 日志（app 日志 + systemd 服务日志）
└── ...             # 项目代码
```

### 服务管理（systemd）
```bash
systemctl start file_know       # 启动
systemctl stop file_know        # 停止
systemctl restart file_know     # 重启
systemctl status file_know      # 查看状态
tail -f /opt/file_know/logs/service.log  # 查看日志
```

### 更新代码
```bash
# 本地执行
rsync -avz --exclude='.git' --exclude='output' --exclude='__pycache__' \
  --exclude='.DS_Store' --exclude='english' --exclude='venv' --exclude='.claude' \
  -e "ssh -i ~/.ssh/id_rsa" \
  /Users/yuyunlong/CursorProject/file_know/ root@124.221.7.242:/opt/file_know/
# 服务器执行
ssh root@124.221.7.242 "systemctl restart file_know"
```

### 访问地址
- http://124.221.7.242:7860（需在腾讯云安全组开放 TCP 7860 端口）

### `deploy/setup.sh`
腾讯云 Ubuntu 一键部署脚本。自动完成：系统依赖安装、虚拟环境创建、pip 依赖安装、目录权限配置、.env 模板生成、systemd 服务注册。

## 关键设计决策

1. **LLM 调用格式**：使用 OpenAI 兼容格式（`/v1/chat/completions`），非 Anthropic 原生 SDK，方便接入中转站。
2. **两级过滤**：LLM 语义识别 + ref_parser 类型校验（非期刊剔除），降低误判。
3. **PDF 高亮三级降级**：完整句子 → 去引用标记 → 关键词定位，应对跨行文本。
4. **机构查询三级回退**（2026-05-15）：LLM 知识推断 → DOI 精确查询 → 标题搜索（带相似度校验），显著提升中文文献机构识别率。
5. **批量任务**：内存 dict 存储状态，仅适合单进程本地运行。
