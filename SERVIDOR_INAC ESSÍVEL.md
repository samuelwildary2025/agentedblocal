# 🔴 PROBLEMA: Servidor de Banco de Dados Inacessível

## Status Atual
❌ **O servidor PostgreSQL está OFFLINE ou INACESSÍVEL**

## Tentativas Realizadas
```bash
# Porta 8877 (Banco Vetorial)
nc -zv 31.97.252.6 8877
# Resultado: Connection refused

# Porta 3043 (Banco de Produtos)  
nc -zv 31.97.252.6 3043
# Resultado: Connection refused

# Tentativa via psql
psql "postgres://...@31.97.252.6:8877/..."
# Resultado: Connection refused
```

## Possíveis Causas

### 1. Servidor Offline
- O servidor 31.97.252.6 pode estar desligado
- O PostgreSQL pode não estar rodando

### 2. Firewall/Segurança
- Portas 8877 e 3043 podem estar bloqueadas
- Pode ser necessário VPN
- Pode ser necessário SSH tunnel

### 3. Mudança de Configuração
- O endereço do banco pode ter mudado
- As credenciais podem estar desatualizadas

## 📋 Opções para Resolver

### Opção 1: Verificar se o Servidor está Online
```bash
ping 31.97.252.6
```

### Opção 2: Usar SSH Tunnel (se houver acesso SSH)
```bash
# Se você tem acesso SSH ao servidor
ssh -L 8877:localhost:5432 usuario@31.97.252.6

# Em outro terminal
psql "postgres://poostgres:85885885@localhost:8877/agente-db-pgvectorstore?sslmode=disable" -f scripts/fix_hybrid_search_duplicate.sql
```

### Opção 3: Conectar via Painel de Controle
Se o banco está hospedado em **EasyPanel, Render, Railway**, etc:
1. Acessar o painel
2. Abrir o terminal do container PostgreSQL
3. Executar o SQL diretamente:

```bash
psql -d agente-db-pgvectorstore
```

Depois copiar e colar o conteúdo de `scripts/fix_hybrid_search_duplicate.sql`

### Opção 4: Usar Banco Local Temporário
Enquanto o banco remoto está inacessível, você pode configurar um banco local:

```bash
# Instalar PostgreSQL com pgvector
brew install postgresql@16 pgvector  # Mac
# ou
sudo apt install postgresql-16-pgvector  # Linux

# Iniciar PostgreSQL
brew services start postgresql@16  # Mac
# ou
sudo systemctl start postgresql  # Linux

# Criar banco local
createdb agente-db-local
psql agente-db-local -c "CREATE EXTENSION vector;"

# Atualizar settings.py temporariamente
# vector_db_connection_string = "postgresql://localhost/agente-db-local"
```

## 🔍 Como Saber Qual Opção Usar?

Execute na sua máquina:
```bash
# Teste de conectividade
ping 31.97.252.6

# Se ping funcionar, teste as portas
telnet 31.97.252.6 8877
# ou
nc -zv 31.97.252.6 8877
```

Se o ping **NÃO funcionar**: Servidor offline → Contatar administrador  
Se o ping **funcionar** mas portas recusarem: Firewall/VPN → Usar SSH tunnel ou painel

## ⚠️ Impacto no Agente

**Enquanto o banco estiver inacessível:**
- ❌ Agente NÃO consegue buscar produtos
- ❌ Cliente recebe respostas vazias/genéricas
- ❌ Sistema está INOPERANTE

**Prioridade:** 🔴 **CRÍTICA** - Sistema completamente offline

## 📞 Próximos Passos Recomendados

1. **Verificar se você tem acesso SSH ao servidor 31.97.252.6**
2. **OU verificar se o banco está em algum painel (EasyPanel, etc)**
3. **OU contatar o administrador do servidor**

Após conseguir acessar o banco, execute:
```sql
-- No psql ou terminal do banco
\i scripts/fix_hybrid_search_duplicate.sql
```

---

**Gerado em:** 19/01/2026 01:49  
**Servidor:** 31.97.252.6  
**Portas testadas:** 8877 (vetorial), 3043 (produtos)  
**Status:** ❌ AMBAS INACESSÍVEIS
