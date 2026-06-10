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

# Teste econômico. Depois que funcionar, pode subir para 25.
MAX_ITEMS_TESTE = 25


def normalizar(texto):
    if texto is None:
        return ""
    texto = str(texto).lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto


def corrigir_cidade(cidade):
    cidade_limpa = cidade.strip()

    correcoes = {
        "campinas": "Campinas",
        "paulinia": "Paulínia",
        "paulínia": "Paulínia",
        "jundiai": "Jundiaí",
        "jundiaí": "Jundiaí",
        "sao paulo": "São Paulo",
        "são paulo": "São Paulo"
    }

    chave = normalizar(cidade_limpa)
    return correcoes.get(chave, cidade_limpa)


def formatar_localizacao_linkedin(cidade):
    cidade = corrigir_cidade(cidade)

    if "," in cidade:
        return cidade

    return f"{cidade}, São Paulo, Brazil"


def palavras(texto):
    texto = normalizar(texto)

    for ch in ["/", "-", "|", ",", ".", "(", ")", "[", "]", ";", ":"]:
        texto = texto.replace(ch, " ")

    stop = {
        "de", "da", "do", "das", "dos", "para", "com",
        "uma", "por", "the", "and", "jr", "pl", "sr", "em"
    }

    return [p for p in texto.split() if len(p) >= 3 and p not in stop]


def pegar_nome(perfil):
    first = perfil.get("firstName", "") or perfil.get("first_name", "")
    last = perfil.get("lastName", "") or perfil.get("last_name", "")

    nome = f"{first} {last}".strip()

    if nome:
        return nome

    return (
        perfil.get("fullName", "")
        or perfil.get("name", "")
        or "Nome não informado"
    )


def pegar_link(perfil):
    return (
        perfil.get("linkedinUrl", "")
        or perfil.get("url", "")
        or perfil.get("profileUrl", "")
        or perfil.get("linkedin", "")
        or perfil.get("publicIdentifier", "")
    )


def pegar_localizacao_texto(perfil):
    partes = []

    location = perfil.get("location", "")

    if isinstance(location, str):
        partes.append(location)

    if isinstance(location, dict):
        parsed = location.get("parsed", {})

        if isinstance(parsed, dict):
            partes.append(parsed.get("city", ""))
            partes.append(parsed.get("state", ""))
            partes.append(parsed.get("country", ""))

        for chave in [
            "linkedinText", "text", "name", "location",
            "full", "displayName", "raw"
        ]:
            valor = location.get(chave)
            if valor:
                partes.append(str(valor))

    for chave in [
        "locationName", "geoLocationName", "city",
        "address", "locationText"
    ]:
        valor = perfil.get(chave)
        if isinstance(valor, str):
            partes.append(valor)

    return " ".join([str(p) for p in partes if p]).strip()


def pegar_cargo_atual(perfil):
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

    for chave in [
        "headline", "occupation", "title", "position",
        "jobTitle", "subTitle"
    ]:
        valor = perfil.get(chave)
        if valor:
            cargos.append(str(valor))

    return " | ".join(cargos).strip()


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

    for chave in ["about", "summary", "headline", "description"]:
        valor = perfil.get(chave)
        if valor:
            textos.append(str(valor))

    return " ".join(textos)


def cidade_exata_bate(perfil, cidade):
    localizacao = normalizar(pegar_localizacao_texto(perfil))
    cidade_n = normalizar(cidade)

    if not localizacao:
        return False

    return cidade_n in localizacao


def cargo_bate(cargo_busca, cargo_atual, historico):
    cargo_n = normalizar(cargo_busca)
    atual_n = normalizar(cargo_atual)
    hist_n = normalizar(historico)
    texto_total = f"{atual_n} {hist_n}"

    # Cargo exato no cargo atual/headline
    if cargo_n and cargo_n in atual_n:
        return True, 50, "cargo atual exato"

    # Cargo exato no histórico
    if cargo_n and cargo_n in hist_n:
        return True, 25, "cargo no histórico"

    cargo_palavras = palavras(cargo_busca)

    # Regra especial para vendas/comercial
    busca_vendas = any(t in cargo_n for t in [
        "venda", "vendas", "comercial", "consultor",
        "executivo", "representante", "account"
    ])

    if busca_vendas:
        termos_vendas = [
            "venda", "vendas", "sales", "comercial"
        ]

        termos_funcao = [
            "consultor", "consultora", "executivo", "executiva",
            "representante", "key account", "account executive",
            "consultant"
        ]

        tem_vendas_atual = any(t in atual_n for t in termos_vendas)
        tem_funcao_atual = any(t in atual_n for t in termos_funcao)

        tem_vendas_total = any(t in texto_total for t in termos_vendas)
        tem_funcao_total = any(t in texto_total for t in termos_funcao)

        # Para "Consultor de vendas", exige função + vendas.
        if "consultor" in cargo_n and "venda" in cargo_n:
            if tem_funcao_atual and tem_vendas_atual:
                return True, 45, "cargo atual compatível"

            if tem_funcao_total and tem_vendas_total:
                return True, 20, "histórico compatível"

            return False, 0, ""

        # Para outros cargos comerciais
        if tem_funcao_atual and tem_vendas_atual:
            return True, 40, "cargo atual comercial"

        if tem_vendas_atual:
            return True, 30, "cargo atual em vendas"

        if tem_funcao_total and tem_vendas_total:
            return True, 18, "histórico comercial"

        return False, 0, ""

    # Regra geral para outras vagas
    if cargo_palavras:
        acertos_atual = sum(1 for p in cargo_palavras if p in atual_n)
        acertos_total = sum(1 for p in cargo_palavras if p in texto_total)

        if acertos_atual / len(cargo_palavras) >= 0.7:
            return True, 45, "cargo atual compatível"

        if acertos_total / len(cargo_palavras) >= 0.7:
            return True, 20, "histórico compatível"

    return False, 0, ""


def score_candidato(perfil, cargo, cidade, idioma, habilidades, empresa, origem_busca):
    score = 0
    motivos = []

    if not cidade_exata_bate(perfil, cidade):
        return None, []

    score += 50
    motivos.append("cidade exata")

    cargo_atual = pegar_cargo_atual(perfil)
    historico = pegar_historico_texto(perfil)

    ok_cargo, pontos_cargo, motivo_cargo = cargo_bate(cargo, cargo_atual, historico)

    if not ok_cargo:
        return None, []

    score += pontos_cargo
    motivos.append(motivo_cargo)

    if origem_busca == "exata":
        score += 20
        motivos.append("busca exata")

    if origem_busca == "ampla":
        score += 5
        motivos.append("busca ampla")

    if perfil.get("openToWork"):
        score += 10
        motivos.append("open to work")

    texto_total = normalizar(f"{cargo_atual} {historico}")

    if idioma:
        idioma_n = normalizar(idioma)

        if "ingles" in idioma_n:
            if "ingles" in texto_total or "english" in texto_total:
                score += 8
                motivos.append("inglês citado")
        elif idioma_n in texto_total:
            score += 8
            motivos.append(f"idioma: {idioma}")

    if habilidades:
        for hab in habilidades.split(","):
            hab = hab.strip()
            if not hab:
                continue

            hab_n = normalizar(hab)

            if hab_n in texto_total:
                score += 8
                motivos.append(f"skill: {hab}")

    if empresa:
        empresa_n = normalizar(empresa)

        if empresa_n in texto_total:
            score += 20
            motivos.append(f"empresa: {empresa}")

    return score, motivos


def montar_inputs_busca(cargo, cidade, idioma="", habilidades="", empresa=""):
    buscas = []

    cargo_base = cargo.strip()
    cidade_base = cidade.strip()

    # Busca 1: simples, sem locations
    buscas.append({
        "nome": "texto_livre",
        "input": {
            "searchQuery": f"{cargo_base} {cidade_base}",
            "profileScraperMode": "Full",
            "maxItems": MAX_ITEMS_TESTE
        }
    })

    # Busca 2: cargo + cidade + variações comerciais, sem locations
    cargo_n = normalizar(cargo_base)

    if "venda" in cargo_n or "comercial" in cargo_n or "consultor" in cargo_n or "executivo" in cargo_n:
        buscas.append({
            "nome": "comercial_texto_livre",
            "input": {
                "searchQuery": f"{cidade_base} consultor de vendas consultor comercial executivo de vendas representante comercial sales consultant",
                "profileScraperMode": "Full",
                "maxItems": MAX_ITEMS_TESTE
            }
        })

    # Busca 3: extras, sem locations
    extras = []

    if habilidades:
        extras.append(habilidades)

    if idioma:
        extras.append(idioma)

    if empresa:
        extras.append(empresa)

    if extras:
        buscas.append({
            "nome": "extras_texto_livre",
            "input": {
                "searchQuery": f"{cargo_base} {cidade_base} {' '.join(extras)}",
                "profileScraperMode": "Full",
                "maxItems": MAX_ITEMS_TESTE
            }
        })

    return buscas


def iniciar_run_apify(apify_input):
    url = f"https://api.apify.com/v2/acts/{APIFY_ACTOR}/runs"

    headers = {
        "Authorization": f"Bearer {APIFY_KEY}",
        "Content-Type": "application/json"
    }

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
    print("TOTAL RAW:", len(data) if isinstance(data, list) else "não é lista")

    if isinstance(data, list) and len(data) > 0:
        primeiro = data[0]
        if isinstance(primeiro, dict):
            print("CHAVES PRIMEIRO PERFIL:", list(primeiro.keys()))
            print("PRIMEIRO PERFIL:", primeiro)

    print("========== FIM APIFY RESULTADOS ==========")

    if not isinstance(data, list):
        return []

    return data


def rodar_busca_apify(apify_input):
    run_id = iniciar_run_apify(apify_input)
    run_data = aguardar_run_apify(run_id)

    dataset_id = run_data.get("defaultDatasetId")

    if not dataset_id:
        raise Exception("Apify terminou, mas não retornou dataset.")

    return buscar_resultados_apify(dataset_id)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/buscar", methods=["POST"])
def buscar():
    try:
        data = request.json or {}

        cargo = data.get("cargo", "").strip()
        cidade = data.get("cidade", "").strip()
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

        buscas = montar_inputs_busca(cargo, cidade, idioma, habilidades, empresa)

        perfis_por_chave = {}

        for busca_config in buscas:
            origem = busca_config["nome"]
            apify_input = busca_config["input"]

            print("========== RODANDO BUSCA ==========")
            print("ORIGEM:", origem)
            print("INPUT:", apify_input)

            perfis = rodar_busca_apify(apify_input)

            for perfil in perfis:
                if not isinstance(perfil, dict):
                    continue

                chave = (
                    pegar_link(perfil)
                    or pegar_nome(perfil)
                )

                chave_n = normalizar(chave)

                if not chave_n:
                    continue

                if chave_n not in perfis_por_chave:
                    perfil["_origens_busca"] = [origem]
                    perfis_por_chave[chave_n] = perfil
                else:
                    perfis_por_chave[chave_n].setdefault("_origens_busca", [])
                    if origem not in perfis_por_chave[chave_n]["_origens_busca"]:
                        perfis_por_chave[chave_n]["_origens_busca"].append(origem)

        todos_perfis = list(perfis_por_chave.values())

        candidatos_com_score = []

        for perfil in todos_perfis:
            origens = perfil.get("_origens_busca", [])

            if "exata" in origens:
                origem_principal = "exata"
            elif "extras" in origens:
                origem_principal = "extras"
            else:
                origem_principal = "ampla"

            score, motivos = score_candidato(
                perfil,
                cargo,
                cidade,
                idioma,
                habilidades,
                empresa,
                origem_principal
            )

            if score is None:
                continue

            # bônus se apareceu em mais de uma busca
            if len(origens) >= 2:
                score += 15
                motivos.append("apareceu em busca cruzada")

            if "extras" in origens:
                score += 10
                motivos.append("apareceu na busca com extras")

            nome = pegar_nome(perfil)
            cargo_atual = pegar_cargo_atual(perfil)
            cidade_display = pegar_localizacao_texto(perfil)
            link = pegar_link(perfil)

            resumo = (
                perfil.get("about", "")
                or perfil.get("summary", "")
                or perfil.get("description", "")
            )

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
        print("TOTAL ÚNICO RAW:", len(todos_perfis))
        print("APROVADOS FILTRO:", len(candidatos_com_score))
        for c in candidatos_com_score[:10]:
            print(c["nome"], c["score"], c["motivos"], c["cargo_atual"], c["cidade"])
        print("========== FIM RESULTADO FINAL ==========")

        if enviar_email and candidatos_com_score:
            enviar_email_resultado(candidatos_com_score, cargo, cidade)

        return jsonify({
            "ok": True,
            "total": len(candidatos_com_score),
            "candidatos": candidatos_com_score,
            "email_enviado": enviar_email and len(candidatos_com_score) > 0
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
        <small style="color:#999;">Ranqueado por busca cruzada: cidade + cargo + extras • {datetime.now().strftime("%d/%m/%Y %H:%M")}</small>
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
