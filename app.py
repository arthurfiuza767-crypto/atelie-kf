import os
import json
import time
import base64
import uuid
import requests
from datetime import datetime, date
from functools import wraps
from dotenv import load_dotenv

load_dotenv()  # TEM que ser antes de ler as chaves!

from flask import Flask, request, redirect, url_for, session, flash, render_template_string, send_from_directory
from werkzeug.utils import secure_filename

import cloudinary
import cloudinary.uploader

cloudinary.config(
    cloud_name=os.getenv("CLOUDINARY_CLOUD_NAME"),
    api_key=os.getenv("CLOUDINARY_API_KEY"),
    api_secret=os.getenv("CLOUDINARY_API_SECRET"),
    secure=True
)

def enviar_para_cloudinary(imagem_base64):
    if not os.getenv("CLOUDINARY_CLOUD_NAME"):
        return imagem_base64
    try:
        resultado = cloudinary.uploader.upload(imagem_base64, folder="atelie-kf")
        return resultado.get("secure_url", imagem_base64)
    except Exception as e:
        print("[CLOUDINARY] Erro:", e)
        return imagem_base64

# ===== SUPABASE =====
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "")

SUPA_HEADERS = {
    "apikey": SUPABASE_KEY,
    "Authorization": f"Bearer {SUPABASE_KEY}",
    "Content-Type": "application/json",
    "Prefer": "return=representation"
}

def supabase_ler(chave):
    try:
        r = requests.get(f"{SUPABASE_URL}/rest/v1/dados?chave=eq.{chave}&select=valor", headers=SUPA_HEADERS, timeout=10)
        print("[SUPABASE] LER", chave, r.status_code, r.text[:200])
        if r.ok and r.json():
            return r.json()[0].get("valor")
    except Exception as e:
        print("[SUPABASE] Erro ler:", e)
    return None

def supabase_salvar(chave, valor):
    try:
        # upsert: cria se não existir, atualiza se existir
        r = requests.post(
            f"{SUPABASE_URL}/rest/v1/dados?on_conflict=chave",
            headers={**SUPA_HEADERS, "Prefer": "resolution=merge-duplicates,return=representation"},
            json={"chave": chave, "valor": valor},
            timeout=15
        )
        print("[SUPABASE] SALVAR", chave, r.status_code, r.text[:300])
    except Exception as e:
        print("[SUPABASE] Erro salvar:", e)


load_dotenv()

# ==========================================
# KF ATELIÊ BY KEILA FIUZA
# ==========================================

load_dotenv()

app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "chave-temporaria-local"
)

SENHA_ADMIN = os.getenv("SENHA_ADMIN", "")

REPLICATE_API_TOKEN = os.getenv(
    "REPLICATE_API_TOKEN", ""
).strip()

BASE_DIR = os.path.dirname(
    os.path.abspath(__file__)
)

UPLOAD_FOLDER = os.path.join(
    BASE_DIR, "uploads"
)

TRABALHOS_FILE = os.path.join(
    BASE_DIR, "trabalhos.json"
)

AGENDAMENTOS_FILE = os.path.join(
    BASE_DIR, "agendamentos.json"
)

DIAS_BLOQUEADOS_FILE = os.path.join(
    BASE_DIR, "dias_bloqueados.json"
)

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024

HORARIOS = [
    f"{hora:02d}:{minuto:02d}"
    for hora in range(10, 22)
    for minuto in [0, 15, 30, 45]
    if not (hora == 21 and minuto > 0)
]

EXTENSOES_PERMITIDAS = {
    "png", "jpg", "jpeg", "webp", "heic", "heif", "avif"
}

REPLICATE_BASE = "https://api.replicate.com/v1"

FLUX_MODEL = "black-forest-labs/flux-schnell"

IDM_MODEL = "cuuupid/idm-vton"

# ==========================================
# ARQUIVOS JSON
# ==========================================

def ler_json(caminho, padrao):
    nome = os.path.basename(caminho).replace(".json", "")
    dado_nuvem = supabase_ler(nome)
    if dado_nuvem is not None:
        return dado_nuvem
    # se não tem na nuvem, tenta o arquivo local antigo
    if not os.path.exists(caminho):
        return padrao
    try:
        with open(caminho, "r", encoding="utf-8") as arquivo:
            return json.load(arquivo)
    except Exception as erro:
        print("[JSON] Erro:", erro)
        return padrao

def salvar_json(caminho, dados):
    nome = os.path.basename(caminho).replace(".json", "")
    supabase_salvar(nome, dados)
    # continua salvando local também, pra não quebrar nada
    try:
        temporario = caminho + ".tmp"
        with open(temporario, "w", encoding="utf-8") as arquivo:
            json.dump(dados, arquivo, ensure_ascii=False, indent=4)
        os.replace(temporario, caminho)
    except Exception as e:
        print("[JSON] Erro salvar local:", e)


def carregar_trabalhos():
    return ler_json(TRABALHOS_FILE, [])


def carregar_agendamentos():
    return ler_json(AGENDAMENTOS_FILE, [])


def carregar_dias_bloqueados():
    return ler_json(DIAS_BLOQUEADOS_FILE, [])


# ==========================================
# ADMINISTRAÇÃO
# ==========================================

def admin_obrigatorio(funcao):

    @wraps(funcao)
    def verificar(*args, **kwargs):

        if not session.get("admin_logado"):

            flash(
                "Entre na Área da Dona.",
                "erro"
            )

            return redirect(url_for("admin"))

        return funcao(*args, **kwargs)

    return verificar


# ==========================================
# REPLICATE API
# ==========================================

def replicate_headers():

    if not REPLICATE_API_TOKEN:

        raise RuntimeError(
            "Token da Replicate não configurado. "
            "Verifique o arquivo .env."
        )

    return {
        "Authorization": (
            f"Bearer {REPLICATE_API_TOKEN}"
        ),
        "Content-Type": "application/json"
    }


def buscar_versao(modelo):

    print("[REPLICATE] Buscando versão:", modelo)

    resposta = requests.get(
        f"{REPLICATE_BASE}/models/{modelo}",
        headers=replicate_headers(),
        timeout=30
    )

    resposta.raise_for_status()

    dados = resposta.json()

    versao = (
        dados.get("latest_version") or {}
    ).get("id")

    if not versao:

        raise RuntimeError(
            "Não foi possível encontrar a versão do modelo."
        )

    print("[REPLICATE] Versão:", versao)

    return versao


def get_flux_version():

    return buscar_versao(FLUX_MODEL)


def get_idm_version():

    return buscar_versao(IDM_MODEL)


def replicate_run_by_version(
    versao,
    entrada,
    timeout_total=360
):

    print("[REPLICATE] Criando geração...")

    resposta = requests.post(
        f"{REPLICATE_BASE}/predictions",

        headers=replicate_headers(),

        json={
            "version": versao,
            "input": entrada
        },

        timeout=60
    )

    if not resposta.ok:

        raise RuntimeError(
            "Erro na API Replicate: "
            + resposta.text
        )

    prediction = resposta.json()

    prediction_id = prediction.get("id")

    if not prediction_id:

        raise RuntimeError(
            "A API não retornou o ID da geração."
        )

    print(
        "[REPLICATE] ID:",
        prediction_id
    )

    inicio = time.time()

    while time.time() - inicio < timeout_total:

        time.sleep(2)

        consulta = requests.get(
            f"{REPLICATE_BASE}/predictions/{prediction_id}",

            headers={
                "Authorization":
                f"Bearer {REPLICATE_API_TOKEN}"
            },

            timeout=30
        )

        consulta.raise_for_status()

        dados = consulta.json()

        status = dados.get("status")

        print("[REPLICATE] Status:", status)

        if status == "succeeded":

            return dados.get("output")

        if status in ["failed", "canceled"]:

            raise RuntimeError(
                "Falha na geração: "
                + str(dados.get("error"))
            )

    raise TimeoutError(
        "Tempo limite da geração excedido."
    )


def primeira_url_saida(saida):

    if isinstance(saida, str):
        return saida

    if isinstance(saida, list) and saida:
        return primeira_url_saida(saida[0])

    if isinstance(saida, dict):

        for chave in ["url", "image", "output"]:

            if chave in saida:
                return primeira_url_saida(
                    saida[chave]
                )

    raise RuntimeError(
        "A imagem não foi encontrada."
    )


def baixar_imagem_base64(url):

    print("[IMAGEM] Baixando resultado...")

    resposta = requests.get(
        url,
        timeout=90
    )

    resposta.raise_for_status()

    tipo = resposta.headers.get(
        "Content-Type",
        "image/png"
    ).split(";")[0]

    if not tipo.startswith("image/"):
        tipo = "image/png"

    imagem = base64.b64encode(
        resposta.content
    ).decode("utf-8")

    return f"data:{tipo};base64,{imagem}"


def gerar_com_flux(prompt):

    print("[FLUX] Iniciando...")

    versao = get_flux_version()

    resultado = replicate_run_by_version(
        versao,
        {"prompt": prompt}
    )

    return baixar_imagem_base64(
        primeira_url_saida(resultado)
    )


def gerar_provador(foto_pessoa_base64, descricao):
    print("[PROVADOR] Gerando roupa...")

    # PASSO 0 - Diminui a foto para ficar mais rápido!
    try:
        from PIL import Image
        import io

        if "," in foto_pessoa_base64:
            cabecalho, dados = foto_pessoa_base64.split(",", 1)
        else:
            cabecalho = "data:image/jpeg;base64"
            dados = foto_pessoa_base64

        imagem_bytes = base64.b64decode(dados)
        img = Image.open(io.BytesIO(imagem_bytes))
        img.thumbnail((768, 1024))

        buffer = io.BytesIO()
        img.save(buffer, format="JPEG")
        foto_pessoa_base64 = cabecalho + "," + base64.b64encode(buffer.getvalue()).decode("utf-8")
        print("[PROVADOR] Foto diminuida para 768px")
    except Exception as e:
        print(f"Não consegui diminuir: {e}")

    # Descobre o tipo de roupa sozinho
    d = descricao.lower()
    if any(p in d for p in ["conjunto", "macacão", "macacao", "jumpsuit", "vestido", "noiva", "longo"]):
        categoria = "dresses"
        tipo = "vestido / roupa de corpo inteiro"
    elif any(p in d for p in ["calça", "calca", "legging", "jeans", "short", "saia", "bermuda", "pantalona"]):
        categoria = "lower_body"
        tipo = "parte de baixo"
    elif any(p in d for p in ["blusa", "camisa", "camiseta", "top", "cropp", "cropped", "jaqueta", "casaco", "blazer", "suéter", "moletom", "colete"]):
        categoria = "upper_body"
        tipo = "parte de cima"
    else:
        categoria = "dresses"
        tipo = "roupa"

    # PASSO 1 - Criando a roupa
    roupa = gerar_com_flux(
        f"foto de produto profissional de {descricao}, {tipo}, roupa sozinha em cabide em fundo branco limpo, vista plana frontal, sem pessoa, sem modelo, sem corpo, só a roupa"
    )

    print("[PROVADOR] Aplicando roupa na pessoa...")

    # Pausa curtinha para não dar erro 429
    time.sleep(8)

    versao = get_idm_version()

    resultado = None
    for tentativa in range(3):
        try:
            resultado = replicate_run_by_version(
                versao,
                {
                    "garm_img": roupa,
                    "human_img": foto_pessoa_base64,
                    "garment_des": descricao,
                    "category": categoria
                }
            )
            break
        except Exception as e:
            print(f"Tentativa {tentativa+1} falhou: {e}")
            time.sleep(10)

    if resultado is None:
        raise RuntimeError("Deu erro, tenta de novo em 1 minutinho!")

    return baixar_imagem_base64(
        primeira_url_saida(resultado)
    )

# ==========================================
# DESIGN E TEMPLATE PRINCIPAL
# ==========================================

BASE_HTML = """
<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta property="og:title" content="KF Ateliê by Keila Fiuza">
<meta property="og:description" content="Roupas sob medida | Agende seu horário ✨">
<meta property="og:image" content="https://atelie-kf.onrender.com/static/logo.png">
<meta property="og:url" content="https://atelie-kf.onrender.com/">
<meta property="og:type" content="website">
<meta name="twitter:card" content="summary_large_image">

<title>{{ titulo }} | KF Ateliê</title>

<style>

:root {
    --fundo: #f3e6f7;
    --texto: #3d2a4d;
    --roxo: #8a5aa8;
    --hover: #6d3d8a;
    --header: #e9d0f0;
}

* {
    box-sizing: border-box;
}

body {
    margin: 0;
    background: var(--fundo);
    color: var(--texto);
    font-family: Arial, Helvetica, sans-serif;
    line-height: 1.5;
}

header {
    background: var(--header);
    padding: 25px;
    text-align: center;
    border-bottom: 3px solid var(--roxo);
}

header img {
    max-width: 520px;
    width: 95%;
    background: white;
    border-radius: 20px;
    padding: 14px;
    box-shadow: 0 4px 12px rgba(0,0,0,.15);
}

nav {
    display: flex;
    flex-wrap: wrap;
    justify-content: center;
    gap: 8px;
    padding: 15px;
}

nav a, .botao {
    background: var(--roxo);
    color: white;
    padding: 12px 17px;
    border: none;
    border-radius: 10px;
    text-decoration: none;
    font-weight: bold;
    cursor: pointer;
    display: inline-block;
}

nav a:hover, .botao:hover {
    background: var(--hover);
}

main {
    max-width: 1100px;
    margin: 25px auto;
    padding: 0 15px;
}

.card {
    background: white;
    padding: 22px;
    border-radius: 16px;
    box-shadow: 0 4px 12px rgba(0,0,0,.10);
    margin-bottom: 20px;
}

h1, h2, h3 {
    color: var(--texto);
}

input, textarea, select {
    width: 100%;
    padding: 12px;
    border: 1px solid #c9b4d1;
    border-radius: 9px;
    margin: 7px 0 15px;
    font-size: 16px;
}

label {
    font-weight: bold;
    display: block;
}

.grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(230px, 1fr));
    gap: 16px;
}

.trabalho img {
    width: 100%;
    height: auto;
    object-fit: contain;
    border-radius: 10px;
}

.trabalho {
    overflow: visible;
}

.flash {
    padding: 12px;
    border-radius: 9px;
    margin-bottom: 12px;
    background: #f4e7fa;
}

.erro {
    background: #ffe1e1;
    color: #8c1d2c;
}

.sucesso {
    background: #e1f6e8;
    color: #176b36;
}

.whatsapp {
    background: #168b54;
}

.perigo {
    background: #b33b55;
}

.agendamento {
    border: 1px solid #e3d6e9;
    padding: 15px;
    border-radius: 12px;
    margin: 12px 0;
}

.preview {
    max-width: 100%;
    max-height: 450px;
    border-radius: 12px;
}

footer {
    text-align: center;
    padding: 25px;
}

footer a {
    font-size: 11px;
    color: var(--texto);
}

.horarios {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
}

.horarios span {
    background: #efe2f4;
    padding: 8px;
    border-radius: 8px;
}

@media(max-width: 600px) {
    nav a {
        font-size: 13px;
        padding: 9px;
    }

    .card {
        padding: 16px;
    }
}

</style>
</head>

<body>

<header>
    <img src="/static/logo.png" alt="KF Ateliê">
</header>

{% if not admin %}
<nav>
    <a href="/">Início</a>
    <a href="/gerar">Gerar Roupa</a>
    <a href="/provador">Provador Virtual</a>
    <a href="/trabalhos">Meus Trabalhos / Portfólio</a>
    <a href="/agendar">Agendar Horário</a>
</nav>
{% endif %}

<main>

{% with mensagens = get_flashed_messages(with_categories=true) %}
{% for categoria, mensagem in mensagens %}
<div class="flash {{ categoria }}">
    {{ mensagem }}
</div>
{% endfor %}
{% endwith %}

{{ conteudo|safe }}

</main>

<footer>
    <p>📱 ZAP: 38 99944-2556</p>
    <p>📸 Instagram: @k_f_atelie_de_costura</p>
    <p>⏰ Segunda a Sábado das 10h às 21h</p>

    <a href="/admin">Área da Dona</a>
</footer>

</body>
</html>
"""


def pagina(titulo, conteudo, admin=False, **dados):

    conteudo_html = render_template_string(
        conteudo,
        **dados
    )

    return render_template_string(
        BASE_HTML,
        titulo=titulo,
        conteudo=conteudo_html,
        admin=admin
    )


def extensao_permitida(nome):

    return (
        "." in nome
        and nome.rsplit(".", 1)[1].lower()
        in EXTENSOES_PERMITIDAS
    )


def dia_e_horario_validos(data_texto, hora):

    try:
        data_obj = datetime.strptime(
            data_texto, "%Y-%m-%d"
        ).date()
    except ValueError:
        return False

    return (
        data_obj.weekday() <= 5
        and hora in HORARIOS
    )


def numero_whatsapp(numero):

    return "".join(
        c for c in str(numero)
        if c.isdigit()
    )

# ==========================================
# PÁGINA INICIAL
# ==========================================

@app.route("/")
def inicio():

    conteudo = """
    <section class="card" style="text-align:center">

        <h1>KF Ateliê by Keila Fiuza</h1>

        <h2>Seu estilo, sua personalidade!</h2>

        <p>
            Transformamos ideias em peças especiais.
            Conheça nossos trabalhos e agende seu horário.
        </p>

        <p>📱 WhatsApp: 38 99944-2556</p>
        <p>📸 Instagram: KF ateliê</p>
        <p>⏰ Segunda a Sábado: 10h às 21h</p>

        <a class="botao whatsapp"
        href="https://wa.me/5538999442556"
        target="_blank">
            CHAMAR NO WHATSAPP
        </a>

    </section>

    <section class="card">

        <h2>Conheça o KF Ateliê</h2>

        <div class="grid">

            <div>
                <h3>👗 Gerar Roupa</h3>
                <p>Crie modelos de roupas com inteligência artificial.</p>
                <a class="botao" href="/gerar">Acessar</a>
            </div>

            <div>
                <h3>🪞 Provador Virtual</h3>
                <p>Visualize uma roupa em uma foto sua.</p>
                <a class="botao" href="/provador">Acessar</a>
            </div>

            <div>
                <h3>🧵 Portfólio</h3>
                <p>Veja os trabalhos do ateliê.</p>
                <a class="botao" href="/trabalhos">Acessar</a>
            </div>

            <div>
                <h3>📅 Agendamento</h3>
                <p>Escolha seu melhor horário.</p>
                <a class="botao" href="/agendar">Acessar</a>
            </div>

        </div>

    </section>
    """


    return pagina("Início", conteudo)


# ==========================================
# LOGO
# ==========================================

@app.route("/logo.png")
def logo():

    return send_from_directory(
        BASE_DIR,
        "logo.png"
    )


# ==========================================
# GERADOR DE ROUPAS
# ==========================================

@app.route("/gerar", methods=["GET", "POST"])
def gerar():

    resultado = None
    erro = None
    prompt = ""

    if request.method == "POST":

        prompt = request.form.get(
            "prompt", ""
        ).strip()

        if not prompt:

            erro = "Descreva a roupa desejada."

        else:

            try:

                resultado = gerar_com_flux(prompt)

            except Exception as e:

                print("[ERRO GERADOR]", e)

                erro = str(e)

    conteudo = """
    <section class="card">

        <h1>Gerar Roupa</h1>

        <p>
            Descreva a roupa que deseja criar.
        </p>

        {% if erro %}
        <div class="flash erro">{{ erro }}</div>
        {% endif %}

        <form method="POST">

            <label>Descrição da roupa</label>

            <textarea
                name="prompt"
                rows="5"
                required>{{ prompt }}</textarea>

            <button id="btn-gerar" class="botao" type="submit">
  GERAR ROUPA
</button>
<div id="aviso-gerar" style="display:none; margin-top:15px; font-weight:bold;">
  Criando sua roupa... 💛 Aguarde, não recarregue e não clique de novo!
</div>

<script>
let jaEnviouGerar = false;
document.querySelector('form').addEventListener('submit', function(e) {
  if (jaEnviouGerar) {
    e.preventDefault();
    return;
  }
  jaEnviouGerar = true;
  document.getElementById('btn-gerar').textContent = 'CRIANDO... AGUARDE 💛';
  document.getElementById('btn-gerar').disabled = true;
  document.getElementById('aviso-gerar').style.display = 'block';
});
</script>

        </form>

        {% if resultado %}

        <h2>Resultado</h2>

        <img
            class="preview"
            src="{{ resultado }}"
            alt="Roupa gerada">

                <a href="{{ resultado }}" download="atelie-kf-provador.jpg" style="display:block; background:#22c55e; color:white; padding:12px; text-align:center; border-radius:10px; text-decoration:none; font-weight:bold; margin-top:10px;">
              ⬇️ Baixar minha foto
            </a>
            

        {% endif %}

    </section>
    """

    return pagina(
        "Gerar Roupa",
        conteudo,
        resultado=resultado,
        erro=erro,
        prompt=prompt
    )

# ==========================================
# PROVADOR VIRTUAL
# ==========================================

@app.route("/provador", methods=["GET", "POST"])
def provador():

    resultado = None
    erro = None

    if request.method == "POST":

        foto = request.files.get("foto")

        descricao = request.form.get(
            "descricao", ""
        ).strip()

        if not foto or not foto.filename:

            erro = "Envie uma foto."

        elif not extensao_permitida(foto.filename):

            erro = "Formato de imagem inválido."

        elif not descricao:

            erro = "Descreva a roupa."

        else:

            try:

                conteudo_foto = foto.read()

                tipo = foto.mimetype or "image/jpeg"

                foto_base64 = (
                    f"data:{tipo};base64,"
                    + base64.b64encode(
                        conteudo_foto
                    ).decode("utf-8")
                )

                resultado = gerar_provador(
                    foto_base64,
                    descricao
                )

            except Exception as e:

                print("[ERRO PROVADOR]", e)

                erro = str(e)

    conteudo = """
    <section class="card">

        <h1>Provador Virtual</h1>

        <p>
            Envie uma foto sua e descreva a roupa
            que gostaria de experimentar.
        </p>

        {% if erro %}
        <div class="flash erro">{{ erro }}</div>
        {% endif %}

        <form method="POST" enctype="multipart/form-data">

            <label>Sua fotografia</label>

            <input
                type="file"
                name="foto"
                accept="image/png,image/jpeg,image/webp"
                required>

            <label>Descrição da roupa</label>

            <textarea
                name="descricao"
                rows="4"
                required></textarea>
        
         <button id="btn-provador" class="botao" type="submit">
  EXPERIMENTAR ROUPA
</button>
<div id="aviso-provador" style="display:none; margin-top:15px; font-weight:bold;">
  Gerando sua foto... 💛 Aguarde uns 40 segundos, não recarregue e não clique de novo!
</div>

<script>
let jaEnviouProvador = false;
document.querySelector('form').addEventListener('submit', function(e) {
  if (jaEnviouProvador) {
    e.preventDefault();
    return;
  }
  jaEnviouProvador = true;
  document.getElementById('btn-provador').textContent = 'GERANDO... AGUARDE 💛';
  document.getElementById('btn-provador').disabled = true;
  document.getElementById('aviso-provador').style.display = 'block';
});
</script>

        </form>

        {% if resultado %}

        <h2>Seu resultado</h2><button id="btn-provador" class="botao" type="submit">
  EXPERIMENTAR ROUPA
</button>
<div id="aviso-provador" style="display:none; margin-top:15px; font-weight:bold;">
  Gerando sua foto... 💛 Aguarde uns 40 segundos, não recarregue e não clique de novo!
</div>

<script>
let jaEnviouProvador = false;
document.querySelector('form').addEventListener('submit', function(e) {
  if (jaEnviouProvador) {
    e.preventDefault();
    return;
  }
  jaEnviouProvador = true;
  document.getElementById('btn-provador').textContent = 'GERANDO... AGUARDE 💛';
  document.getElementById('btn-provador').disabled = true;
  document.getElementById('aviso-provador').style.display = 'block';
});
</script>

        <img
            class="preview"
            src="{{ resultado }}"
            alt="Resultado do provador">
                <a href="{{ resultado }}" download="atelie-kf-provador.jpg" style="display:block; background:#22c55e; color:white; padding:12px; text-align:center; border-radius:10px; text-decoration:none; font-weight:bold; margin-top:10px;">
              ⬇️ Baixar minha foto
            </a>
            

        {% endif %}
    </section>
    """

    return pagina(
        "Provador Virtual",
        conteudo,
        resultado=resultado,
        erro=erro
    )


# ==========================================
# PORTFÓLIO
# ==========================================

@app.route("/trabalhos")
def trabalhos():

    lista = carregar_trabalhos()

    conteudo = """
    <section class="card">

        <h1>Meus Trabalhos / Portfólio</h1>

        <p>
            Conheça algumas das peças produzidas
            pelo KF Ateliê.
        </p>

        {% if trabalhos %}

        <div class="grid">

            {% for item in trabalhos %}

            <article class="card trabalho">

                <img
                   src="{{ item.arquivo if item.arquivo.startswith('http') else '/uploads/' + item.arquivo }}"
                    alt="{{ item.titulo }}">

                <h3>{{ item.titulo }}</h3>

                <p>{{ item.descricao }}</p>

            </article>

            {% endfor %}

        </div>

        {% else %}

        <p>Em breve teremos trabalhos cadastrados!</p>

        {% endif %}

    </section>
    """

    return pagina(
        "Portfólio",
        conteudo,
        trabalhos=lista
    )


# ==========================================
# ACESSAR FOTOS
# ==========================================

@app.route("/uploads/<path:nome>")
def uploads(nome):

    return send_from_directory(
        UPLOAD_FOLDER,
        nome
    )

# ==========================================
# AGENDAMENTO
# ==========================================

@app.route("/agendar", methods=["GET", "POST"])
def agendar():

    erro = None
    sucesso = None

    if request.method == "POST":

        nome = request.form.get(
            "nome", ""
        ).strip()

        whatsapp = numero_whatsapp(
            request.form.get("whatsapp", "")
        )

        servico = request.form.get(
            "servico", ""
        ).strip()

        data_texto = request.form.get(
            "data", ""
        )

        hora = request.form.get(
            "hora", ""
        )

        if not all([
            nome,
            whatsapp,
            servico,
            data_texto,
            hora
        ]):

            erro = "Preencha todos os campos."

        elif not dia_e_horario_validos(
            data_texto, hora
        ):

            erro = (
                "Estamos fechados nesse horário. "
                "Funcionamos de Segunda a Sábado "
                "das 10h às 21h"
            )

        elif data_texto in carregar_dias_bloqueados():

            erro = "Agenda fechada neste dia."

        elif data_texto < date.today().isoformat():

            erro = "Não é possível agendar no passado."

        else:

            lista = carregar_agendamentos()

            ocupado = any(
                item["data"] == data_texto
                and item["hora"] == hora
                for item in lista
            )

            if ocupado:

                erro = "Horário já reservado."

            else:

                novo = {
                    "id": uuid.uuid4().hex[:10],
                    "nome": nome,
                    "whatsapp": whatsapp,
                    "servico": servico,
                    "data": data_texto,
                    "hora": hora
                }

                lista.append(novo)

                salvar_json(
                    AGENDAMENTOS_FILE,
                    lista
                )

                mensagem = (
    f"✨ *KF ATELIÊ* ✨\n"
    f"-------------------------\n"
    f"Olá, *{nome}!* 👋\n\n"
    f"Seu horário foi reservado com sucesso! ✅\n\n"
    f"📅 *Data:* {data_texto}\n"
    f"🕙 *Horário:* {hora}\n"
    f"✂️ *Serviço:* {servico}\n"
    f"📍 *Local:* KF Ateliê\n\n"
    f"Te esperamos lá! Qualquer dúvida, é só chamar aqui. 💛\n\n"
    f"_Obrigada pela preferência!_"
)
                link = (
                    "https://wa.me/5538999442556"
                    "?text="
                    + __import__("urllib.parse").parse.quote(
                        mensagem
                    )
                )

                sucesso = link

    conteudo = """
    <section class="card">

        <h1>Agendar Horário</h1>

        
<p style="text-align:center; line-height:1.8; margin: 10px 0 20px 0;">
    ✨ <b>Segunda a Sábado</b><br>
    🕙 <span style="font-size:16px;">das <b>10h às 21h</b></span><br>
    <span style="color:#999; font-size:13px;">— Domingo: fechado —</span>
</p>
        {% if erro %}
        <div class="flash erro">{{ erro }}</div>
        {% endif %}

        {% if sucesso %}
        <div class="flash sucesso">
            Agendamento registrado!
            <p>
                <a class="botao whatsapp"
                   href="{{ sucesso }}"
                   target="_blank">
                   ENVIAR PELO WHATSAPP
                </a>
            </p>
        </div>
        {% endif %}

        <form method="POST">

            <label>Nome</label>
            <input name="nome" required>

            <label>WhatsApp com DDD</label>
            <input name="whatsapp" required>

            <label>Serviço desejado</label>

            <select name="servico" required>
                <option value="">Selecione</option>
                <option>Ajustes e consertos</option>
                <option>Confecção sob medida</option>
                <option>Barra e pequenos reparos</option>
                <option>Prova de roupa</option>
                <option>Outro</option>
            </select>

            <label>Data</label>
            <input
                type="date"
                name="data"
                min="{{ hoje }}"
                required>

            <label>Horário</label>

            <select name="hora" required>
                <option value="">Selecione</option>

                {% for hora in horarios %}
                <option value="{{ hora }}">
                    {{ hora }}
                </option>
                {% endfor %}

            </select>

            <button class="botao" type="submit">
                SOLICITAR AGENDAMENTO
            </button>

        </form>

    </section>
    """

    return pagina(
        "Agendar Horário",
        conteudo,
        erro=erro,
        sucesso=sucesso,
        hoje=date.today().isoformat(),
        horarios=HORARIOS
    )

# ==========================================
# LOGIN ADMINISTRATIVO
# ==========================================

@app.route("/admin", methods=["GET", "POST"])
def admin():

    if request.method == "POST":

        if request.form.get("acao") == "sair":

            session.clear()

            return redirect("/")

        senha = request.form.get("senha", "")

        if senha == SENHA_ADMIN:

            session["admin_logado"] = True

            return redirect("/admin")

        flash("Senha incorreta.", "erro")

    if not session.get("admin_logado"):

        conteudo = """
        <section class="card">

            <h1>Área da Dona</h1>

            <form method="POST">

                <label>Senha de acesso</label>

                <input
                    type="password"
                    name="senha"
                    required>

                <button class="botao">
                    ENTRAR
                </button>

            </form>

        </section>
        """

        return pagina(
            "Área da Dona",
            conteudo,
            admin=True
        )

    lista = sorted(
        carregar_agendamentos(),
        key=lambda x: (x["data"], x["hora"])
    )

    portfolio = carregar_trabalhos()

    bloqueados = carregar_dias_bloqueados()

    conteudo = """
    <section class="card">

        <h1>Painel Administrativo</h1>

        <p>Bem-vinda ao gerenciamento do KF Ateliê!</p>

        <form method="POST">
            <input type="hidden" name="acao" value="sair">
            <button class="botao">SAIR</button>
        </form>

    </section>

    <section class="card">

        <h2>Bloquear um dia</h2>

        <form method="POST" action="/admin/bloquear">

            <label>Escolha a data</label>

            <input
                type="date"
                name="data"
                min="{{ hoje }}"
                required>

            <button class="botao">
                BLOQUEAR DIA
            </button>

        </form>

        <h3>Dias bloqueados</h3>

        {% for dia in bloqueados %}

        <form method="POST" action="/admin/desbloquear">
            <p>{{ dia }}</p>
            <input type="hidden" name="data" value="{{ dia }}">
            <button class="botao">REABRIR DIA</button>
        </form>

        {% endfor %}

    </section>

    <section class="card">

        <h2>Agendamentos</h2>

        {% for item in agendamentos %}

        <div class="agendamento">

            <h3>{{ item.nome }}</h3>

            <p>📅 {{ item.data }} — {{ item.hora }}</p>

            <p>📱 {{ item.whatsapp }}</p>

            <p>✂️ {{ item.servico }}</p>

            <a class="botao whatsapp"
            target="_blank"
            href="https://wa.me/55{{ item.whatsapp }}?text={{ mensagem|urlencode }}">
                CANCELAR E AVISAR
            </a>

            <form method="POST" action="/admin/remarcar">

                <input
                    type="hidden"
                    name="id"
                    value="{{ item.id }}">

                <label>Nova data</label>

                <input
                    type="date"
                    name="data"
                    min="{{ hoje }}"
                    required>

                <label>Novo horário</label>

                <select name="hora" required>

                    {% for hora in horarios %}
                    <option value="{{ hora }}">
                        {{ hora }}
                    </option>
                    {% endfor %}

                </select>

                <button class="botao">
                    REMARCAR
                </button>

            </form>

            <form method="POST" action="/admin/excluir-agendamento">
                <input type="hidden" name="id" value="{{ item.id }}">
                <button class="botao perigo">
                    EXCLUIR AGENDAMENTO
                </button>
            </form>

        </div>

        {% else %}

        <p>Nenhum agendamento encontrado.</p>

        {% endfor %}

    </section>

    <section class="card">

        <h2>Adicionar trabalho</h2>

        <form
            method="POST"
            action="/admin/adicionar-trabalho"
            enctype="multipart/form-data">

            <label>Foto</label>
            <input type="file" name="foto" accept="image/*" required>

            <label>Título</label>
            <input name="titulo" required>

            <label>Descrição</label>
            <textarea name="descricao"></textarea>

            <button class="botao">
                ADICIONAR TRABALHO
            </button>

        </form>

        <h2>Trabalhos cadastrados</h2>

        {% for item in trabalhos %}

        <div class="agendamento">

            <img
                src="{{ item.arquivo }}"
                class="preview">

            <h3>{{ item.titulo }}</h3>

            <p>{{ item.descricao }}</p>

            <form method="POST"
            action="/admin/excluir-trabalho/{{ item.id }}">

                <button class="botao perigo">
                    EXCLUIR TRABALHO
                </button>

            </form>

        </div>

        {% endfor %}

    </section>
    """
    mensagem = (
        "Oi, tudo bem, {nome}? 💛 Aqui é do KF Ateliê!\n\n"
        "Ai, me desculpa! 🥺 Infelizmente não vou conseguir te atender no dia {dia}, tive um imprevisto de saúde e vou precisar descansar um pouquinho.\n\n"
        "Mas não se preocupe, viu? A gente remarca! ✨\n"
        "Me fala um dia que fica bom para você que eu já deixo reservado na agenda, combinado? 🗓️💕\n\n"
        "Obrigada pela compreensão! 🙏"
    )

    return pagina(
        "Painel Administrativo",
        conteudo,
        admin=True,
        agendamentos=lista,
        trabalhos=portfolio,
        bloqueados=bloqueados,
        hoje=date.today().isoformat(),
        horarios=HORARIOS,
        mensagem=mensagem
    )

# ==========================================
# BLOQUEAR DIA
# ==========================================

@app.route("/admin/bloquear", methods=["POST"])
@admin_obrigatorio
def bloquear_dia():

    data_texto = request.form.get("data", "")

    try:
        data_obj = datetime.strptime(
            data_texto, "%Y-%m-%d"
        ).date()

        if data_obj < date.today():
            flash("Escolha uma data válida.", "erro")
        else:
            lista = carregar_dias_bloqueados()

            if data_texto not in lista:
                lista.append(data_texto)

            salvar_json(
                DIAS_BLOQUEADOS_FILE,
                lista
            )

            flash("Agenda fechada neste dia.", "sucesso")

    except ValueError:
        flash("Data inválida.", "erro")

    return redirect("/admin")


# ==========================================
# DESBLOQUEAR DIA
# ==========================================

@app.route("/admin/desbloquear", methods=["POST"])
@admin_obrigatorio
def desbloquear_dia():

    data_texto = request.form.get("data")

    lista = [
        dia
        for dia in carregar_dias_bloqueados()
        if dia != data_texto
    ]

    salvar_json(
        DIAS_BLOQUEADOS_FILE,
        lista
    )

    flash("Dia reaberto.", "sucesso")

    return redirect("/admin")


# ==========================================
# REMARCAR AGENDAMENTO
# ==========================================

@app.route("/admin/remarcar", methods=["POST"])
@admin_obrigatorio
def remarcar():

    identificador = request.form.get("id")

    nova_data = request.form.get("data")

    nova_hora = request.form.get("hora")

    if not dia_e_horario_validos(
        nova_data, nova_hora
    ):

        flash("Horário inválido.", "erro")

        return redirect("/admin")

    if nova_data in carregar_dias_bloqueados():

        flash("Agenda fechada neste dia.", "erro")

        return redirect("/admin")

    lista = carregar_agendamentos()

    ocupado = any(
        item["id"] != identificador
        and item["data"] == nova_data
        and item["hora"] == nova_hora
        for item in lista
    )

    if ocupado:

        flash("Horário já reservado.", "erro")

        return redirect("/admin")

    encontrado = False

    for item in lista:

        if item["id"] == identificador:

            item["data"] = nova_data
            item["hora"] = nova_hora

            encontrado = True

            break

    if encontrado:

        salvar_json(
            AGENDAMENTOS_FILE,
            lista
        )

        flash("Agendamento remarcado!", "sucesso")

    else:

        flash("Agendamento não encontrado.", "erro")

    return redirect("/admin")


# ==========================================
# EXCLUIR AGENDAMENTO
# ==========================================

@app.route("/admin/excluir-agendamento", methods=["POST"])
@admin_obrigatorio
def excluir_agendamento():

    identificador = request.form.get("id")

    lista = carregar_agendamentos()

    lista = [
        item
        for item in lista
        if item["id"] != identificador
    ]

    salvar_json(
        AGENDAMENTOS_FILE,
        lista
    )

    flash("Agendamento excluído.", "sucesso")

    return redirect("/admin")


# ==========================================
# ADICIONAR TRABALHO
# ==========================================

@app.route("/admin/adicionar-trabalho", methods=["POST"])
@admin_obrigatorio
def adicionar_trabalho():

    foto = request.files.get("foto")

    titulo = request.form.get(
        "titulo", ""
    ).strip()

    descricao = request.form.get(
        "descricao", ""
    ).strip()

    if not foto or not foto.filename or not titulo:

        flash("Informe o título e selecione uma foto.", "erro")

        return redirect("/admin")

    if not extensao_permitida(foto.filename):

        flash("Formato de imagem inválido.", "erro")

        return redirect("/admin")
    nome_original = secure_filename(
        foto.filename
    )

    extensao = nome_original.rsplit(
        ".", 1
    )[1].lower()

    nome_arquivo = (
        uuid.uuid4().hex + ".jpg"
    )

    # Manda pro Cloudinary em vez de salvar no Render
    try:
        import cloudinary
        import cloudinary.uploader

        cloudinary.config(
            cloud_name=os.environ.get("CLOUDINARY_CLOUD_NAME"),
            api_key=os.environ.get("CLOUDINARY_API_KEY"),
            api_secret=os.environ.get("CLOUDINARY_API_SECRET")
        )

        foto.seek(0)
        upload_result = cloudinary.uploader.upload(
            foto,
            public_id=nome_arquivo.rsplit(".", 1)[0],
            folder="kf-atelie",
            resource_type="image"
        )

        url_imagem = upload_result.get("secure_url")
        lista = carregar_trabalhos()
        lista.append({
            "id": uuid.uuid4().hex,
            "titulo": titulo,
            "descricao": descricao,
            "arquivo": url_imagem
        })
        salvar_json(TRABALHOS_FILE, lista)
        flash("Trabalho adicionado!", "sucesso")
        return redirect("/admin")

    except Exception as e:
        print("[ERRO CLOUDINARY]", e)
        flash("Erro ao enviar a foto para o Cloudinary.", "erro")
        return redirect("/admin")

 
# ==========================================
# EXCLUIR TRABALHO
# ==========================================

@app.route(
    "/admin/excluir-trabalho/<trabalho_id>",
    methods=["POST"]
)
@admin_obrigatorio
def excluir_trabalho(trabalho_id):

    lista = carregar_trabalhos()
    removidos = [item for item in lista if str(item.get("id")) == str(trabalho_id)]
    lista = [item for item in lista if str(item.get("id")) != str(trabalho_id)]
    salvar_json(TRABALHOS_FILE, lista)

    for item in removidos:

        caminho = os.path.join(
            UPLOAD_FOLDER,
            os.path.basename(item["arquivo"])
        )

        if os.path.isfile(caminho):
            os.remove(caminho)

    flash("Trabalho excluído.", "sucesso")

    return redirect("/admin")


# ==========================================
# ERRO DE ARQUIVO GRANDE
# ==========================================

@app.errorhandler(413)
def arquivo_grande(erro):

    return (
        "Arquivo muito grande. "
        "Envie uma imagem menor que 12 MB.",
        413
    )


# ==========================================
# INICIAR SISTEMA
# ==========================================
@app.route('/loaderio-f488d573a62d709942d416f7ca95b72c.txt')
def verifica_loader():
    return 'loaderio-f488d573a62d709942d416f7ca95b72c'


# ==========================================
# SISTEMA DE FILA DO PROVADOR - KF ATELIÊ
# ==========================================

import threading
import uuid
import base64
from flask import jsonify

# Armazenamento temporário dos trabalhos
PROVADOR_JOBS = {}

# Proteção para acesso simultâneo
PROVADOR_LOCK = threading.Lock()


def executar_trabalho_provador(job_id, foto_base64, descricao):

    try:
        # Executa a função original do provador
        resultado = gerar_provador(foto_base64, descricao)

        with PROVADOR_LOCK:
            PROVADOR_JOBS[job_id]["resultado"] = resultado
            PROVADOR_JOBS[job_id]["status"] = "concluido"

    except Exception as erro:

        with PROVADOR_LOCK:
            PROVADOR_JOBS[job_id]["status"] = "erro"
            PROVADOR_JOBS[job_id]["erro"] = str(erro)


# Rota para iniciar uma geração em segundo plano
@app.route("/iniciar_provador", methods=["POST"])
def iniciar_provador():

    foto = request.files.get("foto")
    descricao = request.form.get("descricao", "").strip()

    if not foto or not foto.filename:
        return jsonify({"erro": "Selecione uma foto."}), 400

    if not descricao:
        return jsonify({"erro": "Informe a descrição da roupa."}), 400

    try:
        imagem = foto.read()

        foto_base64 = (
            f"data:{foto.mimetype or 'image/jpeg'};base64,"
            + base64.b64encode(imagem).decode("utf-8")
        )

        job_id = str(uuid.uuid4())

        with PROVADOR_LOCK:
            PROVADOR_JOBS[job_id] = {
                "status": "gerando",
                "resultado": None,
                "erro": None
            }

        thread = threading.Thread(
            target=executar_trabalho_provador,
            args=(job_id, foto_base64, descricao),
            daemon=True
        )

        thread.start()

        return jsonify({
            "job_id": job_id,
            "status": "gerando"
        })

    except Exception as erro:
        return jsonify({"erro": str(erro)}), 500


# Rota para consultar o andamento da geração
@app.route("/status_provador/<job_id>", methods=["GET"])
def status_provador(job_id):

    with PROVADOR_LOCK:
        trabalho = PROVADOR_JOBS.get(job_id)

        if trabalho is None:
            return jsonify({
                "status": "nao_encontrado"
            }), 404

        return jsonify({
            "status": trabalho["status"],
            "resultado": trabalho["resultado"],
            "erro": trabalho["erro"]
        })

if __name__ == "__main__":

    print("=" * 50)

    print("KF ATELIÊ BY KEILA FIUZA")

    print("Servidor iniciando...")

    print("Token Replicate:",
          "CONFIGURADO" if REPLICATE_API_TOKEN else "NÃO CONFIGURADO")

    print("Site: http://127.0.0.1:5000")

    print("Admin: http://127.0.0.1:5000/admin")

    print("=" * 50)

    app.run(
        debug=True,
        host="127.0.0.1",
        port=5000
    )