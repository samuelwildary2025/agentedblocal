"""
Ferramentas Redis para buffer de mensagens e cooldown
Apenas funcionalidades essenciais mantidas
"""
import redis
import time
import uuid
from typing import Optional, Dict, List, Tuple
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)

# Conexão global com Redis
_redis_client: Optional[redis.Redis] = None
# Buffer local em memória (fallback quando Redis não está disponível)
_local_buffer: Dict[str, List[str]] = {}

def normalize_phone(telefone: str) -> str:
    telefone = "" if telefone is None else str(telefone)
    digits = "".join(ch for ch in telefone if ch.isdigit())
    return digits or telefone.strip()

def _maybe_migrate_key(client: redis.Redis, old_key: str, new_key: str) -> None:
    if not old_key or not new_key or old_key == new_key:
        return
    try:
        if client.type(old_key) == "none":
            return
        if client.type(new_key) != "none":
            return
        moved = client.renamenx(old_key, new_key)
        if moved:
            logger.info(f"🔁 Redis key migrada: {old_key} -> {new_key}")
    except Exception:
        return

def _lock_key(namespace: str, telefone: str) -> str:
    return f"lock:{namespace}:{normalize_phone(telefone)}"

def _release_lock(client: redis.Redis, key: str, token: str) -> bool:
    script = """
    if redis.call("get", KEYS[1]) == ARGV[1] then
        return redis.call("del", KEYS[1])
    else
        return 0
    end
    """
    try:
        res = client.eval(script, 1, key, token)
        return bool(res)
    except Exception:
        return False

def _acquire_lock(client: redis.Redis, key: str, ttl_seconds: int, wait_seconds: int) -> Optional[str]:
    token = uuid.uuid4().hex
    deadline = time.monotonic() + max(0, int(wait_seconds))
    while True:
        try:
            ok = client.set(key, token, nx=True, ex=max(1, int(ttl_seconds)))
        except Exception:
            return None
        if ok:
            return token
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.15)

def acquire_agent_lock(telefone: str, ttl_seconds: int = 600, wait_seconds: int = 120) -> Optional[str]:
    client = get_redis_client()
    if client is None:
        return "NOLOCK"
    telefone = normalize_phone(telefone)
    return _acquire_lock(client, _lock_key("agent", telefone), ttl_seconds=ttl_seconds, wait_seconds=wait_seconds)

def release_agent_lock(telefone: str, token: str) -> bool:
    if token == "NOLOCK":
        return True
    client = get_redis_client()
    if client is None:
        return False
    telefone = normalize_phone(telefone)
    return _release_lock(client, _lock_key("agent", telefone), token)


def get_redis_client() -> Optional[redis.Redis]:
    """
    Retorna a conexão com o Redis (singleton)
    """
    global _redis_client
    
    if _redis_client is None:
        try:
            _redis_client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=5,
            )
            # Testar conexão
            _redis_client.ping()
            logger.info("Conectado ao Redis")
        
        except redis.exceptions.ConnectionError as e:
            logger.error(f"Erro ao conectar ao Redis: {e}")
            _redis_client = None
        
        except Exception as e:
            logger.error(f"Erro inesperado ao conectar ao Redis: {e}")
            _redis_client = None
    
    return _redis_client


# ============================================
# Buffer de mensagens (concatenação por janela)
# ============================================

def buffer_key(telefone: str) -> str:
    """Retorna a chave da lista de buffer de mensagens no Redis."""
    return f"msgbuf:{normalize_phone(telefone)}"


def push_message_to_buffer(telefone: str, mensagem: str, message_id: str = None, ttl_seconds: int = 300) -> bool:
    """
    Empilha a mensagem recebida em uma lista no Redis para o telefone.
    Salva como JSON {"text": "...", "mid": "..."} para preservar o ID.
    """
    client = get_redis_client()
    import json
    
    # Payload seguro
    payload = json.dumps({"text": mensagem, "mid": message_id})

    telefone = normalize_phone(telefone)
    if client is None:
        # Fallback em memória
        msgs = _local_buffer.get(telefone)
        if msgs is None:
            _local_buffer[telefone] = [payload]
        else:
            msgs.append(payload)
        logger.info(f"[fallback] Mensagem empilhada em memória para {telefone}")
        return True

    key = buffer_key(telefone)
    try:
        client.rpush(key, payload)
        if client.ttl(key) in (-1, -2):
            client.expire(key, ttl_seconds)
        logger.info(f"Mensagem empilhada no buffer: {key}")
        return True
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao empilhar mensagem no Redis: {e}")
        return False


def get_buffer_length(telefone: str) -> int:
    """Retorna o tamanho atual do buffer de mensagens para o telefone."""
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        # Fallback em memória
        msgs = _local_buffer.get(telefone) or []
        return len(msgs)
    try:
        return int(client.llen(buffer_key(telefone)))
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao consultar tamanho do buffer: {e}")
        return 0


def pop_all_messages(telefone: str) -> Tuple[List[str], Optional[str]]:
    """
    Obtém todas as mensagens do buffer e limpa a chave.
    Retorna (lista_de_textos, lista_de_mids).
    """
    client = get_redis_client()
    import json
    telefone = normalize_phone(telefone)
    
    texts = []
    # mids (plural) para marcar todos como lidos
    
    if client is None:
        # Fallback em memória
        msgs_raw = _local_buffer.get(telefone) or []
        _local_buffer.pop(telefone, None)
    else:
        key = buffer_key(telefone)
        try:
            pipe = client.pipeline()
            pipe.lrange(key, 0, -1)
            pipe.delete(key)
            result = pipe.execute()
            msgs_raw = result[0] if result else []
        except redis.exceptions.RedisError as e:
            logger.error(f"Erro ao consumir buffer: {e}")
            return [], None

    mids = []
    
    # Processar payloads
    for raw in msgs_raw:
        try:
            # Tenta ler como JSON novo
            data = json.loads(raw)
            if isinstance(data, dict):
                txt = data.get("text", "")
                mid = data.get("mid")
                if txt: texts.append(txt)
                if mid: mids.append(mid)
            else:
                # String antiga ou inválida
                texts.append(str(raw))
        except:
            # Não é JSON, assume texto puro (retrocompatibilidade)
            texts.append(str(raw))
            
    logger.info(f"Buffer consumido para {telefone}: {len(texts)} mensagens. MIDs: {len(mids)}")
    return texts, mids


# ============================================
# Cooldown do agente (pausa de automação)
# ============================================

def cooldown_key(telefone: str) -> str:
    """Chave do cooldown no Redis."""
    return f"cooldown:{normalize_phone(telefone)}"


def set_agent_cooldown(telefone: str, ttl_seconds: int = 60) -> bool:
    """
    Define uma chave de cooldown para o telefone, pausando a automação.

    - Armazena valor "1" com TTL (padrão 60s).
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        # Fallback: não há persistência real, apenas log
        logger.warning(f"[fallback] Cooldown não persistido (Redis indisponível) para {telefone}")
        return False
    try:
        key = cooldown_key(telefone)
        client.set(key, "1", ex=ttl_seconds)
        logger.info(f"Cooldown definido para {telefone} por {ttl_seconds}s")
        return True
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao definir cooldown: {e}")
        return False


def is_agent_in_cooldown(telefone: str) -> Tuple[bool, int]:
    """
    Verifica se há cooldown ativo e retorna (ativo, ttl_restante).
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return (False, -1)
    try:
        key = cooldown_key(telefone)
        val = client.get(key)
        if val is None:
            return (False, -1)
        ttl = client.ttl(key)
        ttl = ttl if isinstance(ttl, int) else -1
        return (True, ttl)
    except redis.exceptions.RedisError as e:
        logger.error(f"Erro ao consultar cooldown: {e}")
        return (False, -1)


# ============================================
# Gerenciamento de Sessão de Pedidos
# ============================================

import json
from datetime import datetime

# Constantes de tempo (em segundos)
SESSION_TTL = 30 * 60  # 30 minutos para montar pedido (Auto-expire)
MODIFICATION_TTL = 15 * 60  # 15 minutos para alterar após envio


def order_session_key(telefone: str) -> str:
    """Chave da sessão de pedido no Redis."""
    return f"order_session:{normalize_phone(telefone)}"


def get_order_session(telefone: str) -> Optional[Dict]:
    """
    Retorna a sessão de pedido atual do cliente.
    
    Returns:
        Dict com campos:
        - status: 'building' (montando) ou 'sent' (enviado)
        - started_at: timestamp de início
        - sent_at: timestamp de envio (se enviado)
        - order_id: ID do pedido (se enviado)
    """
    client = get_redis_client()
    raw_phone = "" if telefone is None else str(telefone).strip()
    telefone = normalize_phone(raw_phone)
    if client is None:
        return None
    
    try:
        if raw_phone and raw_phone != telefone:
            _maybe_migrate_key(client, f"order_session:{raw_phone}", f"order_session:{telefone}")
        key = order_session_key(telefone)
        data = client.get(key)
        if data:
            return json.loads(data)
        return None
    except Exception as e:
        logger.error(f"Erro ao obter sessão de pedido: {e}")
        return None


def start_order_session(telefone: str) -> bool:
    """
    Inicia uma nova sessão de pedido (status: building).
    TTL de 40 minutos.
    """
    client = get_redis_client()
    raw_phone = "" if telefone is None else str(telefone).strip()
    telefone = normalize_phone(raw_phone)
    if client is None:
        return False
    
    try:
        if raw_phone and raw_phone != telefone:
            _maybe_migrate_key(client, f"order_session:{raw_phone}", f"order_session:{telefone}")
        key = order_session_key(telefone)
        session = {
            "status": "building",
            "started_at": datetime.now().isoformat(),
            "sent_at": None,
            "order_id": None
        }
        client.set(key, json.dumps(session), ex=SESSION_TTL)
        logger.info(f"📦 Nova sessão de pedido iniciada para {telefone} (TTL: {SESSION_TTL//60}min)")
        return True
    except Exception as e:
        logger.error(f"Erro ao iniciar sessão de pedido: {e}")
        return False


def mark_order_sent(telefone: str, order_id: str = None) -> bool:
    """
    Marca o pedido como enviado. 
    Atualiza TTL para 15 minutos (janela de alteração).
    Também marca flag de pedido completado (2h TTL) para evitar mensagem de "não finalizado".
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return False
    
    try:
        key = order_session_key(telefone)
        session = get_order_session(telefone)
        
        if session is None:
            session = {"started_at": datetime.now().isoformat()}
        
        session["status"] = "sent"
        session["sent_at"] = datetime.now().isoformat()
        session["order_id"] = order_id
        
        client.set(key, json.dumps(session), ex=MODIFICATION_TTL) # 15 min TTL na sessão
        
        # Manter Carrinho e Comprovante vivos pela mesma janela de 15min
        client.expire(cart_key(telefone), MODIFICATION_TTL)
        client.expire(comprovante_key(telefone), MODIFICATION_TTL)
        
        # Marcar que pedido foi completado (TTL 2 horas)
        # Isso evita a mensagem "pedido não finalizado" quando cliente voltar
        completed_key = f"order_completed:{telefone}"
        client.set(completed_key, "1", ex=7200)  # 2 horas
        
        logger.info(f"✅ Pedido marcado como enviado para {telefone} (Janela de alteração: 15min)")
        return True
    except Exception as e:
        logger.error(f"Erro ao marcar pedido como enviado: {e}")
        return False


def clear_order_session(telefone: str) -> bool:
    """Remove a sessão de pedido."""
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return False
    
    try:
        client.delete(order_session_key(telefone))
        logger.info(f"🗑️ Sessão de pedido removida para {telefone}")
        return True
    except Exception as e:
        logger.error(f"Erro ao limpar sessão de pedido: {e}")
        return False


def get_order_context(telefone: str, mensagem: str = "") -> str:
    """
    Retorna o contexto de pedido para injetar no agente.
    
    Args:
        telefone: Número do cliente
        mensagem: Mensagem atual do cliente (para detectar saudações)
    
    Returns:
        String com instrução para o agente baseada no estado da sessão.
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    session = get_order_session(telefone)
    
    # Detectar se é uma saudação/novo atendimento
    saudacoes = [
        "boa tarde", "boa noite", "bom dia", "boa", "olá", "ola", "oi", 
        "eae", "eai", "e ai", "oii", "oiee", "hello", "hi", "hey",
        "opa", "opaa", "fala", "salve", "blz", "beleza"
    ]
    msg_lower = mensagem.strip().lower()
    is_greeting = any(msg_lower.startswith(s) or msg_lower == s for s in saudacoes)
    
    # Chave para rastrear se o ÚLTIMO pedido foi finalizado
    completed_key = f"order_completed:{telefone}"
    
    if session is None:
        # Verificar se o último pedido foi finalizado
        was_completed = False
        if client:
            try:
                was_completed = client.get(completed_key) is not None
            except:
                pass
        
        # Iniciar nova sessão
        start_order_session(telefone)
        
        # Limpar flag de pedido completado para próximo ciclo
        if client and was_completed:
            try:
                client.delete(completed_key)
            except:
                pass
        
        if was_completed:
            # Pedido anterior FOI finalizado - iniciar novo normalmente
            return "[SESSÃO] Novo pedido iniciado. Cliente já fez pedido anteriormente."
        else:
            # Conversa nova ou sessão expirou SEM finalizar
            return "[SESSÃO] Nova conversa. Monte o pedido normalmente."
    
    status = session.get("status", "building")
    
    if status == "building":
        # Ainda montando pedido - renovar TTL
        refresh_session_ttl(telefone)
        return ""
    
    elif status == "sent":
        # Pedido já foi enviado - está na janela de modificação (15min)
        # MAS se cliente mandou saudação, ele quer NOVO pedido!
        if is_greeting:
            logger.info(f"🔄 Saudação detectada para {telefone} - iniciando NOVO pedido (limpando sessão anterior)")
            # Limpar sessão antiga e carrinho
            clear_order_session(telefone)
            clear_cart(telefone)
            start_order_session(telefone)
            return "[SESSÃO] Novo pedido iniciado. Cliente iniciou nova conversa com saudação."
        
        return "[SESSÃO] Pedido já enviado. Se cliente quiser adicionar algo, use alterar_tool."
    
    return ""


def check_can_modify_order(telefone: str) -> Tuple[bool, str]:
    """
    Verifica se o cliente pode modificar o pedido.
    
    Returns:
        (pode_modificar, mensagem_explicativa)
    """
    telefone = normalize_phone(telefone)
    session = get_order_session(telefone)
    
    if session is None:
        return (False, "Nenhum pedido ativo. Será criado um novo.")
    
    status = session.get("status", "building")
    
    if status == "building":
        return (True, "Pedido ainda em montagem.")
    
    elif status == "sent":
        # Está na janela de 15min (Redis ainda tem a chave)
        return (True, "Pedido enviado recentemente. Pode alterar com alterar_tool.")
    
    return (False, "Sessão expirada. Novo pedido será criado.")


def refresh_session_ttl(telefone: str) -> bool:
    """
    Renova o TTL da sessão quando o cliente interage (se ainda em building).
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return False
    
    try:
        session = get_order_session(telefone)
        if session and session.get("status") == "building":
            key = order_session_key(telefone)
            client.expire(key, SESSION_TTL)
            logger.debug(f"TTL da sessão renovado para {telefone}")
            return True
        return False
    except Exception as e:
        logger.error(f"Erro ao renovar TTL da sessão: {e}")
        return False


# ============================================
# Carrinho de Compras (Redis List)
# ============================================

def cart_key(telefone: str) -> str:
    """Chave da lista de itens do carrinho no Redis."""
    return f"cart:{normalize_phone(telefone)}"


def add_item_to_cart(telefone: str, item_json: str) -> bool:
    """
    Adiciona um item (JSON string) ao carrinho.
    Inicia sessão se não existir e renova TTL (30min).
    Implementa DEDUPLICAÇÃO: Se item já existe, soma quantidade.
    """
    client = get_redis_client()
    raw_phone = "" if telefone is None else str(telefone).strip()
    telefone = normalize_phone(raw_phone)
    if client is None:
        return False

    lock_token = None
    try:
        if raw_phone and raw_phone != telefone:
            _maybe_migrate_key(client, f"order_session:{raw_phone}", f"order_session:{telefone}")
            _maybe_migrate_key(client, f"cart:{raw_phone}", f"cart:{telefone}")

        lock_token = _acquire_lock(client, _lock_key("cart", telefone), ttl_seconds=30, wait_seconds=5)
        if not lock_token:
            logger.warning(f"⏳ Timeout aguardando lock do carrinho para {telefone}")
            return False

        # Garante que existe sessão ativa
        session = get_order_session(telefone)
        if not session or session.get("status") != "building":
            start_order_session(telefone)
            session = get_order_session(telefone)

        key = cart_key(telefone)
        
        # 1. Parse do novo item
        import json
        try:
            new_item = json.loads(item_json)
        except Exception:
            logger.error(f"Item JSON inválido para {telefone}")
            return False
        new_prod_name = new_item.get("produto", "").strip().lower()
        
        # 2. Ler itens existentes para deduplicação
        current_items = get_cart_items(telefone)
        found_index = -1
        
        for i, item in enumerate(current_items):
            existing_name = item.get("produto", "").strip().lower()
            # Match exato de nome (simples e seguro)
            if existing_name == new_prod_name:
                found_index = i
                break
        
        if found_index >= 0:
            # --- CENÁRIO: ATUALIZAÇÃO (MERGE) ---
            existing_item = current_items[found_index]
            
            try:
                qtd_old_raw = existing_item.get("quantidade", 0)
                qtd_new_raw = new_item.get("quantidade", 0)
                try:
                    qtd_old = float(qtd_old_raw or 0)
                except Exception:
                    qtd_old = 0.0
                try:
                    qtd_new = float(qtd_new_raw or 0)
                except Exception:
                    qtd_new = 0.0

                nova_qtd = qtd_old + qtd_new
                existing_item["quantidade"] = nova_qtd
                
                # Somar unidades se houver
                unidades_old_raw = existing_item.get("unidades", 0)
                unidades_new_raw = new_item.get("unidades", 0)
                try:
                    unidades_old = int(unidades_old_raw or 0)
                except Exception:
                    unidades_old = 0
                try:
                    unidades_new = int(unidades_new_raw or 0)
                except Exception:
                    unidades_new = 0

                if unidades_old or unidades_new:
                    existing_item["unidades"] = unidades_old + unidades_new
                
                # Atualizar preço (assume que o novo preço é o vigente)
                existing_item["preco"] = new_item.get("preco", existing_item.get("preco"))
                
                # Fundir observações se forem diferentes
                obs_old = existing_item.get("observacao", "")
                obs_new = new_item.get("observacao", "")
                if obs_new and obs_new not in obs_old:
                    existing_item["observacao"] = (f"{obs_old} {obs_new}").strip()
                
                logger.info(f"🔄 Item '{new_prod_name}' atualizado no carrinho (MERGE): {nova_qtd}")
                
                # ATUALIZAÇÃO SEGURA (LSET) - Não apaga o carrinho inteiro!
                client.lset(key, found_index, json.dumps(existing_item, ensure_ascii=False))
                    
            except Exception as e:
                logger.error(f"Erro ao fazer merge de itens: {e}")
                # Fallback: Adiciona como novo se der erro no merge
                client.rpush(key, item_json)

        else:
            # --- CENÁRIO: NOVO ITEM ---
            client.rpush(key, item_json)
        
        # Renova TTL do carrinho e da sessão
        client.expire(key, SESSION_TTL)
        refresh_session_ttl(telefone)
        
        # --- AUTO-UPDATE para pedidos já enviados ---
        # Se o pedido já foi enviado (status='sent'), qualquer adição deve ser propagada para a API imediatamente.
        # Isso corrige o bug onde o agente diz "Adicionei" mas só adiciona no Redis e não na Dashboard.
        if session and session.get("status") == "sent":
            try:
                from tools.http_tools import overwrite_order
                # Para garantir sincronia total, enviamos o carrinho COMPLETO
                full_cart = get_cart_items(telefone)
                payload_api = json.dumps({"itens": full_cart}, ensure_ascii=False)
                
                logger.info(f"🚀 Pedido {session.get('order_id')} já enviado: Disparando overwrite_order() para sync completo.")
                alterar_result = overwrite_order(telefone, payload_api)
                logger.info(f"✅ Auto-update resultado: {alterar_result}")
                
            except Exception as ex_api:
                logger.error(f"❌ Falha no auto-update do pedido enviado: {ex_api}")

        return True
    except Exception as e:
        logger.error(f"Erro ao adicionar item ao carrinho: {e}")
        return False
    finally:
        try:
            if client and lock_token:
                _release_lock(client, _lock_key("cart", telefone), lock_token)
        except Exception:
            pass


def get_cart_items(telefone: str) -> List[Dict]:
    """
    Retorna todos os itens do carrinho como lista de dicionários.
    """
    client = get_redis_client()
    raw_phone = "" if telefone is None else str(telefone).strip()
    telefone = normalize_phone(raw_phone)
    if client is None:
        return []

    try:
        if raw_phone and raw_phone != telefone:
            _maybe_migrate_key(client, f"cart:{raw_phone}", f"cart:{telefone}")
        key = cart_key(telefone)
        # LRANGE 0 -1 pega toda a lista
        items_raw = client.lrange(key, 0, -1)
        
        items = []
        for raw in items_raw:
            try:
                if isinstance(raw, str):
                    items.append(json.loads(raw))
            except:
                continue
                
        return items
    except Exception as e:
        logger.error(f"Erro ao ler carrinho: {e}")
        return []


def remove_item_from_cart(telefone: str, index: int) -> bool:
    """
    Remove item pelo índice (0-based).
    NOTA: Redis Lists não são ideais para remover por índice concorrente, 
    mas para este caso de uso simples (1 usuário), funciona usando LSET + LREM 
    ou apenas recriando a lista.
    
    Abordagem segura: Ler tudo, remover no python, reescrever.
    """
    client = get_redis_client()
    raw_phone = "" if telefone is None else str(telefone).strip()
    telefone = normalize_phone(raw_phone)
    if client is None:
        return False

    lock_token = None
    try:
        if raw_phone and raw_phone != telefone:
            _maybe_migrate_key(client, f"cart:{raw_phone}", f"cart:{telefone}")

        lock_token = _acquire_lock(client, _lock_key("cart", telefone), ttl_seconds=30, wait_seconds=5)
        if not lock_token:
            logger.warning(f"⏳ Timeout aguardando lock do carrinho para {telefone}")
            return False

        key = cart_key(telefone)
        items = client.lrange(key, 0, -1)
        
        if 0 <= index < len(items):
            # Elemento placeholder para marcar remoção
            deleted_marker = "__DELETED__"
            client.lset(key, index, deleted_marker)
            client.lrem(key, 0, deleted_marker)
            
            # --- AUTO-UPDATE (Sync Deletions) ---
            try:
                session = get_order_session(telefone)
                if session and session.get("status") == "sent":
                    from tools.http_tools import overwrite_order
                    # Ler carrinho atualizado
                    full_cart_after = get_cart_items(telefone)
                    payload_api = json.dumps({"itens": full_cart_after}, ensure_ascii=False)
                    
                    logger.info(f"🗑️ Item removido de pedido enviado: Disparando overwrite_order()")
                    overwrite_order(telefone, payload_api)
            except Exception as ex_del:
                logger.error(f"❌ Falha no sync de remoção: {ex_del}")

            return True
            
        return False
    except Exception as e:
        logger.error(f"Erro ao remover item do carrinho: {e}")
        return False
    finally:
        try:
            if client and lock_token:
                _release_lock(client, _lock_key("cart", telefone), lock_token)
        except Exception:
            pass


def update_item_quantity(telefone: str, index: int, quantidade_remover: float) -> dict:
    """
    Reduz a quantidade de um item no carrinho.
    Se a quantidade resultante for <= 0, remove o item completamente.
    
    Args:
        telefone: Número do cliente
        index: Índice do item (0-based)
        quantidade_remover: Quantidade a ser removida (ex: 1 para tirar 1 unidade)
    
    Returns:
        {
            "success": bool,
            "removed_completely": bool,  # True se item foi removido totalmente
            "new_quantity": float,  # Nova quantidade (0 se removido)
            "item_name": str
        }
    """
    client = get_redis_client()
    raw_phone = "" if telefone is None else str(telefone).strip()
    telefone = normalize_phone(raw_phone)
    if client is None:
        return {"success": False, "removed_completely": False, "new_quantity": 0, "item_name": ""}

    lock_token = None
    try:
        if raw_phone and raw_phone != telefone:
            _maybe_migrate_key(client, f"cart:{raw_phone}", f"cart:{telefone}")

        lock_token = _acquire_lock(client, _lock_key("cart", telefone), ttl_seconds=30, wait_seconds=5)
        if not lock_token:
            logger.warning(f"⏳ Timeout aguardando lock do carrinho para {telefone}")
            return {"success": False, "removed_completely": False, "new_quantity": 0, "item_name": ""}

        key = cart_key(telefone)
        items = client.lrange(key, 0, -1)
        
        if not (0 <= index < len(items)):
            return {"success": False, "removed_completely": False, "new_quantity": 0, "item_name": ""}
        
        # Parse do item
        try:
            item = json.loads(items[index])
        except:
            return {"success": False, "removed_completely": False, "new_quantity": 0, "item_name": ""}
        
        item_name = item.get("produto", "Item")
        current_qty = float(item.get("quantidade", 1))
        current_units = int(item.get("unidades", 0))
        
        # Calcular nova quantidade
        new_qty = current_qty - quantidade_remover
        
        if new_qty <= 0:
            # Remover item completamente
            deleted_marker = "__DELETED__"
            client.lset(key, index, deleted_marker)
            client.lrem(key, 0, deleted_marker)
            logger.info(f"🗑️ Item '{item_name}' removido completamente (quantidade <= 0)")
            
            result = {"success": True, "removed_completely": True, "new_quantity": 0, "item_name": item_name}
        else:
            # Atualizar quantidade
            item["quantidade"] = new_qty
            
            # Atualizar unidades proporcionalmente se aplicável
            if current_units > 0:
                proporcao = new_qty / current_qty
                item["unidades"] = max(0, int(current_units * proporcao))
            
            # Salvar item atualizado
            client.lset(key, index, json.dumps(item, ensure_ascii=False))
            logger.info(f"📉 Item '{item_name}' atualizado: {current_qty} → {new_qty}")
            
            result = {"success": True, "removed_completely": False, "new_quantity": new_qty, "item_name": item_name}
        
        # --- AUTO-UPDATE (Sync Changes) ---
        try:
            session = get_order_session(telefone)
            if session and session.get("status") == "sent":
                from tools.http_tools import overwrite_order
                full_cart_after = get_cart_items(telefone)
                payload_api = json.dumps({"itens": full_cart_after}, ensure_ascii=False)
                logger.info(f"🔄 Quantidade alterada em pedido enviado: Disparando overwrite_order()")
                overwrite_order(telefone, payload_api)
        except Exception as ex_upd:
            logger.error(f"❌ Falha no sync de alteração: {ex_upd}")
        
        return result
        
    except Exception as e:
        logger.error(f"Erro ao atualizar quantidade do item: {e}")
        return {"success": False, "removed_completely": False, "new_quantity": 0, "item_name": ""}
    finally:
        try:
            if client and lock_token:
                _release_lock(client, _lock_key("cart", telefone), lock_token)
        except Exception:
            pass


def clear_cart(telefone: str) -> bool:
    """Remove todo o carrinho."""
    client = get_redis_client()
    raw_phone = "" if telefone is None else str(telefone).strip()
    telefone = normalize_phone(raw_phone)
    if client is None:
        return False

    lock_token = None
    try:
        if raw_phone and raw_phone != telefone:
            _maybe_migrate_key(client, f"cart:{raw_phone}", f"cart:{telefone}")
        lock_token = _acquire_lock(client, _lock_key("cart", telefone), ttl_seconds=30, wait_seconds=5)
        if not lock_token:
            logger.warning(f"⏳ Timeout aguardando lock do carrinho para {telefone}")
            return False
        client.delete(cart_key(telefone))
        logger.info(f"🛒 Carrinho limpo para {telefone}")
        return True
    except Exception as e:
        logger.error(f"Erro ao limpar carrinho: {e}")
        return False
    finally:
        try:
            if client and lock_token:
                _release_lock(client, _lock_key("cart", telefone), lock_token)
        except Exception:
            pass


# ============================================
# Comprovante PIX (Receipt URL Storage)
# ============================================

def comprovante_key(telefone: str) -> str:
    """Chave para armazenar URL do comprovante PIX."""
    return f"comprovante:{normalize_phone(telefone)}"


def set_comprovante(telefone: str, url: str) -> bool:
    """
    Salva a URL do comprovante PIX do cliente.
    TTL de 2 horas (mesmo período que sessão de pedido).
    
    Args:
        telefone: Número do cliente
        url: URL da imagem do comprovante
    
    Returns:
        True se salvo com sucesso
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return False
    
    try:
        key = comprovante_key(telefone)
        client.set(key, url, ex=7200)  # 2 horas
        logger.info(f"🧾 Comprovante PIX salvo para {telefone}: {url[:50]}...")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar comprovante: {e}")
        return False


def get_comprovante(telefone: str) -> Optional[str]:
    """
    Recupera a URL do comprovante PIX do cliente.
    
    Returns:
        URL do comprovante ou None
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return None
    
    try:
        key = comprovante_key(telefone)
        url = client.get(key)
        if url:
            logger.info(f"🧾 Comprovante recuperado para {telefone}")
        return url
    except Exception as e:
        logger.error(f"Erro ao recuperar comprovante: {e}")
        return None


def clear_comprovante(telefone: str) -> bool:
    """Remove o comprovante do cliente (após finalizar pedido)."""
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return False
    
    try:
        client.delete(comprovante_key(telefone))
        logger.info(f"🧾 Comprovante limpo para {telefone}")
        return True
    except Exception as e:
        logger.error(f"Erro ao limpar comprovante: {e}")
        return False

# ============================================
# Endereço do Cliente (Persistence)
# ============================================

def address_key(telefone: str) -> str:
    """Chave para armazenar endereço do cliente temporariamente."""
    return f"address:{normalize_phone(telefone)}"


def set_address(telefone: str, endereco: str) -> bool:
    """
    Salva o endereço do cliente.
    TTL de 2 horas.
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return False
    
    try:
        key = address_key(telefone)
        client.set(key, endereco, ex=7200)  # 2 horas
        logger.info(f"🏠 Endereço salvo para {telefone}: {endereco[:50]}...")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar endereço: {e}")
        return False


def get_address(telefone: str) -> Optional[str]:
    """Recupera o endereço salvo do cliente."""
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return None
    
    try:
        key = address_key(telefone)
        addr = client.get(key)
        if addr:
            logger.info(f"🏠 Endereço recuperado para {telefone}")
        return addr
    except Exception as e:
        logger.error(f"Erro ao recuperar endereço: {e}")
        return None


def clear_address(telefone: str) -> bool:
    """Remove o endereço salvo."""
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        return False
    
    try:
        client.delete(address_key(telefone))
        logger.info(f"🏠 Endereço limpo para {telefone}")
        return True
    except Exception as e:
        logger.error(f"Erro ao limpar endereço: {e}")
        return False
# ============================================
# Aliases para compatibilidade com agent_multiagent.py
# ============================================

def save_address(telefone: str, endereco: str) -> bool:
    """Alias para set_address"""
    return set_address(telefone, endereco)

def get_saved_address(telefone: str) -> Optional[str]:
    """Alias para get_address"""
    return get_address(telefone)


# ============================================
# Cache de Produtos Sugeridos (Memória Compartilhada Vendedor ↔ Analista)
# ============================================

SUGGESTIONS_TTL = 600  # 10 minutos

def suggestions_key(telefone: str) -> str:
    """Chave para armazenar produtos sugeridos."""
    return f"suggestions:{normalize_phone(telefone)}"


def save_suggestions(telefone: str, products: List[Dict]) -> bool:
    """
    Salva os produtos sugeridos pelo Analista para o cliente.
    O Vendedor pode recuperar esses dados quando o cliente confirmar.
    
    Args:
        telefone: Número do cliente
        products: Lista de produtos [{nome, preco, termo_busca}, ...]
    
    Returns:
        True se salvo com sucesso
    """
    client = get_redis_client()
    telefone = normalize_phone(telefone)
    if client is None:
        logger.warning(f"[fallback] Sugestões não persistidas (Redis indisponível) para {telefone}")
        return False
    
    try:
        key = suggestions_key(telefone)
        
        # 1. Recuperar existentes para merge
        existing_data = client.get(key)
        existing_products = []
        if existing_data:
            try:
                existing_products = json.loads(existing_data)
            except:
                pass
                
        # 2. Merge com novos (deduplicando por nome)
        # Mapa para deduplicar: chave = nome_lower
        prod_map = {p.get("nome", "").lower(): p for p in existing_products}
        
        for new_p in products:
            nome = new_p.get("nome", "").lower()
            # Sobrescreve anterior se existir (assumindo que o novo é mais recente/melhor)
            # Ou mantém ambos? Melhor sobrescrever se for o mesmo produto para atualizar preço
            prod_map[nome] = new_p
            
        final_list = list(prod_map.values())
        
        # Salvar como JSON
        client.set(key, json.dumps(final_list, ensure_ascii=False), ex=SUGGESTIONS_TTL)
        logger.info(f"💡 {len(final_list)} sugestões salvas (Merge: {len(existing_products)} + {len(products)}) para {telefone}")
        return True
    except Exception as e:
        logger.error(f"Erro ao salvar sugestões: {e}")
        return False


def get_suggestions(telefone: str) -> List[Dict]:
    """
    Recupera os produtos sugeridos anteriormente para o cliente.
    
    Returns:
        Lista de produtos [{nome, preco, termo_busca}, ...] ou lista vazia
    """
    client = get_redis_client()
    if client is None:
        return []
    
    try:
        key = suggestions_key(telefone)
        data = client.get(key)
        if data:
            products = json.loads(data)
            logger.info(f"💡 Sugestões recuperadas para {telefone}: {len(products)} produtos")
            return products if isinstance(products, list) else []
        return []
    except Exception as e:
        logger.error(f"Erro ao recuperar sugestões: {e}")
        return []


def clear_suggestions(telefone: str) -> bool:
    """Remove as sugestões após serem usadas."""
    client = get_redis_client()
    if client is None:
        return False
    
    try:
        client.delete(suggestions_key(telefone))
        logger.info(f"💡 Sugestões limpas para {telefone}")
        return True
    except Exception as e:
        logger.error(f"Erro ao limpar sugestões: {e}")
        return False

# ============================================
# Circuit Breaker (Disjuntor de API)
# ============================================

def circuit_failure_key(service: str) -> str:
    return f"circuit:failures:{service}"

def circuit_open_key(service: str) -> str:
    return f"circuit:open:{service}"

def check_circuit_open(service: str) -> bool:
    """
    Verifica se o disjuntor está ABERTO (serviço fora do ar).
    Retorna True se estiver aberto (não deve chamar o serviço).
    """
    client = get_redis_client()
    if client is None: return False
    
    try:
        # Se a chave circuit:open existir, o circuito está aberto
        is_open = client.get(circuit_open_key(service))
        if is_open:
            logger.warning(f"⚡ Circuit Breaker ABERTO para {service}. Bloqueando chamada.")
            return True
        return False
    except:
        return False

def report_failure(service: str, threshold: int = 15, cooldown: int = 30) -> None:
    """
    Reporta uma falha no serviço. Se atingir o threshold, abre o circuito.
    Aumentado threshold (5->15) e reduzido cooldown (60->30) para evitar falsos positivos de "sistema fora".
    """
    client = get_redis_client()
    if client is None: return

    try:
        fkey = circuit_failure_key(service)
        # Incrementa contador de falhas (TTL 60s para janela de falhas)
        failures = client.incr(fkey)
        if failures == 1:
            client.expire(fkey, 60) # Janela de 1 min para acumular falhas
            
        if failures >= threshold:
            # Abre o circuito!
            okey = circuit_open_key(service)
            client.set(okey, "1", ex=cooldown)
            logger.critical(f"⚡⚡ CIRCUIT BREAKER DISPARADO: {service} falhou {failures}x. Pausando por {cooldown}s.")
            # Limpa contador para reiniciar ciclo após cooldown
            client.delete(fkey)
            
    except Exception as e:
        logger.error(f"Erro no circuit breaker (fail): {e}")

def report_success(service: str) -> None:
    """
    Reporta sucesso. Se o circuito estava instável, reseta contadores.
    """
    client = get_redis_client()
    if client is None: return

    try:
        # Se houve sucesso, podemos limpar a contagem de falhas recente
        # Isso implementa uma recuperação "Half-Open" implícita: se passar uma, zera as falhas.
        fkey = circuit_failure_key(service)
        if client.exists(fkey):
            client.delete(fkey)
    except Exception as e:
        logger.error(f"Erro no circuit breaker (success): {e}")
