"""LLM 配置：全部走环境变量，无 key 时不报错、由调用方降级处理。"""
import os

from dotenv import load_dotenv

load_dotenv()  # 允许从项目根目录 .env 读取；缺失也不报错


def llm_settings() -> dict:
    return {
        "api_key": os.getenv("LLM_API_KEY", "").strip(),
        "base_url": os.getenv(
            "LLM_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta/openai",
        ).strip(),
        "model": os.getenv("LLM_MODEL", "gemini-3.5-flash-lite").strip(),
    }


def is_llm_configured() -> bool:
    """是否配置了 LLM key（每次调用都读环境变量，便于测试 monkeypatch）。"""
    return bool(os.getenv("LLM_API_KEY", "").strip())


def app_lang() -> str:
    """界面与问答语言：APP_LANG=zh 时中文，其他一律英文（默认 en）。"""
    return "zh" if os.getenv("APP_LANG", "en").strip().lower() == "zh" else "en"


def _positive_int(value: str, default: int) -> int:
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return default


def schema_retrieval_settings() -> dict:
    """Schema 预检索（RAG-for-Text2SQL）开关，全部走环境变量。

    SCHEMA_RETRIEVAL: auto（默认，表数量超过阈值才预筛）| tfidf | embedding | off
    SCHEMA_TOP_K: 预筛保留的候选表数量（默认 10）
    SCHEMA_RETRIEVAL_THRESHOLD: auto 模式下触发预筛的表数量阈值（默认 12）
    EMBEDDING_MODEL: embedding 模式用的向量模型（默认 text-embedding-004）
    """
    method = os.getenv("SCHEMA_RETRIEVAL", "auto").strip().lower()
    if method not in ("auto", "tfidf", "embedding", "off"):
        method = "auto"
    return {
        "method": method,
        "top_k": _positive_int(os.getenv("SCHEMA_TOP_K", "10"), 10),
        "threshold": _positive_int(os.getenv("SCHEMA_RETRIEVAL_THRESHOLD", "12"), 12),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "text-embedding-004").strip(),
    }


def get_llm():
    """返回 ChatOpenAI 实例；未配置 key 时返回 None（不抛异常）。"""
    if not is_llm_configured():
        return None
    from langchain_openai import ChatOpenAI

    s = llm_settings()
    return ChatOpenAI(
        api_key=s["api_key"],
        base_url=s["base_url"],
        model=s["model"],
        temperature=0,
        request_timeout=60,
    )
