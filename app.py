from flask import Flask, request, jsonify, render_template
import requests
import os
from datetime import datetime

app = Flask(__name__)

DESTINATARIO = "ti@virtusexec.com.br"
REMETENTE = os.environ.get("EMAIL_REMETENTE")
SENDGRID_KEY = os.environ.get("SENDGRID_KEY")
SERPAPI_KEY = os.environ.get("SERPAPI_KEY")

def busca_serpapi(query):
    try:
        resp = requests.get("https://serpapi.com/search", params={
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": 10,
            "hl": "pt",
            "gl": "br"
        }, timeout=25)
        return resp.json().get("organic_results", [])
    except:
        return []

def montar_candidatos(resultados, cargo_lower):
    candidatos = {}
    for r in resultados:
        link = r.get("link", "")
        titulo = r.get("title", "").lower()
        snippet = r.get("snippet", "").lower()
        # Só aceita se for perfil LinkedIn E cargo aparecer no título ou snippet
        if "linkedin.com/in/" in link and cargo_lower in titulo or cargo_lower in snippet:
            if link not in candidatos:
                candidatos[link] = {
                    "nome": r.get("title", "").split(" - ")[0].strip(),
                    "link": link,
                    "resumo": r.get("snippet", ""),
                    "titulo": r.get("title", "")
                }
    return candidatos

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/buscar", methods=["POST"])
def buscar():
    data = request.json
    cargo = data.get("cargo", "").strip()
    cidade = data.get("cidade", "Campinas").strip()
    habilidades = data.get("habilidades", "").strip()
    idioma = data.get("idioma", "").strip()
    enviar_email = data.get("enviar_email", False)

    if not cargo:
        return jsonify({"ok": False, "mensagem": "Cargo obrigatório"}), 400

    cargo_lower = cargo.lower()

    # Localização exata como o LinkedIn gera nos perfis
    loc1 = f'"{cidade}, São Paulo, Brasil"'
    loc2 = f'"{cidade} e Região"'

    # Busca 1: cargo exato + localização formato 1
    q1 = f'site:linkedin.com/in "{cargo}" {loc1}'

    # Busca 2: cargo exato + localização formato 2
    q2 = f'site:linkedin.com/in "{cargo}" {loc2}'

    # Busca 3: variação do cargo (sem aspas) + localização 1 — pega variações do título
    q3 = f'site:linkedin.com/in {cargo} {loc1}'

    # Executa as 3 buscas
    r1 = busca_serpapi(q1)
    r2 = busca_serpapi(q2)
    r3 = busca_serpapi(q3)

    # Cruza e deduplica — prioriza quem apareceu em mais de uma busca
    todos = {}
    for r in r1:
        link = r.get("link", "")
        if "linkedin.com/in/" in link:
            todos[link] = {"data": r, "score": todos.get(link, {}).get("score", 0) + 2}

    for r in r2:
        link = r.get("link", "")
        if "linkedin.com/in/" in link:
            if link in todos:
                todos[link]["score"] += 2
            else:
                todos[link] = {"data": r, "score": 2}

    for r in r3:
        link = r.get("link", "")
        if "linkedin.com/in/" in link:
            if link in todos:
                todos[link]["score"] += 1
            else:
                todos[link] = {"data": r, "score": 1}

    # Filtra por cargo no título ou snippet e ordena por score
    candidatos = []
    for link, item in todos.items():
        r = item["data"]
        titulo = r.get("title", "").lower()
        snippet = r.get("snippet", "").lower()
        if cargo_lower in titulo or cargo_lower in snippet:
            candidatos.append({
                "nome": r.get("title", "").split(" - ")[0].strip(),
                "link": link,
                "resumo": r.get("snippet", ""),
                "score": item["score"]
            })

    # Ordena: quem apareceu em mais buscas primeiro
    candidatos.sort(key=lambda x: x["score"], reverse=True)

    # Remove o score da resposta final
    for c in candidatos:
        del c["score"]

    # Envia e-mail se solicitado
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
            <p style="color:#ffcccc;margin:5px 0 0;">Busca: <strong>{cargo} | {cidade}</strong></p>
            <p style="color:#ffcccc;margin:2px 0 0;font-size:12px;">{datetime.now().strftime("%d/%m/%Y %H:%M")}</p>
          </div>
          <table style="width:100%;border-collapse:collapse;background:white;">
            <tr style="background:#f5f5f5;">
              <th style="padding:10px;text-align:left;">#</th>
              <th style="padding:10px;text-align:left;">Candidato</th>
              <th style="padding:10px;text-align:left;">LinkedIn</th>
            </tr>{linhas}
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
