"""Módulo de IA. Funciona com qualquer API no formato OpenAI /chat/completions
(Groq, OpenRouter, Gemini no modo compatível etc.). Trocar de provedor = mudar o .env."""
import httpx

from .config import get_settings
from .db import log


def generate_reply(comment: str, system_prompt: str, post: dict | None = None) -> str | None:
    """Gera a resposta pública. Devolve None se não houver chave ou se algo falhar
    (o chamador usa então um texto de reserva)."""
    s = get_settings()
    if not s.ai_api_key:
        return None
    user_msg = f"Comentário: {comment[:500]}"
    system = system_prompt
    caption = " ".join(((post or {}).get("caption") or "").split())[:600]
    if caption:
        kind = (post or {}).get("product_type") or (post or {}).get("media_type") or "post"
        system += (
            " Você também recebe a legenda do post comentado, só como contexto: use-a para entender"
            " o assunto e reagir de forma relevante, mas não invente detalhes que não estejam nela"
            " e não repita a legenda."
        )
        user_msg = f"Post ({kind}), legenda: {caption}\n{user_msg}"
    body = {
        "model": s.ai_model,
        "max_tokens": 400,  # folga: modelos de raciocínio gastam tokens "pensando"
        "temperature": 0.8,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_msg},
        ],
    }
    if s.ai_model.startswith("openai/gpt-oss"):
        body["reasoning_effort"] = "low"  # resposta curta e rápida
    try:
        resp = httpx.post(
            f"{s.ai_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {s.ai_api_key}"},
            json=body,
            timeout=20,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
        text = resp.json()["choices"][0]["message"]["content"].strip()
        return text or None
    except Exception as exc:  # noqa: BLE001 - qualquer falha cai no texto de reserva
        log("warn", f"IA indisponível, usando texto de reserva: {exc}")
        return None
