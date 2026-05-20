"""
app.py
━━━━━━
Servidor web que recibe el webhook de Monday.com,
genera el contrato PDF y lo adjunta al item.
"""

import io
import os
import re
import requests
from datetime import datetime
from flask import Flask, request, jsonify
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as rl_canvas

app = Flask(__name__)

MONDAY_API_KEY = os.environ.get("MONDAY_API_KEY", "")
BOARD_ID       = os.environ.get("BOARD_ID", "18391352531")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

FONT_NAME = "Helvetica"
FONT_SIZE = 9

COL = {
    "address":   "long_text_mkygb4vx",
    "phone":     "text_mkyg2nmy",
    "email":     "email_mkznsja1",
    "insurance": "text_mkyg3b97",
    "mit_date":  "date_mkygs2tz",
    "dol":       "date_mkygxc2w",
    "claim":     "text_mkygka3z",
    "policy":    "text_mkyge54a",
}

HEADERS = {
    "Authorization": MONDAY_API_KEY,
    "Content-Type": "application/json",
    "API-Version": "2024-01",
}

def get_item_data(item_id):
    query = f"""
    {{
      items(ids: [{item_id}]) {{
        id
        name
        column_values {{ id text }}
      }}
    }}
    """
    resp = requests.post(
        "https://api.monday.com/v2",
        headers=HEADERS,
        json={"query": query},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    item = data["data"]["items"][0]
    cols = {cv["id"]: cv["text"] for cv in item["column_values"]}
    return {"id": item["id"], "name": item["name"], "cols": cols}


def upload_file_to_item(item_id, filename, file_bytes):
    query = """
    mutation ($file: File!) {
      add_file_to_column(
        item_id: %s,
        column_id: "files",
        file: $file
      ) { id }
    }
    """ % item_id
    resp = requests.post(
        "https://api.monday.com/v2/file",
        headers={"Authorization": MONDAY_API_KEY, "API-Version": "2024-01"},
        data={"query": query},
        files={"variables[file]": (filename, file_bytes, "application/pdf")},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def parse_client(item):
    cols = item["cols"]
    raw_addr = cols.get(COL["address"]) or ""
    city, zip_code, addr_line = "", "", raw_addr
    m = re.search(r",\s*([^,]+),\s*[A-Z]{2}\s*(\d{5})", raw_addr)
    if m:
        city     = m.group(1).strip()
        zip_code = m.group(2)
    if "," in raw_addr:
        addr_line = raw_addr.split(",")[0].strip()

    def fmt(raw):
        try: return datetime.strptime(raw, "%Y-%m-%d").strftime("%m/%d/%Y")
        except: return raw or ""

    name      = re.sub(r"\s*\(.*?\)\s*$", "", item["name"]).strip()
    mit_date  = fmt(cols.get(COL["mit_date"]) or "")
    dol       = fmt(cols.get(COL["dol"]) or "")
    insurance = cols.get(COL["insurance"]) or ""
    claim     = cols.get(COL["claim"]) or ""
    policy    = cols.get(COL["policy"]) or ""

    return {
        "name":           name,
        "today":          mit_date,          # → campos "Date"
        "address":        addr_line,
        "city":           f"{city}, FL" if city else "",   # "Miami, FL"
        "zip":            zip_code,
        "phone":          cols.get(COL["phone"]) or "",
        "email":          cols.get(COL["email"]) or "",
        "insurance":      insurance,
        "dol":            dol,
        "claim":          claim,             # Claim# → campo Claim#
        "policy":         policy,            # Policy# → campo Policy#
        "city_state_zip": f"{city}, FL {zip_code}".strip(", "),
    }


def make_overlay(d, page_num, page_w, page_h):
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setFont(FONT_NAME, FONT_SIZE)

    def text(x, pdf_top, value, max_w=None):
        if not value:
            return
        rl_y = page_h - pdf_top - FONT_SIZE + 2
        if max_w:
            while c.stringWidth(value, FONT_NAME, FONT_SIZE) > max_w and len(value) > 1:
                value = value[:-1]
        c.drawString(x, rl_y, value)

    # ── PÁGINA 1 — Formulario principal ──────────────────────
    # Page size: 610 x 789
    # Campos: Client Name | Date
    #         Address | City | Zip
    #         Home Phone | Cell Phone | Email
    #         Insurance Company | Date of Loss
    #         Policy # | Claim #
    if page_num == 1:
        text(77,  23, d["name"],      max_w=355)   # Client Name
        text(449, 23, d["today"],     max_w=128)   # Date (Mitigation Date)
        text(63,  40, d["address"],   max_w=218)   # Address
        text(302, 40, d["city"],      max_w=158)   # City → "Miami, FL"
        text(479, 40, d["zip"],       max_w=96)    # Zip
        text(79,  57, d["phone"],     max_w=130)   # Home Phone
        text(251, 57, d["phone"],     max_w=138)   # Cell Phone
        text(415, 57, d["email"],     max_w=160)   # Email
        text(101, 74, d["insurance"], max_w=216)   # Insurance Company
        text(366, 74, d["dol"],       max_w=208)   # Date of Loss
        # FIX: Policy# va primero (x=62), Claim# va segundo (x=349)
        text(62,  91, d["policy"],    max_w=278)   # Policy #
        text(349, 91, d["claim"],     max_w=224)   # Claim #

    # ── PÁGINA 2 — Lien Law + firmas ─────────────────────────
    # Page size: 610 x 789
    # "Client Name" label en top=364.1, "Date" label en top=398.9
    # El texto va justo después del label → mismo top, x después del label
    elif page_num == 2:
        text(88,  364, d["name"],  max_w=200)   # Client Name (izquierda)
        text(88,  399, d["today"], max_w=200)   # Date (izquierda)

    # ── PÁGINA 3 — Customer Responsibility Form ───────────────
    # Page size: 612 x 792
    # "Homeowner Printed Name:" label en top=627.5
    # "Date:" label en top=640.2
    elif page_num == 3:
        text(193, 628, d["name"],  max_w=350)   # Homeowner Printed Name
        text(82,  640, d["today"], max_w=200)   # Date

    # ── PÁGINA 4 — Certificate of Completion ─────────────────
    # Page size: 612 x 792
    # Tabla con labels en: Owner(148.9), Phone(148.9), Address(171),
    # Claim Number(171), City/State/Zip(192.4), DOL(192.4),
    # Email(214), Insurance Co(214)
    # "Print Name and Title" en top=612.2 (lado cliente)
    elif page_num == 4:
        text(106, 149, d["name"],          max_w=170)   # Owner(s)
        text(324, 149, d["phone"],         max_w=234)   # Phone #
        text(90,  171, d["address"],       max_w=186)   # Address
        text(351, 171, d["claim"],         max_w=207)   # Claim Number
        text(118, 193, d["city_state_zip"],max_w=158)   # City, State, Zip
        text(312, 193, d["dol"],           max_w=246)   # DOL
        text(81,  214, d["email"],         max_w=195)   # Email
        text(344, 214, d["insurance"],     max_w=214)   # Insurance Co
        text(42,  612, d["name"],          max_w=270)   # Print Name and Title (cliente)

    c.save()
    buf.seek(0)
    return buf


def generate_pdf_bytes(item):
    d = parse_client(item)
    template_path = os.path.join(os.path.dirname(__file__), "Mitigation_Contract.pdf")
    reader = PdfReader(template_path)
    writer = PdfWriter()

    # El PDF tiene 4 páginas: índices 0,1,2,3 → page_num 1,2,3,4
    page_map = {0: 1, 1: 2, 2: 3, 3: 4}

    for i, page in enumerate(reader.pages):
        if i in page_map:
            page_num = page_map[i]
            w = float(page.mediabox.width)
            h = float(page.mediabox.height)
            overlay_buf = make_overlay(d, page_num, w, h)
            overlay_reader = PdfReader(overlay_buf)
            page.merge_page(overlay_reader.pages[0])
        writer.add_page(page)

    out_buf = io.BytesIO()
    writer.write(out_buf)
    out_buf.seek(0)
    return out_buf.read()


@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Contract Generator running"}), 200


@app.route("/generate", methods=["POST"])
def generate():
    payload = request.get_json(force=True, silent=True) or {}

    if "challenge" in payload:
        return jsonify({"challenge": payload["challenge"]}), 200

    item_id = None
    try:
        item_id = (
            payload.get("event", {}).get("pulseId") or
            payload.get("event", {}).get("itemId") or
            payload.get("pulseId") or
            payload.get("itemId")
        )
    except Exception:
        pass

    if not item_id:
        return jsonify({"error": "No item_id found in payload"}), 400

    try:
        item = get_item_data(item_id)
        name = item["name"]
        print(f"Generating contract for: {name}")

        pdf_bytes = generate_pdf_bytes(item)

        safe_name = re.sub(r'[^a-zA-Z0-9_ ]', '', name)[:50].strip().replace(" ", "_")
        filename  = f"Contract_{safe_name}.pdf"
        upload_file_to_item(item_id, filename, pdf_bytes)

        print(f"✅ Contract uploaded: {filename}")
        return jsonify({"success": True, "file": filename}), 200

    except Exception as e:
        print(f"❌ Error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
