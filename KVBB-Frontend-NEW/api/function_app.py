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
import requests

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)

N8N_URL = os.environ.get(
    "N8N_URL",
    "https://mms-n8n.germanywestcentral.cloudapp.azure.com/webhook/49f5ce3d-1cc8-4b03-a825-e613a4ca7518",
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
    headers = {"Content-Type": "application/json"}
    if x_token:
        headers["x-token"] = x_token
    resp = requests.post(N8N_URL, json=payload, headers=headers, timeout=120)
    resp.raise_for_status()
    raw = resp.text
    logging.info("n8n Antwort: %s", raw)
    if not raw or not raw.strip():
        return {}
    data = resp.json()
    entry = data[0] if isinstance(data, list) else data
    return entry.get("output", entry)


# ── POST /api/submit ─────────────────────────────────────────
@app.route(route="submit", methods=["POST"])
def submit(req: func.HttpRequest) -> func.HttpResponse:
    debug_log = []

    try:
        payload = req.get_json()
        debug_log.append(f"payload_received: {json.dumps(payload, ensure_ascii=False)}")
    except ValueError as e:
        return _json_response({"error": "Ungültiges JSON", "detail": str(e)}, 400)

    if not N8N_URL:
        return _json_response({"error": "N8N_URL nicht konfiguriert"}, 500)

    debug_log.append(f"n8n_url: {N8N_URL}")

    vnr = generate_vorgangsnummer()
    debug_log.append(f"vorgangsnummer: {vnr}")

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

    debug_log.append(f"n8n_payload: {json.dumps(n8n_payload, ensure_ascii=False)}")

    created_success = False
    n8n_error = None
    n8n_raw_response = None
    try:
        headers = {"Content-Type": "application/json"}
        x_token = req.headers.get("x-token")
        if x_token:
            headers["x-token"] = x_token
        debug_log.append(f"x-token present: {bool(x_token)}")

        resp = requests.post(N8N_URL, json=n8n_payload, headers=headers, timeout=120)
        n8n_raw_response = resp.text
        debug_log.append(f"n8n_status: {resp.status_code}")
        debug_log.append(f"n8n_raw_response: {repr(n8n_raw_response)}")
        debug_log.append(f"n8n_headers: {dict(resp.headers)}")
        resp.raise_for_status()

        if not n8n_raw_response or not n8n_raw_response.strip():
            debug_log.append("n8n returned empty body – treating as success without output")
            created_success = True
        else:
            data = json.loads(n8n_raw_response)
            entry = data[0] if isinstance(data, list) else data
            output = entry.get("output", entry)
            debug_log.append(f"n8n_parsed_output: {json.dumps(output, ensure_ascii=False)}")

            vnr = output.get("vorgangsnummer", None) or vnr
            _cs = output.get("created_success", False)
            created_success = _cs is True or str(_cs).lower() == "true"
            debug_log.append(f"created_success_raw: {_cs}")
            debug_log.append(f"created_success_final: {created_success}")
    except requests.exceptions.HTTPError as e:
        error_body = e.response.text if e.response else ''
        n8n_error = f"HTTPError {e.response.status_code if e.response else '?'}: {error_body}"
        debug_log.append(f"n8n_http_error: {n8n_error}")
        logging.error("n8n HTTPError: %s", n8n_error)
    except Exception as e:
        n8n_error = f"{type(e).__name__}: {e}"
        debug_log.append(f"n8n_exception: {n8n_error}")
        logging.error("n8n Fehler: %s", e)

    logging.info("submit debug_log: %s", debug_log)

    return _json_response({
        "vorgangsnummer": vnr,
        "created_success": created_success,
        "n8n_error": n8n_error,
        "debug_log": debug_log,
    })


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
