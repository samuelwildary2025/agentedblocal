# 🔴 PROBLEMA CRÍTICO: Agente Não Responde Corretamente

## Sintomas
- Cliente envia pedido: "5 laranjas 6 ceboolas 5 tomates e um cheiro verde"
- Agente responde: "Olá! Boa noite! Como posso te ajudar?" (mensagem genérica)
- Log mostra: `Completion: 0` tokens (LLM não gera resposta)

## Causa Raiz
Erro no banco de dados vetorial PostgreSQL:
```
❌ function hybrid_search_v2(...) is not unique
```

Há **funções duplicadas** no banco, causando ambiguidade. O PostgreSQL não sabe qual usar, retorna erro, e o agente não consegue buscar produtos.

## ✅ Solução

### Passo 1: Conectar ao Banco Vetorial
```bash
# Usar as credenciais do .env
psql "postgres://poostgres:85885885@31.97.252.6:8877/agente-db-pgvectorstore?sslmode=disable"
```

### Passo 2: Executar Script de Correção
```bash
# No terminal do projeto
psql "postgres://poostgres:85885885@31.97.252.6:8877/agente-db-pgvectorstore?sslmode=disable" -f scripts/fix_hybrid_search_duplicate.sql
```

**OU** copiar e colar o conteúdo de `scripts/fix_hybrid_search_duplicate.sql` direto no psql.

### Passo 3: Verificar se Corrigiu
```sql
-- Deve retornar APENAS 1 função
SELECT 
    p.proname as function_name,
    pg_get_function_arguments(p.oid) as arguments
FROM pg_proc p
JOIN pg_namespace n ON p.pronamespace = n.oid
WHERE p.proname = 'hybrid_search_v2';
```

### Passo 4: Testar o Agente
```python
# Abrir Python no diretório do projeto
from tools.db_vector_search import search_products_vector

# Testar busca
result = search_products_vector("laranja")
print(result)  # Deve retornar produtos sem erro
```

### Passo 5: Reiniciar o Servidor
```bash
# Se estiver rodando no Docker/EasyPanel, fazer redeploy
# Se estiver rodando localmente:
pkill -f "python.*server.py"
python server.py
```

## 🔍 Verificação

Após a correção, o log deve mostrar:
```
✅ [BUSCA LOTE] Sucesso com 'LARANJA' (R$ X.XX)
```

Em vez de:
```
❌ Erro na busca vetorial: function is not unique
```

## 🚨 Prevenção Futura

Para evitar que isso aconteça novamente:
1. ✅ Sempre use `CREATE OR REPLACE FUNCTION` ao criar funções
2. ✅ Especifique tipos explícitos nos argumentos (`VECTOR(1536)` em vez de `VECTOR`)
3. ✅ Antes de criar função, execute `DROP FUNCTION IF EXISTS` primeiro

## 📊 Impacto

**Antes da correção:**
- ❌ Agente não consegue buscar produtos
- ❌ Cliente recebe respostas genéricas
- ❌ Pedidos não são processados

**Depois da correção:**
- ✅ Busca vetorial funciona normalmente
- ✅ Agente processa pedidos corretamente
-  ✅ Cliente recebe preços e consegue finalizar compra

---

**Arquivo gerado em:** 19/01/2026 01:44
**Prioridade:** 🔴 CRÍTICA - Sistema não funciona sem essa correção
