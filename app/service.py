"""Fluxo principal: comentário -> resposta pública + DM -> link do grupo."""
from . import ai, db, meta, rules
from .config import get_settings


def should_skip_dm(ig_user_id: str, exclude_item: int = 0) -> str:
    """Devolve o motivo para NÃO enviar DM a esta pessoa, ou '' se pode enviar."""
    contact = db.get_contact(ig_user_id)
    if contact is not None and contact["state"] == "opted_out":
        return "pediu para não receber mensagens"
    days = get_settings().dm_cooldown_days
    if db.dm_sent_recently(ig_user_id, days, exclude_item):
        return f"já recebeu a DM nos últimos {days} dias"
    return ""


def post_context(media_id: str | None) -> dict | None:
    """Dados do post comentado, com cache (uma consulta à Meta por post). Falha => None."""
    if not media_id or not get_settings().post_context:
        return None
    cached = db.get_media(media_id)
    if cached is not None:
        return dict(cached)
    try:
        data = meta.get_media(media_id)
    except Exception as exc:  # noqa: BLE001 - sem contexto, a resposta sai só com o comentário
        db.log("warn", f"Não consegui ler o post {media_id}; resposta sem contexto: {exc}")
        return None
    db.save_media(media_id, data.get("caption"), data.get("media_type"),
                  data.get("media_product_type"), data.get("permalink"))
    return dict(db.get_media(media_id))


def handle_comment(comment_id: str, text: str, from_id: str, username: str, media_id: str | None) -> None:
    s = get_settings()

    # Nunca responder a si mesmo (evita loop infinito).
    if s.ig_account_id and str(from_id) == str(s.ig_account_id):
        return
    if not db.register_comment(comment_id, from_id, username, media_id, text):
        return  # evento repetido

    config = rules.load_rules()
    rule = rules.match_rule(text, media_id, config)
    if rule is None:
        db.log("info", f"Comentário {comment_id} ignorado (nenhuma regra combinou)")
        return

    skip_reason = should_skip_dm(from_id)  # já recebeu DM? então só responde em público
    public_reply = None
    if rule.get("use_ai"):
        prompt = rules.repeat_prompt(rule) if skip_reason else rule.get("ai_prompt", "")
        public_reply = ai.generate_reply(text, prompt, post_context(media_id))
    fallback = rules.pick_repeat_template(rule) if skip_reason else rules.pick_template(rule)
    public_reply = rules.sanitize_public_reply(public_reply or "") or fallback

    if skip_reason:
        item_id = db.add_queue_item(comment_id, from_id, username, text, rule.get("name", ""), public_reply, "")
        db.update_item(item_id, dm_done=1)  # nada de DM neste item
        db.log("info", f"Comentário de @{username} entrou na fila (#{item_id}); sem DM: {skip_reason}")
    else:
        dm_text = " ".join((rule.get("dm") or "").split()).replace("{nome}", username or "tudo bem")
        item_id = db.add_queue_item(comment_id, from_id, username, text, rule.get("name", ""), public_reply, dm_text)
        db.log("info", f"Comentário de @{username} entrou na fila (#{item_id}, regra {rule.get('name')})")

    if not s.approval_mode:
        send_item(item_id)


def send_item(item_id: int) -> bool:
    """Publica a resposta e envia a DM de um item da fila. Pode ser repetido sem duplicar."""
    s = get_settings()
    item = db.get_item(item_id)
    if item is None or item["status"] in ("sent", "dry_run", "rejected"):
        return False

    if db.count_sent_last_hour() >= s.max_replies_per_hour:
        db.update_item(item_id, error="Limite por hora atingido; tente de novo mais tarde")
        db.log("warn", f"Item #{item_id} não enviado: limite por hora")
        return False

    # Revalida na hora do envio: outro comentário da mesma pessoa pode ter mandado a DM antes.
    if not item["dm_done"]:
        reason = should_skip_dm(item["ig_user_id"], exclude_item=item_id)
        if reason:
            db.update_item(item_id, dm_done=1, dm_text="")
            db.log("info", f"Item #{item_id}: DM não enviada a @{item['username']} ({reason})")
            item = db.get_item(item_id)
    sends_dm = not item["dm_done"] and bool(item["dm_text"])

    try:
        if s.dry_run:
            what = "e enviaria DM" if sends_dm else "(sem DM)"
            db.log("info", f"[TESTE] Responderia '{item['public_reply']}' {what} para @{item['username']}")
            if sends_dm:
                db.set_contact(item["ig_user_id"], "dm_sent", item["comment_id"])
            db.update_item(item_id, status="dry_run", reply_done=1, dm_done=1, error=None)
            return True

        if not item["reply_done"]:
            meta.reply_to_comment(item["comment_id"], item["public_reply"])
            db.update_item(item_id, reply_done=1)
        if sends_dm:
            meta.send_private_reply(item["comment_id"], item["dm_text"])
            db.update_item(item_id, dm_done=1)
            db.set_contact(item["ig_user_id"], "dm_sent", item["comment_id"])
        db.update_item(item_id, status="sent", error=None)
        db.log("info", f"Item #{item_id} enviado para @{item['username']}" + ("" if sends_dm else " (só resposta pública)"))
        return True
    except Exception as exc:  # noqa: BLE001
        db.update_item(item_id, status="failed", error=str(exc)[:300])
        db.log("error", f"Falha no item #{item_id}: {exc}")
        return False


def handle_message(sender_id: str, text: str, is_echo: bool = False) -> None:
    """Pessoa respondeu a DM: envia o link do grupo (uma única vez)."""
    s = get_settings()
    if is_echo or (s.ig_account_id and str(sender_id) == str(s.ig_account_id)):
        return
    contact = db.get_contact(sender_id)
    if contact is None:
        db.log("warn", f"Mensagem de {sender_id} ignorada: ninguém com esse ID recebeu a DM inicial")
        return
    if contact["state"] != "dm_sent":
        db.log("info", f"Mensagem de {sender_id} ignorada: estado do contato é '{contact['state']}'")
        return
    db.log("info", f"Resposta recebida de {sender_id}: preparando o link do grupo")

    config = rules.load_rules()
    if rules.is_optout(text, config):
        db.set_contact(sender_id, "opted_out")  # primeiro registra a recusa, mesmo que o envio falhe
        try:
            if s.dry_run:
                db.log("info", f"[TESTE] Agradeceria a {sender_id}, que recusou o link")
            else:
                meta.send_dm(sender_id, rules.optout_reply(config))
                db.log("info", f"Contato {sender_id} recusou; agradecimento enviado (sem link)")
        except Exception as exc:  # noqa: BLE001
            db.log("error", f"Contato {sender_id} recusou; falha ao enviar o agradecimento: {exc}")
        return

    if not s.group_link or "exemplo.com" in s.group_link:
        db.log("error", "GROUP_LINK não está configurado no .env; link não enviado")
        return
    message = config["follow_up"].replace("{link}", s.group_link)
    try:
        if s.dry_run:
            db.log("info", f"[TESTE] Enviaria o link do grupo para {sender_id}")
        else:
            meta.send_dm(sender_id, message)
            db.log("info", f"Link do grupo enviado para {sender_id}")
        db.set_contact(sender_id, "link_sent")
    except Exception as exc:  # noqa: BLE001
        db.log("error", f"Falha ao enviar link para {sender_id}: {exc}")
