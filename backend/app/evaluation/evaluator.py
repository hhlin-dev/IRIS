"""
自动化评测编排器。

为每个测试用例运行 IRIS 的完整管线（planner → researcher → writer → reviewer），
收集中间状态，再用 Judge LLM 对最终报告进行多维度评分，最后聚合输出评测报告。

复用节点：plan_node, research_node, write_node, review_node（不修改原有代码）
独立管线：不走 SSE 流式，不依赖 checkpointer，批量执行
"""

import json
import os
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langgraph.graph import StateGraph, END

from app.graph.state import AgentState
from app.graph.nodes.planner import plan_node
from app.graph.nodes.researcher import research_node
from app.graph.nodes.writer import write_node
from app.graph.nodes.reviewer import review_node
from app.evaluation.metrics import judge_report, aggregate_metrics
from app.evaluation.test_set import load_test_set

# 输出目录（相对于 backend 目录）
DEFAULT_OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "evaluation_reports")

# 最大重试次数（与原始 reviewer 的 should_continue 对齐）
MAX_REVISIONS = 3


# ---- 评测专用图：不包含 route_query / refiner，简化但保留自校正循环 ----

def _eval_should_continue(state: AgentState) -> str:
    """评测版条件路由：与原始 should_continue 逻辑一致。"""
    if state.get("revision_number", 0) >= MAX_REVISIONS:
        return END
    if state.get("review_status") == "FAIL":
        return "planner"
    return END


def _route_after_research(state: AgentState) -> str:
    """与原始 route_after_research 逻辑一致。"""
    if state.get("should_stop", False):
        return END
    return "writer"


def _build_eval_graph():
    """构建评测专用图，拓扑：planner → researcher → writer → reviewer → [loop | END]"""
    workflow = StateGraph(AgentState)

    workflow.add_node("planner", plan_node)
    workflow.add_node("researcher", research_node)
    workflow.add_node("writer", write_node)
    workflow.add_node("reviewer", review_node)

    workflow.set_entry_point("planner")

    workflow.add_edge("planner", "researcher")
    workflow.add_conditional_edges("researcher", _route_after_research, {"writer": "writer", END: END})
    workflow.add_edge("writer", "reviewer")
    workflow.add_conditional_edges("reviewer", _eval_should_continue, {"planner": "planner", END: END})

    return workflow.compile()


_eval_app = None


def _get_eval_app():
    global _eval_app
    if _eval_app is None:
        _eval_app = _build_eval_graph()
    return _eval_app


# ---- 单用例运行 ----

def run_single_case(test_case: dict, case_index: int = 0) -> dict[str, Any]:
    """
    对单个测试用例运行完整的 IRIS 管线 + Judge 评分。

    Args:
        test_case: {"id", "query", "search_mode", "expected_aspects", "category", "weight"}
        case_index: 序号

    Returns:
        {case_id, category, query, graph: {...}, judge: {...}, elapsed_ms}
    """
    case_id = test_case.get("id", f"case_{case_index}")
    query = test_case["query"]
    search_mode = test_case.get("search_mode", "hybrid")
    expected_aspects = test_case.get("expected_aspects", [])

    initial_state: AgentState = {
        "query": query,
        "plan": [],
        "search_results": [],
        "final_report": "",
        "critique": "",
        "revision_number": 0,
        "review_status": "",
        "search_mode": search_mode,
        "should_stop": False,
    }

    t0 = time.perf_counter()
    graph_error = None

    try:
        app = _get_eval_app()
        final_state = app.invoke(initial_state)
    except Exception as e:
        graph_error = str(e)
        traceback.print_exc()
        final_state = {
            "final_report": f"[GRAPH ERROR] {e}",
            "review_status": "ERROR",
            "revision_number": 0,
            "should_stop": False,
            "search_results": [],
            "plan": [],
        }

    elapsed_ms = round((time.perf_counter() - t0) * 1000)

    graph_summary = {
        "review_status": final_state.get("review_status", "UNKNOWN"),
        "revision_number": final_state.get("revision_number", 0),
        "should_stop": final_state.get("should_stop", False),
        "search_results_count": len(final_state.get("search_results", [])),
        "plan_steps": final_state.get("plan", []),
        "error": graph_error,
    }

    # Judge 评分
    final_report = final_state.get("final_report", "")
    if graph_error or not final_report.strip():
        judge_result = {
            "relevance": 0, "completeness": 0, "accuracy": 0, "structure": 0,
            "overall_score": 0.0,
            "overall_comment": f"管线错误，无法评分: {graph_error}" if graph_error else "报告为空，无法评分",
            "judge_errors": [graph_error] if graph_error else ["empty report"],
        }
    else:
        judge_result = judge_report(query, final_report, expected_aspects)

    return {
        "case_id": case_id,
        "category": test_case.get("category", "general"),
        "query": query,
        "graph": graph_summary,
        "judge": judge_result,
        "elapsed_ms": elapsed_ms,
    }


# ---- 批量评测 ----

def run_evaluation(test_cases: list[dict] | None = None,
                   custom_cases_path: str | None = None,
                   progress_callback=None) -> dict[str, Any]:
    """
    批量运行所有评测用例。

    Args:
        test_cases: 自定义用例列表，为 None 则使用内置默认用例
        custom_cases_path: 自定义用例 JSON 文件路径
        progress_callback: 可选，每完成一个用例时回调 callback(index, total, result)

    Returns:
        完整评测报告 dict
    """
    if test_cases is None:
        test_cases = load_test_set(custom_cases_path)

    results = []
    total = len(test_cases)
    t_start = time.perf_counter()

    for i, case in enumerate(test_cases):
        print(f"\n{'='*60}")
        print(f"[Evaluation] [{i+1}/{total}] 正在评测: {case.get('id', '?')} - {case['query'][:60]}...")
        print(f"{'='*60}")

        result = run_single_case(case, case_index=i)
        results.append(result)

        print(f"  Reviewer: {result['graph']['review_status']} | "
              f"Revisions: {result['graph']['revision_number']} | "
              f"Score: {result['judge']['overall_score']}/100 | "
              f"Time: {result['elapsed_ms']}ms")

        if progress_callback:
            try:
                progress_callback(i, total, result)
            except Exception:
                pass

        # 避免触发 API 限流
        if i < total - 1:
            time.sleep(2.0)

    total_elapsed_ms = round((time.perf_counter() - t_start) * 1000)

    summary = aggregate_metrics(results)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_elapsed_ms": total_elapsed_ms,
        "test_cases_count": total,
        "summary": summary,
        "results": results,
    }

    return report


# ---- 报告渲染与持久化 ----

def render_markdown_report(report: dict[str, Any]) -> str:
    """生成 Markdown 格式的评测报告。"""
    summary = report.get("summary", {})
    results = report.get("results", [])
    avg = summary.get("avg_scores", {})

    lines = [
        "# IRIS 自动化评测报告",
        "",
        f"**生成时间**: {report.get('generated_at', 'N/A')}",
        f"**总耗时**: {report.get('total_elapsed_ms', 0)}ms",
        f"**评测用例数**: {report.get('test_cases_count', 0)}",
        "",
        "---",
        "",
        "## 总览",
        "",
        "| 指标 | 值 |",
        "|------|----|",
        f"| 综合平均分 | **{avg.get('overall_score', 0)}/100** |",
        f"| 相关性 (Relevance) | {avg.get('relevance', 0)}/5 |",
        f"| 完整性 (Completeness) | {avg.get('completeness', 0)}/5 |",
        f"| 准确性 (Accuracy) | {avg.get('accuracy', 0)}/5 |",
        f"| 结构质量 (Structure) | {avg.get('structure', 0)}/5 |",
        f"| Reviewer 一次通过率 | {summary.get('reviewer_first_pass_rate', 0)}% |",
        f"| 平均修正次数 | {summary.get('avg_revisions', 0)} |",
        f"| Should-Stop 触发率 | {summary.get('should_stop_rate', 0)}% |",
        f"| 管线错误数 | {summary.get('errors', 0)} |",
        "",
    ]

    # 分类细分
    category_breakdown = summary.get("category_breakdown", {})
    if category_breakdown:
        lines.append("## 按题型分类")
        lines.append("")
        lines.append("| 类别 | 平均分 |")
        lines.append("|------|--------|")
        for cat, score in sorted(category_breakdown.items()):
            lines.append(f"| {cat} | {score}/100 |")
        lines.append("")

    # 各用例详情
    lines.append("---")
    lines.append("")
    lines.append("## 各用例详情")
    lines.append("")

    for i, r in enumerate(results, 1):
        judge = r.get("judge", {})
        graph = r.get("graph", {})
        lines.append(f"### {i}. [{r.get('case_id')}] {r.get('query', '')[:80]}")
        lines.append("")
        lines.append(f"- **类别**: {r.get('category', 'N/A')}")
        lines.append(f"- **综合分**: **{judge.get('overall_score', 0)}/100**")
        lines.append(f"- **评分明细**: 相关性={judge.get('relevance', 0)}, "
                     f"完整性={judge.get('completeness', 0)}, "
                     f"准确性={judge.get('accuracy', 0)}, "
                     f"结构={judge.get('structure', 0)}")
        lines.append(f"- **管线状态**: Reviewer={graph.get('review_status', '?')}, "
                     f"修正次数={graph.get('revision_number', 0)}, "
                     f"ShouldStop={graph.get('should_stop', False)}")
        lines.append(f"- **耗时**: {r.get('elapsed_ms', 0)}ms")
        if graph.get("error"):
            lines.append(f"- **管线错误**: {graph['error']}")
        comment = judge.get("overall_comment", "")
        if comment:
            lines.append(f"- **评语**: {comment}")
        plan_steps = graph.get("plan_steps", [])
        if plan_steps:
            lines.append(f"- **搜索规划**: {' → '.join(plan_steps[:5])}")
        lines.append("")

    # 改进建议
    worst = summary.get("needs_improvement", {})
    best = summary.get("top_performer", {})
    lines.append("---")
    lines.append("")
    lines.append("## 改进建议")
    lines.append("")
    lines.append(f"- **最佳表现**: {best.get('case_id', '?')} ({best.get('overall_score', 0)}/100)")
    lines.append(f"- **最需改进**: {worst.get('case_id', '?')} ({worst.get('overall_score', 0)}/100)")
    lines.append("")
    lines.append("### 下一步行动")
    lines.append("")
    lines.append("1. 针对低分用例，检查是搜索策略不足还是 Writer 表达问题")
    lines.append("2. 若 Reviewer 一次通过率低于 60%，考虑加强 Planner 的搜索关键词生成")
    lines.append("3. 若 Should-Stop 触发频繁但 Judge 评分低，检查 Relevance Grader 的阈值")
    lines.append("4. 将评测报告作为回测基线，每次修改管线后重新跑，对比分数变化")
    lines.append("")

    return "\n".join(lines)


def save_report(report: dict[str, Any], output_dir: str | None = None) -> dict[str, str]:
    """
    保存评测报告为 JSON + Markdown。

    Returns:
        {"json_path": str, "md_path": str}
    """
    out = Path(output_dir or DEFAULT_OUTPUT_DIR)
    out.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = out / f"evaluation_{timestamp}.json"
    md_path = out / f"evaluation_{timestamp}.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    # 同时写入 latest 快捷方式
    (out / "evaluation_latest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "evaluation_latest.md").write_text(render_markdown_report(report), encoding="utf-8")

    return {"json_path": str(json_path), "md_path": str(md_path)}
