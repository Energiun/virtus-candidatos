from flask import Flask, request, jsonify, render_template
import requests
import os
from datetime import datetime

app = Flask(__name__)

DESTINATARIO = "ti@virtusexec.com.br"
REMETENTE = os.environ.get("EMAIL_REMETENTE")
SENDGRID_KEY = os.environ.get("SENDGRID_KEY")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")

REGIOES = {
    "campinas": "Campinas OR Valinhos OR Vinhedo OR Indaiatuba OR Sumaré OR Hortolândia OR Americana OR Paulínia OR Jundiaí",
    "valinhos": "Campinas OR Valinhos OR Vinhedo OR Indaiatuba",
    "são paulo": "São Paulo OR SP",
    "default": "{cidade}"
}

def montar_query(cargo, cidade, habilidades, idioma):
    cidade_lower = cidade.lower()
    regiao = REGIOES.get(cidade_lower, REGIOES["default"].format(cidade=cidade))
    query = f'site:linkedin.com/in "{cargo}" ({regiao}) "open to work"'
    if idioma and idioma.lower() not in ["português", "portugues", ""]:
        query += f' "{idioma}"'
    if habilidades:
        primeira = habilidades.split(",")[0].strip()
        if primeira:
            query += f' "{primeira}"'
    return query

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
    enviar_email = data.get("enviar_email", False)

    if not cargo:
        return jsonify({"ok": False, "mensagem": "Cargo obrigatório"}), 400

    query = montar_query(cargo, cidade, habilidades, idioma)
    busca_label = f"{cargo} | {cidade} | {idioma}"

    try:
        resp = requests.get("https://serpapi.com/search", params={
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": 10,
            "hl": "pt",
            "gl": "br"
        }, timeout=25)
        resultados = resp.json().get("organic_results", [])
    except Exception as e:
        return jsonify({"ok": False, "mensagem": f"Erro na busca: {str(e)}"}), 500

    candidatos = []
    for r in resultados:
        if "linkedin.com/in/" in r.get("link", ""):
            candidatos.append({
                "nome": r.get("title", "").split(" - ")[0].strip(),
                "link": r.get("link", ""),
                "resumo": r.get("snippet", "")
            })

    if enviar_email and candidatos:
        linhas = ""
        for i, c in enumerate(candidatos, 1):
            linhas += f"""<tr>
                <td style="padding:10px;border-bottom:1px solid #eee;color:#999;">{i}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;"><strong>{c['nome']}</strong><br>
                <small style="color:#666;">{c['resumo']}</small></td>
                <td style="padding:10px;border-bottom:1px solid #eee;">
                <a href="{c['link']}" style="color:#B22222;font-weight:bold;">Ver perfil →</a></td>
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
            requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
                json={
                    "personalizations": [{"to": [{"email": DESTINATARIO}]}],
                    "from": {"email": REMETENTE},
                    "subject": f"[Virtus Exec] {cargo} | {cidade} — {datetime.now().strftime('%d/%m/%Y')}",
                    "content": [{"type": "text/html", "value": corpo}]
                },
                timeout=15
            )
        except Exception as e:
            print(f"Erro e-mail: {e}")

    return jsonify({
        "ok": True,
        "total": len(candidatos),
        "candidatos": candidatos,
        "email_enviado": enviar_email and len(candidatos) > 0
    })

if __name__ == "__main__":
    app.run(debug=False)
