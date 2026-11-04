"""Wrapper da Gmail API com retry, rate limiting e tratamento de erros."""
import time
from typing import Any, Callable, TypeVar

from googleapiclient.discovery import Resource
from googleapiclient.errors import HttpError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    before_sleep_log,
)

from src.utils.config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__)
T = TypeVar("T")


def _is_retryable(exc: Exception) -> bool:
    """Determina se o erro HTTP é recuperável (rate limit ou erro de servidor)."""
    if isinstance(exc, HttpError):
        return exc.resp.status in (429, 500, 502, 503, 504)
    return False


class GmailClient:
    """
    Wrapper da Gmail API com retry automático e rate limiting.

    Encapsula todas as chamadas à API, respeitando quota de 250 units/segundo
    e implementando exponential backoff em caso de erros transitórios.
    """

    def __init__(self, service: Resource) -> None:
        self.service = service
        self._settings = get_settings()
        self._last_call_time: float = 0.0

    def _throttle(self) -> None:
        """Garante intervalo mínimo entre chamadas para respeitar rate limit."""
        now = time.monotonic()
        elapsed = now - self._last_call_time
        delay = self._settings.rate_limit_delay
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_call_time = time.monotonic()

    def _execute_with_retry(self, request: Any) -> Any:
        """
        Executa uma request da API com retry automático.

        Args:
            request: Objeto request da googleapiclient.

        Returns:
            Resposta da API.

        Raises:
            HttpError: Se erro não recuperável ou esgotadas as tentativas.
        """
        max_retries = self._settings.max_retries

        for attempt in range(1, max_retries + 1):
            try:
                self._throttle()
                return request.execute()
            except HttpError as exc:
                if not _is_retryable(exc):
                    logger.error(f"Erro não recuperável da Gmail API: {exc}")
                    raise
                wait = 2 ** attempt
                logger.warning(
                    f"Rate limit / erro de servidor (tentativa {attempt}/{max_retries}). "
                    f"Aguardando {wait}s..."
                )
                time.sleep(wait)

        raise RuntimeError(f"Gmail API falhou após {max_retries} tentativas.")

    # ── Messages ──────────────────────────────────────────────────────────────

    def list_messages(
        self,
        query: str = "",
        label_ids: list[str] | None = None,
        page_token: str | None = None,
        max_results: int = 100,
    ) -> dict:
        """Lista mensagens com suporte a paginação."""
        kwargs: dict[str, Any] = {
            "userId": "me",
            "maxResults": min(max_results, 500),
        }
        if query:
            kwargs["q"] = query
        if label_ids:
            kwargs["labelIds"] = label_ids
        if page_token:
            kwargs["pageToken"] = page_token

        req = self.service.users().messages().list(**kwargs)
        return self._execute_with_retry(req)

    def get_message(self, message_id: str, format: str = "metadata") -> dict:
        """
        Obtém detalhes de uma mensagem.

        Args:
            message_id: ID da mensagem Gmail.
            format: 'minimal', 'metadata', 'full', 'raw'.
        """
        req = self.service.users().messages().get(
            userId="me",
            id=message_id,
            format=format,
            metadataHeaders=["From", "Subject", "Date", "List-Unsubscribe"],
        )
        return self._execute_with_retry(req)

    def batch_get_messages(
        self, message_ids: list[str], format: str = "metadata"
    ) -> list[dict]:
        """
        Obtém múltiplas mensagens em sequência com throttling.

        Args:
            message_ids: Lista de IDs.
            format: Formato da resposta.

        Returns:
            Lista de dicts com dados das mensagens.
        """
        results = []
        for msg_id in message_ids:
            try:
                msg = self.get_message(msg_id, format=format)
                results.append(msg)
            except HttpError as exc:
                logger.warning(f"Erro ao buscar mensagem {msg_id}: {exc}")
        return results

    def modify_message(
        self,
        message_id: str,
        add_label_ids: list[str] | None = None,
        remove_label_ids: list[str] | None = None,
    ) -> dict:
        """Modifica labels de uma mensagem."""
        body: dict[str, list[str]] = {}
        if add_label_ids:
            body["addLabelIds"] = add_label_ids
        if remove_label_ids:
            body["removeLabelIds"] = remove_label_ids

        req = self.service.users().messages().modify(
            userId="me", id=message_id, body=body
        )
        return self._execute_with_retry(req)

    def trash_message(self, message_id: str) -> dict:
        """Move mensagem para lixeira (reversível)."""
        req = self.service.users().messages().trash(
            userId="me", id=message_id
        )
        return self._execute_with_retry(req)

    def delete_message(self, message_id: str) -> None:
        """
        Deleta mensagem permanentemente (IRREVERSÍVEL).

        Só deve ser chamado após confirmação explícita do usuário.
        """
        req = self.service.users().messages().delete(
            userId="me", id=message_id
        )
        self._execute_with_retry(req)

    # ── Labels ────────────────────────────────────────────────────────────────

    def list_labels(self) -> list[dict]:
        """Lista todas as labels da conta."""
        req = self.service.users().labels().list(userId="me")
        result = self._execute_with_retry(req)
        return result.get("labels", [])

    def create_label(self, name: str) -> dict:
        """Cria uma nova label."""
        body = {
            "name": name,
            "labelListVisibility": "labelShow",
            "messageListVisibility": "show",
        }
        req = self.service.users().labels().create(userId="me", body=body)
        return self._execute_with_retry(req)

    def get_or_create_label(self, name: str) -> str:
        """Retorna ID de label existente ou cria nova. Retorna o label_id."""
        labels = self.list_labels()
        for label in labels:
            if label["name"].lower() == name.lower():
                return label["id"]
        new_label = self.create_label(name)
        logger.info(f"Label criada: '{name}' (id: {new_label['id']})")
        return new_label["id"]

    # ── Profile ───────────────────────────────────────────────────────────────

    def get_profile(self) -> dict:
        """Retorna informações do perfil Gmail."""
        req = self.service.users().getProfile(userId="me")
        return self._execute_with_retry(req)
