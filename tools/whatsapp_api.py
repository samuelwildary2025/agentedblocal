import requests
import json
import re
from typing import Optional, Dict, Any
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)

class WhatsAppAPI:
    def __init__(self):
        self.base_url = (settings.whatsapp_api_base_url or "").rstrip("/")
        self.token = settings.whatsapp_instance_token
        
        if not self.base_url:
            logger.warning("WHATSAPP_API_BASE_URL não configurado!")
            
    def _get_headers(self):
        # Tenta cobrir vários padrões de auth de APIs de WhatsApp
        return {
            "Content-Type": "application/json",
            "apikey": self.token,
            "token": self.token,
            "Authorization": f"Bearer {self.token}",
            "X-Instance-Token": self.token # Header específico confirmado no teste
        }

    def _clean_number(self, phone: str) -> str:
        """Remove caracteres não numéricos"""
        return re.sub(r"\D", "", str(phone))

    def send_media(self, to: str, media_url: str = None, caption: str = "", base64_data: str = None, mimetype: str = "image/jpeg") -> bool:
        """
        Envia mensagem de mídia (Imagem/Vídeo/PDF)
        POST /message/media
        Aceita URL ou Base64
        """
        if not self.base_url: return False
        
        url = f"{self.base_url}/message/media"
        
        # Limpa o número
        clean_num = self._clean_number(to)
        jid = f"{clean_num}@s.whatsapp.net"
        
        payload = {
            "to": jid,
            "caption": caption
        }
        
        if base64_data:
            # API espera 'base64' e 'mimetype' como campos
            payload["base64"] = base64_data
            payload["mimetype"] = mimetype
        elif media_url:
            payload["mediaUrl"] = media_url
            
        logger.info(f"📷 Enviando mídia para {jid} | HasURL: {bool(media_url)} | HasBase64: {bool(base64_data)}")
        
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=25)
            if resp.status_code != 200:
                logger.error(f"❌ Erro envio mídia ({resp.status_code}): {resp.text[:200]}")
                # Fallback: Tentar endpoint antigo ou alternativo se falhar? 
                # Por enquanto apenas logar erro.
            else:
                logger.info("✅ Mídia enviada com sucesso")
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"❌ Erro ao enviar mídia: {e}")
            return False

    def send_text(self, to: str, text: str) -> bool:
        """
        Envia mensagem de texto simples
        POST /message/text
        Suporta o delimitador <BREAK> para enviar múltiplas mensagens sequenciais.
        """
        if not self.base_url: 
            logger.error("❌ WHATSAPP_API_BASE_URL não configurado! Mensagem NÃO enviada.")
            return False
            
        # Verifica se há o delimitador de quebra
        if "<BREAK>" in text:
            parts = text.split("<BREAK>")
            logger.info(f"🔄 Mensagem multi-parte detectada! Dividindo em {len(parts)} mensagens.")
            
            success_all = True
            import time
            
            for index, part in enumerate(parts):
                part = part.strip()
                if not part: continue
                
                # Pequeno delay entre mensagens (exceto a primeira)
                if index > 0:
                    time.sleep(3.0)
                    
                if not self.send_text(to, part):
                    success_all = False
            
            return success_all
        
        url = f"{self.base_url}/message/text"
        
        # Limpa o número e formata como JID se necessário
        clean_num = self._clean_number(to)
        # Tenta com JID completo (@s.whatsapp.net)
        jid = f"{clean_num}@s.whatsapp.net"
        
        payload = {
            "to": jid,  # Usando JID completo
            "text": text
        }
        
        logger.info(f"📤 Enviando mensagem para {jid}: {text[:50]}...")
        # logger.info(f"📤 URL: {url}")
        
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=10)
            
            # Log da resposta COMPLETA
            # logger.info(f"📥 Resposta API WhatsApp: Status={resp.status_code}")
            # logger.info(f"📥 Resposta Body: {resp.text[:500]}")
            
            if resp.status_code != 200:
                logger.error(f"❌ Erro API WhatsApp ({resp.status_code}): {resp.text[:500]}")
                return False
            else:
                logger.info(f"✅ Mensagem enviada com sucesso para {to}")
                
            # resp.raise_for_status() # Removido para evitar exceção duplicada
            return True
        except requests.exceptions.HTTPError as e:
            logger.error(f"❌ Erro HTTP ao enviar mensagem para {to}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Erro ao enviar mensagem WhatsApp para {to}: {e}")
            return False

    def send_presence(self, to: str, presence: str = "composing") -> bool:
        """
        Envia status de presença (digitando...)
        POST /message/presence
        Valores: composing, recording, available, unavailable
        """
        if not self.base_url: return False
        
        url = f"{self.base_url}/message/presence"
        payload = {
            "to": self._clean_number(to),
            "presence": presence
        }
        
        try:
            requests.post(url, headers=self._get_headers(), json=payload, timeout=5)
            return True
        except Exception:
            return False

    def mark_as_read(self, chat_id: str, message_id: str = None) -> bool:
        """
        Marca o chat como lido (Tick Azul)
        POST /message/read
        Body: { "chatId": "55...", "messageId": "ABC123" }
        
        Nota: whatsmeow EXIGE messageId para funcionar.
        """
        if not self.base_url or not chat_id: 
            logger.warning("⚠️ mark_as_read: base_url ou chat_id não configurado")
            return False
        
        if not message_id:
            logger.warning("⚠️ mark_as_read: messageId não fornecido, ignorando")
            return False
        
        # Limpa o número (remove caracteres especiais)
        clean_num = self._clean_number(chat_id)
        
        url = f"{self.base_url}/message/read"
        
        # API requer chatId + messageId
        payload = {
            "chatId": clean_num,
            "messageId": message_id
        }
        
        logger.info(f"👀 mark_as_read: {payload}")
        
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=5)
            if resp.status_code == 200:
                logger.info(f"✅ Chat {chat_id} marcado como lido")
            else:
                logger.warning(f"⚠️ mark_as_read falhou ({resp.status_code}): {resp.text[:200]}")
            return resp.status_code == 200
        except Exception as e:
            logger.error(f"❌ Erro mark_as_read: {e}")
            return False

    def get_media_base64(self, message_id: str) -> Optional[Dict[str, str]]:
        """
        Obtém mídia em Base64
        POST /message/download
        Retorna dict com 'base64' e 'mimetype'
        """
        if not self.base_url: return None
        
        url = f"{self.base_url}/message/download"
        payload = {
            "id": message_id,
            "return_link": False,
            "return_base64": True
        }
        
        logger.info(f"🌐 DEBUG API CALL: {url} | ID: {message_id}")
        
        try:
            resp = requests.post(url, headers=self._get_headers(), json=payload, timeout=30)
            logger.info(f"🌐 DEBUG API RESPONSE: Status={resp.status_code}") # Timeout maior para download
            if resp.status_code == 200:
                data = resp.json()
                # A API retorna { success: true, data: { base64: "...", mimetype: "..." } }
                if data.get("success") and "data" in data:
                    return data["data"]
                # Ou pode retornar direto no root se a versão for diferente
                if "base64" in data:
                    return data
            else:
                logger.warning(f"⚠️ Erro API Mídia ({resp.status_code}): {resp.text[:200]}")
        except Exception as e:
            logger.error(f"Erro ao obter mídia WhatsApp ({message_id}): {e}")
            
        return None

# Instância global
whatsapp = WhatsAppAPI()
