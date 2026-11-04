"""OAuth2 Gmail — gerencia credenciais e refresh token."""
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build, Resource

from src.utils.config import get_settings
from src.utils.logger import get_logger

logger = get_logger(__name__)


def get_credentials(
    credentials_path: Optional[Path] = None,
    token_path: Optional[Path] = None,
    scopes: Optional[list[str]] = None,
) -> Credentials:
    """
    Obtém credenciais OAuth2 válidas.

    Tenta carregar token existente; se expirado, faz refresh automático.
    Se não existir token, abre browser para autenticação interativa.

    Args:
        credentials_path: Caminho para credentials.json do Google Cloud.
        token_path: Caminho para salvar/carregar token.json.
        scopes: Lista de escopos OAuth2.

    Returns:
        Credenciais Google válidas.

    Raises:
        FileNotFoundError: Se credentials.json não existir.
        RuntimeError: Se autenticação falhar.
    """
    settings = get_settings()
    credentials_path = credentials_path or settings.gmail_credentials_path
    token_path = token_path or settings.gmail_token_path
    scopes = scopes or settings.gmail_scopes

    credentials_path = Path(credentials_path)
    token_path = Path(token_path)

    if not credentials_path.exists():
        raise FileNotFoundError(
            f"credentials.json não encontrado em: {credentials_path}\n"
            "Siga o README.md para criar credenciais no Google Cloud Console."
        )

    creds: Optional[Credentials] = None

    # Carrega token existente
    if token_path.exists():
        logger.debug(f"Carregando token de: {token_path}")
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    # Refresh ou novo login
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Token expirado — fazendo refresh automático...")
            creds.refresh(Request())
        else:
            logger.info("Iniciando autenticação OAuth2 no browser...")
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), scopes
            )
            creds = flow.run_local_server(port=0)

        # Salva token para próximo uso
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())
        logger.info(f"Token salvo em: {token_path}")

    return creds


def get_gmail_service(
    credentials_path: Optional[Path] = None,
    token_path: Optional[Path] = None,
) -> Resource:
    """
    Constrói e retorna o serviço Gmail API autenticado.

    Args:
        credentials_path: Caminho para credentials.json.
        token_path: Caminho para token.json.

    Returns:
        Resource do Gmail API pronto para uso.
    """
    creds = get_credentials(credentials_path, token_path)
    service = build("gmail", "v1", credentials=creds)
    logger.info("Gmail API autenticada com sucesso.")
    return service
