"""批量处理结果 HTML 报告生成器"""

from datetime import datetime
from pathlib import Path


def write_batch_report(output_path: str, results: list[dict], output_dir: str = "") -> str:
    """生成批量处理结果 HTML 报告

    Args:
        output_path: HTML 文件保存路径
        results: 每篇论文的处理结果列表，每项包含：
            - name: str
            - count: int
            - status: str
            - records: list[CommentRecord]（可选）
            - metadata: PaperMetadata（可选）
        output_dir: 输出根目录（用于生成相对路径链接）

    Returns:
        HTML 文件路径
    """
    total = len(results)
    # 有结果的排前面，其次无结果，最后失败
    results = sorted(results, key=lambda r: (0 if r["status"] == "成功" else 1 if r["status"] == "无结果" else 2, -r.get("count", 0)))
    success = [r for r in results if r["status"] == "成功"]
    no_result = [r for r in results if r["status"] == "无结果"]
    failed = [r for r in results if r["status"].startswith("失败")]
    total_sentences = sum(r.get("count", 0) for r in results)

    rows = []
    for i, r in enumerate(results, 1):
        records = r.get("records", [])
        name = r["name"]
        status = r["status"]
        count = r.get("count", 0)

        if status == "成功":
            status_badge = f'<span class="badge ok">成功</span>'
        elif status == "无结果":
            status_badge = f'<span class="badge none">无结果</span>'
        else:
            status_badge = f'<span class="badge err">失败</span>'

        # 展开/折叠的评论句详情
        detail_rows = ""
        if records:
            sentence_items = []
            for rec in records:
                ep = rec.被评文献
                sentence_items.append(
                    f'<li>'
                    f'<span class="marker">{_esc(rec.标志词)}</span> '
                    f'<span class="author">{_esc(ep.第一作者)}({_esc(ep.年份)})</span> '
                    f'— {_esc(rec.评论句原文[:120])}{"…" if len(rec.评论句原文) > 120 else ""}'
                    f'</li>'
                )
            detail_rows = f'<ul class="sentences">{"".join(sentence_items)}</ul>'

        toggle_id = f"detail_{i}"
        detail_block = ""
        if records:
            detail_block = (
                f'<tr class="detail-row" id="{toggle_id}" style="display:none">'
                f'<td colspan="4">{detail_rows}</td></tr>'
            )
            name_cell = (
                f'<a href="#" onclick="toggle(\'{toggle_id}\');return false">'
                f'{_esc(name)}</a>'
            )
        else:
            name_cell = _esc(name)

        rows.append(
            f'<tr>'
            f'<td class="num">{i}</td>'
            f'<td>{name_cell}</td>'
            f'<td class="num">{count if count > 0 else "—"}</td>'
            f'<td>{status_badge}</td>'
            f'</tr>'
            + detail_block
        )

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>批量解析结果报告 — {now}</title>
<style>
  body {{ font-family: "PingFang SC", "Microsoft YaHei", sans-serif; font-size: 14px;
          color: #222; margin: 0; padding: 24px; background: #f5f5f5; }}
  h1 {{ font-size: 18px; margin-bottom: 4px; }}
  .meta {{ color: #666; font-size: 12px; margin-bottom: 20px; }}
  .summary {{ display: flex; gap: 16px; margin-bottom: 20px; }}
  .card {{ background: #fff; border-radius: 6px; padding: 12px 20px;
           box-shadow: 0 1px 3px rgba(0,0,0,.1); min-width: 90px; text-align: center; }}
  .card .num {{ font-size: 28px; font-weight: 700; }}
  .card .label {{ font-size: 11px; color: #888; margin-top: 2px; }}
  .card.green .num {{ color: #16a34a; }}
  .card.red .num {{ color: #dc2626; }}
  .card.orange .num {{ color: #ea580c; }}
  .card.blue .num {{ color: #2563eb; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff;
           border-radius: 6px; box-shadow: 0 1px 3px rgba(0,0,0,.1); overflow: hidden; }}
  th {{ background: #f0f0f0; text-align: left; padding: 8px 12px;
        font-size: 12px; color: #555; border-bottom: 1px solid #ddd; }}
  td {{ padding: 7px 12px; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
  tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: center; width: 60px; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 12px; }}
  .badge.ok {{ background: #dcfce7; color: #15803d; }}
  .badge.none {{ background: #fef9c3; color: #854d0e; }}
  .badge.err {{ background: #fee2e2; color: #b91c1c; }}
  a {{ color: #2563eb; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  ul.sentences {{ margin: 4px 0 8px 0; padding-left: 18px; list-style: disc; }}
  ul.sentences li {{ margin-bottom: 4px; line-height: 1.5; }}
  .marker {{ background: #dbeafe; color: #1e40af; border-radius: 3px;
             padding: 1px 5px; font-size: 12px; }}
  .author {{ color: #7c3aed; font-weight: 600; }}
  .detail-row td {{ background: #fafafa; }}
</style>
</head>
<body>
<h1>批量解析结果报告</h1>
<div class="meta">生成时间：{now} &nbsp;|&nbsp; 共处理 {total} 篇论文</div>
<div class="summary">
  <div class="card blue"><div class="num">{total}</div><div class="label">总计</div></div>
  <div class="card green"><div class="num">{len(success)}</div><div class="label">成功</div></div>
  <div class="card orange"><div class="num">{len(no_result)}</div><div class="label">无结果</div></div>
  <div class="card red"><div class="num">{len(failed)}</div><div class="label">失败</div></div>
  <div class="card blue"><div class="num">{total_sentences}</div><div class="label">评论句总数</div></div>
</div>
<table>
  <thead><tr>
    <th class="num">#</th>
    <th>论文名称</th>
    <th class="num">评论句数</th>
    <th>状态</th>
  </tr></thead>
  <tbody>{"".join(rows)}</tbody>
</table>
<script>
function toggle(id) {{
  var el = document.getElementById(id);
  el.style.display = el.style.display === 'none' ? 'table-row' : 'none';
}}
</script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path


def _esc(s: str) -> str:
    """HTML 转义"""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
