"""Ferramenta de Sub-Agente para Busca Especializada de Produtos"""
import json
import logging
from typing import List, Dict, Any, Optional
from pathlib import Path
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent
from pydantic.v1 import BaseModel, Field
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.output_parsers import JsonOutputParser

from config.settings import settings
from config.logger import setup_logger
from tools.vector_search_subagent import run_vector_search_subagent
from tools.http_tools import estoque_preco

logger = setup_logger(__name__)

_ANALISTA_PROMPT_CACHE: Optional[str] = None



def _load_analista_prompt() -> str:
    global _ANALISTA_PROMPT_CACHE
    if _ANALISTA_PROMPT_CACHE is not None:
        return _ANALISTA_PROMPT_CACHE

    base_dir = Path(__file__).resolve().parent.parent
    prompt_path = base_dir / "prompts" / "analista.md"
    _ANALISTA_PROMPT_CACHE = prompt_path.read_text(encoding="utf-8")
    return _ANALISTA_PROMPT_CACHE





@tool("banco_vetorial")
def banco_vetorial_tool(query: str, limit: int = 10) -> str:
    """
    Realiza uma busca vetorial no banco de dados de produtos.
    Retorna uma lista de produtos mais similares semanticamente à query.
    """
    return run_vector_search_subagent(query, limit=limit)


@tool("estoque_preco")
def estoque_preco_tool(ean: str) -> str:
    """
    Consulta o estoque e preço atual de um produto pelo seu código EAN.
    Retorna JSON com dados atualizados.
    """
    return estoque_preco(ean)


@tool("calculadora")
def calculadora_tool(expressao: str) -> str:
    """
    Calculadora simples. Avalia expressões matemáticas básicas.
    Use para calcular quantidade = valor / preco_kg.
    Ex: calculadora("5 / 40") retorna "0.125"
    """
    try:
        # Sanitizar expressão (apenas permitir números e operadores básicos)
        allowed_chars = set("0123456789.+-*/() ")
        if not all(c in allowed_chars for c in expressao):
            return "Erro: Expressão inválida"
        result = eval(expressao)
        return str(round(result, 3))
    except Exception as e:
        return f"Erro: {e}"


def _run_analista_agent_for_term(term: str, telefone: Optional[str] = None) -> dict:
    prompt = _load_analista_prompt()
    
    llm = _get_fast_llm()
    agent = create_react_agent(llm, [banco_vetorial_tool, estoque_preco_tool], prompt=prompt)

    user_payload = json.dumps(
        {"termo": term},
        ensure_ascii=False,
    )

    config = {"recursion_limit": 8}
    if telefone:
        config["configurable"] = {"thread_id": telefone}

    result = agent.invoke({"messages": [HumanMessage(content=user_payload)]}, config)
    messages = result.get("messages", []) if isinstance(result, dict) else []

    for m in reversed(messages):
        if getattr(m, "type", None) != "ai":
            continue
        content = m.content if isinstance(m.content, str) else str(m.content)
        content = (content or "").strip()
        if not content:
            continue
        try:
            return json.loads(content)
        except Exception:
            return {"ok": False, "termo": term, "motivo": "Resposta nao-JSON do analista"}

    return {"ok": False, "termo": term, "motivo": "Sem resposta"}


TERM_EXTRACTOR_PROMPT = """
Você é um extrator de termos de produtos para um supermercado.

Tarefa: Dado o texto do cliente, retorne uma lista JSON pura de termos para busca no catálogo.

## REGRAS CRÍTICAS:

1. **NUNCA REMOVA MARCAS**: Palavras como "Vô", "Vo", "Omo", "Ala", "Pilão", "Melita", "Dolca", "Richester" SÃO MARCAS.
   - ❌ ERRADO: "arroz vô parboizado" → ["arroz parboizado"]
   - ✅ CERTO: "arroz vô parboizado" → ["arroz vô parboizado"]
   - ❌ ERRADO: "café Pilão" → ["café"]
   - ✅ CERTO: "café Pilão" → ["café Pilão"]

2. **MANTENHA NÚMEROS DE PRODUTO**: "Kit 3", "Pack 12", "Coca 2L", "1kg" fazem parte do produto.

3. **REMOVA APENAS QUANTIDADE DO PEDIDO**: "2x arroz" → ["arroz"], "1 coca" → ["coca"]

4. **PEDIDO POR VALOR**: Se tem R$ ou "reais de", adicione "KG":
   - "5 reais de presunto" → ["presunto KG"]
   - "10 reais de queijo" → ["queijo KG"]

5. **OPÇÕES**: Mantenha palavras como "opções", "quais", "tipos":
   - "sabão (opções)" → ["sabão opções"]

Retorne APENAS JSON (lista de strings).

Texto: {text}
""".strip()

# ============================================
# 2. Configurações do Modelo
# ============================================

_HTTP_CLIENT_CACHE = None
_HTTP_ASYNC_CLIENT_CACHE = None

def _get_fast_llm():
    """Retorna um modelo rápido e barato para tarefas de sub-agente."""
    global _HTTP_CLIENT_CACHE, _HTTP_ASYNC_CLIENT_CACHE

    # PREFERÊNCIA: Usar o modelo configurado no settings (ex: grok-beta)
    model_name = getattr(settings, "llm_model", "gemini-2.5-flash")
    temp = 0.0 # Temperatura zero para precisão
    
    # Se quiser forçar um modelo mais leve para providers específicos:
    if settings.llm_provider == "openai" and "gpt" in model_name:
         # Se for OpenAI oficial, podemos tentar o mini. Se for xAI (que usa client openai), mantemos o do settings.
         if "x.ai" not in str(settings.openai_api_base):
            model_name = "gpt-4o-mini" 
         
    # Se houver override no settings, respeitar (mas idealmente forçamos um modelo rápido aqui)
    
    if settings.llm_provider == "google":
        return ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.google_api_key,
            temperature=temp
        )
    else:
        client_kwargs = {}
        if settings.openai_api_base:
            client_kwargs["base_url"] = settings.openai_api_base

        import httpx
        
        # Singleton Clients para evitar abrir mil conexões no loop
        if _HTTP_CLIENT_CACHE is None:
            _HTTP_CLIENT_CACHE = httpx.Client(timeout=30.0)
        if _HTTP_ASYNC_CLIENT_CACHE is None:
            _HTTP_ASYNC_CLIENT_CACHE = httpx.AsyncClient(timeout=30.0)
        
        return ChatOpenAI(
            model=model_name,
            api_key=settings.openai_api_key,
            temperature=temp,
            http_client=_HTTP_CLIENT_CACHE,
            http_async_client=_HTTP_ASYNC_CLIENT_CACHE,
            **client_kwargs
        )

# ============================================
# 3. Função Principal (Tool)
# ============================================

def analista_produtos_tool(queries_str: str, telefone: str = None) -> str:
    """
    [ANALISTA DE PRODUTOS]
    Agente Especialista que traduz pedidos do cliente em produtos reais do banco de dados.
    Usa busca vetorial + inteligência semântica.
    
    Args:
        queries_str: Termos de busca (ex: "arroz, feijão, pão").
        telefone: Opcional - número do cliente para salvar sugestões no cache.
    """
    results = []
    validated_products = []  # Para cache no Redis
    
    extracted_terms: List[str] = []
    try:
        llm_terms = _get_fast_llm()
        prompt_terms = ChatPromptTemplate.from_template(TERM_EXTRACTOR_PROMPT)
        chain_terms = prompt_terms | llm_terms | JsonOutputParser()
        extracted = chain_terms.invoke({"text": queries_str})

        if isinstance(extracted, list):
            extracted_terms = [str(t).strip() for t in extracted if str(t).strip()]
        elif isinstance(extracted, dict):
            raw_list = extracted.get("terms") or extracted.get("itens") or extracted.get("produtos") or []
            if isinstance(raw_list, list):
                extracted_terms = [str(t).strip() for t in raw_list if str(t).strip()]
    except Exception as e:
        logger.warning(f"⚠️ [SUB-AGENT] Falha ao extrair termos via LLM: {e}")

    if not extracted_terms:
        extracted_terms = [t.strip() for t in queries_str.replace("\n", ",").split(",") if t.strip()]

    mode = "lote" if len(extracted_terms) > 1 else "individual"
    logger.info(f"🕵️ [SUB-AGENT] Modo de busca: {mode} | termos: {extracted_terms}")
    
    # Função helper para processar cada termo em paralelo
    def _process_single_term(term: str):
        try:
            decision = _run_analista_agent_for_term(term, telefone=telefone)
            if not isinstance(decision, dict) or not decision.get("ok"):
                motivo = (decision or {}).get("motivo") if isinstance(decision, dict) else None
                return (f"❌ {term}: {motivo or 'Nao encontrado'}", None)

            # MODO MULTIPLAS OPÇÕES
            opcoes = decision.get("opcoes")
            if opcoes and isinstance(opcoes, list) and len(opcoes) > 0:
                out_lines = [f"📋 [ANALISTA] OPÇÕES PARA '{term}' (Pergunte ao cliente):"]
                for i, opt in enumerate(opcoes, 1):
                    n = opt.get("nome", "Item")
                    p = float(opt.get("preco", 0.0))
                    out_lines.append(f"   {i}. {n} - R$ {p:.2f}")
                
                out_lines.append("\n⚠️ NÃO Adicionado automaticamente. Liste as opções para o cliente.")
                return ("\n".join(out_lines), None)

            # MODO ÚNICO
            nome = str(decision.get("nome") or "").strip()
            preco = float(decision.get("preco") or 0.0)

            if not nome:
                return (f"❌ {term}: Resposta incompleta do analista", None)

            validated_item = {"nome": nome, "preco": preco, "termo_busca": term}
            razao = str(decision.get("razao") or "").strip()
            
            result_str = (
                "🔍 [ANALISTA] ITEM VALIDADO:\n"
                f"- Nome: {nome}\n"
                f"- Preço Tabela: R$ {preco:.2f}\n"
                f"- Obs: {razao}\n"
                f"\n🔔 DICA: use add_item_tool AGORA para adicionar este item."
            )
            return (result_str, validated_item)
            
        except Exception as e:
            logger.error(f"❌ [SUB-AGENT] Erro no agente Analista para '{term}': {e}")
            return (f"❌ {term}: Erro interno na busca.", None)

    # Execução Paralela
    import concurrent.futures
    
    # Limitar número de workers para não saturar
    max_workers = min(10, len(extracted_terms) + 1)
    
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submeter tarefas mantendo a ordem: mapa {future: index}
        future_to_index = {
            executor.submit(_process_single_term, term): i 
            for i, term in enumerate(extracted_terms)
        }
        
        # Array para guardar resultados na ordem correta
        ordered_results = [None] * len(extracted_terms)
        
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                ordered_results[index] = future.result()
            except Exception as e:
                logger.error(f"Erro fatal processando future index {index}: {e}")
                ordered_results[index] = (f"❌ Erro interno.", None)
                
    # Coletar resultados finais
    for res in ordered_results:
        if not res: 
            continue
        res_str, val_item = res
        if res_str:
            results.append(res_str)
        if val_item:
            validated_products.append(val_item)

    # SALVAR CACHE NO REDIS SE TIVER TELEFONE
    if telefone and validated_products:
        try:
            from tools.redis_tools import save_suggestions
            save_suggestions(telefone, validated_products)
            logger.info(f"💾 [SUB-AGENT] Cache salvo: {len(validated_products)} produtos para {telefone}")
        except Exception as e:
            logger.error(f"Erro ao salvar cache de sugestões: {e}")

    if not results:
        return "Nenhum produto encontrado."
        
    return "\n".join(results)
