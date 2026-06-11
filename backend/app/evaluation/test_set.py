"""
内置评测用例集。

每个用例包含：
- id: 唯一标识
- query: 用户问题
- search_mode: "hybrid" 或 "document"
- expected_aspects: 期望报告覆盖的关键方面（Judge LLM 据此评分）
- category: 问题类型，用于分组统计
- weight: 权重（某些用例可能更重要）
"""

DEFAULT_TEST_CASES = [
    {
        "id": "tc_001",
        "query": "什么是 Transformer 架构中的自注意力机制（Self-Attention）？请解释其计算流程。",
        "search_mode": "hybrid",
        "expected_aspects": [
            "Q/K/V 三个矩阵的含义和来源",
            "缩放点积注意力的计算公式",
            "多头注意力的并行机制",
            "自注意力相比 RNN/CNN 的优势",
        ],
        "category": "technical_explanation",
        "weight": 1.0,
    },
    {
        "id": "tc_002",
        "query": "比较 RAG（检索增强生成）和传统微调方案在大模型应用中的优缺点。",
        "search_mode": "hybrid",
        "expected_aspects": [
            "成本对比（训练成本 vs 检索成本）",
            "知识实时性",
            "幻觉控制能力",
            "系统维护复杂度",
            "适用场景分析",
        ],
        "category": "comparative_analysis",
        "weight": 1.2,
    },
    {
        "id": "tc_003",
        "query": "2024-2025 年 AI Agent 领域最重要的技术突破有哪些？",
        "search_mode": "hybrid",
        "expected_aspects": [
            "多智能体编排框架的进展",
            "工具使用和函数调用能力的提升",
            "规划与推理能力的演进",
            "记忆与上下文管理的改进",
        ],
        "category": "trend_analysis",
        "weight": 1.0,
    },
    {
        "id": "tc_004",
        "query": "请解释 LangGraph 中 StateGraph 和条件边（Conditional Edge）的概念及它们如何协同工作。",
        "search_mode": "hybrid",
        "expected_aspects": [
            "StateGraph 作为有状态图的结构定义",
            "条件边的路由函数和分支映射",
            "Checkpointer 持久化机制的作用",
            "与 LCEL 线性链的对比",
        ],
        "category": "technical_explanation",
        "weight": 1.3,
    },
    {
        "id": "tc_005",
        "query": "如何科学地评估一个 RAG 系统的检索质量？请列出关键指标和方法。",
        "search_mode": "hybrid",
        "expected_aspects": [
            "召回率 (Recall) 和精确率 (Precision)",
            "MRR (Mean Reciprocal Rank)",
            "NDCG (Normalized Discounted Cumulative Gain)",
            "Faithfulness（忠实度）评估",
            "Answer Relevance 评估方法",
        ],
        "category": "methodology",
        "weight": 1.1,
    },
    {
        "id": "tc_006",
        "query": "大模型应用中的 Prompt 注入攻击有哪些常见形式？如何防御？",
        "search_mode": "hybrid",
        "expected_aspects": [
            "直接注入和间接注入的区别",
            "越狱攻击 (Jailbreak) 的常见手法",
            "输入过滤与净化策略",
            "权限隔离与最小权限原则",
            "输出审核与监控机制",
        ],
        "category": "security",
        "weight": 1.0,
    },
    {
        "id": "tc_007",
        "query": "什么是 MCP（Model Context Protocol）协议？它解决了 AI 应用中的什么问题？",
        "search_mode": "hybrid",
        "expected_aspects": [
            "MCP 的客户端-服务器架构",
            "标准化工具暴露机制",
            "与 Function Calling 的对比",
            "在 Agent 系统中的实际价值",
        ],
        "category": "technical_explanation",
        "weight": 1.2,
    },
    {
        "id": "tc_008",
        "query": "Agent 系统中的记忆管理有哪些设计模式？各自的适用场景是什么？",
        "search_mode": "hybrid",
        "expected_aspects": [
            "短期记忆 vs 长期记忆的划分",
            "滑动窗口与摘要压缩策略",
            "向量记忆 (Vector Memory) 方案",
            "结构化画像 (Structured Profile) 方案",
            "Token 预算管理与裁剪策略",
        ],
        "category": "architecture_design",
        "weight": 1.1,
    },
]

# 文档模式下使用的测试用例（需要先上传相关文档）
DOCUMENT_MODE_CASES = [
    {
        "id": "tc_doc_001",
        "query": "根据上传的论文，Transformer 架构中的位置编码有哪几种实现方式？",
        "search_mode": "document",
        "expected_aspects": [
            "正弦位置编码的原理",
            "可学习位置编码",
            "相对位置编码方案",
        ],
        "category": "document_qa",
        "weight": 1.0,
    },
]


def load_test_set(custom_cases_path: str | None = None) -> list[dict]:
    """加载评测用例，优先使用自定义用例，否则使用内置默认用例。"""
    if custom_cases_path:
        import json
        import os
        if os.path.exists(custom_cases_path):
            with open(custom_cases_path, "r", encoding="utf-8") as f:
                return json.load(f)

    return DEFAULT_TEST_CASES


def get_document_test_cases() -> list[dict]:
    """返回文档模式专用评测用例。"""
    return DOCUMENT_MODE_CASES
