"""
WhatsApp API - STUB/PLACEHOLDER
================================
A API de WhatsApp foi removida.
Este arquivo contém funções stub que apenas logam as operações.

Para integrar uma nova API:
1. Implemente os métodos desta classe
2. Configure as variáveis de ambiente necessárias
"""

from typing import Optional, Dict
from config.logger import setup_logger

logger = setup_logger(__name__)


class WhatsAppAPI:
    """
    STUB - API de WhatsApp desativada.
    Todos os métodos apenas logam a operação solicitada.
    """
    
    def __init__(self):
        logger.warning("⚠️ WhatsApp API está DESATIVADA. Configure uma nova integração.")
    
    def send_text(self, to: str, text: str) -> bool:
        """STUB: Envia mensagem de texto"""
        logger.info(f"📤 [STUB] send_text para {to}: {text[:50]}...")
        return True  # Retorna True para não quebrar fluxo
    
    def send_media(self, to: str, media_url: str = None, caption: str = "", 
                   base64_data: str = None, mimetype: str = "image/jpeg") -> bool:
        """STUB: Envia mídia"""
        logger.info(f"📷 [STUB] send_media para {to}")
        return True
    
    def send_presence(self, to: str, presence: str = "composing") -> bool:
        """STUB: Envia presença (digitando...)"""
        logger.debug(f"⌨️ [STUB] send_presence {presence} para {to}")
        return True
    
    def mark_as_read(self, chat_id: str, message_id: str = None) -> bool:
        """STUB: Marca como lido"""
        logger.debug(f"👀 [STUB] mark_as_read chat={chat_id}")
        return True
    
    def get_media_base64(self, message_id: str) -> Optional[Dict[str, str]]:
        """STUB: Obtém mídia - retorna None (não disponível)"""
        logger.info(f"🖼️ [STUB] get_media_base64 id={message_id} - Não disponível")
        return None


# Instância global
whatsapp = WhatsAppAPI()
