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

# ─────────────────────────────────────────────────────────────
#  CONFIGURACIÓN — se leen desde variables de entorno
# ─────────────────────────────────────────────────────────────
MONDAY_API_KEY = os.environ.get("MONDAY_API_KEY", "")
BOARD_ID       = os.environ.get("BOARD_ID", "18391352531")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")  # opcional

FONT_NAME = "Helvetica"
FONT_SIZE = 9

COL = {
    "address":  "long_text_mkygb4vx",
    "phone":    "text_mkyg2nmy",
    "email":    "email_mkznsja1",
    "insurance":"text_mkyg3b97",
    "mit_date": "date_mkygs2tz",
    "dol":      "date_mkygxc2w",
    "claim":    "text_mkygka3z",
    "policy":   "text_mkyge54a",
}

# ─────────────────────────────────────────────────────────────
#  MONDAY API
# ─────────────────────────────────────────────────────────────
HEADERS = {
    "Authorization": MONDAY_API_KEY,
    "Content-Type": "application/json",
    "API-Version": "2024-01",
}

def get_item_data(item_id):
    """Obtiene todos los datos de un item de Monday."""
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
    """Sube el PDF como archivo adjunto al item de Monday."""
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


# ─────────────────────────────────────────────────────────────
#  PARSEO DE DATOS
# ─────────────────────────────────────────────────────────────
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

    today = datetime.today().strftime("%m/%d/%Y")
    # Remove anything in parentheses at the end e.g. "(Roof Leak)", "(RETARP)"
    name  = re.sub(r"\s*\(.*?\)\s*$", "", item["name"]).strip()
    return {
        "name":           name,
        "today":          today,
        "address":        addr_line,
        "city":           city,
        "zip":            zip_code,
        "phone":          cols.get(COL["phone"]) or "",
        "email":          cols.get(COL["email"]) or "",
        "insurance":      cols.get(COL["insurance"]) or "",
        "dol":            fmt(cols.get(COL["dol"]) or ""),
        "claim":          cols.get(COL["claim"]) or "",
        "policy":         cols.get(COL["policy"]) or "",
        "city_state_zip": f"{city}, FL {zip_code}".strip(", "),
    }


# ─────────────────────────────────────────────────────────────
#  GENERACIÓN DEL PDF
# ─────────────────────────────────────────────────────────────
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

    if page_num == 1:
        text(77,  23, d["name"],           max_w=355)
        text(449, 23, d["today"],           max_w=128)
        text(63,  40, d["address"],         max_w=218)
        text(302, 40, d["city"],            max_w=158)
        text(479, 40, d["zip"],             max_w=96)
        text(79,  57, d["phone"],           max_w=130)
        text(251, 57, d["phone"],           max_w=138)
        text(415, 57, d["email"],           max_w=160)
        text(101, 74, d["insurance"],       max_w=216)
        text(366, 74, d["dol"],             max_w=208)
        text(62,  91, d["claim"],           max_w=278)
        text(349, 91, d["policy"],          max_w=224)

    elif page_num == 3:
        text(190, 627, d["name"],           max_w=310)
        text(84,  640, d["today"],          max_w=200)

    elif page_num == 4:
        text(106, 148, d["name"],           max_w=170)
        text(324, 148, d["phone"],          max_w=234)
        text(90,  171, d["address"],        max_w=186)
        text(351, 171, d["claim"],          max_w=207)
        text(118, 192, d["city_state_zip"], max_w=158)
        text(312, 192, d["dol"],            max_w=246)
        text(81,  214, d["email"],          max_w=195)
        text(344, 214, d["insurance"],      max_w=214)

    c.save()
    buf.seek(0)
    return buf


def generate_pdf_bytes(item):
    """Genera el PDF en memoria y devuelve los bytes."""
    d = parse_client(item)

    # Leer plantilla PDF desde disco
    template_path = os.path.join(os.path.dirname(__file__), "Mitigation_Contract.pdf")
    reader = PdfReader(template_path)
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        if i in (0, 2, 3):
            page_num = {0: 1, 2: 3, 3: 4}[i]
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


# ─────────────────────────────────────────────────────────────
#  ENDPOINTS
# ─────────────────────────────────────────────────────────────
@app.route("/", methods=["GET"])
def health():
    return jsonify({"status": "ok", "message": "Contract Generator running"}), 200


@app.route("/generate", methods=["POST"])
def generate():
    """
    Recibe el webhook de Monday con el item_id,
    genera el PDF y lo adjunta al item.
    """
    payload = request.get_json(force=True, silent=True) or {}

    # Monday envía un challenge para verificar el webhook
    if "challenge" in payload:
        return jsonify({"challenge": payload["challenge"]}), 200

    # Obtener item_id del payload
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
        # 1. Obtener datos del cliente desde Monday
        item = get_item_data(item_id)
        name = item["name"]
        print(f"Generating contract for: {name}")

        # 2. Generar PDF
        pdf_bytes = generate_pdf_bytes(item)

        # 3. Subir PDF al item de Monday
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
