"""学术评论句提取工具 - FastAPI Web 服务 (Vercel Serverless 适配版)"""

import base64
import logging
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.responses import HTMLResponse
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

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Vercel 环境只有 /tmp 可写
OUTPUT_DIR = os.path.join(tempfile.gettempdir(), "file_know_output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _file_to_base64(file_path: str) -> str:
    """将文件读取为 base64 编码字符串"""
    if file_path and os.path.exists(file_path):
        with open(file_path, "rb") as f:
            return base64.b64encode(f.read()).decode("utf-8")
    return ""


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
    """同步分析 - 处理完毕后直接返回结果（含文件 base64 数据）"""
    effective_api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")
    if not effective_api_key:
        return JSONResponse(
            status_code=400,
            content={"error": "请提供 API Key（通过页面输入或设置环境变量 ANTHROPIC_API_KEY）"},
        )

    # 保存上传的 PDF 到 /tmp
    upload_dir = os.path.join(OUTPUT_DIR, "uploads")
    os.makedirs(upload_dir, exist_ok=True)
    pdf_path = os.path.join(upload_dir, pdf_file.filename)
    with open(pdf_path, "wb") as f:
        content = await pdf_file.read()
        f.write(content)

    config = AppConfig(
        output_dir=OUTPUT_DIR,
        llm=LLMConfig(
            api_key=effective_api_key,
            base_url=base_url.rstrip("/"),
            model=model,
        ),
    )

    try:
        result = process_paper(
            pdf_path=pdf_path,
            config=config,
            provider=provider,
        )

        records_data = []
        files = {}

        if result["records"]:
            pdf_name = Path(pdf_path).stem
            out_dir = os.path.join(OUTPUT_DIR, pdf_name)

            # 打包 zip
            zip_path = os.path.join(out_dir, "全部结果.zip")
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for path in (
                    [result.get("highlighted_pdf_path", ""),
                     result.get("excel_path", "")]
                    + result.get("word_paths", [])
                ):
                    if path and os.path.exists(path):
                        zf.write(path, os.path.basename(path))

            # 将所有文件编码为 base64，直接在响应中返回
            files["zip"] = {
                "name": "全部结果.zip",
                "data": _file_to_base64(zip_path),
            }
            highlighted_pdf = result.get("highlighted_pdf_path", "")
            if highlighted_pdf:
                files["highlighted_pdf"] = {
                    "name": os.path.basename(highlighted_pdf),
                    "data": _file_to_base64(highlighted_pdf),
                }
            excel_path = result.get("excel_path", "")
            if excel_path:
                files["excel"] = {
                    "name": os.path.basename(excel_path),
                    "data": _file_to_base64(excel_path),
                }
            for i, wp in enumerate(result.get("word_paths", [])):
                files[f"word_{i}"] = {
                    "name": os.path.basename(wp),
                    "data": _file_to_base64(wp),
                }

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
            "files": files,
            "log": "\n".join(result.get("log", [])),
        }

    except Exception as e:
        logger.exception("分析任务失败")
        return JSONResponse(status_code=500, content={"error": str(e)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
