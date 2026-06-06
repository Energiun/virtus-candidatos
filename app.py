from flask import Flask, request, jsonify, render_template
import smtplib
import requests
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

app = Flask(__name__)

DESTINATARIO = "ti@virtusexec.com.br"
REMETENTE = os.environ.get("EMAIL_REMETENTE")
SENHA_EMAIL = os.environ.get("EMAIL_SENHA")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")

def buscar_candidatos(cargo, cidade, habilidades, idioma):
    query = f'site:linkedin.com/in "open to work" "{cidade}" "{cargo}"'
    if habilidades:
        query += f' "{habilidades}"'
    if idioma:
        query += f' "{idioma}"'

    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "api_key": SERPAPI_KEY,
        "num": 5,
        "hl": "pt",
        "gl": "br"
    }

    resp = requests.get(url, params=params)
    data = resp.json()

    candidatos = []
    for r in data.get("organic_results", []):
        link = r.get("link", "")
        titulo = r.get("title", "")
        snippet = r.get("snippet", "")
        if "linkedin.com/in/" in link:
            candidatos.append({
                "nome": titulo.split(" - ")[0].strip(),
                "link": link,
                "resumo": snippet
            })

    return candidatos

def enviar_email(candidatos, busca):
    if not candidatos:
        corpo = f"<p>Nenhum candidato encontrado para a busca: <strong>{busca}</strong></p>"
    else:
        linhas = ""
        for i, c in enumerate(candidatos, 1):
            linhas += f"""
            <tr>
                <td style="padding:10px;border-bottom:1px solid #eee;">{i}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;"><strong>{c['nome']}</strong><br>
                <small style="color:#666;">{c['resumo']}</small></td>
                <td style="padding:10px;border-bottom:1px solid #eee;">
                <a href="{c['link']}" style="color:#B22222;font-weight:bold;">Ver perfil →</a></td>
            </tr>"""

        corpo = f"""
        <div style="font-family:Arial,sans-serif;max-width:700px;margin:auto;">
          <div style="background:#B22222;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="color:white;margin:0;">🎯 Virtus Exec — Candidatos Encontrados</h2>
            <p style="color:#ffcccc;margin:5px 0 0;">Busca: <strong>{busca}</strong></p>
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
            <small style="color:#999;">Total: {len(candidatos)} candidatos encontrados</small>
          </div>
        </div>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[Virtus Exec] Candidatos: {busca} — {datetime.now().strftime('%d/%m/%Y')}"
    msg["From"] = REMETENTE
    msg["To"] = DESTINATARIO
    msg.attach(MIMEText(corpo, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(REMETENTE, SENHA_EMAIL)
        smtp.sendmail(REMETENTE, DESTINATARIO, msg.as_string())

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

    busca_label = f"{cargo} | {cidade} | {idioma}"
    if habilidades:
        busca_label += f" | {habilidades}"

    try:
        candidatos = buscar_candidatos(cargo, cidade, habilidades, idioma)
        enviar_email(candidatos, busca_label)
        return jsonify({"ok": True, "total": len(candidatos), "mensagem": f"{len(candidatos)} candidatos encontrados e enviados para {DESTINATARIO}"})
    except Exception as e:
        return jsonify({"ok": False, "mensagem": str(e)}), 500

if __name__ == "__main__":
    app.run(debug=False)
