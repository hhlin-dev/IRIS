from fastapi import APIRouter, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import List, Optional
from app.graph.graph import create_graph
import json
import asyncio
import os
import shutil
from app.rag.engine import process_documents, reset_knowledge_base, UPLOAD_DIR
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(CURRENT_DIR, "checkpoints.db")
router = APIRouter()


class ChatRequest(BaseModel):
    query: str
    search_mode: str = "hybrid" # 默认为混合搜索
    thread_id: str             

@router.post("/clear")
async def clear_endpoint():
    try:
        reset_knowledge_base() 
        return {"message": "知识库已重置", "status": "success"}
    except Exception as e:
        print(f"清空失败: {e}")
        return {"message": f"清空失败: {str(e)}", "status": "error"}

@router.post("/upload")
async def upload_files(files: List[UploadFile] = File(...)):
    """
    批量上传接口
    """

    if len(files) > 5:
        raise HTTPException(status_code=400, detail="一次最多只能上传 5 个文件")

    try:

        reset_knowledge_base()
        
        saved_paths = []

        for file in files:

            file_path = os.path.join(UPLOAD_DIR, file.filename)
            with open(file_path, "wb") as buffer:
                shutil.copyfileobj(file.file, buffer)
            saved_paths.append(file_path)
            

        chunks_num, errors = process_documents(saved_paths)

        return {
            "status": "success",
            "file_count": len(files),
            "chunks_stored": chunks_num,
            "errors": errors,
            "message": "Documents processed" if chunks_num > 0 else "No text could be extracted from the uploaded files"
        }
    except Exception as e:
        print(f"上传处理失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    config = {"configurable": {"thread_id": request.thread_id}}
    async def event_generator():

        initial_state = {
            "query": request.query,
            "revision_number": 0,
            "search_mode": request.search_mode
        }

        print(f"--- [IRIS] New task | Mode: {request.search_mode} | Query: {request.query}")

        try:
            async with AsyncSqliteSaver.from_conn_string(DB_PATH) as memory:
                app = create_graph(memory=memory)

                async for event in app.astream(initial_state, config=config):
                     for node_name, state_update in event.items():
                        data = json.dumps({"step": node_name, "data": state_update}, ensure_ascii=False)
                        yield f"data: {data}\n\n"
                        await asyncio.sleep(0.1)

            yield "data: [DONE]\n\n"
        except Exception as e:
            print(f"--- [IRIS] Error in agent stream: {e} ---")
            import traceback
            traceback.print_exc()
            error_data = json.dumps({"step": "ERROR", "data": {"error": str(e)}}, ensure_ascii=False)
            yield f"data: {error_data}\n\n"
            yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ============================================================
#  自动化评测与回测端点 (Evaluation & Regression Testing)
#  命中 JD 第 5 条：建立自动化评测与回测机制
# ============================================================

# 内存中保存最近一次评测结果（轻量级状态，重启丢失，仅用于快速查询）
_latest_eval_result: dict | None = None
_eval_running: bool = False
_eval_progress: dict = {"current": 0, "total": 0, "status": "idle"}


class EvalRequest(BaseModel):
    custom_cases: Optional[List[dict]] = None
    # 若提供 custom_cases，则使用自定义用例；否则使用内置默认用例集


@router.post("/evaluate")
async def start_evaluation(request: EvalRequest = EvalRequest()):
    """
    启动一次完整的自动化评测。

    使用内置 8 条默认用例（覆盖技术解释、对比分析、趋势分析、方法论、安全、架构设计等题型），
    也可通过 custom_cases 传入自定义用例。

    评测流程：
    1) 每条用例依次经过 planner → researcher → writer → reviewer 完整管线
    2) Judge LLM 对最终报告进行 4 维度评分（相关性/完整性/准确性/结构）
    3) 聚合全部结果，生成 JSON + Markdown 评测报告
    """
    global _latest_eval_result, _eval_running, _eval_progress

    if _eval_running:
        raise HTTPException(status_code=409, detail="评测正在运行中，请等待完成后再发起新评测")

    _eval_running = True
    test_cases = request.custom_cases

    try:
        from app.evaluation.evaluator import run_evaluation, save_report

        def progress_cb(idx: int, total: int, result: dict):
            _eval_progress = {
                "current": idx + 1,
                "total": total,
                "status": "running",
                "last_case_id": result.get("case_id", ""),
            }

        _eval_progress = {"current": 0, "total": len(test_cases) if test_cases else 8, "status": "running"}

        report = run_evaluation(
            test_cases=test_cases,
            progress_callback=progress_cb,
        )
        paths = save_report(report)
        _latest_eval_result = report
        _eval_progress = {"current": _eval_progress["total"], "total": _eval_progress["total"], "status": "completed"}

        return {
            "status": "completed",
            "summary": report["summary"],
            "total_elapsed_ms": report["total_elapsed_ms"],
            "files": paths,
        }
    except Exception as e:
        import traceback
        traceback.print_exc()
        _eval_progress["status"] = "error"
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        _eval_running = False


@router.get("/evaluate/status")
async def get_evaluation_status():
    """获取当前评测进度和最近一次评测摘要。"""
    return {
        "running": _eval_running,
        "progress": _eval_progress,
        "latest_summary": _latest_eval_result["summary"] if _latest_eval_result else None,
        "latest_generated_at": _latest_eval_result.get("generated_at") if _latest_eval_result else None,
    }


@router.get("/evaluate/report/latest")
async def get_latest_report(format: str = "json"):
    """
    下载最近一次评测的完整报告。

    - format=json: 返回 JSON 格式（机器可读）
    - format=md: 返回 Markdown 格式（人类可读）
    """
    if _latest_eval_result is None:
        raise HTTPException(status_code=404, detail="暂无评测报告。请先调用 POST /api/evaluate 生成。")

    from app.evaluation.evaluator import DEFAULT_OUTPUT_DIR

    if format == "md":
        md_path = os.path.join(DEFAULT_OUTPUT_DIR, "evaluation_latest.md")
        if os.path.exists(md_path):
            return FileResponse(md_path, media_type="text/markdown; charset=utf-8", filename="evaluation_latest.md")
        raise HTTPException(status_code=404, detail="Markdown report file not found")

    return _latest_eval_result


@router.get("/evaluate/report/history")
async def list_evaluation_history():
    """列出所有历史评测报告文件。"""
    from app.evaluation.evaluator import DEFAULT_OUTPUT_DIR
    if not os.path.exists(DEFAULT_OUTPUT_DIR):
        return {"history": []}

    files = sorted(
        [f for f in os.listdir(DEFAULT_OUTPUT_DIR) if f.startswith("evaluation_") and not f.startswith("evaluation_latest")],
        reverse=True,
    )
    return {"history": files[:20], "directory": DEFAULT_OUTPUT_DIR}