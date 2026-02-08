"""Sub-subagente focado exclusivamente em busca vetorial."""

import json
from typing import Optional

from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.prebuilt import create_react_agent

from config.settings import settings
from config.logger import setup_logger
from tools.db_vector_search import search_products_vector


logger = setup_logger(__name__)


VECTOR_SEARCH_AGENT_PROMPT = """
Você é o AGENTE BANCO VETORIAL do Mercadinho Queiroz.

Sua única responsabilidade é executar uma busca no banco vetorial usando a ferramenta `vector_search`.

REGRAS:
- Sempre use `vector_search`.
- Nunca invente produtos, EANs ou preços.
- Retorne apenas o resultado bruto da ferramenta, sem reformatar.
""".strip()


def _get_fast_llm():
    model_name = getattr(settings, "llm_model", "gemini-2.5-flash")
    temp = 0.0

    if settings.llm_provider == "openai" and "gpt" in model_name:
        if "x.ai" not in str(settings.openai_api_base):
            model_name = "gpt-4o-mini"

    if settings.llm_provider == "google":
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.google_api_key,
            temperature=temp,
        )

    client_kwargs = {}
    if settings.openai_api_base:
        client_kwargs["base_url"] = settings.openai_api_base

    return ChatOpenAI(
        model=model_name,
        api_key=settings.openai_api_key,
        temperature=temp,
        **client_kwargs,
    )


@tool("vector_search")
def vector_search_tool(query: str, limit: int = 15) -> str:
    """
    Busca produtos no índice vetorial e retorna resultados relevantes.
    """
    return search_products_vector(query, limit=limit)


def run_vector_search_subagent(query: str, limit: int = 15, thread_id: Optional[str] = None) -> str:
    q = (query or "").strip()
    if not q:
        return "Nenhum produto encontrado."

    def _strip_leading_qty(text: str) -> str:
        import re
        s = (text or "").strip()
        kg_suffix = re.search(r"\b\d+(?:[.,]\d+)?\s*(kg|kilo|quilo|quilos)\b\s*$", s, flags=re.IGNORECASE)
        if kg_suffix:
            s = re.sub(r"\b\d+(?:[.,]\d+)?\s*(kg|kilo|quilo|quilos)\b\s*$", "kg", s, flags=re.IGNORECASE).strip()
            return s.strip()
        kg_prefix = re.match(r"^\s*\d+(?:[.,]\d+)?\s*(kg|kilo|quilo|quilos)\b\s*(de\s+)?", s, flags=re.IGNORECASE)
        if kg_prefix:
            s = s[kg_prefix.end():].strip()
            if s and not re.search(r"\bkg\b", s, flags=re.IGNORECASE):
                s = f"{s} kg"
            return s.strip()

        s = re.sub(r"^\s*\d+(?:[.,]\d+)?\s*[xX]?\s+", "", s)
        s = re.sub(r"^\s*(aprox\.?|aproximadamente)\s+", "", s, flags=re.IGNORECASE)
        return s.strip()

    q = _strip_leading_qty(q)

    if getattr(settings, "vector_search_term_mappings", False) and getattr(settings, "vector_search_mode", "exact").lower() != "exact":
        TERM_MAPPINGS = {
            "pacote de pao": "pao hot dog",
            "pacote de pão": "pao hot dog",
        }
        
        q_lower = q.lower()
        for original, replacement in TERM_MAPPINGS.items():
            if original in q_lower:
                q = q_lower.replace(original, replacement)
                logger.info(f"🔄 [TERM MAPPING] '{original}' → '{replacement}'")
                break

    logger.info(f"🧩 [SUB-SUB-AGENT][VETORIAL] Buscando (DIRECT): '{q}' (limit={limit})")
    
    # Otimização: Chamada direta à função de busca, removendo layer de agente desnecessário
    # O Analista já é o agente inteligente que decide o que buscar.
    try:
        return search_products_vector(q, limit=limit)
    except Exception as e:
        logger.error(f"Erro na busca vetorial direta: {e}")
        return f"Erro ao buscar produtos: {str(e)}"
