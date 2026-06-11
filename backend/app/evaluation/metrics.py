"""
Judge LLM 与指标计算。

评测维度：
1. 相关性 (relevance):     报告是否直接回应用户问题        [1-5]
2. 完整性 (completeness):  是否覆盖 expected_aspects        [1-5]
3. 准确性 (accuracy):      结论是否有事实错误或逻辑漏洞    [1-5]
4. 结构质量 (structure):   组织是否清晰、排版是否可读      [1-5]
"""

import json
import time
from typing import Any

from langchain_core.messages import HumanMessage

from app.utils.llm import get_llm

# Judge 使用聪明模型（temperature=0，确保评分一致性）
_judge_llm = None


def _get_judge():
    global _judge_llm
    if _judge_llm is None:
        _judge_llm = get_llm(model_type="smart")
    return _judge_llm


JUDGE_SYSTEM_PROMPT = """你是一个严格、公正的技术报告评估专家。
你需要对一份 AI 生成的调研报告进行多维度打分。

评分规则：
- 每个维度 1-5 分（整数），5 分 = 优秀，1 分 = 极差
- 相关性：报告是否直接回应用户问题？有没有跑题或答非所问？
- 完整性：报告是否覆盖了期望的关键方面？有没有明显遗漏？
- 准确性：报告的结论是否有事实依据？有没有明显错误或胡编乱造？
- 结构质量：报告的组织是否清晰？Markdown 排版是否易读？标题层级是否合理？

严格禁止：
- 不要因为报告"看起来很长"就给高分
- 不要因为不懂某个专业术语就扣分
- 如果报告明确指出了"资料不足"或"无法回答"，在准确性维度上不应扣分（诚实是加分项）

请严格按照以下 JSON 格式返回（不要包含 Markdown 代码块标记）：
{
    "relevance": <1-5>,
    "completeness": <1-5>,
    "accuracy": <1-5>,
    "structure": <1-5>,
    "overall_comment": "<一两句话的总体评价，指出最大的优点和最需要改进的地方>"
}"""


def _clean_json(text: str) -> str:
    """清洗 LLM 输出，提取 JSON 部分。"""
    text = (text or "").strip()
    text = text.replace("```json", "").replace("```", "").strip()
    l = text.find("{")
    r = text.rfind("}")
    if l != -1 and r != -1 and r > l:
        text = text[l : r + 1]
    return text


def judge_report(query: str, report: str, expected_aspects: list[str]) -> dict[str, Any]:
    """
    对生成的报告进行多维度评分。

    Returns:
        {
            "relevance": int,
            "completeness": int,
            "accuracy": int,
            "structure": int,
            "overall_score": float,   # 加权综合分 (0-100)
            "overall_comment": str,
            "judge_errors": [...]      # 降级/兜底说明
        }
    """
    llm = _get_judge()

    aspects_text = "\n".join(f"  - {a}" for a in expected_aspects)

    user_prompt = f"""用户问题：
{query}

期望覆盖的关键方面：
{aspects_text}

--- 待评估的报告 ---
{report[:8000]}
--- 报告结束 ---

请按 JSON 格式给出评分。"""

    full_prompt = f"{JUDGE_SYSTEM_PROMPT}\n\n{user_prompt}"

    errors = []

    try:
        raw = llm.invoke([HumanMessage(content=full_prompt)]).content
    except Exception as e:
        return {
            "relevance": 0,
            "completeness": 0,
            "accuracy": 0,
            "structure": 0,
            "overall_score": 0.0,
            "overall_comment": f"Judge LLM 调用失败: {e}",
            "judge_errors": [str(e)],
        }

    try:
        result = json.loads(_clean_json(raw))
    except json.JSONDecodeError:
        # 重试一次（更严厉的格式要求）
        retry_prompt = f"""{JUDGE_SYSTEM_PROMPT}

你的上一次输出无法被 JSON 解析。请只输出一行合法 JSON，不要 Markdown 标记，不要解释。

用户问题：{query}
报告：{report[:4000]}"""
        try:
            retry_raw = llm.invoke([HumanMessage(content=retry_prompt)]).content
            result = json.loads(_clean_json(retry_raw))
            errors.append("首次 JSON 解析失败，重试成功")
        except Exception:
            # 兜底评分
            errors.append(f"两次 JSON 解析均失败，使用兜底评分。raw preview: {raw[:200]}")
            result = {
                "relevance": 0,
                "completeness": 0,
                "accuracy": 0,
                "structure": 0,
                "overall_comment": "Judge 评分器输出格式异常，无法自动评分。请人工审阅报告质量。",
            }

    # 确保数值在合法范围内
    for key in ("relevance", "completeness", "accuracy", "structure"):
        try:
            result[key] = max(1, min(5, int(result.get(key, 0))))
        except (TypeError, ValueError):
            result[key] = 0
            errors.append(f"{key} 评分数值异常，已归零")

    # 加权综合分 (relevance 和 completeness 权重更高，因为面试官最关心)
    weights = {"relevance": 0.30, "completeness": 0.30, "accuracy": 0.25, "structure": 0.15}
    overall = sum(result.get(k, 0) * w for k, w in weights.items())
    result["overall_score"] = round(overall * 20, 1)  # 映射到 0-100
    result["overall_comment"] = result.get("overall_comment", "")
    result["judge_errors"] = errors

    return result


def aggregate_metrics(case_results: list[dict]) -> dict[str, Any]:
    """
    聚合所有用例的评测结果。

    Args:
        case_results: [{"case_id": ..., "judge": {...}, "graph": {...}}, ...]

    Returns:
        聚合指标字典
    """
    if not case_results:
        return {"error": "no results to aggregate"}

    n = len(case_results)
    scores = {
        "relevance": [],
        "completeness": [],
        "accuracy": [],
        "structure": [],
        "overall_score": [],
    }
    revisions = []
    reviewer_pass_count = 0
    should_stop_count = 0
    errors_count = 0
    category_breakdown: dict[str, list[float]] = {}

    for r in case_results:
        judge = r.get("judge", {})
        graph_info = r.get("graph", {})
        category = r.get("category", "unknown")

        for key in scores:
            val = judge.get(key, 0)
            try:
                scores[key].append(float(val))
            except (TypeError, ValueError):
                scores[key].append(0.0)

        revisions.append(graph_info.get("revision_number", 0))
        if graph_info.get("review_status") == "PASS":
            reviewer_pass_count += 1
        if graph_info.get("should_stop"):
            should_stop_count += 1
        if graph_info.get("error"):
            errors_count += 1

        if category not in category_breakdown:
            category_breakdown[category] = []
        category_breakdown[category].append(judge.get("overall_score", 0))

    def safe_avg(vals: list[float]) -> float:
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    category_avg = {cat: safe_avg(vals) for cat, vals in category_breakdown.items()}

    return {
        "total_cases": n,
        "errors": errors_count,
        "avg_scores": {key: safe_avg(vals) for key, vals in scores.items()},
        "reviewer_first_pass_rate": round(reviewer_pass_count / n * 100, 1),
        "avg_revisions": safe_avg([float(r) for r in revisions]),
        "should_stop_rate": round(should_stop_count / n * 100, 1),
        "category_breakdown": category_avg,
        "top_performer": _find_best(case_results),
        "needs_improvement": _find_worst(case_results),
    }


def _find_best(results: list[dict]) -> dict:
    best = max(results, key=lambda r: r.get("judge", {}).get("overall_score", 0))
    return {"case_id": best.get("case_id"), "overall_score": best.get("judge", {}).get("overall_score", 0)}


def _find_worst(results: list[dict]) -> dict:
    worst = min(results, key=lambda r: r.get("judge", {}).get("overall_score", 0))
    return {"case_id": worst.get("case_id"), "overall_score": worst.get("judge", {}).get("overall_score", 0)}
