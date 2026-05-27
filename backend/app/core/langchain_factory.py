"""
LangChain factory for ChatModel and Embeddings.

Uses DashScope (Alibaba Bailian) via OpenAI-compatible endpoints.
"""

from __future__ import annotations

from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.core.config import settings


def get_chat_model() -> ChatOpenAI:
    """Return a ChatOpenAI instance configured for DashScope."""
    return ChatOpenAI(
        model=settings.dashscope_chat_model,
        api_key=settings.dashscope_api_key or "not-set",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature=0.2,
        timeout=120,
        max_retries=2,
    )


def get_embeddings() -> OpenAIEmbeddings:
    """Return an OpenAIEmbeddings instance configured for DashScope."""
    return OpenAIEmbeddings(
        model=settings.dashscope_embedding_model,
        api_key=settings.dashscope_api_key or "not-set",
        base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
    )
