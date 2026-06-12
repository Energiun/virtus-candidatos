from flask import Flask, request, jsonify, render_template, send_file
import requests
import os
import time
import unicodedata
from datetime import datetime
from html import escape
from io import BytesIO
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

app = Flask(__name__)

# =========================
# CONFIGURAÇÕES
# =========================

DESTINATARIO = "contato@virtusexec.com.br"

REMETENTE = os.environ.get("EMAIL_REMETENTE")
SENDGRID_KEY = os.environ.get("SENDGRID_KEY")
APIFY_KEY = os.environ.get("APIFY_KEY")

APIFY_ACTOR = "harvestapi~linkedin-profile-search"

# Quantos perfis por busca.
# 25 = bom para teste real. Se quiser economizar, coloque 10.
MAX_ITEMS_APIFY = int(os.environ.get("MAX_ITEMS_APIFY", "25"))

# Segurança para não rodar buscas demais sem querer.
MAX_BUSCAS_APIFY = int(os.environ.get("MAX_BUSCAS_APIFY", "4"))


# =========================
# FUNÇÕES DE TEXTO
# =========================

def normalizar(texto):
    if texto is None:
        return ""

    texto = str(texto).lower().strip()
    texto = unicodedata.normalize("NFD", texto)
    texto = "".join(c for c in texto if unicodedata.category(c) != "Mn")
    return texto


def limpar_duplicados(lista):
    vistos = set()
    resultado = []

    for item in lista:
        if not item:
            continue

        item_limpo = str(item).strip()
        chave = normalizar(item_limpo)

        if chave and chave not in vistos:
            vistos.add(chave)
            resultado.append(item_limpo)

    return resultado

def limitar_search_query(texto, limite=240):
    texto = " ".join(str(texto or "").split()).strip()

    if len(texto) <= limite:
        return texto

    palavras_query = texto.split()
    resultado = ""

    for palavra in palavras_query:
        tentativa = (resultado + " " + palavra).strip()

        if len(tentativa) > limite:
            break

        resultado = tentativa

    if not resultado:
        resultado = texto[:limite]

    return resultado.strip()

def sanitizar_input_apify(apify_input):
    novo_input = {}

    campos_permitidos = [
        "profileScraperMode",
        "searchQuery",
        "maxItems",
        "locations",
        "currentCompanies",
        "pastCompanies",
        "schools",
        "currentJobTitles",
        "pastJobTitles",
        "industryIds",
        "profileLanguages"
    ]

    for campo in campos_permitidos:
        if campo in apify_input:
            valor = apify_input[campo]

            if valor is None:
                continue

            if isinstance(valor, str) and not valor.strip():
                continue

            if isinstance(valor, list):
                valor = [v for v in valor if str(v).strip()]
                if not valor:
                    continue

            novo_input[campo] = valor

    novo_input["profileScraperMode"] = novo_input.get("profileScraperMode", "Full")
    novo_input["maxItems"] = int(novo_input.get("maxItems", MAX_ITEMS_APIFY))

    if "searchQuery" in novo_input:
        novo_input["searchQuery"] = limitar_search_query(novo_input["searchQuery"], 240)

    return novo_input


def corrigir_cidade(cidade):
    cidade_limpa = cidade.strip()

    correcoes = {
        "cammpinas": "Campinas",
        "campinas": "Campinas",
        "paulinia": "Paulínia",
        "paulínia": "Paulínia",
        "valinhos": "Valinhos",
        "vinhedo": "Vinhedo",
        "jundiai": "Jundiaí",
        "jundiaí": "Jundiaí",
        "sao paulo": "São Paulo",
        "são paulo": "São Paulo",
        "ribeirao preto": "Ribeirão Preto",
        "ribeirão preto": "Ribeirão Preto",
    }

    chave = normalizar(cidade_limpa)
    return correcoes.get(chave, cidade_limpa)


def formatar_localizacoes(cidade):
    cidade_corrigida = corrigir_cidade(cidade)

    opcoes = [
        f"{cidade_corrigida}, São Paulo, Brazil",
        f"{cidade_corrigida}, São Paulo, Brasil",
        cidade_corrigida
    ]

    return limpar_duplicados(opcoes)


def palavras(texto):
    texto = normalizar(texto)

    for ch in ["/", "-", "|", ",", ".", "(", ")", "[", "]", ";", ":", "•", "·"]:
        texto = texto.replace(ch, " ")

    stop = {
        "de", "da", "do", "das", "dos", "para", "com", "uma",
        "por", "the", "and", "jr", "pl", "sr", "em", "na", "no",
        "as", "os", "a", "o", "e"
    }

    return [p for p in texto.split() if len(p) >= 3 and p not in stop]


def texto_tem(texto, termo):
    return normalizar(termo) in normalizar(texto)


# =========================
# LEITURA DO PERFIL DO APIFY
# =========================

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
            "linkedinText",
            "text",
            "name",
            "location",
            "full",
            "displayName",
            "raw"
        ]:
            valor = location.get(chave)
            if valor:
                partes.append(str(valor))

    for chave in [
        "locationName",
        "geoLocationName",
        "city",
        "address",
        "locationText"
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
        "headline",
        "occupation",
        "title",
        "position",
        "jobTitle",
        "subTitle"
    ]:
        valor = perfil.get(chave)
        if valor:
            cargos.append(str(valor))

    return " | ".join(limpar_duplicados(cargos)).strip()


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


def pegar_resumo(perfil):
    return (
        perfil.get("about", "")
        or perfil.get("summary", "")
        or perfil.get("description", "")
        or ""
    )


# =========================
# LÓGICA DE CARGO / CIDADE / RANKING
# =========================

def cargo_eh_comercial(cargo):
    c = normalizar(cargo)

    termos = [
        "venda", "vendas", "comercial", "consultor", "consultora",
        "executivo", "executiva", "representante", "account",
        "sales", "negocios", "negócios"
    ]

    return any(t in c for t in termos)


def montar_titulos_cargo(cargo):
    cargo_base = cargo.strip()

    titulos = [cargo_base]

    if cargo_eh_comercial(cargo_base):
        titulos += [
            "Consultor de vendas",
            "Consultora de vendas",
            "Consultor comercial",
            "Consultora comercial",
            "Executivo de vendas",
            "Executiva de vendas",
            "Representante comercial",
            "Representante de vendas",
            "Consultor de negócios",
            "Consultora de negócios",
            "Sales Consultant",
            "Account Executive",
            "Key Account"
        ]

    return limpar_duplicados(titulos)


def cidade_bate(perfil, cidade):
    cidade_corrigida = corrigir_cidade(cidade)
    cidade_n = normalizar(cidade_corrigida)

    localizacao = pegar_localizacao_texto(perfil)
    localizacao_n = normalizar(localizacao)

    if cidade_n and cidade_n in localizacao_n:
        return True, "cidade exata", 60

    # Fallback: às vezes a cidade aparece no texto do perfil.
    texto_total = normalizar(
        f"{pegar_cargo_atual(perfil)} {pegar_historico_texto(perfil)}"
    )

    if cidade_n and cidade_n in texto_total:
        return True, "cidade citada no perfil", 35

    return False, "", 0


def cargo_bate(cargo_busca, cargo_atual, historico):
    cargo_n = normalizar(cargo_busca)
    atual_n = normalizar(cargo_atual)
    hist_n = normalizar(historico)
    texto_total = f"{atual_n} {hist_n}"

    if cargo_n and cargo_n in atual_n:
        return True, "cargo atual exato", 60

    if cargo_n and cargo_n in hist_n:
        return True, "cargo no histórico", 30

    busca_comercial = cargo_eh_comercial(cargo_busca)

    if busca_comercial:
        termos_vendas = [
            "venda", "vendas", "sales", "comercial", "negocios", "negócios"
        ]

        termos_funcao = [
            "consultor", "consultora", "executivo", "executiva",
            "representante", "account", "sales consultant",
            "key account"
        ]

        tem_vendas_atual = any(t in atual_n for t in termos_vendas)
        tem_funcao_atual = any(t in atual_n for t in termos_funcao)

        tem_vendas_total = any(t in texto_total for t in termos_vendas)
        tem_funcao_total = any(t in texto_total for t in termos_funcao)

        if tem_vendas_atual and tem_funcao_atual:
            return True, "cargo atual comercial/vendas", 50

        if tem_vendas_atual:
            return True, "cargo atual em vendas", 40

        if tem_vendas_total and tem_funcao_total:
            return True, "histórico comercial/vendas", 25

        if tem_vendas_total:
            return True, "histórico em vendas", 18

        return False, "", 0

    cargo_palavras = palavras(cargo_busca)

    if cargo_palavras:
        acertos_atual = sum(1 for p in cargo_palavras if p in atual_n)
        acertos_total = sum(1 for p in cargo_palavras if p in texto_total)

        if acertos_atual / len(cargo_palavras) >= 0.7:
            return True, "cargo atual compatível", 50

        if acertos_total / len(cargo_palavras) >= 0.7:
            return True, "histórico compatível", 25

    return False, "", 0


def pontuar_termos(texto_total, termos, pontos_por_termo, prefixo, limite=None):
    score = 0
    motivos = []
    usados = 0

    for termo in termos:
        if not termo:
            continue

        if texto_tem(texto_total, termo):
            score += pontos_por_termo
            motivos.append(f"{prefixo}: {termo}")
            usados += 1

            if limite and usados >= limite:
                break

    return score, motivos


def score_candidato(
    perfil,
    cargo,
    cidade,
    segmento,
    empresa_anterior,
    idioma,
    palavras_chave,
    origem_busca
):
    score = 0
    motivos = []

    cargo_atual = pegar_cargo_atual(perfil)
    historico = pegar_historico_texto(perfil)
    resumo = pegar_resumo(perfil)

    texto_total = f"{cargo_atual} {historico} {resumo}"

    # 1. Cidade é filtro principal.
    ok_cidade, motivo_cidade, pontos_cidade = cidade_bate(perfil, cidade)

    if not ok_cidade:
        return None, []

    score += pontos_cidade
    motivos.append(motivo_cidade)

    # 2. Cargo é filtro principal.
    ok_cargo, motivo_cargo, pontos_cargo = cargo_bate(cargo, cargo_atual, historico)

    if not ok_cargo:
        return None, []

    score += pontos_cargo
    motivos.append(motivo_cargo)

    # 3. Origem da busca.
    if origem_busca == "cargo_cidade_nativo":
        score += 20
        motivos.append("busca nativa cargo/cidade")
    elif origem_busca == "segmento_idiomas":
        score += 12
        motivos.append("busca por segmento")
    elif origem_busca == "empresa_segmento":
        score += 15
        motivos.append("busca empresa/segmento")
    elif origem_busca == "texto_livre":
        score += 8
        motivos.append("busca texto livre")

    # 4. Open to Work.
    if perfil.get("openToWork"):
        score += 10
        motivos.append("open to work")

    # 5. Segmento desejado.
    termos_segmento = separar_termos(segmento)

    # Termos automáticos para vagas ligadas a escola de inglês/idiomas.
    auto_termos_educacao = [
        "curso de inglês",
        "curso ingles",
        "inglês",
        "english",
        "idiomas",
        "escola de idiomas",
        "educacional",
        "educação",
        "educacao",
        "matrícula",
        "matricula",
        "captação de alunos",
        "captacao de alunos",
        "alunos",
        "ensino",
        "treinamento"
    ]

    termos_segmento += auto_termos_educacao
    termos_segmento = limpar_duplicados(termos_segmento)

    pontos, motivos_extra = pontuar_termos(
        texto_total,
        termos_segmento,
        pontos_por_termo=12,
        prefixo="segmento",
        limite=4
    )

    score += pontos
    motivos += motivos_extra

    # 6. Empresa anterior desejada.
    if empresa_anterior:
        empresas = separar_termos(empresa_anterior)

        pontos, motivos_extra = pontuar_termos(
            texto_total,
            empresas,
            pontos_por_termo=35,
            prefixo="empresa desejada",
            limite=3
        )

        score += pontos
        motivos += motivos_extra

    # 7. Idioma.
    if idioma:
        idioma_n = normalizar(idioma)

        if "ingles" in idioma_n or "english" in idioma_n:
            if "ingles" in normalizar(texto_total) or "english" in normalizar(texto_total):
                score += 15
                motivos.append("inglês citado")
        else:
            if idioma_n in normalizar(texto_total):
                score += 10
                motivos.append(f"idioma: {idioma}")

    # 8. Palavras-chave.
    termos_palavras = separar_termos(palavras_chave)

    pontos, motivos_extra = pontuar_termos(
        texto_total,
        termos_palavras,
        pontos_por_termo=8,
        prefixo="palavra-chave",
        limite=6
    )

    score += pontos
    motivos += motivos_extra

    return score, motivos


# =========================
# BUSCAS NO APIFY
# =========================

def montar_query_segmento(cargo, cidade, segmento, empresa_anterior, idioma, palavras_chave):
    partes = [
        cargo,
        cidade,
        segmento,
        empresa_anterior,
        idioma,
        palavras_chave
    ]

    texto = " ".join([p for p in partes if p])
    return limitar_search_query(texto, 280)


def montar_inputs_busca(cargo, cidade, segmento="", empresa_anterior="", idioma="", palavras_chave=""):
    cidade_corrigida = corrigir_cidade(cidade)

    buscas = []

    # 1. Igual ao teste manual do Apify:
    # Search query + Locations Filter simples
    buscas.append({
        "nome": "apify_manual_simples",
        "input": {
            "profileScraperMode": "Full",
            "searchQuery": limitar_search_query(cargo, 240),
            "maxItems": 20,
            "locations": [cidade_corrigida]
        }
    })

    # 2. Mesma busca, mas cidade com estado/país
    buscas.append({
        "nome": "apify_cidade_completa",
        "input": {
            "profileScraperMode": "Full",
            "searchQuery": limitar_search_query(cargo, 240),
            "maxItems": 20,
            "locations": [f"{cidade_corrigida}, São Paulo, Brasil"]
        }
    })

    # 3. Texto livre sem filtro de localização
    buscas.append({
        "nome": "texto_livre_cargo_cidade",
        "input": {
            "profileScraperMode": "Full",
            "searchQuery": limitar_search_query(f"{cargo} {cidade_corrigida}", 240),
            "maxItems": 20
        }
    })

    # 4. Cargo puro, sem cidade
    # Essa é para testar se o Actor ainda encontra alguém com a palavra-chave.
    buscas.append({
        "nome": "cargo_puro_diagnostico",
        "input": {
            "profileScraperMode": "Full",
            "searchQuery": limitar_search_query(cargo, 240),
            "maxItems": 20
        }
    })

    return buscas[:MAX_BUSCAS_APIFY]


def iniciar_run_apify(apify_input):
    apify_input = sanitizar_input_apify(apify_input)

    print("========== CHECAGEM FINAL APIFY ==========")
    print("SEARCH QUERY:", apify_input.get("searchQuery", ""))
    print("TAMANHO SEARCH QUERY:", len(apify_input.get("searchQuery", "")))
    print("INPUT FINAL:", apify_input)
    print("========== FIM CHECAGEM FINAL ==========")

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

    for tentativa in range(30):
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


# =========================
# ROTAS FLASK
# =========================

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/buscar", methods=["POST"])
def buscar():
    try:
        data = request.json or {}

        cargo = data.get("cargo", "").strip()
        cidade = data.get("cidade", "").strip()

        segmento = data.get("segmento", "").strip()
        empresa_anterior = (
            data.get("empresa_anterior", "")
            or data.get("empresa", "")
        ).strip()

        idioma = data.get("idioma", "").strip()

        palavras_chave = (
            data.get("palavras_chave", "")
            or data.get("habilidades", "")
        ).strip()

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

        cidade = corrigir_cidade(cidade)

        buscas = montar_inputs_busca(
            cargo=cargo,
            cidade=cidade,
            segmento=segmento,
            empresa_anterior=empresa_anterior,
            idioma=idioma,
            palavras_chave=palavras_chave
        )

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

                chave = pegar_link(perfil) or pegar_nome(perfil)
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

            if "cargo_cidade_nativo" in origens:
                origem_principal = "cargo_cidade_nativo"
            elif "segmento_idiomas" in origens:
                origem_principal = "segmento_idiomas"
            elif "empresa_segmento" in origens:
                origem_principal = "empresa_segmento"
            else:
                origem_principal = "texto_livre"

            score, motivos = score_candidato(
                perfil=perfil,
                cargo=cargo,
                cidade=cidade,
                segmento=segmento,
                empresa_anterior=empresa_anterior,
                idioma=idioma,
                palavras_chave=palavras_chave,
                origem_busca=origem_principal
            )

            if score is None:
                continue

            if len(origens) >= 2:
                score += 18
                motivos.append("apareceu em busca cruzada")

            if len(origens) >= 3:
                score += 12
                motivos.append("apareceu em múltiplas buscas")

            nome = pegar_nome(perfil)
            cargo_atual = pegar_cargo_atual(perfil)
            cidade_display = pegar_localizacao_texto(perfil)
            link = pegar_link(perfil)
            resumo = pegar_resumo(perfil)

            candidatos_com_score.append({
                "nome": nome,
                "link": link,
                "cargo_atual": cargo_atual,
                "cidade": cidade_display,
                "open_to_work": perfil.get("openToWork", False),
                "score": score,
                "motivos": motivos,
                "resumo": resumo[:240] if resumo else ""
            })

        candidatos_com_score.sort(key=lambda x: x["score"], reverse=True)

        print("========== RESULTADO FINAL ==========")
        print("CARGO:", cargo)
        print("CIDADE:", cidade)
        print("SEGMENTO:", segmento)
        print("EMPRESA ANTERIOR:", empresa_anterior)
        print("IDIOMA:", idioma)
        print("PALAVRAS:", palavras_chave)
        print("TOTAL ÚNICO RAW:", len(todos_perfis))
        print("APROVADOS FILTRO:", len(candidatos_com_score))

        for c in candidatos_com_score[:15]:
            print(c["nome"], c["score"], c["motivos"], c["cargo_atual"], c["cidade"])

        print("========== FIM RESULTADO FINAL ==========")

        return jsonify({
            "ok": True,
            "total": len(candidatos_com_score),
            "candidatos": candidatos_com_score,
            "email_enviado": False
        })

    except Exception as e:
        print("ERRO GERAL:", str(e))

        return jsonify({
            "ok": False,
            "mensagem": str(e)
        }), 500

# =========================
# PDF - SHORTLIST
# =========================

def pdf_safe(valor):
    if valor is None:
        return ""
    return escape(str(valor), quote=True)


def gerar_pdf_shortlist(data):
    buffer = BytesIO()

    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=1.2 * cm,
        leftMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm
    )

    styles = getSampleStyleSheet()

    titulo_style = ParagraphStyle(
        "TituloVirtus",
        parent=styles["Title"],
        fontSize=20,
        leading=24,
        textColor=colors.HexColor("#1a3a5c"),
        spaceAfter=8
    )

    subtitulo_style = ParagraphStyle(
        "SubtituloVirtus",
        parent=styles["Normal"],
        fontSize=10,
        leading=14,
        textColor=colors.HexColor("#60758a"),
        spaceAfter=12
    )

    nome_style = ParagraphStyle(
        "NomeCandidato",
        parent=styles["Heading3"],
        fontSize=12,
        leading=15,
        textColor=colors.HexColor("#1a3a5c"),
        spaceAfter=4
    )

    texto_style = ParagraphStyle(
        "TextoNormal",
        parent=styles["Normal"],
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#333333")
    )

    pequeno_style = ParagraphStyle(
        "TextoPequeno",
        parent=styles["Normal"],
        fontSize=8,
        leading=10,
        textColor=colors.HexColor("#666666")
    )

    story = []

    cargo = data.get("cargo", "—")
    cidade = data.get("cidade", "—")
    segmento = data.get("segmento", "—")
    empresa_anterior = data.get("empresa_anterior", "—")
    idioma = data.get("idioma", "—")
    palavras_chave = data.get("palavras_chave", "—")
    candidatos = data.get("candidatos", [])

    story.append(Paragraph("VIRTUS EXEC", titulo_style))
    story.append(Paragraph("Shortlist de candidatos ranqueados", subtitulo_style))

    filtros = [
        ["Cargo", pdf_safe(cargo)],
        ["Cidade", pdf_safe(cidade)],
        ["Segmento", pdf_safe(segmento or "—")],
        ["Empresa anterior desejada", pdf_safe(empresa_anterior or "—")],
        ["Idioma", pdf_safe(idioma or "—")],
        ["Palavras-chave", pdf_safe(palavras_chave or "—")],
        ["Total de candidatos", str(len(candidatos))]
    ]

    tabela_filtros = Table(filtros, colWidths=[5 * cm, 12 * cm])
    tabela_filtros.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e8eef5")),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#1a3a5c")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d9e2ec")),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))

    story.append(tabela_filtros)
    story.append(Spacer(1, 14))

    for i, candidato in enumerate(candidatos, 1):
        nome = pdf_safe(candidato.get("nome", "Nome não informado"))
        cargo_atual = pdf_safe(candidato.get("cargo_atual", "—"))
        cidade_candidato = pdf_safe(candidato.get("cidade", "—"))
        score = pdf_safe(candidato.get("score", 0))
        resumo = pdf_safe(candidato.get("resumo", ""))
        link = pdf_safe(candidato.get("link", ""))
        motivos = candidato.get("motivos", [])

        if isinstance(motivos, list):
            motivos_txt = ", ".join([str(m) for m in motivos])
        else:
            motivos_txt = str(motivos or "")

        motivos_txt = pdf_safe(motivos_txt or "—")

        bloco = []

        bloco.append(Paragraph(f"#{i} — {nome}", nome_style))
        bloco.append(Paragraph(f"<b>Cargo atual:</b> {cargo_atual}", texto_style))
        bloco.append(Paragraph(f"<b>Localização:</b> {cidade_candidato}", texto_style))
        bloco.append(Paragraph(f"<b>Score:</b> {score} pontos", texto_style))
        bloco.append(Paragraph(f"<b>Motivos do ranking:</b> {motivos_txt}", pequeno_style))

        if resumo:
            bloco.append(Paragraph(f"<b>Resumo:</b> {resumo}", pequeno_style))

        if link:
            bloco.append(Paragraph(f"<b>LinkedIn:</b> <a href='{link}' color='blue'>{link}</a>", pequeno_style))

        tabela_candidato = Table([[bloco]], colWidths=[17 * cm])
        tabela_candidato.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#d9e2ec")),
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#ffffff")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ]))

        story.append(tabela_candidato)
        story.append(Spacer(1, 8))

    doc.build(story)

    buffer.seek(0)
    return buffer


@app.route("/baixar_pdf", methods=["POST"])
def baixar_pdf():
    try:
        data = request.json or {}
        candidatos = data.get("candidatos", [])

        if not candidatos:
            return jsonify({
                "ok": False,
                "mensagem": "Nenhum candidato enviado para gerar PDF."
            }), 400

        pdf_buffer = gerar_pdf_shortlist(data)

        nome_arquivo = "shortlist-virtus-exec.pdf"

        return send_file(
            pdf_buffer,
            mimetype="application/pdf",
            as_attachment=True,
            download_name=nome_arquivo
        )

    except Exception as e:
        print("ERRO PDF:", str(e))
        return jsonify({
            "ok": False,
            "mensagem": f"Erro ao gerar PDF: {str(e)}"
        }), 500

# =========================
# EMAIL
# =========================

def enviar_email_resultado(
    candidatos_com_score,
    cargo,
    cidade,
    segmento,
    empresa_anterior,
    idioma,
    palavras_chave
):
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
        resumo = escape(c["resumo"] or "")

        badge_otw = ""

        if c["open_to_work"]:
            badge_otw = (
                '<span style="background:#22c55e;color:white;padding:2px 8px;'
                'border-radius:20px;font-size:11px;margin-left:6px;">Open to Work</span>'
            )

        tags = "".join([
            f'<span style="background:#e8eef5;color:#1a3a5c;padding:2px 8px;'
            f'border-radius:20px;font-size:11px;margin:2px;display:inline-block;">{escape(m)}</span>'
            for m in c["motivos"]
        ])

        resumo_html = ""

        if resumo:
            resumo_html = f'<div style="color:#777;font-size:12px;margin-top:6px;line-height:1.4;">{resumo}</div>'

        linhas += f"""
        <tr>
            <td style="padding:12px;border-bottom:1px solid #eee;color:#999;font-weight:700;">{i}</td>

            <td style="padding:12px;border-bottom:1px solid #eee;">
                <strong style="font-size:15px;">{nome}</strong>{badge_otw}<br>
                <span style="color:#555;font-size:13px;">{cargo_atual}</span><br>
                <span style="color:#888;font-size:12px;">📍 {cidade_txt}</span><br>
                <div style="margin-top:5px;">{tags}</div>
                {resumo_html}
            </td>

            <td style="padding:12px;border-bottom:1px solid #eee;text-align:right;">
                <span style="background:#1a3a5c;color:white;padding:3px 10px;border-radius:20px;font-size:12px;font-weight:700;">
                    {c['score']} pts
                </span><br><br>
                <a href="{link}" style="color:#1a3a5c;font-weight:bold;font-size:13px;">Ver perfil →</a>
            </td>
        </tr>
        """

    filtros_html = f"""
    <p style="color:#a8c4e0;margin:12px 0 0;">
        Busca: <strong>{escape(cargo)} | {escape(cidade)}</strong>
    </p>
    <p style="color:#a8c4e0;margin:4px 0 0;font-size:12px;">
        Segmento: {escape(segmento or "—")}<br>
        Empresa anterior desejada: {escape(empresa_anterior or "—")}<br>
        Idioma: {escape(idioma or "—")}<br>
        Palavras-chave: {escape(palavras_chave or "—")}
    </p>
    """

    corpo = f"""
    <div style="font-family:Arial,sans-serif;max-width:820px;margin:auto;">
      <div style="background:#1a3a5c;padding:24px;border-radius:12px 12px 0 0;">
        <h2 style="color:white;margin:0;font-family:Arial;letter-spacing:2px;">VIRTUS EXEC</h2>
        <p style="color:#a8c4e0;margin:4px 0 0;font-size:12px;letter-spacing:1px;">
            BUSCA INTELIGENTE DE CANDIDATOS
        </p>

        {filtros_html}

        <p style="color:#a8c4e0;margin:12px 0 0;font-size:11px;">
            {datetime.now().strftime("%d/%m/%Y %H:%M")} • {len(candidatos_com_score)} candidatos ranqueados
        </p>
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
        <small style="color:#999;">
            Ranqueado por: cargo + cidade + segmento + empresa anterior + idioma + palavras-chave.
        </small>
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
                "subject": (
                    f"[Virtus Exec] {cargo} | {cidade} — "
                    f"{len(candidatos_com_score)} candidatos • {datetime.now().strftime('%d/%m/%Y')}"
                ),
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
