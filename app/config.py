"""Configuração lida das variáveis de ambiente (arquivo .env)."""
import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Carrega o arquivo .env sem sobrescrever variáveis já definidas no ambiente."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


if not os.environ.get("SONAR_SKIP_DOTENV"):
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    load_dotenv(Path.cwd() / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "sim", "on")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    admin_password: str
    dry_run: bool
    approval_mode: bool
    max_replies_per_hour: int
    dm_cooldown_days: int
    post_context: bool
    verify_token: str
    app_secret: str
    ig_account_id: str
    ig_access_token: str
    graph_base: str
    graph_version: str
    group_link: str
    ai_base_url: str
    ai_api_key: str
    ai_model: str
    db_path: str
    rules_file: str


def get_settings() -> Settings:
    """Lê o ambiente a cada chamada (facilita testes e troca de valores)."""
    return Settings(
        admin_password=os.environ.get("ADMIN_PASSWORD", ""),
        dry_run=_bool("DRY_RUN", True),
        approval_mode=_bool("APPROVAL_MODE", True),
        max_replies_per_hour=_int("MAX_REPLIES_PER_HOUR", 30),
        dm_cooldown_days=_int("DM_COOLDOWN_DAYS", 30),
        post_context=_bool("POST_CONTEXT", True),
        verify_token=os.environ.get("META_VERIFY_TOKEN", ""),
        app_secret=os.environ.get("META_APP_SECRET", ""),
        ig_account_id=os.environ.get("IG_ACCOUNT_ID", ""),
        ig_access_token=os.environ.get("IG_ACCESS_TOKEN", ""),
        graph_base=os.environ.get("GRAPH_BASE", "https://graph.instagram.com").rstrip("/"),
        graph_version=os.environ.get("GRAPH_VERSION", "v23.0"),
        group_link=os.environ.get("GROUP_LINK", ""),
        ai_base_url=os.environ.get("AI_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/"),
        ai_api_key=os.environ.get("AI_API_KEY", ""),
        ai_model=os.environ.get("AI_MODEL", "openai/gpt-oss-120b"),
        db_path=os.environ.get("DB_PATH", "data/sonar.db"),
        rules_file=os.environ.get("RULES_FILE", "rules.yaml"),
    )
