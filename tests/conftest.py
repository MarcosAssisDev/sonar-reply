import os
import shutil
import sys
from pathlib import Path

import pytest

os.environ["SONAR_SKIP_DOTENV"] = "1"  # os testes não podem ler o .env real

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class FakeMeta:
    """Substitui a API da Meta: registra as chamadas em vez de enviar."""

    def __init__(self):
        self.replies, self.private, self.dms = [], [], []
        self.fail_private = False
        self.media_calls = []
        self.media_data = {"caption": "Meu cachorro aprendeu a sentar!", "media_type": "VIDEO",
                           "media_product_type": "REELS", "permalink": "https://instagram.com/p/x"}
        self.fail_media = False

    def get_media(self, media_id):
        self.media_calls.append(media_id)
        if self.fail_media:
            raise RuntimeError("erro simulado ao ler o post")
        return dict(self.media_data)

    def reply_to_comment(self, comment_id, message):
        self.replies.append((comment_id, message))
        return {"id": "r1"}

    def send_private_reply(self, comment_id, text):
        if self.fail_private:
            raise RuntimeError("erro simulado da Meta")
        self.private.append((comment_id, text))
        return {"message_id": "m1"}

    def send_dm(self, user_id, text):
        self.dms.append((user_id, text))
        return {"message_id": "m2"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    shutil.copy(ROOT / "rules.yaml", tmp_path / "rules.yaml")
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("RULES_FILE", str(tmp_path / "rules.yaml"))
    monkeypatch.setenv("ADMIN_PASSWORD", "segredo")
    monkeypatch.setenv("DRY_RUN", "false")
    monkeypatch.setenv("APPROVAL_MODE", "true")
    monkeypatch.setenv("META_VERIFY_TOKEN", "verifica")
    monkeypatch.setenv("META_APP_SECRET", "appsecret")
    monkeypatch.setenv("IG_ACCOUNT_ID", "999")
    monkeypatch.setenv("GROUP_LINK", "https://grupo.exemplo/abc")
    monkeypatch.setenv("AI_API_KEY", "")
    monkeypatch.setenv("MAX_REPLIES_PER_HOUR", "30")

    fake = FakeMeta()
    from app import meta

    for name in ("reply_to_comment", "send_private_reply", "send_dm", "get_media"):
        monkeypatch.setattr(meta, name, getattr(fake, name))
    return fake


@pytest.fixture
def client(env):
    from fastapi.testclient import TestClient

    from app.main import app

    c = TestClient(app)
    c.auth = ("admin", "segredo")
    return c
