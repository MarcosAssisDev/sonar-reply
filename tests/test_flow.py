import hashlib
import hmac
import json

from app import ai, db, rules, service


def sign(body: bytes, secret="appsecret") -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def comment_payload(cid="c1", text="Que fofo!", uid="111", username="ana", media="m1"):
    return {
        "entry": [{
            "id": "999",
            "changes": [{
                "field": "comments",
                "value": {"id": cid, "text": text, "from": {"id": uid, "username": username},
                          "media": {"id": media}},
            }],
        }]
    }


def post_webhook(client, payload):
    body = json.dumps(payload).encode()
    return client.post("/webhook", content=body,
                       headers={"X-Hub-Signature-256": sign(body), "Content-Type": "application/json"})


def message_payload(sender="111", text="quero", echo=False):
    return {"entry": [{"id": "999", "messaging": [
        {"sender": {"id": sender}, "message": {"text": text, "is_echo": echo}}]}]}


# ------------------------------------------------------------------ regras
def test_normalize_and_keyword_match(env):
    cfg = {"rules": [{"name": "k", "match": {"keywords": ["Preço", "link"]}}], "optout_words": []}
    assert rules.match_rule("qual o PRECO?", None, cfg)["name"] == "k"
    assert rules.match_rule("lindo demais", None, cfg) is None
    assert rules.match_rule("   ", None, cfg) is None


def test_media_filter(env):
    cfg = {"rules": [{"name": "k", "match": {"any": True}, "media_ids": ["abc"]}], "optout_words": []}
    assert rules.match_rule("oi", "abc", cfg) is not None
    assert rules.match_rule("oi", "outro", cfg) is None


def test_sanitize_removes_links_and_limits_size(env):
    out = rules.sanitize_public_reply("Veja https://site.com/x agora " + "a" * 300)
    assert "http" not in out and len(out) <= 150


# ------------------------------------------------------------------ webhook
def test_webhook_verification(client):
    ok = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "verifica",
                                        "hub.challenge": "123"})
    assert ok.status_code == 200 and ok.text == "123"
    bad = client.get("/webhook", params={"hub.mode": "subscribe", "hub.verify_token": "x",
                                         "hub.challenge": "123"})
    assert bad.status_code == 403


def test_webhook_rejects_bad_signature(client):
    body = json.dumps(comment_payload()).encode()
    r = client.post("/webhook", content=body, headers={"X-Hub-Signature-256": "sha256=errada"})
    assert r.status_code == 403
    assert db.list_items(["pending"]) == []


# ------------------------------------------------------------------ fluxo principal
def test_comment_goes_to_queue_then_approval_sends_everything_once(client, env):
    assert post_webhook(client, comment_payload()).status_code == 200
    items = db.list_items(["pending"])
    assert len(items) == 1 and env.replies == [] and env.private == []

    item = items[0]
    r = client.post(f"/queue/{item['id']}/approve", follow_redirects=False,
                    data={"public_reply": "Valeu! Olha o direct 🐾", "dm_text": item["dm_text"]})
    assert r.status_code == 303
    assert env.replies == [("c1", "Valeu! Olha o direct 🐾")]
    assert len(env.private) == 1 and "ana" in env.private[0][1]
    assert db.get_item(item["id"])["status"] == "sent"
    assert db.get_contact("111")["state"] == "dm_sent"

    # aprovar de novo não duplica nada
    client.post(f"/queue/{item['id']}/approve", data={"public_reply": "x", "dm_text": "y"})
    assert len(env.replies) == 1 and len(env.private) == 1


def test_duplicate_event_creates_one_item(client):
    post_webhook(client, comment_payload())
    post_webhook(client, comment_payload())
    assert len(db.list_items(["pending"])) == 1


def test_own_comment_is_ignored(client):
    post_webhook(client, comment_payload(uid="999", username="sonar_pet"))
    assert db.list_items(["pending"]) == []


def test_auto_mode_sends_immediately(client, env, monkeypatch):
    monkeypatch.setenv("APPROVAL_MODE", "false")
    post_webhook(client, comment_payload())
    assert len(env.replies) == 1 and len(env.private) == 1


def test_partial_failure_retry_does_not_repeat_public_reply(client, env):
    post_webhook(client, comment_payload())
    item = db.list_items(["pending"])[0]
    env.fail_private = True
    client.post(f"/queue/{item['id']}/approve", data={"public_reply": "Oi", "dm_text": "dm?"})
    assert db.get_item(item["id"])["status"] == "failed"
    assert len(env.replies) == 1

    env.fail_private = False
    client.post(f"/queue/{item['id']}/retry")
    assert db.get_item(item["id"])["status"] == "sent"
    assert len(env.replies) == 1 and len(env.private) == 1


def test_dry_run_never_calls_meta(client, env, monkeypatch):
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("APPROVAL_MODE", "false")
    post_webhook(client, comment_payload())
    assert env.replies == [] and env.private == []
    assert db.list_items(["dry_run"])[0]["username"] == "ana"


def test_rate_limit_keeps_item_pending(client, env, monkeypatch):
    monkeypatch.setenv("APPROVAL_MODE", "false")
    monkeypatch.setenv("MAX_REPLIES_PER_HOUR", "1")
    post_webhook(client, comment_payload(cid="c1"))
    post_webhook(client, comment_payload(cid="c2", uid="222", username="bia"))
    assert len(env.replies) == 1
    assert len(db.list_items(["pending"])) == 1


# ------------------------------------------------------------------ follow-up
def test_follow_up_sends_link_once(client, env):
    post_webhook(client, comment_payload())
    item = db.list_items(["pending"])[0]
    client.post(f"/queue/{item['id']}/approve", data={"public_reply": "Oi", "dm_text": "dm?"})

    post_webhook(client, message_payload(text="quero sim"))
    assert len(env.dms) == 1 and "https://grupo.exemplo/abc" in env.dms[0][1]
    post_webhook(client, message_payload(text="obrigado"))
    assert len(env.dms) == 1


def test_optout_does_not_send_link(client, env):
    post_webhook(client, comment_payload())
    item = db.list_items(["pending"])[0]
    client.post(f"/queue/{item['id']}/approve", data={"public_reply": "Oi", "dm_text": "dm?"})
    post_webhook(client, message_payload(text="não, obrigado"))
    assert len(env.dms) == 1  # só o agradecimento
    assert "grupo.exemplo" not in env.dms[0][1] and "?" not in env.dms[0][1]
    assert "Obrigado" in env.dms[0][1]
    assert db.get_contact("111")["state"] == "opted_out"


def test_message_from_stranger_or_echo_is_ignored(client, env):
    post_webhook(client, message_payload(sender="777", text="oi"))
    post_webhook(client, message_payload(sender="999", text="oi", echo=True))
    assert env.dms == []


# ------------------------------------------------------------------ IA
def test_ai_failure_falls_back_to_template(client, env, monkeypatch):
    monkeypatch.setattr(ai, "generate_reply", lambda *a, **k: None)
    post_webhook(client, comment_payload())
    reply = db.list_items(["pending"])[0]["public_reply"]
    cfg = rules.load_rules()
    assert reply in cfg["rules"][0]["public_replies"]


def test_ai_reply_is_sanitized(client, env, monkeypatch):
    monkeypatch.setattr(ai, "generate_reply", lambda *a, **k: "Compre em https://x.com já!")
    post_webhook(client, comment_payload())
    assert "http" not in db.list_items(["pending"])[0]["public_reply"]


# ------------------------------------------------------------------ painel
def test_panel_requires_password(env):
    from fastapi.testclient import TestClient

    from app.main import app

    anon = TestClient(app)
    assert anon.get("/").status_code == 401
    wrong = TestClient(app)
    wrong.auth = ("admin", "errada")
    assert wrong.get("/").status_code == 401


def test_panel_renders_and_escapes_html(client):
    post_webhook(client, comment_payload(text="<script>alert(1)</script>"))
    page = client.get("/")
    assert page.status_code == 200
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_rules_page_rejects_invalid_yaml_and_saves_valid(client):
    bad = client.post("/rules", data={"text": "rules: [unclosed"}, follow_redirects=False)
    assert bad.status_code == 400
    good = client.post("/rules", data={"text": "rules:\n  - name: x\n    match: {any: true}\n"},
                       follow_redirects=False)
    assert good.status_code == 303
    assert rules.load_rules()["rules"][0]["name"] == "x"


# ------------------------------------------------------------------ .env
def test_load_dotenv_reads_values_without_overriding(tmp_path, monkeypatch):
    from app.config import load_dotenv

    f = tmp_path / ".env"
    f.write_text('# comentario\nA_NOVA=valor1\nJA_EXISTE=do_arquivo\nCOM_ASPAS="entre aspas"\nVAZIA=\n',
                 encoding="utf-8")
    monkeypatch.delenv("A_NOVA", raising=False)
    monkeypatch.delenv("COM_ASPAS", raising=False)
    monkeypatch.delenv("VAZIA", raising=False)
    monkeypatch.setenv("JA_EXISTE", "do_ambiente")
    load_dotenv(f)
    import os
    assert os.environ["A_NOVA"] == "valor1"
    assert os.environ["JA_EXISTE"] == "do_ambiente"
    assert os.environ["COM_ASPAS"] == "entre aspas"
    assert os.environ["VAZIA"] == ""
    for k in ("A_NOVA", "COM_ASPAS", "VAZIA"):
        os.environ.pop(k, None)


# ------------------------------------------------------------------ exclusão de dados (LGPD)
def test_delete_user_data_removes_everything_of_that_person_only(client, env):
    post_webhook(client, comment_payload(cid="c1", uid="111", username="Ana_Pet"))
    post_webhook(client, comment_payload(cid="c2", uid="222", username="bia"))
    item = db.list_items(["pending"])[-1]  # item da Ana (mais antigo)
    client.post(f"/queue/{item['id']}/approve", data={"public_reply": "Oi", "dm_text": "dm?"})

    r = client.post("/privacy/delete", data={"identifier": "@ana_pet"}, follow_redirects=False)
    assert r.status_code == 303 and "deleted=" in r.headers["location"]

    assert db.get_contact("111") is None
    with db.connect() as c:
        assert c.execute("SELECT COUNT(*) n FROM comments WHERE ig_user_id='111'").fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) n FROM queue WHERE ig_user_id='111'").fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) n FROM logs WHERE lower(message) LIKE '%ana_pet%'").fetchone()["n"] == 0
        assert c.execute("SELECT COUNT(*) n FROM queue WHERE ig_user_id='222'").fetchone()["n"] == 1


def test_delete_user_data_requires_admin_and_ignores_empty(env):
    from fastapi.testclient import TestClient

    from app.main import app

    assert TestClient(app).post("/privacy/delete", data={"identifier": "x"}).status_code == 401
    assert db.delete_user_data("   ") == {"comments": 0, "queue": 0, "contacts": 0, "logs": 0}


# ------------------------------------------------------------------ paginação e reativar
def test_rejected_item_can_be_restored_and_then_sent(client, env):
    post_webhook(client, comment_payload())
    item = db.list_items(["pending"])[0]
    client.post(f"/queue/{item['id']}/reject")
    assert db.get_item(item["id"])["status"] == "rejected"

    r = client.post(f"/queue/{item['id']}/restore", data={"next": "/?h=rejeitados"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/?h=rejeitados"
    assert db.get_item(item["id"])["status"] == "pending"

    client.post(f"/queue/{item['id']}/approve", data={"public_reply": "Oi", "dm_text": "dm?"})
    assert db.get_item(item["id"])["status"] == "sent" and len(env.private) == 1


def test_restore_only_touches_rejected_and_blocks_external_redirect(client, env):
    post_webhook(client, comment_payload())
    item = db.list_items(["pending"])[0]
    client.post(f"/queue/{item['id']}/approve", data={"public_reply": "Oi", "dm_text": "dm?"})
    r = client.post(f"/queue/{item['id']}/restore", data={"next": "//evil.com"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    assert db.get_item(item["id"])["status"] == "sent"  # enviado não volta para a fila


def test_history_and_logs_are_paginated_and_filtered(client):
    for i in range(13):
        post_webhook(client, comment_payload(cid=f"c{i}", uid=f"u{i}", username=f"user{i}"))
    for it in db.list_items(["pending"]):
        client.post(f"/queue/{it['id']}/reject")
    p1 = client.get("/?h=rejeitados&hp=1").text
    p2 = client.get("/?h=rejeitados&hp=2").text
    assert "Página 1 de 2" in p1 and "Página 2 de 2" in p2
    assert p1.count("Reativar</button>") == 10 and p2.count("Reativar</button>") == 3
    assert "Reativar</button>" not in client.get("/?h=enviados").text
    assert client.get("/?hp=999&lp=-5").status_code == 200  # páginas fora do limite não quebram


# ------------------------------------------------------------------ não repetir DM
def _approve_all_pending(client):
    for it in reversed(db.list_items(["pending"])):
        client.post(f"/queue/{it['id']}/approve",
                    data={"public_reply": it["public_reply"] or "Oi", "dm_text": it["dm_text"]})


def test_second_comment_gets_public_reply_only_without_mentioning_direct(client, env):
    post_webhook(client, comment_payload(cid="c1"))
    _approve_all_pending(client)
    assert len(env.private) == 1

    post_webhook(client, comment_payload(cid="c2", text="Amei o post"))
    item = db.list_items(["pending"])[0]
    assert item["dm_text"] == "" and item["dm_done"] == 1
    assert "direct" not in item["public_reply"].lower() and "mensagem" not in item["public_reply"].lower()
    assert "Sem DM" in client.get("/").text

    client.post(f"/queue/{item['id']}/approve", data={"public_reply": item["public_reply"], "dm_text": ""})
    assert len(env.private) == 1  # nenhuma DM nova
    assert [c for c, _ in env.replies] == ["c1", "c2"]  # mas respondeu em público
    assert db.get_item(item["id"])["status"] == "sent"


def test_skipped_dm_does_not_reset_contact_so_link_is_not_sent_twice(client, env):
    post_webhook(client, comment_payload(cid="c1"))
    _approve_all_pending(client)
    post_webhook(client, message_payload(text="quero"))  # recebe o link
    assert db.get_contact("111")["state"] == "link_sent" and len(env.dms) == 1

    post_webhook(client, comment_payload(cid="c2"))
    _approve_all_pending(client)
    assert db.get_contact("111")["state"] == "link_sent"  # não voltou para dm_sent
    post_webhook(client, message_payload(text="quero"))
    assert len(env.dms) == 1  # link não foi reenviado


def test_two_pending_items_same_person_only_first_sends_dm(client, env):
    post_webhook(client, comment_payload(cid="c1"))
    post_webhook(client, comment_payload(cid="c2"))  # ainda sem DM enviada: os dois entram com DM
    items = list(reversed(db.list_items(["pending"])))
    assert all(i["dm_text"] for i in items)
    for it in items:
        client.post(f"/queue/{it['id']}/approve", data={"public_reply": "Oi", "dm_text": it["dm_text"]})
    assert len(env.private) == 1 and len(env.replies) == 2


def test_cooldown_zero_disables_check(client, env, monkeypatch):
    monkeypatch.setenv("DM_COOLDOWN_DAYS", "0")
    post_webhook(client, comment_payload(cid="c1"))
    _approve_all_pending(client)
    post_webhook(client, comment_payload(cid="c2"))
    assert db.list_items(["pending"])[0]["dm_text"] != ""


def test_opted_out_person_never_gets_dm_again_even_with_cooldown_zero(client, env, monkeypatch):
    monkeypatch.setenv("DM_COOLDOWN_DAYS", "0")
    post_webhook(client, comment_payload(cid="c1"))
    _approve_all_pending(client)
    post_webhook(client, message_payload(text="não, obrigado"))
    post_webhook(client, comment_payload(cid="c2"))
    assert db.list_items(["pending"])[0]["dm_text"] == ""


def test_repeat_commenter_uses_prompt_without_direct(client, env, monkeypatch):
    prompts = []
    monkeypatch.setattr(ai, "generate_reply", lambda text, prompt, *a: prompts.append(prompt) or None)
    post_webhook(client, comment_payload(cid="c1"))
    _approve_all_pending(client)
    post_webhook(client, comment_payload(cid="c2"))
    assert "direct" in prompts[0].lower() and "NÃO mencione" in prompts[1]


def test_other_people_still_get_dm(client, env):
    post_webhook(client, comment_payload(cid="c1", uid="111", username="ana"))
    _approve_all_pending(client)
    post_webhook(client, comment_payload(cid="c2", uid="222", username="bia"))
    assert db.list_items(["pending"])[0]["dm_text"] != ""


# ------------------------------------------------------------------ contexto do post
def test_post_caption_is_fetched_once_and_cached(client, env, monkeypatch):
    monkeypatch.setattr(ai, "generate_reply", lambda *a, **k: None)
    post_webhook(client, comment_payload(cid="c1", uid="111", username="ana", media="m1"))
    post_webhook(client, comment_payload(cid="c2", uid="222", username="bia", media="m1"))
    post_webhook(client, comment_payload(cid="c3", uid="333", username="cai", media="m2"))
    assert env.media_calls == ["m1", "m2"]  # m1 foi consultado uma vez só
    assert db.get_media("m1")["caption"] == "Meu cachorro aprendeu a sentar!"


def test_ai_receives_post_caption_as_context(client, env, monkeypatch):
    sent = {}

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"choices": [{"message": {"content": "Que fofo! 🐶"}}]}

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.update(json)
        return FakeResp()

    monkeypatch.setenv("AI_API_KEY", "k")
    monkeypatch.setattr(ai.httpx, "post", fake_post)
    post_webhook(client, comment_payload(cid="c1"))
    user_msg = sent["messages"][1]["content"]
    assert "Meu cachorro aprendeu a sentar!" in user_msg and "Que fofo!" in user_msg
    assert "não invente" in sent["messages"][0]["content"]
    assert db.list_items(["pending"])[0]["public_reply"] == "Que fofo! 🐶"


def test_media_failure_falls_back_to_comment_only_and_is_not_cached(client, env, monkeypatch):
    monkeypatch.setattr(ai, "generate_reply", lambda *a, **k: "Boa! 🐾")
    env.fail_media = True
    post_webhook(client, comment_payload(cid="c1"))
    assert db.list_items(["pending"])[0]["public_reply"] == "Boa! 🐾"  # comentário seguiu normalmente
    assert db.get_media("m1") is None
    assert "Não consegui ler o post" in client.get("/").text
    env.fail_media = False
    post_webhook(client, comment_payload(cid="c2", uid="222", username="bia"))
    assert db.get_media("m1") is not None  # tentou de novo e guardou


def test_post_context_can_be_turned_off(client, env, monkeypatch):
    monkeypatch.setenv("POST_CONTEXT", "false")
    monkeypatch.setattr(ai, "generate_reply", lambda *a, **k: None)
    post_webhook(client, comment_payload())
    assert env.media_calls == []


def test_panel_shows_post_snippet(client, env, monkeypatch):
    monkeypatch.setattr(ai, "generate_reply", lambda *a, **k: None)
    post_webhook(client, comment_payload())
    assert "Meu cachorro aprendeu a sentar!" in client.get("/").text


# ------------------------------------------------------------------ agradecimento a quem recusa
def _declined(client):
    post_webhook(client, comment_payload())
    item = db.list_items(["pending"])[0]
    client.post(f"/queue/{item['id']}/approve", data={"public_reply": "Oi", "dm_text": "dm?"})


def test_thanks_is_sent_only_once(client, env):
    _declined(client)
    post_webhook(client, message_payload(text="não quero"))
    post_webhook(client, message_payload(text="parar"))
    post_webhook(client, message_payload(text="oi?"))
    assert len(env.dms) == 1


def test_custom_thanks_text_from_rules(client, env, tmp_path):
    f = tmp_path / "rules.yaml"
    f.write_text(f.read_text(encoding="utf-8") + '\noptout_reply: "Valeu mesmo assim! 🧡"\n', encoding="utf-8")
    _declined(client)
    post_webhook(client, message_payload(text="não"))
    assert env.dms == [("111", "Valeu mesmo assim! 🧡")]


def test_dry_run_does_not_send_thanks(client, env, monkeypatch):
    _declined(client)
    monkeypatch.setenv("DRY_RUN", "true")
    post_webhook(client, message_payload(text="não"))
    assert env.dms == [] and db.get_contact("111")["state"] == "opted_out"


def test_thanks_failure_still_records_optout(client, env, monkeypatch):
    from app import meta

    def boom(*a, **k):
        raise RuntimeError("falhou")

    _declined(client)
    monkeypatch.setattr(meta, "send_dm", boom)
    post_webhook(client, message_payload(text="não"))
    assert db.get_contact("111")["state"] == "opted_out"
    assert "falha ao enviar o agradecimento" in client.get("/").text
