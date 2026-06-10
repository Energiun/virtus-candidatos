from flask import Flask, request, jsonify, render_template
import requests
import os
import time
import unicodedata
from datetime import datetime
from html import escape

app = Flask(__name__)

DESTINATARIO = "ti@virtusexec.com.br"

REMETENTE = os.environ.get("EMAIL_REMETENTE")
SENDGRID_KEY = os.environ.get("SENDGRID_KEY")
APIFY_KEY = os.environ.get("APIFY_KEY")

APIFY_ACTOR = "harvestapi~linkedin-profile-search"

# Enquanto estamos testando, deixei 10 para economizar crédito.
# Depois que funcionar bem, pode subir para 25.
MAX_ITEMS_TESTE = 10

REGIOES = {
    "campinas": [
        "Campinas", "Valinhos", "Vinhedo", "Indaiatuba",
        "Paulínia", "Sumaré", "Hortolândia", "Americana", "Jundiaí"
    ],
    "valinhos": [
        "Valinhos", "Campinas", "Vinhedo", "Indaiatuba", "Paulínia"
    ],
    "paulinia": [
        "Paulínia", "Campinas", "Americana", "Sumaré", "Hortolândia"
    ],
    "paulínia": [
        "Paulínia", "Campinas", "Americana", "Sumaré", "Hortolândia"
    ],
    "jundiai": [
        "Jundiaí", "Itupeva", "Várzea Paulista", "Campo Limpo Paulista"
    ],
    "jundiaí": [
        "Jundiaí", "Itupeva", "Várzea Paulista", "Campo Limpo Paulista"
    ],
    "sorocaba": [
        "Sorocaba", "Votorantim", "Itu", "Boituva"
    ],
    "ribeirao preto": [
        "Ribeirão Preto", "Sertãozinho", "Jaboticabal"
    ],
    "ribeirão preto": [
        "Ribeirão Preto", "Sertãozinho", "Jaboticabal"
    ],
    "sao paulo": [
        "São Paulo"
    ],
    "são paulo": [
        "São Paulo"
    ],
}


def normalizar(texto):
    if texto is None:
        return ""
    texto = str(texto).lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto


def texto_contem(texto, termo):
    texto_n = normalizar(texto)
    termo_n = normalizar(termo)
    return termo_n in texto_n


def pegar_localizacao_texto(perfil):
    """
    Tenta encontrar localização em vários formatos possíveis do Apify.
    """
    location = perfil.get("location", "")

    if isinstance(location, str):
        return location

    if isinstance(location, dict):
        partes = []

        parsed = location.get("parsed", {})
        if isinstance(parsed, dict):
            cidade = parsed.get("city", "")
            estado = parsed.get("state", "")
            pais = parsed.get("country", "")
            partes.extend([cidade, estado, pais])

        for chave in ["linkedinText", "text", "name", "location", "full"]:
            valor = location.get(chave)
            if valor:
                partes.append(str(valor))

        return " ".join([p for p in partes if p])

    for chave in ["locationName", "geoLocationName", "city", "address", "locationText"]:
        valor = perfil.get(chave)
        if valor:
            return str(valor)

    return ""


def pegar_cidade(perfil):
    """
    Tenta encontrar a cidade exata.
    """
    location = perfil.get("location", "")

    if isinstance(location, dict):
        parsed = location.get("parsed", {})
        if isinstance(parsed, dict):
            cidade = parsed.get("city", "")
            if cidade:
                return cidade

        for chave in ["city", "name"]:
            valor = location.get(chave)
            if valor:
                return str(valor)

    for chave in ["city", "locationName", "geoLocationName"]:
        valor = perfil.get(chave)
        if valor:
            texto = str(valor)
            return texto.split(",")[0].strip()

    texto_loc = pegar_localizacao_texto(perfil)
    if texto_loc:
        return texto_loc.split(",")[0].strip()

    return ""


def pegar_cargo_atual(perfil):
    """
    Tenta encontrar o cargo atual em vários formatos.
    """
    cargos = []

    current_position = perfil.get("currentPosition")
    if isinstance(current_position, list):
        for item in current_position:
            if isinstance(item, dict):
                pos = item.get("position") or item.get("title") or item.get("name")
                empresa = item.get("companyName") or item.get("company")
                if pos and empresa:
                    cargos.append(f"{pos} na {empresa}")
                elif pos:
                    cargos.append(str(pos))

    if isinstance(current_position, dict):
        pos = current_position.get("position") or current_position.get("title") or current_position.get("name")
        empresa = current_position.get("companyName") or current_position.get("company")
        if pos and empresa:
            cargos.append(f"{pos} na {empresa}")
        elif pos:
            cargos.append(str(pos))

    for chave in ["headline", "occupation", "title", "position", "jobTitle", "subTitle"]:
        valor = perfil.get(chave)
        if valor:
            cargos.append(str(valor))

    return " | ".join(cargos)


def pegar_empresa_atual(perfil):
    current_position = perfil.get("currentPosition")

    if isinstance(current_position, list) and len(current_position) > 0:
        item = current_position[0]
        if isinstance(item, dict):
            return item.get("companyName", "") or item.get("company", "")

    if isinstance(current_position, dict):
        return current_position.get("companyName", "") or current_position.get("company", "")

    return ""


def pegar_historico_texto(perfil):
    textos = []

    experience = perfil.get("experience", [])
    if isinstance(experience, list):
        for exp in experience:
            if isinstance(exp, dict):
                textos.append(str(exp.get("position", "")))
                textos.append(str(exp.get("title", "")))
                textos.append(str(exp.get("companyName", "")))
                textos.append(str(exp.get("company", "")))
                textos.append(str(exp.get("description", "")))

    for chave in ["about", "summary", "description", "headline"]:
        valor = perfil.get(chave)
        if valor:
            textos.append(str(valor))

    return " ".join(textos)


def palavras_importantes_cargo(cargo):
    stopwords = {
        "de", "da", "do", "das", "dos", "e", "a", "o", "as", "os",
        "em", "para", "com", "na", "no", "jr", "pl", "sr", "senior",
        "junior", "pleno"
    }

    palavras = []
    for p in normalizar(cargo).replace("/", " ").replace("-", " ").split():
        p = p.strip()
        if len(p) >= 3 and p not in stopwords:
            palavras.append(p)

    return palavras


def cargo_compativel(cargo_busca, cargo_atual, historico):
    """
    Regra principal:
    - cargo atual vale mais;
    - histórico também aceita;
    - evita descartar bons casos como "Executivo Comercial", "Representante Comercial de Vendas",
      "Consultor Comercial", etc.
    """
    cargo_n = normalizar(cargo_busca)
    atual_n = normalizar(cargo_atual)
    hist_n = normalizar(historico)

    texto_total = f"{atual_n} {hist_n}"

    # Match direto
    if cargo_n and cargo_n in atual_n:
        return "atual_forte"

    if cargo_n and cargo_n in hist_n:
        return "historico_forte"

    palavras = palavras_importantes_cargo(cargo_busca)
    if not palavras:
        return None

    qtd_atual = sum(1 for p in palavras if p in atual_n)
    qtd_total = sum(1 for p in palavras if p in texto_total)

    # Exemplo: "consultor de vendas"
    # palavras importantes: consultor, vendas
    if len(palavras) == 1:
        if qtd_atual >= 1:
            return "atual_medio"
        if qtd_total >= 1:
            return "historico_medio"

    if len(palavras) >= 2:
        if qtd_atual >= 2:
            return "atual_medio"
        if qtd_total >= 2:
            return "historico_medio"

    # Sinônimos úteis para área comercial/vendas
    cargo_vendas = any(p in cargo_n for p in ["venda", "vendas", "comercial", "consultor", "executivo", "representante"])

    if cargo_vendas:
        termos_vendas = [
            "vendas", "venda", "comercial", "consultor comercial",
            "consultor de vendas", "executivo de vendas",
            "executivo comercial", "representante comercial",
            "key account", "account executive", "sales",
            "business development", "bdr", "sdr"
        ]

        if any(t in atual_n for t in termos_vendas):
            return "atual_relacionado"

        if any(t in texto_total for t in termos_vendas):
            return "historico_relacionado"

    return None


def esta_na_regiao(perfil, cidade, regiao_cidades):
    cidade_perfil = pegar_cidade(perfil)
    localizacao_texto = pegar_localizacao_texto(perfil)

    cidade_n = normalizar(cidade)
    cidade_perfil_n = normalizar(cidade_perfil)
    localizacao_n = normalizar(localizacao_texto)

    regiao_norm = [normalizar(c) for c in regiao_cidades]

    if cidade_n and (cidade_perfil_n == cidade_n or cidade_n in localizacao_n):
        return "cidade_exata"

    for c in regiao_norm:
        if c and (cidade_perfil_n == c or c in localizacao_n):
            return "cidade_regiao"

    return None


def score_candidato(perfil, cargo, cidade, idioma, habilidades, empresa, regiao_cidades):
    score = 0
    motivos = []

    # 1. Região é obrigatória
    match_regiao = esta_na_regiao(perfil, cidade, regiao_cidades)

    if match_regiao == "cidade_exata":
        score += 40
        motivos.append("cidade exata")
    elif match_regiao == "cidade_regiao":
        score += 22
        motivos.append("cidade da região")
    else:
        return None, []

    # 2. Cargo é obrigatório
    cargo_atual = pegar_cargo_atual(perfil)
    historico = pegar_historico_texto(perfil)

    match_cargo = cargo_compativel(cargo, cargo_atual, historico)

    if match_cargo == "atual_forte":
        score += 45
        motivos.append("cargo atual exato")
    elif match_cargo == "atual_medio":
        score += 35
        motivos.append("cargo atual compatível")
    elif match_cargo == "atual_relacionado":
        score += 25
        motivos.append("cargo atual relacionado")
    elif match_cargo == "historico_forte":
        score += 25
        motivos.append("cargo no histórico")
    elif match_cargo == "historico_medio":
        score += 18
        motivos.append("histórico compatível")
    elif match_cargo == "historico_relacionado":
        score += 12
        motivos.append("histórico relacionado")
    else:
        return None, []

    # 3. Open to Work
    if perfil.get("openToWork"):
        score += 10
        motivos.append("open to work")

    # 4. Idioma
    if idioma:
        idioma_n = normalizar(idioma)
        idiomas = perfil.get("languages", [])

        if isinstance(idiomas, list):
            for lang in idiomas:
                if isinstance(lang, dict):
                    nome = normalizar(lang.get("name", ""))
                    nivel = normalizar(lang.get("proficiency", ""))

                    bate_idioma = idioma_n in nome
                    if "ingles" in idioma_n and "english" in nome:
                        bate_idioma = True

                    if bate_idioma:
                        if any(x in nivel for x in [
                            "advanced", "fluent", "native", "bilingual",
                            "professional", "avancado", "fluente", "nativo"
                        ]):
                            score += 15
                            motivos.append("idioma avançado")
                        else:
                            score += 6
                            motivos.append("idioma informado")
                        break

        # Alguns perfis podem trazer idioma no resumo/texto
        texto_total = normalizar(f"{cargo_atual} {historico}")
        if "idioma" not in " ".join(motivos):
            if "ingles" in idioma_n and ("ingles" in texto_total or "english" in texto_total):
                score += 6
                motivos.append("idioma citado")

    # 5. Habilidades
    if habilidades:
        skills_texto = ""

        skills = perfil.get("skills", [])
        if isinstance(skills, list):
            for s in skills:
                if isinstance(s, dict):
                    skills_texto += " " + str(s.get("name", ""))
                else:
                    skills_texto += " " + str(s)

        texto_total = normalizar(f"{skills_texto} {cargo_atual} {historico}")

        for hab in habilidades.split(","):
            hab = hab.strip()
            if not hab:
                continue

            hab_n = normalizar(hab)
            if hab_n and hab_n in texto_total:
                score += 8
                motivos.append(f"skill: {hab}")

    # 6. Empresa passada ou atual
    if empresa:
        empresa_n = normalizar(empresa)
        texto_total = normalizar(f"{pegar_empresa_atual(perfil)} {historico}")

        if empresa_n and empresa_n in texto_total:
            score += 20
            motivos.append(f"empresa: {empresa}")

    return score, motivos


def montar_apify_input(cargo, cidade, regiao_cidades):
    """
    Input para o actor do Apify.
    Importante:
    - empresa não entra aqui, porque empresa é extra de ranking.
    - se colocar empresa aqui, a busca fica muito fechada e pode voltar 0.
    """
    return {
        "search": cargo,
        "locations": regiao_cidades,
        "profileScraperMode": "Full",
        "maxItems": MAX_ITEMS_TESTE
    }


def iniciar_run_apify(apify_input):
    url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/runs"

    headers = {
        "Authorization": f"Bearer {APIFY_KEY}",
        "Content-Type": "application/json"
    }

    # Importante: o input vai direto no body.
    # Não enviar {"input": apify_input}
    resp = requests.post(
        url,
        headers=headers,
        json=apify_input,
        timeout=30
    )

    try:
        data = resp.json()
    except Exception:
        data = {"raw": resp.text}

    print("========== APIFY START ==========")
    print("STATUS CODE:", resp.status_code)
    print("INPUT ENVIADO:", apify_input)
    print("RESPOSTA START:", data)
    print("========== FIM APIFY START ==========")

    if resp.status_code >= 400:
        raise Exception(f"Erro Apify HTTP {resp.status_code}: {data}")

    run_id = data.get("data", {}).get("id")
    if not run_id:
        raise Exception(f"Apify não retornou run_id: {data}")

    return run_id


def aguardar_run_apify(run_id):
    headers = {"Authorization": f"Bearer {APIFY_KEY}"}

    status_final = None
    run_data_final = None

    for tentativa in range(24):
        time.sleep(5)

        resp = requests.get(
            f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/runs/{run_id}",
            headers=headers,
            timeout=30
        )

        data = resp.json()
        run_data = data.get("data", {})
        status = run_data.get("status", "")

        print(f"APIFY STATUS tentativa {tentativa + 1}: {status}")

        status_final = status
        run_data_final = run_data

        if status == "SUCCEEDED":
            return run_data

        if status in ["FAILED", "ABORTED", "TIMED-OUT"]:
            raise Exception(f"Busca falhou no Apify: {status}")

    raise Exception(f"Busca demorou demais no Apify. Último status: {status_final}")


def buscar_resultados_apify(dataset_id):
    headers = {"Authorization": f"Bearer {APIFY_KEY}"}

    resp = requests.get(
        f"https://api.apify.com/v2/datasets/{dataset_id}/items",
        headers=headers,
        timeout=60
    )

    try:
        data = resp.json()
    except Exception:
        raise Exception(f"Erro ao ler JSON do dataset: {resp.text}")

    print("========== APIFY RESULTADOS ==========")
    print("STATUS CODE:", resp.status_code)
    print("TIPO:", type(data))
    print("TOTAL RAW:", len(data) if isinstance(data, list) else "não é lista")

    if isinstance(data, list) and len(data) > 0:
        primeiro = data[0]
        if isinstance(primeiro, dict):
            print("CHAVES PRIMEIRO PERFIL:", list(primeiro.keys()))
            print("PRIMEIRO PERFIL:", primeiro)
    else:
        print("RETORNO DATASET:", data)

    print("========== FIM APIFY RESULTADOS ==========")

    if not isinstance(data, list):
        return []

    return data


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/buscar", methods=["POST"])
def buscar():
    try:
        data = request.json or {}

        cargo = data.get("cargo", "").strip()
        cidade = data.get("cidade", "Campinas").strip()
        idioma = data.get("idioma", "").strip()
        habilidades = data.get("habilidades", "").strip()
        empresa = data.get("empresa", "").strip()
        enviar_email = data.get("enviar_email", False)

        if not cargo or not cidade:
            return jsonify({
                "ok": False,
                "mensagem": "Cargo e cidade são obrigatórios."
            }), 400

        if not APIFY_KEY:
            return jsonify({
                "ok": False,
                "mensagem": "APIFY_KEY não encontrada no Render."
            }), 500

        cidade_lower = normalizar(cidade)
        regiao_cidades = REGIOES.get(cidade_lower, [cidade])

        apify_input = montar_apify_input(cargo, cidade, regiao_cidades)

        run_id = iniciar_run_apify(apify_input)
        run_data = aguardar_run_apify(run_id)

        dataset_id = run_data.get("defaultDatasetId")
        if not dataset_id:
            return jsonify({
                "ok": False,
                "mensagem": "Apify terminou, mas não retornou dataset."
            }), 500

        perfis = buscar_resultados_apify(dataset_id)

        candidatos_com_score = []

        for perfil in perfis:
            if not isinstance(perfil, dict):
                continue

            score, motivos = score_candidato(
                perfil,
                cargo,
                cidade,
                idioma,
                habilidades,
                empresa,
                regiao_cidades
            )

            if score is None:
                continue

            first = perfil.get("firstName", "") or perfil.get("first_name", "")
            last = perfil.get("lastName", "") or perfil.get("last_name", "")
            nome = f"{first} {last}".strip()

            if not nome:
                nome = perfil.get("fullName", "") or perfil.get("name", "") or "Nome não informado"

            cargo_atual = pegar_cargo_atual(perfil)
            cidade_display = pegar_localizacao_texto(perfil)

            link = (
                perfil.get("linkedinUrl", "")
                or perfil.get("url", "")
                or perfil.get("profileUrl", "")
                or perfil.get("linkedin", "")
            )

            resumo = perfil.get("about", "") or perfil.get("summary", "") or perfil.get("description", "")

            candidatos_com_score.append({
                "nome": nome,
                "link": link,
                "cargo_atual": cargo_atual,
                "cidade": cidade_display,
                "open_to_work": perfil.get("openToWork", False),
                "score": score,
                "motivos": motivos,
                "resumo": resumo[:200] if resumo else ""
            })

        candidatos_com_score.sort(key=lambda x: x["score"], reverse=True)

        print("========== RESULTADO FINAL ==========")
        print("CARGO:", cargo)
        print("CIDADE:", cidade)
        print("RAW PERFIS:", len(perfis))
        print("APROVADOS FILTRO:", len(candidatos_com_score))
        for c in candidatos_com_score[:5]:
            print(c["nome"], c["score"], c["motivos"])
        print("========== FIM RESULTADO FINAL ==========")

        if enviar_email and candidatos_com_score:
            enviar_email_resultado(candidatos_com_score, cargo, cidade)

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

    except Exception as e:
        print("ERRO GERAL:", str(e))
        return jsonify({
            "ok": False,
            "mensagem": str(e)
        }), 500


def enviar_email_resultado(candidatos_com_score, cargo, cidade):
    if not SENDGRID_KEY:
        print("SENDGRID_KEY não configurada. E-mail não enviado.")
        return

    if not REMETENTE:
        print("EMAIL_REMETENTE não configurado. E-mail não enviado.")
        return

    linhas = ""

    for i, c in enumerate(candidatos_com_score, 1):
        nome = escape(c["nome"])
        cargo_atual = escape(c["cargo_atual"] or "—")
        cidade_txt = escape(c["cidade"] or "—")
        link = escape(c["link"] or "#")

        badge_otw = ""
        if c["open_to_work"]:
            badge_otw = '<span style="background:#22c55e;color:white;padding:2px 8px;border-radius:20px;font-size:11px;margin-left:6px;">Open to Work</span>'

        tags = "".join([
            f'<span style="background:#e8eef5;color:#1a3a5c;padding:2px 8px;border-radius:20px;font-size:11px;margin:2px;">{escape(m)}</span>'
            for m in c["motivos"]
        ])

        linhas += f"""
        <tr>
            <td style="padding:12px;border-bottom:1px solid #eee;color:#999;font-weight:700;">{i}</td>
            <td style="padding:12px;border-bottom:1px solid #eee;">
                <strong style="font-size:15px;">{nome}</strong>{badge_otw}<br>
                <span style="color:#555;font-size:13px;">{cargo_atual}</span><br>
                <span style="color:#888;font-size:12px;">📍 {cidade_txt}</span><br>
                <div style="margin-top:4px;">{tags}</div>
            </td>
            <td style="padding:12px;border-bottom:1px solid #eee;text-align:right;">
                <span style="background:#1a3a5c;color:white;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;">{c['score']} pts</span><br><br>
                <a href="{link}" style="color:#1a3a5c;font-weight:bold;font-size:13px;">Ver perfil →</a>
            </td>
        </tr>
        """

    corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:750px;margin:auto;">
      <div style="background:#1a3a5c;padding:24px;border-radius:12px 12px 0 0;">
        <h2 style="color:white;margin:0;font-family:Arial;letter-spacing:2px;">VIRTUS EXEC</h2>
        <p style="color:#a8c4e0;margin:4px 0 0;font-size:12px;letter-spacing:1px;">BUSCA INTELIGENTE DE CANDIDATOS</p>
        <p style="color:#a8c4e0;margin:12px 0 0;">Busca: <strong>{escape(cargo)} | {escape(cidade)}</strong></p>
        <p style="color:#a8c4e0;margin:2px 0 0;font-size:11px;">{datetime.now().strftime("%d/%m/%Y %H:%M")} • {len(candidatos_com_score)} candidatos ranqueados</p>
      </div>

      <table style="width:100%;border-collapse:collapse;background:white;">
        <tr style="background:#f5f7fa;">
          <th style="padding:10px;text-align:left;width:30px;">#</th>
          <th style="padding:10px;text-align:left;">Candidato</th>
          <th style="padding:10px;text-align:right;width:100px;">Score</th>
        </tr>
        {linhas}
      </table>

      <div style="background:#f5f7fa;padding:15px;border-radius:0 0 12px 12px;text-align:center;">
        <small style="color:#999;">Ranqueado por: cidade/região + cargo + open to work + idioma + habilidades + empresa • {datetime.now().strftime("%d/%m/%Y %H:%M")}</small>
      </div>
    </div>
    """

    try:
        resp = requests.post(
            "https://api.sendgrid.com/v3/mail/send",
            headers={
                "Authorization": f"Bearer {SENDGRID_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "personalizations": [{"to": [{"email": DESTINATARIO}]}],
                "from": {"email": REMETENTE},
                "subject": f"[Virtus Exec] {cargo} | {cidade} — {len(candidatos_com_score)} candidatos • {datetime.now().strftime('%d/%m/%Y')}",
                "content": [{"type": "text/html", "value": corpo}]
            },
            timeout=20
        )

        print("SENDGRID STATUS:", resp.status_code)
        if resp.status_code >= 400:
            print("SENDGRID ERRO:", resp.text)

    except Exception as e:
        print(f"Erro e-mail: {e}")


if __name__ == "__main__":
    app.run(debug=False)
