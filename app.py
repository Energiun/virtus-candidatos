from flask import Flask, request, jsonify, render_template
import requests
import os
from datetime import datetime

app = Flask(__name__)

DESTINATARIO = "ti@virtusexec.com.br"
REMETENTE = os.environ.get("EMAIL_REMETENTE")
SENDGRID_KEY = os.environ.get("SENDGRID_KEY")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/buscar", methods=["POST"])
def buscar():
    data = request.json
    cargo = data.get("cargo", "")
    cidade = data.get("cidade", "Campinas")
    habilidades = data.get("habilidades", "")
    idioma = data.get("idioma", "inglês")

    if not cargo:
        return jsonify({"ok": False, "mensagem": "Cargo obrigatório"}), 400

    query = f'site:linkedin.com/in "open to work" "{cidade}" "{cargo}"'
    if idioma:
        query += f' "{idioma}"'
    if habilidades:
        query += f' "{habilidades}"'

    busca_label = f"{cargo} | {cidade} | {idioma}"

    try:
        resp = requests.get("https://serpapi.com/search", params={
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": 5,
            "hl": "pt",
            "gl": "br"
        }, timeout=25)
        resultados = resp.json().get("organic_results", [])
    except Exception as e:
        return jsonify({"ok": False, "mensagem": f"Erro na busca: {str(e)}"}), 500

    candidatos = [r for r in resultados if "linkedin.com/in/" in r.get("link", "")]

    if not candidatos:
        corpo = f"<p>Nenhum candidato encontrado para: <strong>{busca_label}</strong></p>"
    else:
        linhas = ""
        for i, c in enumerate(candidatos, 1):
            nome = c.get("title", "").split(" - ")[0].strip()
            link = c.get("link", "")
            resumo = c.get("snippet", "")
            linhas += f"""<tr>
                <td style="padding:10px;border-bottom:1px solid #eee;">{i}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;"><strong>{nome}</strong><br>
                <small style="color:#666;">{resumo}</small></td>
                <td style="padding:10px;border-bottom:1px solid #eee;">
                <a href="{link}" style="color:#B22222;font-weight:bold;">Ver perfil →</a></td>
            </tr>"""

        corpo = f"""<div style="font-family:Arial,sans-serif;max-width:700px;margin:auto;">
          <div style="background:#B22222;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="color:white;margin:0;">🎯 Virtus Exec — Candidatos Encontrados</h2>
            <p style="color:#ffcccc;margin:5px 0 0;">Busca: <strong>{busca_label}</strong></p>
            <p style="color:#ffcccc;margin:2px 0 0;font-size:12px;">{datetime.now().strftime("%d/%m/%Y %H:%M")}</p>
          </div>
          <table style="width:100%;border-collapse:collapse;background:white;">
            <tr style="background:#f5f5f5;">
              <th style="padding:10px;text-align:left;">#</th>
              <th style="padding:10px;text-align:left;">Candidato</th>
              <th style="padding:10px;text-align:left;">LinkedIn</th>
            </tr>
            {linhas}
          </table>
          <div style="background:#f5f5f5;padding:15px;border-radius:0 0 8px 8px;text-align:center;">
            <small style="color:#999;">Total: {len(candidatos)} candidatos • {datetime.now().strftime("%d/%m/%Y %H:%M")}</small>
          </div>
        </div>"""

    try:
        sg_resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "personalizations": [{"to": [{"email": DESTINATARIO}]}],
                "from": {"email": REMETENTE},
                "subject": f"[Virtus Exec] {cargo} | {cidade} — {datetime.now().strftime('%d/%m/%Y')}",
                "content": [{"type": "text/html", "value": corpo}]
            },
            timeout=15
        )

        if sg_resp.status_code in [200, 202]:
            return jsonify({"ok": True, "total": len(candidatos),
                "mensagem": f"{len(candidatos)} candidatos encontrados! Lista enviada para {DESTINATARIO}."})
        else:
            return jsonify({"ok": False, "mensagem": f"Erro SendGrid: {sg_resp.text}"}), 500

    except Exception as e:
        return jsonify({"ok": False, "mensagem": f"Erro ao enviar: {str(e)}"}), 500

if __name__ == "__main__":
    app.run(debug=False)
