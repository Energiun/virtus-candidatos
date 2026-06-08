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
    "campinas": "Campinas OR Valinhos OR Vinhedo OR Indaiatuba OR Paulínia OR Sumaré OR Hortolândia OR Americana OR Jundiaí",
    "valinhos": "Campinas OR Valinhos OR Vinhedo OR Indaiatuba OR Paulínia",
    "jundiaí": "Jundiaí OR Itupeva OR Várzea Paulista OR Campo Limpo Paulista",
    "sorocaba": "Sorocaba OR Votorantim OR Itu OR Boituva",
    "ribeirão preto": "Ribeirão Preto OR Sertãozinho OR Jaboticabal",
}

def busca_serpapi(query, start=0):
    try:
        resp = requests.get("https://serpapi.com/search", params={
            "q": query,
            "api_key": SERPAPI_KEY,
            "num": 10,
            "start": start,
            "hl": "pt",
            "gl": "br"
        }, timeout=25)
        return resp.json().get("organic_results", [])
    except:
        return []

def extras_para_query(idioma, habilidades, empresa):
    partes = []
    if idioma:
        i = idioma.lower().strip()
        if i in ["inglês", "ingles", "english"]:
            partes.append('("inglês" OR "english" OR "inglês avançado" OR "inglês fluente")')
        elif i in ["espanhol", "spanish"]:
            partes.append('("espanhol" OR "spanish" OR "espanhol avançado")')
        else:
            partes.append(f'"{idioma}"')
    if habilidades:
        for h in habilidades.split(","):
            h = h.strip()
            if h:
                partes.append(f'"{h}"')
    if empresa:
        partes.append(f'"{empresa.strip()}"')
    return " ".join(partes)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/buscar", methods=["POST"])
def buscar():
    data = request.json
    cargo = data.get("cargo", "").strip()
    cidade = data.get("cidade", "Campinas").strip()
    idioma = data.get("idioma", "").strip()
    habilidades = data.get("habilidades", "").strip()
    empresa = data.get("empresa", "").strip()
    enviar_email = data.get("enviar_email", False)

    if not cargo or not cidade:
        return jsonify({"ok": False, "mensagem": "Cargo e cidade são obrigatórios"}), 400

    cidade_lower = cidade.lower()
    regiao = REGIOES.get(cidade_lower, cidade)
    extras = extras_para_query(idioma, habilidades, empresa)

    # 6 queries cruzadas: cargo com/sem aspas x cidade exata/região/estado
    loc_exata = f'"{cidade}, São Paulo, Brasil"'
    loc_regiao = f'"{cidade} e Região"'
    loc_estado = f'"São Paulo"'
    regiao_or = f'({regiao})'

    queries = [
        # Cargo exato + cidade exata (maior precisão)
        (f'site:linkedin.com/in "{cargo}" {loc_exata} {extras}', 3),
        (f'site:linkedin.com/in "{cargo}" {loc_exata} {extras}', 2, 10),  # página 2
        # Cargo exato + região
        (f'site:linkedin.com/in "{cargo}" {loc_regiao} {extras}', 2),
        # Cargo exato + cidades da região
        (f'site:linkedin.com/in "{cargo}" {regiao_or} {extras}', 2),
        # Cargo sem aspas + cidade exata
        (f'site:linkedin.com/in {cargo} {loc_exata} {extras}', 1),
        # Cargo sem aspas + região
        (f'site:linkedin.com/in {cargo} {regiao_or} {extras}', 1),
    ]

    todos = {}
    for q in queries:
        query_str = q[0].strip()
        score_base = q[1]
        start = q[2] if len(q) > 2 else 0
        resultados = busca_serpapi(query_str, start)
        for r in resultados:
            link = r.get("link", "")
            if "linkedin.com/in/" not in link:
                continue
            titulo = r.get("title", "").lower()
            snippet = r.get("snippet", "").lower()
            cargo_lower = cargo.lower()
            # Cargo DEVE aparecer no título ou snippet
            if cargo_lower not in titulo and cargo_lower not in snippet:
                continue
            # Extras obrigatórios: verifica se aparecem no snippet/título
            passou_extras = True
            if idioma:
                i = idioma.lower()
                termos_idioma = [i, "avançado", "fluente", "advanced", "fluent"]
                if not any(t in titulo + snippet for t in termos_idioma):
                    passou_extras = False
            if habilidades and passou_extras:
                for h in habilidades.split(","):
                    h = h.strip().lower()
                    if h and h not in titulo + snippet:
                        passou_extras = False
                        break
            if empresa and passou_extras:
                if empresa.lower() not in titulo + snippet:
                    passou_extras = False

            if link not in todos:
                todos[link] = {
                    "nome": r.get("title", "").split(" - ")[0].strip(),
                    "link": link,
                    "resumo": r.get("snippet", ""),
                    "score": 0,
                    "extras_ok": passou_extras
                }
            todos[link]["score"] += score_base
            if passou_extras:
                todos[link]["extras_ok"] = True

    # Separa: quem passou nos extras primeiro, depois os demais
    com_extras = [c for c in todos.values() if c["extras_ok"]]
    sem_extras = [c for c in todos.values() if not c["extras_ok"]]

    com_extras.sort(key=lambda x: x["score"], reverse=True)
    sem_extras.sort(key=lambda x: x["score"], reverse=True)

    candidatos = com_extras + sem_extras

    # Remove campos internos
    for c in candidatos:
        del c["score"]
        del c["extras_ok"]

    # Envia e-mail se solicitado
    if enviar_email and candidatos:
        linhas = ""
        for i, c in enumerate(candidatos, 1):
            linhas += f"""<tr>
                <td style="padding:10px;border-bottom:1px solid #eee;color:#999;">{i}</td>
                <td style="padding:10px;border-bottom:1px solid #eee;"><strong>{c['nome']}</strong><br>
                <small style="color:#666;">{c['resumo']}</small></td>
                <td style="padding:10px;border-bottom:1px solid #eee;">
                <a href="{c['link']}" style="color:#1a3a5c;font-weight:bold;">Ver perfil →</a></td>
            </tr>"""
        corpo = f"""<div style="font-family:Arial,sans-serif;max-width:700px;margin:auto;">
          <div style="background:#1a3a5c;padding:20px;border-radius:8px 8px 0 0;">
            <h2 style="color:white;margin:0;">VIRTUS EXEC</h2>
            <p style="color:#a8c4e0;margin:4px 0 0;font-size:13px;">Busca cruzada de candidatos</p>
            <p style="color:#a8c4e0;margin:8px 0 0;">Busca: <strong>{cargo} | {cidade}</strong></p>
            <p style="color:#a8c4e0;margin:2px 0 0;font-size:12px;">{datetime.now().strftime("%d/%m/%Y %H:%M")}</p>
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
