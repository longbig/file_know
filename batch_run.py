#!/usr/bin/env python3
"""学术评论句提取工具 - 本地批量处理 CLI

用法:
    python batch_run.py /path/to/pdf/folder
    python batch_run.py /path/to/pdf/folder --output ./results --model claude-opus-4-6

环境变量:
    ANTHROPIC_API_KEY   - API Key（必需，或通过 --api-key 参数传入）
    ANTHROPIC_BASE_URL  - API Base URL（可选，默认 https://timesniper.club）
"""

import argparse
import os
import sys
import logging
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import AppConfig, LLMConfig
from core.pipeline import process_paper
from core.excel_writer import write_merged_excel

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def find_pdfs(folder: str) -> list[str]:
    """递归查找文件夹下所有 PDF 文件"""
    pdf_files = []
    for root, _, files in os.walk(folder):
        for f in sorted(files):
            if f.lower().endswith(".pdf") and not f.startswith("."):
                pdf_files.append(os.path.join(root, f))
    return pdf_files


def process_single(pdf_path: str, config: AppConfig, provider: str, index: int, total: int):
    """处理单篇 PDF"""
    pdf_name = Path(pdf_path).stem
    logger.info(f"\n{'='*60}")
    logger.info(f"[{index}/{total}] 开始处理: {pdf_name}")
    logger.info(f"{'='*60}")

    try:
        result = process_paper(
            pdf_path=pdf_path,
            config=config,
            provider=provider,
            progress_callback=lambda msg: logger.info(f"  {msg}"),
        )

        record_count = len(result["records"])
        if record_count == 0:
            logger.warning(f"  未找到学术评论句，跳过文件生成")
            return {"name": pdf_name, "count": 0, "status": "无结果", "paper_data": None}

        # 打包 zip
        out_dir = os.path.join(config.output_dir, pdf_name)
        zip_path = os.path.join(out_dir, f"{pdf_name}_全部结果.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in (
                [result.get("highlighted_pdf_path", ""),
                 result.get("excel_path", "")]
                + result.get("word_paths", [])
            ):
                if path and os.path.exists(path):
                    zf.write(path, os.path.basename(path))

        logger.info(f"  识别到 {record_count} 条学术评论句")
        logger.info(f"  结果已保存: {out_dir}")

        # 保存合并 Excel 所需的数据
        paper_data = {
            "records": result["records"],
            "metadata": result["metadata"],
            "institution_results": result.get("institution_results"),
            "provider": provider,
        }
        return {"name": pdf_name, "count": record_count, "status": "成功", "paper_data": paper_data}

    except Exception as e:
        logger.error(f"  处理失败: {e}")
        return {"name": pdf_name, "count": 0, "status": f"失败: {e}", "paper_data": None}


def main():
    parser = argparse.ArgumentParser(
        description="学术评论句提取工具 - 批量处理本地 PDF 文件夹",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("input_dir", help="包含 PDF 文件的文件夹路径")
    parser.add_argument("--output", "-o", default="output", help="输出目录（默认: output）")
    parser.add_argument("--model", "-m", default="claude-sonnet-4-6",
                        choices=["claude-sonnet-4-6", "claude-opus-4-6"],
                        help="使用的模型（默认: claude-sonnet-4-6）")
    parser.add_argument("--api-key", default="", help="API Key（也可通过环境变量 ANTHROPIC_API_KEY 设置）")
    parser.add_argument("--base-url", default="", help="API Base URL（也可通过环境变量 ANTHROPIC_BASE_URL 设置）")
    parser.add_argument("--provider", "-p", default="", help="提供者信息（姓名/导师 学校 日期）")

    args = parser.parse_args()

    # 校验输入目录
    input_dir = os.path.abspath(args.input_dir)
    if not os.path.isdir(input_dir):
        logger.error(f"输入路径不存在或不是文件夹: {input_dir}")
        sys.exit(1)

    # 查找 PDF 文件
    pdf_files = find_pdfs(input_dir)
    if not pdf_files:
        logger.error(f"文件夹中未找到 PDF 文件: {input_dir}")
        sys.exit(1)

    logger.info(f"找到 {len(pdf_files)} 个 PDF 文件:")
    for i, f in enumerate(pdf_files, 1):
        logger.info(f"  {i}. {os.path.basename(f)}")

    # 构建配置
    api_key = args.api_key or os.getenv("ANTHROPIC_API_KEY", "")
    base_url = args.base_url or os.getenv("ANTHROPIC_BASE_URL", "https://timesniper.club")

    if not api_key:
        logger.error("未提供 API Key，请通过 --api-key 参数或 ANTHROPIC_API_KEY 环境变量设置")
        sys.exit(1)

    output_dir = os.path.abspath(args.output)
    os.makedirs(output_dir, exist_ok=True)

    config = AppConfig(
        output_dir=output_dir,
        llm=LLMConfig(
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=args.model,
        ),
    )

    # 批量处理
    results = []
    for i, pdf_path in enumerate(pdf_files, 1):
        result = process_single(pdf_path, config, args.provider, i, len(pdf_files))
        results.append(result)

    # 生成合并 Excel
    paper_data_list = [r["paper_data"] for r in results if r.get("paper_data")]
    merged_excel_path = ""
    if paper_data_list:
        merged_excel_path = os.path.join(output_dir, "合并汇总表.xlsx")
        write_merged_excel(merged_excel_path, paper_data_list)
        logger.info(f"合并 Excel 已保存: {merged_excel_path}")

    # 汇总报告
    logger.info(f"\n{'='*60}")
    logger.info("批量处理完成 - 汇总报告")
    logger.info(f"{'='*60}")
    success = [r for r in results if r["status"] == "成功"]
    failed = [r for r in results if r["status"].startswith("失败")]
    no_result = [r for r in results if r["status"] == "无结果"]

    logger.info(f"总计: {len(results)} 篇 | 成功: {len(success)} | 无结果: {len(no_result)} | 失败: {len(failed)}")
    total_count = sum(r["count"] for r in results)
    logger.info(f"共提取学术评论句: {total_count} 条")

    if success:
        logger.info("\n成功:")
        for r in success:
            logger.info(f"  - {r['name']}: {r['count']} 条")
    if no_result:
        logger.info("\n无结果:")
        for r in no_result:
            logger.info(f"  - {r['name']}")
    if failed:
        logger.info("\n失败:")
        for r in failed:
            logger.info(f"  - {r['name']}: {r['status']}")

    logger.info(f"\n输出目录: {output_dir}")


if __name__ == "__main__":
    main()
