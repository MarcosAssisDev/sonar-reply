"""Cliente da API do Instagram (Meta). Todas as chamadas passam por aqui.

ATENÇÃO: confirme endpoints e versão na documentação oficial da Meta antes de
ir para produção (a Meta muda isso com frequência):
https://developers.facebook.com/docs/instagram-platform/private-replies/
https://developers.facebook.com/documentation/instagram-platform/comment-moderation
"""
import httpx

from .config import get_settings


class MetaError(Exception):
    pass


def _base() -> str:
    s = get_settings()
    return f"{s.graph_base}/{s.graph_version}"


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_settings().ig_access_token}"}


def _check(resp: httpx.Response) -> dict:
    if resp.status_code >= 300:
        raise MetaError(f"HTTP {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def reply_to_comment(comment_id: str, message: str) -> dict:
    """Resposta pública ao comentário: POST /<comment_id>/replies"""
    resp = httpx.post(
        f"{_base()}/{comment_id}/replies",
        headers=_headers(),
        data={"message": message},
        timeout=20,
    )
    return _check(resp)


def send_private_reply(comment_id: str, text: str) -> dict:
    """Primeira DM para quem comentou (só 1 por comentário, até 7 dias depois)."""
    s = get_settings()
    resp = httpx.post(
        f"{_base()}/{s.ig_account_id}/messages",
        headers=_headers(),
        json={"recipient": {"comment_id": comment_id}, "message": {"text": text}},
        timeout=20,
    )
    return _check(resp)


def send_dm(user_id: str, text: str) -> dict:
    """DM de acompanhamento (só depois que a pessoa respondeu, em até 24 h)."""
    s = get_settings()
    resp = httpx.post(
        f"{_base()}/{s.ig_account_id}/messages",
        headers=_headers(),
        json={"recipient": {"id": user_id}, "message": {"text": text}},
        timeout=20,
    )
    return _check(resp)


def get_media(media_id: str) -> dict:
    """Dados do post comentado (legenda, tipo, link): GET /<media_id>?fields=..."""
    resp = httpx.get(
        f"{_base()}/{media_id}",
        headers=_headers(),
        params={"fields": "caption,media_type,media_product_type,permalink"},
        timeout=15,
    )
    return _check(resp)
