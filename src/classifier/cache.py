"""Cache SQLite para classificações de emails e remetentes."""
import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Generator, Optional

from src.utils.config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__)

# Número de classificações do mesmo remetente/categoria para considerar cache confiável
SENDER_CACHE_MIN_COUNT = 3
SENDER_CACHE_MIN_CONFIDENCE = 0.85

DDL = """
CREATE TABLE IF NOT EXISTS sender_cache (
    sender_email    TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    confidence      REAL NOT NULL,
    last_seen       TEXT NOT NULL,
    count           INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS email_cache (
    email_id        TEXT PRIMARY KEY,
    category        TEXT NOT NULL,
    action          TEXT NOT NULL,
    confidence      REAL NOT NULL,
    safe_to_automate INTEGER NOT NULL DEFAULT 0,
    reason          TEXT,
    has_unsubscribe INTEGER NOT NULL DEFAULT 0,
    classified_at   TEXT NOT NULL,
    source          TEXT NOT NULL DEFAULT 'claude'
);

CREATE INDEX IF NOT EXISTS idx_email_category ON email_cache(category);
CREATE INDEX IF NOT EXISTS idx_email_action ON email_cache(action);
CREATE INDEX IF NOT EXISTS idx_sender_category ON sender_cache(category);
"""


def _extract_email_address(from_field: str) -> str:
    """Extrai o endereço de email de 'Nome <email@domain.com>'."""
    match = re.search(r"<([^>]+)>", from_field)
    return match.group(1).lower().strip() if match else from_field.lower().strip()


class EmailCache:
    """
    Cache SQLite para evitar reclassificações desnecessárias.

    Mantém dois níveis de cache:
    - email_cache: por email_id (classificação exata)
    - sender_cache: por remetente (reutiliza se >= 3 classificações com mesma categoria)
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        settings = get_settings()
        self.db_path = Path(db_path or settings.db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Context manager para conexão SQLite com WAL mode."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Cria tabelas se não existirem."""
        with self._connect() as conn:
            conn.executescript(DDL)
        logger.debug(f"Cache SQLite inicializado em: {self.db_path}")

    # ── email_cache ───────────────────────────────────────────────────────────

    def get_email(self, email_id: str) -> Optional[dict]:
        """
        Busca classificação de um email pelo ID.

        Args:
            email_id: ID da mensagem Gmail.

        Returns:
            Dict com a classificação ou None se não encontrado.
        """
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM email_cache WHERE email_id = ?", (email_id,)
            ).fetchone()
        if row:
            return dict(row)
        return None

    def save_email(
        self,
        email_id: str,
        category: str,
        action: str,
        confidence: float,
        safe_to_automate: bool,
        reason: str,
        has_unsubscribe: bool,
        source: str = "claude",
    ) -> None:
        """
        Salva ou atualiza classificação de um email.

        Args:
            email_id: ID Gmail.
            category: Categoria classificada.
            action: Ação recomendada.
            confidence: Confiança de 0.0 a 1.0.
            safe_to_automate: Se pode ser executado automaticamente.
            reason: Motivo da classificação.
            has_unsubscribe: Se tem link de descadastro.
            source: 'heuristic', 'claude', ou 'manual'.
        """
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO email_cache
                    (email_id, category, action, confidence, safe_to_automate,
                     reason, has_unsubscribe, classified_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email_id) DO UPDATE SET
                    category=excluded.category,
                    action=excluded.action,
                    confidence=excluded.confidence,
                    safe_to_automate=excluded.safe_to_automate,
                    reason=excluded.reason,
                    has_unsubscribe=excluded.has_unsubscribe,
                    classified_at=excluded.classified_at,
                    source=excluded.source
                """,
                (
                    email_id, category, action, confidence,
                    int(safe_to_automate), reason, int(has_unsubscribe), now, source,
                ),
            )

    def save_emails_batch(self, classifications: list[dict]) -> int:
        """
        Salva múltiplas classificações em uma transação.

        Args:
            classifications: Lista de dicts com campos do save_email.

        Returns:
            Número de registros salvos.
        """
        now = datetime.now(timezone.utc).isoformat()
        rows = [
            (
                c["email_id"],
                c["category"],
                c["recommended_action"],
                c["confidence"],
                int(c.get("safe_to_automate", False)),
                c.get("reason", ""),
                int(c.get("has_unsubscribe", False)),
                now,
                c.get("source", "claude"),
            )
            for c in classifications
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO email_cache
                    (email_id, category, action, confidence, safe_to_automate,
                     reason, has_unsubscribe, classified_at, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email_id) DO UPDATE SET
                    category=excluded.category,
                    action=excluded.action,
                    confidence=excluded.confidence,
                    safe_to_automate=excluded.safe_to_automate,
                    reason=excluded.reason,
                    has_unsubscribe=excluded.has_unsubscribe,
                    classified_at=excluded.classified_at,
                    source=excluded.source
                """,
                rows,
            )
        return len(rows)

    def get_unclassified_ids(self, all_ids: list[str]) -> list[str]:
        """Retorna IDs que ainda não estão no cache."""
        if not all_ids:
            return []
        with self._connect() as conn:
            placeholders = ",".join("?" * len(all_ids))
            cached = conn.execute(
                f"SELECT email_id FROM email_cache WHERE email_id IN ({placeholders})",
                all_ids,
            ).fetchall()
        cached_set = {row["email_id"] for row in cached}
        return [eid for eid in all_ids if eid not in cached_set]

    def get_all_classifications(self, limit: int = 10000) -> list[dict]:
        """Retorna todas as classificações do cache."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM email_cache ORDER BY classified_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_ids_by_category(self, category: str) -> list[str]:
        """Retorna IDs de emails de uma categoria específica."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT email_id FROM email_cache WHERE category = ?",
                (category,),
            ).fetchall()
        return [row["email_id"] for row in rows]

    def delete_by_ids(self, email_ids: list[str]) -> None:
        """Remove emails do cache por ID (para forçar re-classificação)."""
        if not email_ids:
            return
        with self._connect() as conn:
            placeholders = ",".join("?" * len(email_ids))
            conn.execute(
                f"DELETE FROM email_cache WHERE email_id IN ({placeholders})",
                email_ids,
            )

    def get_by_category(self, category: str) -> list[dict]:
        """Retorna todas as classificações de uma categoria."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM email_cache WHERE category = ? ORDER BY classified_at DESC",
                (category,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_by_action(self, action: str, safe_only: bool = True) -> list[dict]:
        """Retorna emails com uma ação recomendada específica."""
        query = "SELECT * FROM email_cache WHERE action = ?"
        params: list = [action]
        if safe_only:
            query += " AND safe_to_automate = 1"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Retorna estatísticas do cache."""
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM email_cache").fetchone()[0]
            by_category = conn.execute(
                "SELECT category, COUNT(*) as cnt FROM email_cache GROUP BY category ORDER BY cnt DESC"
            ).fetchall()
            by_action = conn.execute(
                "SELECT action, COUNT(*) as cnt FROM email_cache GROUP BY action ORDER BY cnt DESC"
            ).fetchall()
            sender_count = conn.execute("SELECT COUNT(*) FROM sender_cache").fetchone()[0]
        return {
            "total_emails": total,
            "total_senders": sender_count,
            "by_category": [dict(r) for r in by_category],
            "by_action": [dict(r) for r in by_action],
        }

    # ── sender_cache ──────────────────────────────────────────────────────────

    def update_sender(self, from_field: str, category: str, confidence: float) -> None:
        """
        Atualiza o cache de remetente após uma classificação.

        Se o remetente já existir com a mesma categoria, incrementa o contador.
        Se for uma categoria diferente, reseta (remetente mudou de comportamento).

        Args:
            from_field: Campo "From" do email.
            category: Categoria classificada.
            confidence: Confiança da classificação.
        """
        sender = _extract_email_address(from_field)
        now = datetime.now(timezone.utc).isoformat()

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT * FROM sender_cache WHERE sender_email = ?", (sender,)
            ).fetchone()

            if existing and existing["category"] == category:
                new_confidence = (existing["confidence"] + confidence) / 2
                new_count = existing["count"] + 1
                conn.execute(
                    """UPDATE sender_cache
                       SET confidence=?, last_seen=?, count=?
                       WHERE sender_email=?""",
                    (new_confidence, now, new_count, sender),
                )
            else:
                conn.execute(
                    """INSERT INTO sender_cache
                           (sender_email, category, confidence, last_seen, count)
                       VALUES (?, ?, ?, ?, 1)
                       ON CONFLICT(sender_email) DO UPDATE SET
                           category=excluded.category,
                           confidence=excluded.confidence,
                           last_seen=excluded.last_seen,
                           count=1""",
                    (sender, category, confidence, now),
                )

    def get_sender_classification(self, from_field: str) -> Optional[dict]:
        """
        Retorna classificação do remetente se confiável (>= 3 vezes, >= 0.85).

        Args:
            from_field: Campo "From" do email.

        Returns:
            Dict com category/confidence/action ou None se não confiável.
        """
        sender = _extract_email_address(from_field)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sender_cache WHERE sender_email = ?", (sender,)
            ).fetchone()

        if not row:
            return None

        if (
            row["count"] >= SENDER_CACHE_MIN_COUNT
            and row["confidence"] >= SENDER_CACHE_MIN_CONFIDENCE
        ):
            return {
                "category": row["category"],
                "confidence": row["confidence"],
                "count": row["count"],
            }
        return None
