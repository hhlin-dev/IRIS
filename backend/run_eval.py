"""
IRIS 自动化评测 —— 一键启动脚本

用法:
    cd IRIS/backend
    python run_eval.py

    可选参数:
    python run_eval.py --cases 5        # 只跑前 5 条用例
    python run_eval.py --debug          # 单条调试模式 (只跑第 1 条)
    python run_eval.py --no-save        # 不保存报告文件
"""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.evaluation.evaluator import run_evaluation, save_report, run_single_case
from app.evaluation.test_set import load_test_set


def main():
    debug = "--debug" in sys.argv
    no_save = "--no-save" in sys.argv

    # 解析 --cases N
    limit = None
    for i, arg in enumerate(sys.argv):
        if arg == "--cases" and i + 1 < len(sys.argv):
            try:
                limit = int(sys.argv[i + 1])
            except ValueError:
                pass

    cases = load_test_set()
    if limit:
        cases = cases[:limit]

    print("=" * 60)
    print(f"  IRIS 自动化评测")
    print(f"  用例数: {len(cases)}")
    print(f"  模式: {'单条调试' if debug else '批量评测'}")
    print("=" * 60)

    if debug:
        result = run_single_case(cases[0])
        print(f"\n  用例: {result['case_id']}")
        print(f"  Reviewer: {result['graph']['review_status']} | Revisions: {result['graph']['revision_number']}")
        print(f"  综合分: {result['judge']['overall_score']}/100")
        print(f"  相关性: {result['judge']['relevance']}/5 | 完整性: {result['judge']['completeness']}/5 | 准确性: {result['judge']['accuracy']}/5 | 结构: {result['judge']['structure']}/5")
        print(f"  耗时: {result['elapsed_ms']}ms")
        print(f"  评语: {result['judge']['overall_comment']}")
        return

    report = run_evaluation(test_cases=cases)
    paths = save_report(report) if not no_save else {}

    summary = report["summary"]
    avg = summary["avg_scores"]
    print("\n" + "=" * 60)
    print("  评测完成")
    print("=" * 60)
    print(f"  综合平均分:  {avg.get('overall_score', 0)}/100")
    print(f"  相关性:      {avg.get('relevance', 0)}/5")
    print(f"  完整性:      {avg.get('completeness', 0)}/5")
    print(f"  准确性:      {avg.get('accuracy', 0)}/5")
    print(f"  结构质量:    {avg.get('structure', 0)}/5")
    print(f"  Reviewer 一次通过率: {summary.get('reviewer_first_pass_rate', 0)}%")
    print(f"  平均修正次数:        {summary.get('avg_revisions', 0)}")
    print(f"  总耗时:              {report['total_elapsed_ms']}ms")
    if paths:
        print(f"\n  报告已保存:")
        print(f"    JSON: {paths['json_path']}")
        print(f"    MD:   {paths['md_path']}")
    print("=" * 60)


if __name__ == "__main__":
    main()
