#!/usr/bin/env python3
"""
KVBB Abrechnungsportal – lokaler Mini-Server
============================================
Start:   python server.py
Browser: http://localhost:8000

- Dient statische Dateien (HTML/CSS/JS) aus diesem Verzeichnis
- POST /api/submit  – leitet an n8n weiter, gibt Vorgangsnummer zurück
- POST /api/n8n     – Proxy für direkte n8n-Anfragen (Status, Widerspruch)
"""

import datetime
import http.server
import json
import os
import random
import re
import socketserver
import string
import urllib.parse
import urllib.request

# Immer aus dem Verzeichnis des Skripts heraus server (für statische Dateien)
os.chdir(os.path.dirname(os.path.abspath(__file__)))

PORT = 8000


def generate_vorgangsnummer():
    year = datetime.datetime.now().year
    # Alphanumerisch ohne verwechslungsanfällige Zeichen (kein I, O, 0, 1)
    chars = ''.join(c for c in string.ascii_uppercase + string.digits if c not in 'IO01')
    suffix = ''.join(random.choices(chars, k=5))
    return f"KVBB-{year}-{suffix}"


# ── Hilfsfunktionen ──────────────────────────────────────────

def get_n8n_url():
    """Liest N8N_URL aus config.js."""
    try:
        with open("config.js", "r", encoding="utf-8") as f:
            content = f.read()
        m = re.search(r'N8N_URL\s*:\s*["\']([^"\']+)["\']', content)
        if m:
            return m.group(1)
    except Exception as e:
        print(f"Warnung: config.js konnte nicht gelesen werden: {e}")
    return None


# ── HTTP-Handler ─────────────────────────────────────────────

class KVBBHandler(http.server.SimpleHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    def send_json(self, code: int, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, x-token")
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/submit":
            self._handle_submit()
        elif self.path == "/api/n8n":
            self._handle_n8n_proxy()
        else:
            self.send_json(404, {"error": "Nicht gefunden"})

    def _handle_n8n_proxy(self):
        """Leitet beliebige Payloads direkt an n8n weiter und gibt die rohe Antwort zurück."""
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
        except Exception:
            self.send_json(400, {"error": "Ungültiges JSON"})
            return

        n8n_url = get_n8n_url()
        if not n8n_url:
            self.send_json(500, {"error": "N8N_URL nicht in config.js konfiguriert"})
            return

        try:
            req_body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            n8n_headers = {"Content-Type": "application/json"}
            x_token = self.headers.get("x-token")
            if x_token:
                n8n_headers["x-token"] = x_token
            req = urllib.request.Request(
                n8n_url,
                data=req_body,
                headers=n8n_headers,
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
            print(f"n8n Proxy Antwort: {raw}")
            data = json.loads(raw)
            entry  = data[0] if isinstance(data, list) else data
            output = entry.get("output", entry)
            self.send_json(200, output)
        except Exception as e:
            print(f"n8n Proxy Fehler: {e}")
            self.send_json(500, {"error": str(e)})

    def _handle_submit(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length))
        except Exception:
            self.send_json(400, {"error": "Ungültiges JSON"})
            return

        n8n_url = get_n8n_url()
        if not n8n_url:
            self.send_json(500, {"error": "N8N_URL nicht in config.js konfiguriert"})
            return

        vnr = generate_vorgangsnummer()

        # n8n-Webhook aufrufen
        n8n_payload = {
            "vorgangsnummer":    vnr,
            "betriebsstaette":   payload.get("betriebsstaette", ""),
            "antragsquartal":    payload.get("antragsquartalText", payload.get("antragsquartal", "")),
            "abgabeFrist":       payload.get("abgabeFrist", "22.01.2026"),
            "begruendung":       payload.get("begruendung", ""),
            "bearbeitungsstatus": payload.get("bearbeitungsstatus", "in_bearbeitung"),
            "eingangsdatum":     payload.get("eingangsdatum", datetime.datetime.now().isoformat()),
            "art":               payload.get("art", "neuer_antrag"),
        }

        created_success = False
        try:
            req_body = json.dumps(n8n_payload, ensure_ascii=False).encode("utf-8")
            n8n_headers = {"Content-Type": "application/json"}
            x_token = self.headers.get("x-token")
            if x_token:
                n8n_headers["x-token"] = x_token
            req = urllib.request.Request(
                n8n_url,
                data=req_body,
                headers=n8n_headers,
            )
            with urllib.request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8")
            print(f"n8n Antwort: {raw}")
            data = json.loads(raw)
            entry  = data[0] if isinstance(data, list) else data
            output = entry.get("output", entry)
            # VNR aus n8n nur nutzen wenn vorhanden, sonst lokal generierte behalten
            vnr              = output.get("vorgangsnummer", None) or vnr
            _cs              = output.get("created_success", False)
            created_success  = _cs is True or str(_cs).lower() == 'true'
        except Exception as e:
            print(f"n8n Fehler: {e}")
            created_success = False

        self.send_json(200, {"vorgangsnummer": vnr, "created_success": created_success})

    def do_GET(self):
        super().do_GET()


# ── Start ────────────────────────────────────────────────────

if __name__ == "__main__":
    n8n = get_n8n_url()
    print("=" * 55)
    print("  KVBB Abrechnungsportal")
    print(f"  Öffnen: http://localhost:{PORT}")
    print(f"  n8n:    {n8n or 'NICHT KONFIGURIERT – config.js prüfen'}")
    print("=" * 55)
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), KVBBHandler) as httpd:
        httpd.serve_forever()
