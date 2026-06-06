from flask import Flask, request, jsonify, render_template
import smtplib
import requests
import os
import threading
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime
 
app = Flask(__name__)
 
DESTINATARIO = "ti@virtusexec.com.br"
REMETENTE = os.environ.get("EMAIL_REMETENTE")
SENHA_EMAIL = os.environ.get("EMAIL_SENHA")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")
 
def buscar_e_enviar(cargo, cidade, habilidades, idioma):
    query = f'site:linkedin.com/in "open to work" "{cidade}" "{cargo}"'
    if habilidades:
        query += f' "{habilidades}"'
    if idioma:
        query += f' "{idioma}"'
 
    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "api_key": SERPAPI_KEY,
        "num": 10,
        "hl": "pt",
        "gl": "br"
    }
 
    try:
        resp = requests.get(url, params=params, timeout=30)
        data = resp.json()
    except Exception as e:
        print(f"Erro SerpAPI: {e}")
        return
 
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
 
    busca_label = f"{cargo} | {cidade} | {idioma}"
    if habilidades:
        busca_label += f" | {habilidades}"
 
    if not candidatos:
        corpo = f"<p>Nenhum candidato encontrado para: <strong>{busca_label}</strong></p>"
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
            <small style="color:#999;">Total: {len(candidatos)} candidatos encontrados</small>
          </div>
        </div>"""
 
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"[Virtus Exec] Candidatos: {busca_label} — {datetime.now().strftime('%d/%m/%Y')}"
        msg["From"] = REMETENTE
        msg["To"] = DESTINATARIO
        msg.attach(MIMEText(corpo, "html"))
 
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(REMETENTE, SENHA_EMAIL)
            smtp.sendmail(REMETENTE, DESTINATARIO, msg.as_string())
        print(f"E-mail enviado! {len(candidatos)} candidatos.")
    except Exception as e:
        print(f"Erro e-mail: {e}")
 
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
 
    # Roda em background para não dar timeout
    t = threading.Thread(target=buscar_e_enviar, args=(cargo, cidade, habilidades, idioma))
    t.daemon = True
    t.start()
 
    return jsonify({
        "ok": True,
        "total": "?",
        "mensagem": f"Busca iniciada! Em até 1 minuto a lista chegará em {DESTINATARIO}. Pode fechar essa tela."
    })
 
if __name__ == "__main__":
    app.run(debug=False)
