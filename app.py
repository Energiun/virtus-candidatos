from flask import Flask, request, jsonify, render_template
import requests
import os
from datetime import datetime

app = Flask(__name__)

DESTINATARIO = "ti@virtusexec.com.br"
REMETENTE = os.environ.get("EMAIL_REMETENTE")
SENDGRID_KEY = os.environ.get("SENDGRID_KEY")
APIFY_KEY = os.environ.get("APIFY_KEY")

REGIOES = {
    "campinas":      ["Campinas", "Valinhos", "Vinhedo", "Indaiatuba", "Paulínia", "Sumaré", "Hortolândia", "Americana", "Jundiaí"],
    "valinhos":      ["Valinhos", "Campinas", "Vinhedo", "Indaiatuba", "Paulínia"],
    "paulínia":      ["Paulínia", "Campinas", "Americana", "Sumaré", "Hortolândia"],
    "paulinia":      ["Paulínia", "Campinas", "Americana", "Sumaré", "Hortolândia"],
    "jundiaí":       ["Jundiaí", "Itupeva", "Várzea Paulista", "Campo Limpo Paulista"],
    "sorocaba":      ["Sorocaba", "Votorantim", "Itu", "Boituva"],
    "ribeirão preto":["Ribeirão Preto", "Sertãozinho", "Jaboticabal"],
    "são paulo":     ["São Paulo"],
}

def score_candidato(perfil, cargo, cidade, idioma, habilidades, empresa, regiao_cidades):
    score = 0
    motivos = []

    # Cidade (peso máximo)
    city = ""
    try:
        city = perfil.get("location", {}).get("parsed", {}).get("city", "").lower()
    except:
        pass

    cidade_lower = cidade.lower()
    if city == cidade_lower:
        score += 30
        motivos.append("cidade exata")
    elif city in [c.lower() for c in regiao_cidades]:
        score += 15
        motivos.append("cidade da região")
    else:
        return None, []  # Fora da região = descarta

    # Cargo atual
    cargo_lower = cargo.lower()
    cargo_atual = ""
    try:
        cargo_atual = perfil.get("currentPosition", [{}])[0].get("position", "").lower()
    except:
        pass

    if cargo_lower in cargo_atual:
        score += 25
        motivos.append("cargo atual exato")
    else:
        # Verifica histórico
        achou_cargo = False
        for exp in perfil.get("experience", []):
            pos = exp.get("position", "").lower()
            if cargo_lower in pos:
                score += 10
                motivos.append("cargo no histórico")
                achou_cargo = True
                break
        if not achou_cargo:
            score -= 5

    # Open to work
    if perfil.get("openToWork"):
        score += 10
        motivos.append("open to work")

    # Idioma
    if idioma:
        idioma_lower = idioma.lower()
        for lang in perfil.get("languages", []):
            nome = lang.get("name", "").lower()
            nivel = lang.get("proficiency", "").lower()
            if idioma_lower in nome or ("inglês" in idioma_lower and "english" in nome):
                if any(x in nivel for x in ["advanced", "fluent", "avançado", "fluente", "native", "bilingual", "professional"]):
                    score += 15
                    motivos.append("idioma avançado")
                else:
                    score += 5
                    motivos.append("idioma básico")
                break

    # Habilidades
    if habilidades:
        skills_perfil = [s.get("name", "").lower() for s in perfil.get("skills", [])]
        texto_exp = " ".join([
            (e.get("description") or "") + " " + e.get("position", "")
            for e in perfil.get("experience", [])
        ]).lower()

        for hab in habilidades.split(","):
            hab = hab.strip().lower()
            if hab and (hab in skills_perfil or hab in texto_exp):
                score += 8
                motivos.append(f"skill: {hab}")

    # Empresa passada
    if empresa:
        empresa_lower = empresa.strip().lower()
        for exp in perfil.get("experience", []):
            company = exp.get("companyName", "").lower()
            if empresa_lower in company:
                score += 20
                motivos.append(f"trabalhou na {empresa}")
                break

    return score, motivos


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/buscar", methods=["POST"])
def buscar():
    data = request.json
    cargo     = data.get("cargo", "").strip()
    cidade    = data.get("cidade", "Campinas").strip()
    idioma    = data.get("idioma", "").strip()
    habilidades = data.get("habilidades", "").strip()
    empresa   = data.get("empresa", "").strip()
    enviar_email = data.get("enviar_email", False)

    if not cargo or not cidade:
        return jsonify({"ok": False, "mensagem": "Cargo e cidade são obrigatórios"}), 400

    cidade_lower = cidade.lower()
    regiao_cidades = REGIOES.get(cidade_lower, [cidade])

    # Monta input para o Apify
    apify_input = {
        "search": cargo,
        "locations": [cidade] + regiao_cidades[1:3],  # cidade + 2 vizinhas
        "profileScraperMode": "Full ($0.1 per search page + $0.004 per full profile)",
        "maxItems": 25
    }

    if empresa:
        apify_input["pastCompanies"] = [empresa]

    # Chama o Apify
    try:
        run_resp = requests.post(
            "https://api.apify.com/v2/acts/harvestapi~linkedin-profile-search/runs",
            headers={
                "Authorization": f"Bearer {APIFY_KEY}",
                "Content-Type": "application/json"
            },
            json={"input": apify_input},
            timeout=10
        )
        run_data = run_resp.json()
        run_id = run_data.get("data", {}).get("id")
        if not run_id:
            return jsonify({"ok": False, "mensagem": f"Erro ao iniciar busca no Apify: {run_data}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "mensagem": f"Erro ao conectar ao Apify: {str(e)}"}), 500

    # Aguarda conclusão (max 90s)
    import time
    for _ in range(18):
        time.sleep(5)
        status_resp = requests.get(
            f"https://api.apify.com/v2/acts/harvestapi~linkedin-profile-search/runs/{run_id}",
            headers={"Authorization": f"Bearer {APIFY_KEY}"}
        )
        status = status_resp.json().get("data", {}).get("status", "")
        if status == "SUCCEEDED":
            break
        if status in ["FAILED", "ABORTED", "TIMED-OUT"]:
            return jsonify({"ok": False, "mensagem": f"Busca falhou no Apify: {status}"}), 500

    # Busca resultados
    try:
        dataset_id = status_resp.json().get("data", {}).get("defaultDatasetId")
        results_resp = requests.get(
            f"https://api.apify.com/v2/datasets/{dataset_id}/items",
            headers={"Authorization": f"Bearer {APIFY_KEY}"},
            timeout=30
        )
        perfis = results_resp.json()
    except Exception as e:
        return jsonify({"ok": False, "mensagem": f"Erro ao buscar resultados: {str(e)}"}), 500

    # Pontua e filtra candidatos
    candidatos_com_score = []
    for perfil in perfis:
        score, motivos = score_candidato(perfil, cargo, cidade, idioma, habilidades, empresa, regiao_cidades)
        if score is None:
            continue

        nome = f"{perfil.get('firstName', '')} {perfil.get('lastName', '')}".strip()
        cargo_atual = ""
        try:
            cargo_atual = perfil.get("currentPosition", [{}])[0].get("position", "")
            empresa_atual = perfil.get("currentPosition", [{}])[0].get("companyName", "")
            cargo_atual = f"{cargo_atual} @ {empresa_atual}" if empresa_atual else cargo_atual
        except:
            pass

        city_display = ""
        try:
            city_display = perfil.get("location", {}).get("linkedinText", "")
        except:
            pass

        candidatos_com_score.append({
            "nome": nome,
            "link": perfil.get("linkedinUrl", ""),
            "cargo_atual": cargo_atual,
            "cidade": city_display,
            "open_to_work": perfil.get("openToWork", False),
            "score": score,
            "motivos": motivos,
            "resumo": perfil.get("about", "")[:200] if perfil.get("about") else ""
        })

    # Ordena por score
    candidatos_com_score.sort(key=lambda x: x["score"], reverse=True)

    # Envia e-mail se solicitado
    if enviar_email and candidatos_com_score:
        linhas = ""
        for i, c in enumerate(candidatos_com_score, 1):
            badge_otw = '<span style="background:#22c55e;color:white;padding:2px 8px;border-radius:20px;font-size:11px;margin-left:6px;">Open to Work</span>' if c["open_to_work"] else ""
            tags = "".join([f'<span style="background:#e8eef5;color:#1a3a5c;padding:2px 8px;border-radius:20px;font-size:11px;margin:2px;">{m}</span>' for m in c["motivos"]])
            linhas += f"""<tr>
                <td style="padding:12px;border-bottom:1px solid #eee;color:#999;font-weight:700;">{i}</td>
                <td style="padding:12px;border-bottom:1px solid #eee;">
                    <strong style="font-size:15px;">{c['nome']}</strong>{badge_otw}<br>
                    <span style="color:#555;font-size:13px;">{c['cargo_atual']}</span><br>
                    <span style="color:#888;font-size:12px;">📍 {c['cidade']}</span><br>
                    <div style="margin-top:4px;">{tags}</div>
                </td>
                <td style="padding:12px;border-bottom:1px solid #eee;text-align:right;">
                    <span style="background:#1a3a5c;color:white;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;">{c['score']} pts</span><br><br>
                    <a href="{c['link']}" style="color:#1a3a5c;font-weight:bold;font-size:13px;">Ver perfil →</a>
                </td>
            </tr>"""

        corpo = f"""<div style="font-family:Arial,sans-serif;max-width:750px;margin:auto;">
          <div style="background:#1a3a5c;padding:24px;border-radius:12px 12px 0 0;">
            <h2 style="color:white;margin:0;font-family:Arial;letter-spacing:2px;">VIRTUS EXEC</h2>
            <p style="color:#a8c4e0;margin:4px 0 0;font-size:12px;letter-spacing:1px;">BUSCA INTELIGENTE DE CANDIDATOS</p>
            <p style="color:#a8c4e0;margin:12px 0 0;">Busca: <strong>{cargo} | {cidade}</strong></p>
            <p style="color:#a8c4e0;margin:2px 0 0;font-size:11px;">{datetime.now().strftime("%d/%m/%Y %H:%M")} • {len(candidatos_com_score)} candidatos ranqueados</p>
          </div>
          <table style="width:100%;border-collapse:collapse;background:white;">
            <tr style="background:#f5f7fa;">
              <th style="padding:10px;text-align:left;width:30px;">#</th>
              <th style="padding:10px;text-align:left;">Candidato</th>
              <th style="padding:10px;text-align:right;width:100px;">Score</th>
            </tr>{linhas}
          </table>
          <div style="background:#f5f7fa;padding:15px;border-radius:0 0 12px 12px;text-align:center;">
            <small style="color:#999;">Ranqueado por: cidade + cargo + open to work + idioma + habilidades • {datetime.now().strftime("%d/%m/%Y %H:%M")}</small>
          </div>
        </div>"""

        try:
            requests.post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {SENDGRID_KEY}", "Content-Type": "application/json"},
                json={
                    "personalizations": [{"to": [{"email": DESTINATARIO}]}],
                    "from": {"email": REMETENTE},
                    "subject": f"[Virtus Exec] {cargo} | {cidade} — {len(candidatos_com_score)} candidatos • {datetime.now().strftime('%d/%m/%Y')}",
                    "content": [{"type": "text/html", "value": corpo}]
                },
                timeout=15
            )
        except Exception as e:
            print(f"Erro e-mail: {e}")

    # Remove score interno da resposta
    resultado_final = []
    for c in candidatos_com_score:
        resultado_final.append({
            "nome": c["nome"],
            "link": c["link"],
            "cargo_atual": c["cargo_atual"],
            "cidade": c["cidade"],
            "open_to_work": c["open_to_work"],
            "score": c["score"],
            "motivos": c["motivos"],
            "resumo": c["resumo"]
        })

    return jsonify({
        "ok": True,
        "total": len(resultado_final),
        "candidatos": resultado_final,
        "email_enviado": enviar_email and len(resultado_final) > 0
    })


if __name__ == "__main__":
    app.run(debug=False)
