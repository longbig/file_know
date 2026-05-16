"""学术评论句提取工具 - FastAPI Web 服务（本地版，支持单篇上传 + 批量文件夹）"""

import logging
import os
import sys
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from starlette.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import AppConfig, LLMConfig
from core.pipeline import process_paper
from core.excel_writer import write_merged_excel

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(BASE_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

# 日志同时输出到控制台和文件
log_file = os.path.join(LOG_DIR, f"app_{datetime.now().strftime('%Y%m%d')}.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file, encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

app = FastAPI(title="学术评论句提取工具")
executor = ThreadPoolExecutor(max_workers=2)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# 任务状态存储（本地运行，内存状态可靠）
tasks: dict[str, dict] = {}


# ────────────────── 页面 ──────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(BASE_DIR, "templates", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


# ────────────────── 单篇分析 ──────────────────

@app.post("/api/analyze")
async def analyze(
    pdf_file: UploadFile = File(...),
    model: str = Form("claude-sonnet-4-6"),
    api_key: str = Form(""),
    base_url: str = Form("https://timesniper.club"),
    provider: str = Form(""),
):
    """单篇 PDF 同步分析"""
    effective_api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not effective_api_key:
        return JSONResponse(status_code=400, content={"error": "请提供 API Key"})

    upload_dir = os.path.join(OUTPUT_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    pdf_path = os.path.join(upload_dir, pdf_file.filename)
    with open(pdf_path, "wb") as f:
        f.write(await pdf_file.read())

    config = AppConfig(
        output_dir=OUTPUT_DIR,
        llm=LLMConfig(api_key=effective_api_key, base_url=base_url.rstrip("/"), model=model),
    )

    try:
        result = process_paper(pdf_path=pdf_path, config=config, provider=provider)
        return _build_single_response(result, pdf_path)
    except Exception as e:
        logger.exception("单篇分析失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


# ────────────────── 批量分析 ──────────────────

@app.post("/api/batch")
async def batch_analyze(
    folder_path: str = Form(...),
    model: str = Form("claude-sonnet-4-6"),
    api_key: str = Form(""),
    base_url: str = Form("https://timesniper.club"),
    provider: str = Form(""),
):
    """批量分析 - 读取本地文件夹中的所有 PDF"""
    effective_api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not effective_api_key:
        return JSONResponse(status_code=400, content={"error": "请提供 API Key"})

    folder = os.path.abspath(folder_path.strip())
    if not os.path.isdir(folder):
        return JSONResponse(status_code=400, content={"error": f"文件夹不存在: {folder}"})

    pdf_files = sorted([
        os.path.join(folder, f) for f in os.listdir(folder)
        if f.lower().endswith(".pdf") and not f.startswith(".")
    ])
    if not pdf_files:
        return JSONResponse(status_code=400, content={"error": f"文件夹中未找到 PDF 文件"})

    task_id = str(uuid.uuid4())[:8]
    tasks[task_id] = {
        "type": "batch",
        "total": len(pdf_files),
        "completed": 0,
        "current_file": "",
        "current_step": "",
        "done": False,
        "error": None,
        "results": [],
        "pdf_names": [Path(f).stem for f in pdf_files],
    }

    config = AppConfig(
        output_dir=OUTPUT_DIR,
        llm=LLMConfig(api_key=effective_api_key, base_url=base_url.rstrip("/"), model=model),
    )

    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_in_executor(executor, _run_batch, task_id, pdf_files, config, provider)

    return {"task_id": task_id, "total": len(pdf_files), "files": [Path(f).name for f in pdf_files]}


def _run_batch(task_id: str, pdf_files: list[str], config: AppConfig, provider: str):
    """后台线程：逐个处理 PDF"""
    paper_data_list = []  # 收集每篇论文的数据，用于生成合并 Excel

    for i, pdf_path in enumerate(pdf_files):
        pdf_name = Path(pdf_path).stem
        tasks[task_id]["current_file"] = pdf_name
        tasks[task_id]["current_step"] = "开始处理..."

        try:
            result = process_paper(
                pdf_path=pdf_path,
                config=config,
                provider=provider,
                progress_callback=lambda msg, tid=task_id: _update_step(tid, msg),
            )

            record_count = len(result["records"])
            out_dir = os.path.join(config.output_dir, pdf_name)

            # 打包
            if record_count > 0:
                zip_path = os.path.join(out_dir, f"{pdf_name}_全部结果.zip")
                with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for p in ([result.get("highlighted_pdf_path", ""),
                               result.get("excel_path", "")]
                              + result.get("word_paths", [])):
                        if p and os.path.exists(p):
                            zf.write(p, os.path.basename(p))

                # 收集合并 Excel 数据
                paper_data_list.append({
                    "records": result["records"],
                    "metadata": result["metadata"],
                    "institution_results": result.get("institution_results"),
                    "provider": provider,
                })
            else:
                zip_path = ""

            tasks[task_id]["results"].append({
                "name": pdf_name,
                "count": record_count,
                "status": "成功" if record_count > 0 else "无结果",
                "output_dir": out_dir if record_count > 0 else "",
                "zip_path": zip_path,
            })

        except Exception as e:
            logger.exception(f"批量处理失败: {pdf_name}")
            tasks[task_id]["results"].append({
                "name": pdf_name, "count": 0, "status": f"失败: {e}",
                "output_dir": "", "zip_path": "",
            })

        tasks[task_id]["completed"] = i + 1

    # 生成合并 Excel
    merged_excel_path = ""
    if paper_data_list:
        tasks[task_id]["current_step"] = "生成合并汇总表..."
        merged_excel_path = os.path.join(config.output_dir, "合并汇总表.xlsx")
        try:
            write_merged_excel(merged_excel_path, paper_data_list)
            logger.info(f"合并 Excel 已保存: {merged_excel_path}")
        except Exception as e:
            logger.error(f"生成合并 Excel 失败: {e}")
            merged_excel_path = ""

    tasks[task_id]["done"] = True
    tasks[task_id]["current_file"] = ""
    tasks[task_id]["current_step"] = "全部完成"
    tasks[task_id]["merged_excel_path"] = merged_excel_path


def _update_step(task_id: str, msg: str):
    if task_id in tasks:
        tasks[task_id]["current_step"] = msg


@app.get("/api/batch/status/{task_id}")
async def batch_status(task_id: str):
    if task_id not in tasks:
        return JSONResponse(status_code=404, content={"error": "任务不存在"})
    return tasks[task_id]


# ────────────────── 文件下载 ──────────────────

@app.get("/download/{path:path}")
async def download(path: str):
    if not path.startswith("/"):
        path = "/" + path
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(OUTPUT_DIR)):
        return JSONResponse(status_code=403, content={"error": "禁止访问"})
    if not os.path.exists(abs_path):
        return JSONResponse(status_code=404, content={"error": "文件不存在"})
    return FileResponse(abs_path, filename=os.path.basename(abs_path),
                        media_type="application/octet-stream")


# ────────────────── 工具函数 ──────────────────

def _build_single_response(result: dict, pdf_path: str) -> dict:
    records_data = []
    output_dir = ""

    if result["records"]:
        pdf_name = Path(pdf_path).stem
        out_dir = os.path.join(OUTPUT_DIR, pdf_name)
        output_dir = out_dir

        zip_path = os.path.join(out_dir, f"{pdf_name}_全部结果.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in ([result.get("highlighted_pdf_path", ""),
                       result.get("excel_path", "")]
                      + result.get("word_paths", [])):
                if p and os.path.exists(p):
                    zf.write(p, os.path.basename(p))

        for r in result["records"]:
            records_data.append({
                "sentence": r.评论句原文,
                "marker": r.标志词,
                "author": r.被评文献.第一作者,
                "year": r.被评文献.年份,
                "journal": r.被评文献.期刊名称,
            })

    return {
        "count": len(result["records"]),
        "records": records_data,
        "output_dir": output_dir,
        "log": "\n".join(result.get("log", [])),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
