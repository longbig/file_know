"""学术评论句提取工具 - FastAPI Web 服务"""

import asyncio
import logging
import os
import shutil
import sys
import tempfile
import uuid
import zipfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import JSONResponse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import AppConfig, LLMConfig
from core.pipeline import process_paper

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

app = FastAPI(title="学术评论句提取工具")
executor = ThreadPoolExecutor(max_workers=2)

# 任务状态存储
tasks: dict[str, dict] = {}

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def index():
    """主页"""
    html_path = os.path.join(BASE_DIR, "templates", "index.html")
    with open(html_path, "r", encoding="utf-8") as f:
        return f.read()


@app.post("/api/analyze")
async def analyze(
    pdf_file: UploadFile = File(...),
    model: str = Form("claude-sonnet-4-6"),
    api_key: str = Form(""),
    base_url: str = Form("https://timesniper.club"),
    provider: str = Form(""),
):
    """启动分析任务"""
    task_id = str(uuid.uuid4())[:8]

    # 保存上传的 PDF
    upload_dir = os.path.join(OUTPUT_DIR, f"upload_{task_id}")
    os.makedirs(upload_dir, exist_ok=True)
    pdf_path = os.path.join(upload_dir, pdf_file.filename)
    with open(pdf_path, "wb") as f:
        content = await pdf_file.read()
        f.write(content)

    # 初始化任务状态
    tasks[task_id] = {
        "progress": 0.0,
        "message": "任务已创建...",
        "done": False,
        "error": None,
        "result": None,
    }

    # 在后台线程中执行
    config = AppConfig(
        output_dir=OUTPUT_DIR,
        llm=LLMConfig(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
        ),
    )

    loop = asyncio.get_event_loop()
    loop.run_in_executor(
        executor,
        _run_task,
        task_id, pdf_path, config, provider,
    )

    return {"task_id": task_id}


def _run_task(task_id: str, pdf_path: str, config: AppConfig, provider: str):
    """在后台线程中执行分析任务"""
    step_progress = {
        "1": 0.05, "2": 0.15, "3": 0.25, "4": 0.60,
        "5": 0.70, "6": 0.80, "7": 0.90,
    }

    def progress_callback(msg: str):
        import re
        m = re.match(r'步骤\s*(\d+)/(\d+)', msg)
        if m:
            step = m.group(1)
            tasks[task_id]["progress"] = step_progress.get(step, 0)
        tasks[task_id]["message"] = msg

    try:
        result = process_paper(
            pdf_path=pdf_path,
            config=config,
            provider=provider,
            progress_callback=progress_callback,
        )

        # 打包 zip
        zip_path = ""
        records_data = []
        if result["records"]:
            pdf_name = Path(pdf_path).stem
            out_dir = os.path.join(OUTPUT_DIR, pdf_name)
            zip_path = os.path.join(out_dir, "全部结果.zip")
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for path in [result.get("highlighted_pdf_path", ""),
                             result.get("excel_path", "")] + result.get("word_paths", []):
                    if path and os.path.exists(path):
                        zf.write(path, os.path.basename(path))

            for r in result["records"]:
                records_data.append({
                    "sentence": r.评论句原文,
                    "marker": r.标志词,
                    "author": r.被评文献.第一作者,
                    "year": r.被评文献.年份,
                    "journal": r.被评文献.期刊名称,
                })

        tasks[task_id]["done"] = True
        tasks[task_id]["progress"] = 1.0
        tasks[task_id]["message"] = "处理完成"
        tasks[task_id]["result"] = {
            "count": len(result["records"]),
            "records": records_data,
            "highlighted_pdf": result.get("highlighted_pdf_path", ""),
            "excel_path": result.get("excel_path", ""),
            "word_paths": result.get("word_paths", []),
            "zip_path": zip_path,
            "log": "\n".join(result.get("log", [])),
        }

    except Exception as e:
        logger.exception(f"任务 {task_id} 失败")
        tasks[task_id]["error"] = str(e)
        tasks[task_id]["done"] = True


@app.get("/api/status/{task_id}")
async def get_status(task_id: str):
    """查询任务状态"""
    if task_id not in tasks:
        return JSONResponse(status_code=404, content={"error": "任务不存在"})
    return tasks[task_id]


@app.get("/download/{path:path}")
async def download(path: str):
    """文件下载"""
    # 安全检查：只允许下载 output 目录下的文件
    abs_path = os.path.abspath(path)
    if not abs_path.startswith(os.path.abspath(OUTPUT_DIR)):
        return JSONResponse(status_code=403, content={"error": "禁止访问"})

    if not os.path.exists(abs_path):
        return JSONResponse(status_code=404, content={"error": "文件不存在"})

    return FileResponse(
        abs_path,
        filename=os.path.basename(abs_path),
        media_type="application/octet-stream",
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
