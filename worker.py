"""
ARQ Worker para processar mensagens em fila
Evita rate limits do Gemini processando no máximo N mensagens simultâneas
"""
import asyncio
import time
import random
import re
from typing import Dict, Any
from arq import create_pool
from arq.connections import RedisSettings

from config.settings import settings
from config.logger import setup_logger
from agent_multiagent import run_agent
from tools.whatsapp_api import WhatsAppAPI

logger = setup_logger(__name__)
whatsapp = WhatsAppAPI()


async def process_message(ctx: Dict[str, Any], telefone: str, mensagem: str, message_id: str = None) -> str:
    """
    Processa uma mensagem do WhatsApp (função executada pelo worker ARQ).
    
    Este é o equivalente ao antigo `process_async` do server.py, mas rodando
    como um job ARQ na fila.
    
    Args:
        ctx: Contexto ARQ (contém pool Redis, etc)
        telefone: Número do cliente
        mensagem: Texto da mensagem
        message_id: ID da mensagem (para mark_as_read)
    
    Returns:
        Status da execução
    """
    try:
        num = re.sub(r"\D", "", telefone)
        
        # 1. Simular "Lendo" (Delay Humano)
        tempo_leitura = random.uniform(2.0, 4.0)
        await asyncio.sleep(tempo_leitura)
        
        # 2. Marcar como LIDO (Azul) - Suporte a múltiplos IDs
        if message_id:
            mids = message_id if isinstance(message_id, list) else [message_id]
            logger.info(f"👀 Marcando chat {telefone} como lido... (MIDs: {len(mids)})")
            
            for mid in mids:
                if mid:
                    whatsapp.mark_as_read(telefone, message_id=mid)
                    # Pequeno delay entre requests para não floodar (se forem muitos)
                    if len(mids) > 1: await asyncio.sleep(0.1)
            
            await asyncio.sleep(0.8)  # Delay tático para UX
        
        # 3. Começar a "Digitar"
        whatsapp.send_presence(num, "composing")
        
        # 3.5 Processar mídia se for placeholder ([MEDIA:TYPE:ID])
        if mensagem.startswith("[MEDIA:"):
            try:
                # Parse: [MEDIA:IMAGE:3EB08C4C6042...]
                parts = mensagem.strip("[]").split(":")
                media_type = parts[1].lower() if len(parts) > 1 else "image"
                media_id = parts[2] if len(parts) > 2 else None
                
                if media_id:
                    logger.info(f"📷 Processando mídia {media_type}: {media_id}")
                    
                    if media_type == "image":
                        # Importar função de análise do server.py
                        from server import analyze_image
                        analysis = analyze_image(media_id, None)
                        if analysis:
                            mensagem = f"[Análise da imagem]: {analysis}"
                            logger.info(f"✅ Imagem analisada: {analysis[:50]}...")
                        else:
                            mensagem = "[Imagem recebida, mas não foi possível analisar]"
                    elif media_type == "audio":
                        from server import transcribe_audio
                        transcription = transcribe_audio(media_id)
                        if transcription:
                            mensagem = f"[Áudio]: {transcription}"
                            logger.info(f"✅ Áudio transcrito: {transcription[:50]}...")
                        else:
                            mensagem = "[Áudio recebido, mas não foi possível transcrever]"
                    elif media_type == "document":
                        from server import process_pdf
                        pdf_text = process_pdf(media_id)
                        if pdf_text:
                            mensagem = f"[Conteúdo PDF]: {pdf_text[:1200]}"
                        else:
                            mensagem = "[Documento/PDF recebido]"
            except Exception as e:
                logger.error(f"❌ Erro ao processar mídia: {e}")
                mensagem = "[Mídia recebida, erro ao processar]"
        
        # 4. Processamento IA (síncrono - run_agent não é async)
        # Rodamos em thread_pool para não bloquear o event loop
        loop = asyncio.get_event_loop()
        res = await loop.run_in_executor(None, run_agent, telefone, mensagem)
        txt = res.get("output", "Erro ao processar.")
        
        # 5. Parar "Digitar"
        whatsapp.send_presence(num, "paused")
        await asyncio.sleep(0.5)
        
        # 6. Enviar Mensagem (também síncrono)
        await loop.run_in_executor(None, _send_whatsapp_message, telefone, txt)
        
        logger.info(f"✅ Mensagem processada com sucesso: {telefone}")
        return "success"
        
    except Exception as e:
        logger.error(f"❌ Erro ao processar mensagem de {telefone}: {e}", exc_info=True)
        # Parar digitando em caso de erro
        try:
            whatsapp.send_presence(telefone, "paused")
        except:
            pass
        raise  # ARQ vai fazer retry automático


def _send_whatsapp_message(telefone: str, mensagem: str) -> bool:
    """Helper síncrono para enviar mensagem (com detecção de múltiplas imagens)"""
    import requests
    import base64
    import re
    
    # Regex para encontrar todas as URLs de imagem (jpg, png, jpeg, webp)
    # OTIMIZADO: Evita pontuação final (.,;!) e captura múltiplos
    regex = r'(https?://[^\s]+\.(?:jpg|jpeg|png|webp))'
    urls_encontradas = re.findall(regex, mensagem, re.IGNORECASE)
    
    if urls_encontradas:
        # Texto limpo: remove todos os links para não ficar redundante no WhatsApp
        texto_limpo = mensagem
        for url in urls_encontradas:
            # Substitui links seguidos opcionalmente por quebras de linha/espaços
            texto_limpo = re.sub(re.escape(url) + r'[\s\n]*', '', texto_limpo).strip()
            
        logger.info(f"📸 Detectadas {len(urls_encontradas)} URLs de imagem. Texto limpo: {texto_limpo[:50]}...")
        
        # 1. Enviar primeiro o TEXTO como mensagem separada (se houver texto)
        if texto_limpo:
            whatsapp.send_text(telefone, texto_limpo)
            # Pequeno delay para a mensagem de texto chegar primeiro
            time.sleep(1.0)
            
        # 2. Enviar cada imagem sequencialmente
        for i, image_url in enumerate(urls_encontradas):
            logger.info(f"⬇️ Baixando imagem [{i+1}/{len(urls_encontradas)}]: {image_url}")
            
            try:
                # Baixar imagem
                img_resp = requests.get(image_url, timeout=15)
                img_resp.raise_for_status()
                
                # Converter para Base64
                img_b64 = base64.b64encode(img_resp.content).decode('utf-8')
                mime = img_resp.headers.get("Content-Type", "image/jpeg")
                
                # Enviar como mídia (sem caption agora, pois o texto já foi enviado)
                whatsapp.send_media(telefone, caption="", base64_data=img_b64, mimetype=mime)
                
                # Pequeno delay entre imagens
                if i < len(urls_encontradas) - 1:
                    time.sleep(1.2)
            
            except Exception as e:
                logger.error(f"❌ Erro ao baixar/enviar imagem {image_url}: {e}")
                # Fallback: Tentar enviar via URL
                whatsapp.send_media(telefone, media_url=image_url, caption="")
        
        return True
    
    # Mensagem normal (sem imagem)
    max_len = 500
    msgs = []
    
    if len(mensagem) > max_len:
        paragrafos = mensagem.split('\n\n')
        curr = ""
        
        for p in paragrafos:
            if len(p) > max_len:
                if curr:
                    msgs.append(curr.strip())
                    curr = ""
                linhas = p.split('\n')
                for linha in linhas:
                    if len(curr) + len(linha) + 1 <= max_len:
                        curr += linha + "\n"
                    else:
                        if curr: msgs.append(curr.strip())
                        curr = linha + "\n"
            elif len(curr) + len(p) + 2 <= max_len:
                curr += p + "\n\n"
            else:
                if curr: msgs.append(curr.strip())
                curr = p + "\n\n"
        
        if curr: msgs.append(curr.strip())
    else:
        msgs = [mensagem]
    
    try:
        for i, msg in enumerate(msgs):
            whatsapp.send_text(telefone, msg)
            if i < len(msgs) - 1:
                time.sleep(random.uniform(0.8, 1.5))
        return True
    except Exception as e:
        logger.error(f"Erro envio: {e}")
        return False


class WorkerSettings:
    """Configuração do ARQ Worker"""
    
    # Conexão Redis (mesma do resto do sistema)
    redis_settings = RedisSettings(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password,
        database=settings.redis_db,
    )
    
    # Funções que o worker pode executar
    functions = [process_message]
    
    # Configurações de concorrência e retry
    max_jobs = settings.workers_max_jobs  # Máximo de jobs simultâneos (5)
    job_timeout = 600  # Timeout de 10 minutos por job (aumentado para pedidos grandes)
    max_tries = settings.worker_retry_attempts  # 3 tentativas
    
    # Configurações de saúde e monitoramento
    health_check_interval = 30  # Verifica saúde a cada 30s
    keep_result = 3600  # Mantém resultado por 1h
    
    # Nome da fila (Removido para usar o padrão arq:queue e casar com o server.py)
    # queue_name = "whatsapp_messages"


async def main():
    """Inicia o worker ARQ"""
    logger.info("🚀 Iniciando ARQ Worker...")
    logger.info(f"📊 Configuração: max_jobs={WorkerSettings.max_jobs}, max_tries={WorkerSettings.max_tries}")
    
    # Configuração com a nova API do ARQ 0.26
    from arq.worker import create_worker, func
    
    # Criar worker com as configurações
    worker = create_worker(WorkerSettings)
    
    # Rodar o worker
    await worker.async_run()


if __name__ == "__main__":
    asyncio.run(main())
