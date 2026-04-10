"""
KVBB Abrechnungsportal – Azure Functions API
=============================================
Endpoints:
  POST /api/submit  – Antrag einreichen (leitet an n8n weiter)
  POST /api/n8n     – Proxy für n8n-Anfragen (Status, Widerspruch)
"""

import azure.functions as func
import datetime
import json
import logging
import os
import random
import string
import urllib.request

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

N8N_URL = os.environ.get(
    "N8N_URL",
    "https://mms-n8n.germanywestcentral.cloudapp.azure.com/webhook/cab2d150-f4ca-40cd-b1d3-7d792a979366",
)


def generate_vorgangsnummer():
    year = datetime.datetime.now().year
    chars = "".join(
        c for c in string.ascii_uppercase + string.digits if c not in "IO01"
    )
    suffix = "".join(random.choices(chars, k=5))
    return f"KVBB-{year}-{suffix}"


def _json_response(data, status_code=200):
    return func.HttpResponse(
        json.dumps(data, ensure_ascii=False),
        status_code=status_code,
        mimetype="application/json",
    )


def _forward_to_n8n(payload, x_token=None):
    """Send a JSON payload to the n8n webhook and return the parsed output."""
    req_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if x_token:
        headers["x-token"] = x_token
    req = urllib.request.Request(N8N_URL, data=req_body, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode("utf-8")
    logging.info("n8n Antwort: %s", raw)
    data = json.loads(raw)
    entry = data[0] if isinstance(data, list) else data
    return entry.get("output", entry)


# ── POST /api/submit ─────────────────────────────────────────
@app.route(route="submit", methods=["POST"])
def submit(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return _json_response({"error": "Ungültiges JSON"}, 400)

    if not N8N_URL:
        return _json_response({"error": "N8N_URL nicht konfiguriert"}, 500)

    vnr = generate_vorgangsnummer()

    n8n_payload = {
        "vorgangsnummer": vnr,
        "betriebsstaette": payload.get("betriebsstaette", ""),
        "antragsquartal": payload.get(
            "antragsquartalText", payload.get("antragsquartal", "")
        ),
        "abgabeFrist": payload.get("abgabeFrist", "22.01.2026"),
        "begruendung": payload.get("begruendung", ""),
        "bearbeitungsstatus": payload.get("bearbeitungsstatus", "in_bearbeitung"),
        "eingangsdatum": payload.get(
            "eingangsdatum", datetime.datetime.now().isoformat()
        ),
        "art": payload.get("art", "neuer_antrag"),
    }

    created_success = False
    try:
        output = _forward_to_n8n(n8n_payload, req.headers.get("x-token"))
        vnr = output.get("vorgangsnummer", None) or vnr
        _cs = output.get("created_success", False)
        created_success = _cs is True or str(_cs).lower() == "true"
    except Exception as e:
        logging.error("n8n Fehler: %s", e)
        created_success = False

    return _json_response({"vorgangsnummer": vnr, "created_success": created_success})


# ── POST /api/n8n ────────────────────────────────────────────
@app.route(route="n8n", methods=["POST"])
def n8n_proxy(req: func.HttpRequest) -> func.HttpResponse:
    try:
        payload = req.get_json()
    except ValueError:
        return _json_response({"error": "Ungültiges JSON"}, 400)

    if not N8N_URL:
        return _json_response({"error": "N8N_URL nicht konfiguriert"}, 500)

    try:
        output = _forward_to_n8n(payload, req.headers.get("x-token"))
        return _json_response(output)
    except Exception as e:
        logging.error("n8n Proxy Fehler: %s", e)
        return _json_response({"error": str(e)}, 500)
