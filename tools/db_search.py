
import psycopg2
from psycopg2.extras import RealDictCursor
from config.settings import settings
from config.logger import setup_logger

logger = setup_logger(__name__)

def search_products_db(query: str, limit: int = 5) -> str:
    """
    Busca produtos no banco de dados Postgres usando ILIKE.
    Retorna uma string formatada com os resultados.
    """
    if not query or len(query.strip()) < 2:
        return "⚠️ Digite pelo menos 2 letras para buscar."

    try:
        conn = psycopg2.connect(settings.postgres_connection_string)
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        
        table_name = settings.postgres_products_table_name or "produtos-sp-queiroz"
        
        # Busca por nome ou descrição (ILIKE para case-insensitive)
        sql = f"""
            SELECT id, nome, preco, estoque, unidade, categoria 
            FROM "{table_name}"
            WHERE unaccent(nome) ILIKE unaccent(%s) 
            OR unaccent(descricao) ILIKE unaccent(%s)
            LIMIT %s
        """
        
        search_term = f"%{query}%"
        cursor.execute(sql, (search_term, search_term, limit))
        results = cursor.fetchall()
        
        conn.close()
        
        if not results:
            return "[]"
        
        # Converte para lista de dicts
        output = []
        for row in results:
            item = {
                "id": row.get('id'),
                "nome": row.get('nome', 'Produto sem nome'),
                "preco": float(row.get('preco', 0.0)),
                "estoque": float(row.get('estoque', 0.0)),
                "unidade": row.get('unidade', 'UN'),
                "categoria": row.get('categoria', '')
            }
            output.append(item)
            
        import json
        return json.dumps(output, ensure_ascii=False)

    except Exception as e:
        logger.error(f"Erro na busca DB: {e}")
        return f"❌ Erro técnico ao buscar produto: {str(e)}"
