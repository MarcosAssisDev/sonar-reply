"""Motor de regras: decide se um comentário dispara a automação e com qual texto."""
import random
import re
import unicodedata

import yaml

from .config import get_settings

URL_RE = re.compile(r"(https?://\S+|www\.\S+)", re.IGNORECASE)
MAX_PUBLIC_LEN = 150


def normalize(text: str) -> str:
    """Minúsculas e sem acentos, para comparar palavras-chave."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().strip()


def load_rules() -> dict:
    with open(get_settings().rules_file, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    data.setdefault("rules", [])
    data.setdefault("optout_words", [])
    data.setdefault("follow_up", "Aqui está o link: {link}")
    return data


def match_rule(text: str, media_id: str | None, config: dict | None = None) -> dict | None:
    """Devolve a primeira regra que combina com o comentário, ou None."""
    config = config or load_rules()
    norm = normalize(text)
    if not norm:
        return None
    for rule in config["rules"]:
        media_ids = [str(m) for m in rule.get("media_ids") or []]
        if media_ids and str(media_id) not in media_ids:
            continue
        match = rule.get("match") or {}
        if match.get("any"):
            return rule
        keywords = [normalize(k) for k in match.get("keywords") or []]
        if any(k and k in norm for k in keywords):
            return rule
    return None


def pick_template(rule: dict) -> str:
    options = rule.get("public_replies") or ["Valeu pelo comentário! Te mandei uma mensagem no direct 🐾"]
    return random.choice(options)


DEFAULT_REPEAT_PROMPT = (
    "Você responde comentários no Instagram do perfil @sonar_pet, que posta humor e dicas "
    "sobre cachorros e gatos. Escreva UMA resposta curta em português do Brasil (até 120 "
    "caracteres), simpática e natural, reagindo ao que a pessoa escreveu. NÃO mencione "
    "mensagem, direct, DM, grupo nem links. Sem preços, promessas ou hashtags. Se o "
    "comentário for ofensivo, responda apenas com um agradecimento neutro."
)
DEFAULT_REPEAT_REPLIES = [
    "Que bom ter você por aqui de novo! 🐾",
    "Valeu pelo carinho! 🧡",
    "Obrigado por comentar! 🐶",
]


DEFAULT_OPTOUT_REPLY = (
    "Tudo bem, sem problemas! 😊 Obrigado pela atenção e por acompanhar o @sonar_pet. "
    "Um abraço e um carinho nos pets! 🐾"
)


def optout_reply(config: dict | None = None) -> str:
    """Mensagem de agradecimento para quem não quis o link (sem pergunta e sem link)."""
    config = config or load_rules()
    return " ".join(str(config.get("optout_reply") or DEFAULT_OPTOUT_REPLY).split())


def repeat_prompt(rule: dict) -> str:
    """Prompt da resposta para quem já recebeu a DM (sem citar o direct)."""
    return rule.get("repeat_ai_prompt") or DEFAULT_REPEAT_PROMPT


def pick_repeat_template(rule: dict) -> str:
    options = rule.get("repeat_replies") or DEFAULT_REPEAT_REPLIES
    return random.choice(options)


def sanitize_public_reply(text: str) -> str:
    """Garante que a resposta pública não tem links e cabe no limite."""
    text = URL_RE.sub("", text or "")
    text = " ".join(text.split()).strip(' "')
    if len(text) > MAX_PUBLIC_LEN:
        text = text[: MAX_PUBLIC_LEN - 1].rstrip() + "…"
    return text


def is_optout(text: str, config: dict | None = None) -> bool:
    config = config or load_rules()
    words = set(re.findall(r"\w+", normalize(text)))
    return any(normalize(w) in words for w in config.get("optout_words", []))
