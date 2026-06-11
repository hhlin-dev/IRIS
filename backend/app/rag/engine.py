import os
import shutil
from typing import Any, List, Optional
from dotenv import load_dotenv
from langchain_community.document_loaders import PyPDFLoader
from langchain_core import vectorstores
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.embeddings import DashScopeEmbeddings
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever

load_dotenv()

try:
    from sentence_transformers import CrossEncoder
except ImportError:
    CrossEncoder = None

RERANKER_MODEL_NAME = "cross-encoder/ms-marco-MiniLM-L-6-v2"
_reranker = None

def get_reranker():
    global _reranker
    if _reranker is not None:
        return _reranker
    if CrossEncoder is None:
        raise RuntimeError(
            "未安装 sentence-transformers，无法启用 reranking。请执行：pip install sentence-transformers"
        )
    _reranker = CrossEncoder(RERANKER_MODEL_NAME)
    return _reranker

class RerankRetriever(BaseRetriever):
    """
    两阶段检索：
    1) Chroma 向量召回 fetch_k 个候选
    2) Cross-Encoder rerank
    3) 返回 top_k
    """

    vectorstore: Any
    reranker: Any
    top_k: int = 5
    fetch_k: int = 20

    def _get_relevant_documents(self, query: str) -> list[Document]:
        # 1) 先召回更多候选
        candidates: list[Document] = self.vectorstore.similarity_search(query, k=self.fetch_k)
        if not candidates:
            return []

        # 2) rerank：对 (query, doc_text) 打分
        pairs = [(query, d.page_content) for d in candidates]
        scores = self.reranker.predict(pairs)

        # 3) 按分数排序，取 top_k
        ranked = sorted(zip(candidates, scores), key=lambda x: float(x[1]), reverse=True)
        top_docs = [doc for doc, _ in ranked[: self.top_k]]

        return top_docs

# 定义数据存储路径
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "chroma_db")   # 数据库文件存这里
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads") # 用户上传的 PDF 存这里


_embeddings = None


def _get_embeddings():
    global _embeddings
    if _embeddings is not None:
        return _embeddings
    # 优先使用阿里云 DashScope（需要设置 DASHSCOPE_API_KEY）
    # 若未配置则回退到本地 HuggingFace 模型
    if os.environ.get("DASHSCOPE_API_KEY"):
        _embeddings = DashScopeEmbeddings(model="text-embedding-v4")
    else:
        _embeddings = HuggingFaceEmbeddings(model_name="moka-ai/m3e-base")
    return _embeddings

def reset_knowledge_base():
    """
    重置知识库：
    Windows 兼容版修复：不删除 DB 文件夹（避免 WinError 32），而是清空数据。
    """

    if os.path.exists(UPLOAD_DIR):
        try:
            shutil.rmtree(UPLOAD_DIR)
        except Exception as e:
            print(f"--- [RAG] 清理上传目录警告: {e} ---")
    os.makedirs(UPLOAD_DIR, exist_ok=True)


    print("--- [RAG] 正在重置知识库数据... ---")
    try:
        if os.path.exists(DB_PATH):
            vectorstore = Chroma(persist_directory=DB_PATH, embedding_function=_get_embeddings())
            try:
                vectorstore.delete_collection()
                print("--- [RAG] 知识库 Collection 已删除 (数据已清空) ---")
            except Exception:
                pass
    except Exception as e:
        print(f"--- [RAG] 重置数据库时遇到非致命错误 (不影响使用): {e} ---")

def process_documents(file_paths: List[str]):
    """
    Reads PDFs, splits into chunks, and stores in vector DB.
    Returns (chunks_count, errors_list).
    """
    all_splits = []
    errors = []

    for file_path in file_paths:
        filename = os.path.basename(file_path)
        print(f"--- [RAG] Processing: {filename} ---")
        try:
            loader = PyPDFLoader(file_path)
            docs = loader.load()

            if not docs:
                errors.append(f"{filename}: PDF is empty or contains no readable text")
                continue

            # filter out pages with no content
            docs_with_text = [d for d in docs if d.page_content.strip()]
            if not docs_with_text:
                errors.append(f"{filename}: PDF pages contain no extractable text (may be a scanned image)")
                continue

            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=500,
                chunk_overlap=50
            )
            splits = text_splitter.split_documents(docs_with_text)
            all_splits.extend(splits)
        except Exception as e:
            msg = f"{filename}: {str(e)}"
            errors.append(msg)
            print(f"--- [RAG] Error processing {filename}: {e} ---")

    if all_splits:
        try:
            print(f"--- [RAG] Writing {len(all_splits)} chunks to vector DB... ---")
            Chroma.from_documents(
                documents=all_splits,
                embedding=_get_embeddings(),
                persist_directory=DB_PATH
            )
            print("--- [RAG] Write complete ---")
        except Exception as e:
            errors.append(f"Vector DB write failed: {str(e)}")
            print(f"--- [RAG] Vector DB error: {e} ---")
            return 0, errors

    return len(all_splits), errors

def get_retriever():
    """
    获取检索器：给 Agent 用的接口
    """
    if not os.path.exists(DB_PATH) or not os.listdir(DB_PATH):
        return None
    vectorstore = Chroma(persist_directory=DB_PATH, embedding_function=_get_embeddings())
    top_k = 5
    fetch_k = 20
    reranker = get_reranker()
    return RerankRetriever(vectorstore=vectorstore, reranker=reranker, top_k=top_k, fetch_k=fetch_k)

