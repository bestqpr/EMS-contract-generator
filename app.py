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
        column_values {{ id text value }}
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
    # Guardamos text Y value para columnas que puedan devolverlo diferente
    cols_text  = {cv["id"]: cv["text"]  for cv in item["column_values"]}
    cols_value = {cv["id"]: cv["value"] for cv in item["column_values"]}
    return {
        "id":         item["id"],
        "name":       item["name"],
        "cols":       cols_text,
        "cols_value": cols_value,
    }


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

    # Log all columns for debugging
    print("=== COLUMN VALUES ===")
    for k, v in cols.items():
        if v:
            print(f"  {k}: {repr(v)}")
    print("====================")

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

    # Insurance: intentar leer directo y también del value JSON por si acaso
    insurance = cols.get(COL["insurance"]) or ""
    if not insurance:
        raw_val = item["cols_value"].get(COL["insurance"]) or ""
        if raw_val and raw_val != "null":
            import json as _json
            try:
                insurance = _json.loads(raw_val).get("text", "") or ""
            except Exception:
                insurance = raw_val

    claim  = cols.get(COL["claim"])  or ""
    policy = cols.get(COL["policy"]) or ""

    print(f"  insurance resolved: {repr(insurance)}")
    print(f"  claim:  {repr(claim)}")
    print(f"  policy: {repr(policy)}")

    return {
        "name":           name,
        "today":          mit_date,
        "address":        addr_line,
        "city":           f"{city}, FL" if city else "",
        "zip":            zip_code,
        "phone":          cols.get(COL["phone"]) or "",
        "email":          cols.get(COL["email"]) or "",
        "insurance":      insurance,
        "dol":            dol,
        "claim":          claim,
        "policy":         policy,
        "city_state_zip": f"{city}, FL {zip_code}".strip(", "),
    }


def make_overlay(d, page_num, page_w, page_h):
    buf = io.BytesIO()
    c = rl_canvas.Canvas(buf, pagesize=(page_w, page_h))
    c.setFont(FONT_NAME, FONT_SIZE)

    def text(x, pdf_top, value, max_w=None):
        """pdf_top = coordenada Y desde arriba del PDF (como pdfplumber).
           El texto se dibuja ENCIMA de la línea subrayada, no sobre el label."""
        if not value:
            return
        rl_y = page_h - pdf_top - FONT_SIZE + 2
        if max_w:
            while c.stringWidth(value, FONT_NAME, FONT_SIZE) > max_w and len(value) > 1:
                value = value[:-1]
        c.drawString(x, rl_y, value)

    # ── PÁGINA 1 ─────────────────────────────────────────────
    # Underlines: top=33.6, 50.6, 67.5, 84.5, 101.6
    # Texto va 2-3pt encima de la línea → pdf_top = línea_top - 11
    if page_num == 1:
        # Fila 1 (línea top=33.6): Client Name | Date
        text(77,  23, d["name"],      max_w=355)
        text(449, 23, d["today"],     max_w=128)
        # Fila 2 (línea top=50.6): Address | City | Zip
        text(63,  40, d["address"],   max_w=218)
        text(302, 40, d["city"],      max_w=158)
        text(479, 40, d["zip"],       max_w=96)
        # Fila 3 (línea top=67.5): Home Phone | Cell Phone | Email
        text(79,  57, d["phone"],     max_w=130)
        text(251, 57, d["phone"],     max_w=138)
        text(415, 57, d["email"],     max_w=160)
        # Fila 4 (línea top=84.5): Insurance Company | Date of Loss
        # Label "Insurance Company:" termina ~x=99, campo empieza en x=100
        text(101, 74, d["insurance"], max_w=216)
        text(366, 74, d["dol"],       max_w=208)
        # Fila 5 (línea top=101.6): Policy # | Claim #
        text(62,  91, d["policy"],    max_w=278)
        text(349, 91, d["claim"],     max_w=224)

    # ── PÁGINA 2 ─────────────────────────────────────────────
    # Underlines: top=361.8 (Client Name), top=396.6 (Date)
    # Labels: "Client Name" x0=35.4 x1=87.4 top=364.1
    #         "Date"        x0=35.4 x1=55.1 top=398.9
    # El texto va sobre la línea → pdf_top = línea_top - 11
    # Y va DESPUÉS del label → x = x1 del label + 5
    elif page_num == 2:
        text(93,  352, d["name"],  max_w=190)   # después de "Client Name"
        text(58,  387, d["today"], max_w=220)   # después de "Date"

    # ── PÁGINA 3 ─────────────────────────────────────────────
    # "Homeowner Printed Name:" top=627.5, x=54..186
    # "Date:" top=640.2, x=54..80
    elif page_num == 3:
        text(193, 618, d["name"],  max_w=350)   # después del label Homeowner Printed Name
        text(82,  631, d["today"], max_w=200)   # después del label Date

    # ── PÁGINA 4 ─────────────────────────────────────────────
    # Tabla: Owner(148.9) | Phone(148.9) | Address(171) | Claim(171)
    #        City/State/Zip(192.4) | DOL(192.4) | Email(214) | Insurance(214)
    # Print Name line: underline top=609.4, label top=612.2
    # → texto encima de la línea: pdf_top = 609.4 - 11 = ~598
    # → texto NO va donde el label (612), sino en la línea vacía ENCIMA (609)
    elif page_num == 4:
        text(106, 149, d["name"],          max_w=170)
        text(324, 149, d["phone"],         max_w=234)
        text(90,  171, d["address"],       max_w=186)
        text(351, 171, d["claim"],         max_w=207)
        text(118, 193, d["city_state_zip"],max_w=158)
        text(312, 193, d["dol"],           max_w=246)
        text(81,  214, d["email"],         max_w=195)
        text(344, 214, d["insurance"],     max_w=214)
        # Print Name: encima de la línea (top=609.4), NO sobre el label (612.2)
        text(42,  599, d["name"],          max_w=248)

    c.save()
    buf.seek(0)
    return buf


def generate_pdf_bytes(item):
    d = parse_client(item)
    template_path = os.path.join(os.path.dirname(__file__), "Mitigation_Contract.pdf")
    reader = PdfReader(template_path)
    writer = PdfWriter()

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
