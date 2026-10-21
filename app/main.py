"""Sonar Reply: webhook do Instagram + painel de aprovação."""
import hashlib
import hmac
import json
import secrets
from pathlib import Path

import yaml
from fastapi import BackgroundTasks, Depends, FastAPI, Form, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import db, rules, service
from .config import get_settings

app = FastAPI(title="Sonar Reply")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
security = HTTPBasic()


# ---------------------------------------------------------------- segurança do painel
def require_admin(credentials: HTTPBasicCredentials = Depends(security)) -> None:
    password = get_settings().admin_password
    if not password:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Defina ADMIN_PASSWORD no .env")
    ok = secrets.compare_digest(credentials.password.encode(), password.encode())
    if not ok:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "Senha incorreta", headers={"WWW-Authenticate": "Basic"}
        )


# ---------------------------------------------------------------- webhook da Meta
@app.get("/webhook")
def webhook_verify(request: Request):
    """A Meta chama este endereço uma vez, ao cadastrar o webhook."""
    q = request.query_params
    token = get_settings().verify_token
    if token and q.get("hub.mode") == "subscribe" and secrets.compare_digest(
        q.get("hub.verify_token", ""), token
    ):
        return PlainTextResponse(q.get("hub.challenge", ""))
    raise HTTPException(status.HTTP_403_FORBIDDEN, "Token inválido")


def _valid_signature(body: bytes, header: str | None) -> bool:
    s = get_settings()
    if not s.app_secret:
        return s.dry_run  # sem segredo só é aceitável em modo de teste
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(s.app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[len("sha256="):], expected)


def process_payload(payload: dict) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "comments":
                continue
            v = change.get("value", {})
            who = v.get("from", {}) or {}
            service.handle_comment(
                comment_id=str(v.get("id", "")),
                text=v.get("text", ""),
                from_id=str(who.get("id", "")),
                username=who.get("username", ""),
                media_id=(v.get("media") or {}).get("id"),
            )
        for event in entry.get("messaging", []):
            message = event.get("message") or {}
            if not message:
                continue
            if not message.get("is_echo"):
                db.log("info", f"Webhook de mensagem recebido de {(event.get('sender') or {}).get('id', '?')}")
            service.handle_message(
                sender_id=str((event.get("sender") or {}).get("id", "")),
                text=message.get("text", ""),
                is_echo=bool(message.get("is_echo")),
            )


@app.post("/webhook")
async def webhook_receive(request: Request, background: BackgroundTasks):
    body = await request.body()
    if not _valid_signature(body, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Assinatura inválida")
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "JSON inválido")
    background.add_task(process_payload, payload)  # responde rápido à Meta
    return {"ok": True}


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------------------------------------------------------------- painel
PAGE_SIZE = 10
HISTORY_FILTERS = {
    "todos": ["sent", "dry_run", "rejected"],
    "enviados": ["sent", "dry_run"],
    "rejeitados": ["rejected"],
}


def _page(total: int, page: int) -> tuple[int, int]:
    """Devolve (página válida, total de páginas)."""
    pages = max(1, -(-total // PAGE_SIZE))
    return min(max(page, 1), pages), pages


@app.get("/", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def panel(request: Request, deleted: int = -1, h: str = "todos", hp: int = 1, lp: int = 1):
    s = get_settings()
    if h not in HISTORY_FILTERS:
        h = "todos"
    statuses = HISTORY_FILTERS[h]
    hp, h_pages = _page(db.count_items(statuses), hp)
    lp, l_pages = _page(db.count_logs(), lp)
    pending = db.list_items(["pending"])
    return templates.TemplateResponse(
        request,
        "panel.html",
        {
            "settings": s,
            "snippets": db.post_snippets(i["comment_id"] for i in pending),
            "pending": pending,
            "failed": db.list_items(["failed"]),
            "history": db.list_items(statuses, PAGE_SIZE, (hp - 1) * PAGE_SIZE),
            "logs": db.recent_logs(PAGE_SIZE, (lp - 1) * PAGE_SIZE),
            "deleted": deleted,
            "h": h, "hp": hp, "h_pages": h_pages,
            "lp": lp, "l_pages": l_pages,
        },
    )


@app.post("/queue/{item_id}/approve", dependencies=[Depends(require_admin)])
def approve(item_id: int, public_reply: str = Form(...), dm_text: str = Form("")):
    clean = rules.sanitize_public_reply(public_reply)
    item = db.get_item(item_id)
    no_dm = item is not None and bool(item["dm_done"]) and not item["dm_text"]  # item só de resposta pública
    if not clean or (not dm_text.strip() and not no_dm):
        raise HTTPException(400, "Os textos não podem ficar vazios")
    db.update_item(item_id, public_reply=clean, dm_text=" ".join(dm_text.split()))
    service.send_item(item_id)
    return RedirectResponse("/", status_code=303)


@app.post("/queue/{item_id}/reject", dependencies=[Depends(require_admin)])
def reject(item_id: int):
    db.update_item(item_id, status="rejected")
    return RedirectResponse("/", status_code=303)


@app.post("/queue/{item_id}/restore", dependencies=[Depends(require_admin)])
def restore(item_id: int, next: str = Form("/")):
    """Devolve um item rejeitado para a fila de aprovação (mudou de ideia)."""
    item = db.get_item(item_id)
    if item is not None and item["status"] == "rejected":
        db.update_item(item_id, status="pending", error=None)
        db.log("info", f"Item #{item_id} reativado e voltou para a fila")
    # só aceita caminho interno (evita redirecionamento para sites externos)
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    return RedirectResponse(target, status_code=303)


@app.post("/queue/{item_id}/retry", dependencies=[Depends(require_admin)])
def retry(item_id: int):
    db.update_item(item_id, status="pending")
    service.send_item(item_id)
    return RedirectResponse("/", status_code=303)


@app.post("/privacy/delete", dependencies=[Depends(require_admin)])
def privacy_delete(identifier: str = Form(...)):
    """Pedido de exclusão de dados (LGPD): apaga tudo da pessoa informada."""
    result = db.delete_user_data(identifier)
    total = sum(result.values())
    db.log("info", f"Exclusão de dados atendida ({total} registros apagados)")  # sem citar a pessoa
    return RedirectResponse(f"/?deleted={total}", status_code=303)


@app.get("/rules", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def rules_page(request: Request, error: str = ""):
    text = Path(get_settings().rules_file).read_text(encoding="utf-8")
    return templates.TemplateResponse(request, "rules.html", {"text": text, "error": error})


@app.post("/rules", dependencies=[Depends(require_admin)])
def rules_save(request: Request, text: str = Form(...)):
    try:
        data = yaml.safe_load(text)
        if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
            raise ValueError("O arquivo precisa ter a lista 'rules'")
    except Exception as exc:  # noqa: BLE001
        return templates.TemplateResponse(
            request, "rules.html", {"text": text, "error": f"Não salvei: {exc}"}, status_code=400
        )
    Path(get_settings().rules_file).write_text(text, encoding="utf-8")
    db.log("info", "Regras atualizadas pelo painel")
    return RedirectResponse("/rules", status_code=303)
