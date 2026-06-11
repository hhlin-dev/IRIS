# IRIS 自动化评测与回测模块

> 命中 JD 第 5 条：建立自动化评测与回测机制，通过调优与 Case 分析不断收敛效果与性能提升的最优路径。

---

## 一、文件位置

```
IRIS/
├── backend/
│   ├── run_eval.py                        # ← 新增：一键启动脚本
│   ├── app/
│   │   ├── evaluation/                    # ← 新增：评测模块
│   │   │   ├── __init__.py                #     模块标记
│   │   │   ├── test_set.py                #     8 条内置评测用例
│   │   │   ├── metrics.py                 #     Judge LLM + 4 维度评分 + 指标聚合
│   │   │   └── evaluator.py               #     评测编排器 + 专用 StateGraph + 报告渲染
│   │   ├── api/
│   │   │   └── routes.py                  # ← 修改：新增 4 个评测 API 端点
│   │   ├── graph/                         #     未修改：复用原有节点函数
│   │   │   ├── nodes/
│   │   │   │   ├── planner.py             #     复用
│   │   │   │   ├── researcher.py          #     复用
│   │   │   │   ├── writer.py              #     复用
│   │   │   │   └── reviewer.py            #     复用
│   │   │   ├── state.py                   #     未修改
│   │   │   └── graph.py                   #     未修改
│   │   └── ...
│   ├── evaluation_reports/                # ← 新增：评测报告输出目录
│   │   ├── evaluation_YYYYMMDD_HHMMSS.json
│   │   ├── evaluation_YYYYMMDD_HHMMSS.md
│   │   ├── evaluation_latest.json         #     最新报告快捷方式 (JSON)
│   │   └── evaluation_latest.md           #     最新报告快捷方式 (Markdown)
│   └── main.py                            #     未修改
└── docs/
    └── EVALUATION_MODULE.md               # ← 本文档
```

**关键设计原则：零侵入。** 评测模块复用了 `planner.py / researcher.py / writer.py / reviewer.py` 的节点函数但未修改它们任何一行代码。`graph.py` 中原有的会话 StateGraph 完全没有变动。

---

## 二、运行启动方式

### 前提条件

确保 `backend/.env` 中已配置 API Key。

### 一键启动（推荐）

```bash
cd IRIS/backend
python run_eval.py
```

就像启动后端 `uvicorn main:app --reload` 一样，一句命令即可跑完 8 条用例并自动保存报告。

**可选参数：**

```bash
python run_eval.py --debug       # 单条调试模式（只跑第 1 条，快速验证）
python run_eval.py --cases 3     # 只跑前 3 条用例
python run_eval.py --no-save     # 不保存报告文件（仅打印结果到终端）
```

运行结束后终端直接打印关键指标：

```
============================================================
  评测完成
============================================================
  综合平均分:  92.9/100
  相关性:      5.0/5
  完整性:      4.1/5
  准确性:      4.6/5
  结构质量:    5.0/5
  Reviewer 一次通过率: 100.0%
  平均修正次数:        1.0
  总耗时:              632319ms

  报告已保存:
    JSON: .../evaluation_reports/evaluation_YYYYMMDD_HHMMSS.json
    MD:   .../evaluation_reports/evaluation_YYYYMMDD_HHMMSS.md
============================================================
```

### 通过 API 调用（配合前端 Swagger 使用）

```bash
# 启动后端
cd IRIS/backend
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# 发起评测
curl -X POST http://localhost:8000/api/evaluate

# 查询进度
curl http://localhost:8000/api/evaluate/status

# 下载最新报告
curl "http://localhost:8000/api/evaluate/report/latest?format=md"
```

### API 端点一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/evaluate` | 启动一次完整评测（内置 8 条用例或自定义） |
| GET | `/api/evaluate/status` | 查询评测进度和最近一次摘要 |
| GET | `/api/evaluate/report/latest?format=json` | 下载最新报告 (JSON) |
| GET | `/api/evaluate/report/latest?format=md` | 下载最新报告 (Markdown) |
| GET | `/api/evaluate/report/history` | 列出历史评测报告文件 |
| POST | `/api/chat` | （原有）会话交互 |
| POST | `/api/upload` | （原有）文档上传 |
| POST | `/api/clear` | （原有）清空知识库 |

---

## 三、架构设计

### 评测图 vs 会话图

```
原有的会话图 (不变):                    新增的评测图 (独立):

START → route_query                     START
  ├→ planner                             ￬
  │   ￬                               planner  ←──────┐
  │  researcher                          ￬              │
  │   ￬                               researcher       │
  │  writer ← route_after_research       ￬              │ (循环)
  │   ￬                               writer           │
  │  reviewer                            ￬              │
  │   ￬→ should_continue               reviewer ───────┘
  │       ￬→ END / planner(loop)        ￬
  └→ refiner → END                      END
  (SSE 流式, 有 checkpointer)            (批量同步, 无 checkpointer)
```

评测图复用了 `plan_node / research_node / write_node / review_node` 四个节点函数，但构建了独立的 `StateGraph`。

### 评测流程

```
For each test_case (共 8 条):
  1. planner → 根据 query 生成 3-5 个搜索关键词
  2. researcher → 检索 ChromaDB + Tavily 搜索，含 Relevance Grader
  3. writer → 基于搜索结果撰写 Markdown 报告
  4. reviewer → 审查报告质量，输出 PASS/FAIL
     ├── FAIL → 回到 planner 重试（最多 3 次）
     └── PASS → 进入 Judge 评分
  5. Judge LLM → 4 维度评分（相关性/完整性/准确性/结构）

所有用例完成后：
  → 聚合指标（平均分/通过率/修正次数/分类细分）
  → 生成 evaluation_YYYYMMDD_HHMMSS.json + .md
```

---

## 四、8 条内置评测用例

| ID | 类别 | 权重 | 考查能力 |
|----|------|------|---------|
| tc_001 | technical_explanation | 1.0 | Transformer 自注意力机制——技术概念解释 |
| tc_002 | comparative_analysis | 1.2 | RAG vs 微调——对比分析能力 |
| tc_003 | trend_analysis | 1.0 | 2024-2025 AI Agent 技术突破——趋势梳理 |
| tc_004 | technical_explanation | 1.3 | LangGraph StateGraph 与条件边——框架机制解释 |
| tc_005 | methodology | 1.1 | RAG 检索质量评估——方法论系统性 |
| tc_006 | security | 1.0 | Prompt 注入攻击与防御——安全攻防 |
| tc_007 | technical_explanation | 1.2 | MCP 协议——新协议/标准理解 |
| tc_008 | architecture_design | 1.1 | Agent 记忆管理设计模式——架构模式分析 |

用例设计直接对齐 JD 技术要求：Agent、LangGraph、RAG、MCP、Prompt 注入、记忆管理。

---

## 五、Judge LLM 评分维度

| 维度 | 权重 | 说明 |
|------|------|------|
| 相关性 (Relevance) | 30% | 报告是否直接回应用户问题？有没有跑题？ |
| 完整性 (Completeness) | 30% | 是否覆盖 expected_aspects 的各个关键方面？ |
| 准确性 (Accuracy) | 25% | 结论是否有事实依据？有无明显错误或编造？ |
| 结构质量 (Structure) | 15% | Markdown 排版清晰度、标题层级、可读性 |

综合分 = 加权 × 20，映射到 0-100。Judge 使用 `model_type="smart"`（temperature=0），通过 `get_llm()` 工厂函数独立调用。

可靠性与兜底策略：
- 首次 JSON 解析失败 → 重试一次（更严格的格式要求）
- 两次均失败 → fail-closed 保守评分 + 标记 judge_errors
- 评分值异常 → 自动归零并记录

---

## 六、运行结果（2026-05-29 实测）

### 总览

| 指标 | 数值 |
|------|------|
| 综合平均分 | **94.9/100** |
| 相关性 | **5.0/5 (满分)** |
| 完整性 | 4.2/5 |
| 准确性 | 4.9/5 |
| 结构质量 | **5.0/5 (满分)** |
| Reviewer 一次通过率 | **100%** |
| 平均修正次数 | 1.0 |
| Should-Stop 触发率 | 0% |
| 管线错误数 | 0 |
| 总耗时 | 595s (~9.9 min, 平均 74s/条) |

### 按题型分类

| 题型 | 平均分 | 排名 |
|------|--------|------|
| 方法论 | 100.0 | 1 |
| 趋势分析 | 95.0 | 2 |
| 技术解释 | 94.0 | 3 (并列) |
| 对比分析 | 94.0 | 3 (并列) |
| 安全攻防 | 94.0 | 3 (并列) |
| 架构设计 | 94.0 | 3 (并列) |

### 各用例详细

| ID | 题型 | 分数 | 评语摘要 |
|----|------|------|---------|
| tc_001 | 自注意力机制 | 94 | 详细深入，逻辑清晰。多头注意力具体实现细节可补充 |
| tc_002 | RAG vs 微调 | 94 | 对比全面深入，逻辑性强。成本对比可更具体量化 |
| tc_003 | AI Agent 突破 | 95 | 全面深入，完整性满分。部分数据来源引用可更具体 |
| tc_004 | LangGraph 概念 | 94 | 解析深入准确。Checkpointer 持久化与 LCEL 对比可补充 |
| tc_005 | RAG 检索评估 | **100** | **满分。全面覆盖所有期望方面，信息准确，结构清晰** |
| tc_006 | Prompt 注入 | 94 | 详实全面。直接/间接注入区别与权限隔离讨论可补充 |
| tc_007 | MCP 协议 | 94 | 相关清晰，信息准确。Agent 系统中实际应用案例可补充 |
| tc_008 | 记忆管理 | 94 | 准确详实。向量记忆与 Token 预算裁剪策略可补充 |

---

## 七、面试话术

### 第一层：一句话概括

> 我在 IRIS 项目基础上设计并实现了一套独立的自动化评测与回测管线。它复用了现有的 Planner/Researcher/Writer/Reviewer 四个节点，构建了专用的评测 StateGraph，用 8 条多题型测试用例和 Judge LLM 对每次生成结果进行 4 维度自动评分。实测综合平均分 94.9/100，其中方法论类满分 100 分，Reviewer 一次通过率 100%，管线零错误。这套机制可以在每次改动代码后重新跑，形成可量化对比的回测基线。

### 第二层：为什么做这个

> 两层原因。第一，JD 第 5 条明确要求"建立自动化评测与回测机制"——这是岗位的直接需求。第二，从工程角度看，Agent 系统的最大问题是输出质量不可控。LLM 每次生成的报告质量参差不齐，手工评估 8 条用例要花 1-2 小时且标准不一致。用 Judge LLM 做标准化评分，10 分钟就能拿到可对比的量化结果。

### 第三层：技术设计

> 设计上我做了三个关键决策：
>
> **第一，不侵入原有管线。** 评测图是独立编译的 StateGraph，和会话图共享节点函数但互不干扰。会话图走 SSE 流式，评测图走批量同步——两个模式的代码完全解耦。
>
> **第二，Judge LLM 和业务 LLM 分离。** Judge 用的是 temperature=0 的 smart 模型，确保评分一致性；业务管线用的是 temperature=0.7 的 fast 模型，保证输出多样性。两者通过 `get_llm(model_type)` 工厂函数共用配置但独立调用。
>
> **第三，评分维度的权重设计有工程依据。** 相关性 30%、完整性 30%、准确性 25%、结构 15%——面试官最关心"有没有回答到点上"和"有没有遗漏关键信息"，这两项占了 60%。

### 第四层：数据解读

> 综合平均分 94.9，相关性满分 5.0——说明 IRIS 在"不跑题"上做到了极致。完整性 4.2 是最低维度，说明能找到对的内容但偶尔覆盖不全，这跟搜索结果的丰富度直接相关。方法论类 tc_005 拿了满分 100——说明 IRIS 在系统性方法论类题目上表现最佳，这也是面试官最可能感兴趣的题目类型。架构设计类 tc_008 从上次的 77 分提升到 94 分，说明管线调优后对记忆管理设计模式的覆盖更全面了。最低分 94 分，所有题型分数高度一致——说明系统在不同类型题目上的表现稳定。Reviewer 一次通过率 100% 不完全是个好消息——可能意味着 Reviewer 的 FAIL 阈值设得偏高，应该进一步收紧让更多报告被打回重写。

### 面试官可能追问的 5 个问题

**Q1: "Judge LLM 自己也可能 hallucinate，你怎么保证评分是可信的？"**

> 三层防护：temperature=0 消除随机性，结构化 JSON 输出便于校验，JSON 解析失败的重试+兜底机制。但坦诚说，Judge LLM 评分和人类评估的相关性需要用人工标注校准——这是我设计的下一步验证实验。

**Q2: "为什么只有 8 条用例？"**

> 8 条是经过设计的。6 种题型保证对不同能力的覆盖测试，不是同质化堆量。每条平均 79 秒，8 条共 10 分钟——这个时间成本在开发迭代中可持续。如果放 100 条跑一次要 2 小时，就失去了"快速回测"的意义。

**Q3: "Should-Stop 触发率 0%，是不是熔断机制没发挥作用？"**

> 0% 是预期行为——8 条用例的 search_mode 都是 hybrid。Hybrid 模式下即使本地文档不相关，系统会自动降级到全网搜索，不会触发熔断。熔断只在 document-only 模式下激活。验证熔断需要用文档模式专用用例。

**Q4: "平均修正次数 1.0，是不是每次都修了 1 次？"**

> 1.0 是因为 IRIS 的图拓扑决定了每个查询至少经过 1 轮 Reviewer——这是设计的必然结果。真正区分指标是修订次数是否 ≥2，才说明 Reviewer 发现了问题并触发了重写循环。

**Q5: "这个评测模块跟 JD 有什么关系？"**

> 直接对应 JD 第 5 条。阿里/蚂蚁需要的是能系统性地衡量和改进 AI 系统质量的人。这个模块做的事就是：把主观的"报告好不好"变成客观的 4 维度分数，形成可追踪的基线，每次改代码都能量化对比——这就是"从单点验证走向规模化落地"的工程实践。

---

## 八、简历 STAR 写法

```
● 自动化评测与回归测试体系：基于 LangGraph 构建独立评测管线，
  复用 4 个核心节点（Planner/Researcher/Writer/Reviewer），
  设计 6 类题型 8 条测试用例覆盖技术解释、对比分析、趋势梳理、
  方法论、安全攻防与架构设计。通过 Judge LLM 对每次生成结果
  进行 4 维度自动评分（相关性/完整性/准确性/结构），
  综合平均分 94.9/100（方法论类满分 100），Reviewer 一次通过率 100%，
  形成可量化对比的回测基线，支持每次管线迭代后快速回归验证。
```

---

## 九、下一步改进方向

1. **收紧 Reviewer 阈值**：当前 PASS 率 100% 偏高，考虑调低 FAIL 门槛以触发更多自校正循环
2. **补充文档模式专项测试**：用 `DOCUMENT_MODE_CASES` 验证熔断机制
3. **人工标注校准**：抽样 20 条 Judge 评分 vs 人工评分，计算皮尔逊相关系数
4. **丰富测试集**：增加代码生成类、数学推理类、多语言类用例
5. **CI 集成**：将评测脚本集成到 GitHub Actions，每次 push 自动跑回测
