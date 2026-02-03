#!/usr/bin/env python3
"""
Script de Debug para Cart e Sessão do Redis
Uso: python scripts/debug_cart_cli.py <telefone>
Ex: python scripts/debug_cart_cli.py 558599999999
"""
import sys
import os
import json
from pathlib import Path

# Adiciona o diretório raiz ao path para imports
root_dir = Path(__file__).resolve().parent.parent
sys.path.append(str(root_dir))

from tools.redis_tools import get_cart_items, get_order_session, get_address, get_comprovante
from config.settings import settings

def inspect_client(phone):
    print(f"\n🔍 Inspecionando dados para: {phone}")
    print(f"🌍 Redis Host: {settings.redis_host}:{settings.redis_port}")
    
    # 1. Sessão
    session = get_order_session(phone)
    print("\n📦 [SESSÃO]")
    if session:
        print(json.dumps(session, indent=2, ensure_ascii=False))
    else:
        print("❌ Nenhuma sessão ativa.")

    # 2. Endereço
    addr = get_address(phone)
    print(f"\n🏠 [ENDEREÇO]: {addr if addr else '❌ Não salvo'}")

    # 3. Comprovante
    comp = get_comprovante(phone)
    print(f"\n🧾 [COMPROVANTE]: {comp if comp else '❌ Não salvo'}")

    # 4. Carrinho
    items = get_cart_items(phone)
    print(f"\n🛒 [CARRINHO] ({len(items)} itens)")
    if items:
        for i, item in enumerate(items, 1):
            print(f"  {i}. {item.get('produto')} | Qtd: {item.get('quantidade')} | Tot: R${item.get('quantidade',0) * item.get('preco',0):.2f}")
    else:
        print("❌ Carrinho vazio.")
    
    print("\n------------------------------------------------")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python scripts/debug_cart_cli.py <telefone>")
        sys.exit(1)
    
    phone = sys.argv[1]
    inspect_client(phone)
