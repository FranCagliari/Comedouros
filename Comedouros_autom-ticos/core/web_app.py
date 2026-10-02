import csv
import logging
import os
import statistics
import subprocess
import threading
import time
from collections import deque
from pathlib import Path

from flask import Flask, jsonify, render_template, request
from werkzeug.exceptions import HTTPException
import RPi.GPIO as GPIO

import balanca as bl
import leitor_fonkan as rfid
import motor
from config import BALANCAS, TAG_INFO_CSV

PORTA_SITE = 5004

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)


@app.errorhandler(Exception)
def tratar_erro_como_json(erro):
    #Sem isso, qualquer excecao (ex: pigpio desconectado) vira uma pagina HTML de
    #erro do Flask; o front-end faz `await r.json()` nela, a promise quebra sem
    #aviso nenhum, e o botao parece simplesmente "nao funcionar".
    if isinstance(erro, HTTPException):
        return erro
    logging.exception("Erro nao tratado em uma rota da API")
    return jsonify(ok=False, erro=str(erro)), 500


HARDWARE_LOCK = threading.RLock()
STATUS_LOCK = threading.Lock()
STATUS = {
    "mensagem": "Aguardando inicialização",
    "peso1": None,
    "peso2": None,
    "idade_peso1": None,
    "idade_peso2": None,
    "motor1": "parado",
    "motor2": "parado",
}

LOGS = deque(maxlen=100)

#Calibracao em dois passos, igual para as duas balancas:
#  1o clique: zera a balanca (tara) -> "zerando" -> "aguardando_peso"
#  2o clique: com o peso conhecido em cima, calcula o fator -> None
CALIBRACAO_LOCK = threading.Lock()
CALIBRACAO = {1: None, 2: None}        #None | "zerando" | "aguardando_peso"
CALIBRACAO_INICIO = {1: 0.0, 2: 0.0}
#Calibracao esquecida no meio e cancelada, para a tara automatica do loop voltar a rodar
CALIBRACAO_TEMPO_MAXIMO = 10 * 60
LEITURAS_FATOR = 10

#Travado enquanto o cocho esta em uso: pelo ciclo de alimentacao (main.py) ou por um
#cadastro de ovelhas aberto no site. Os dois usam o mesmo leitor RFID, entao nunca rodam juntos.
USO_COCHO = threading.Lock()
LEITOR_RFID = None
#Sem atividade por esse tempo, o cadastro fecha sozinho para a alimentacao nao ficar pausada
CADASTRO_TEMPO_MAXIMO = 10 * 60
CADASTRO_LOCK = threading.Lock()
CADASTRO = {
    "estado": "inativo",       #inativo | aguardando_tag | tag_nova
    "tag": None,               #tag nova esperando nome, peso e valor
    "ultima_atividade": 0.0,
    "parar": None,             #threading.Event da sessao atual
}


def registrar_status(mensagem):
    with STATUS_LOCK:
        STATUS["mensagem"] = mensagem
        LOGS.appendleft(f"{time.strftime('%H:%M:%S')} - {mensagem}")
    logging.info(mensagem)

def registrar_evento_botao(mensagem):
    """Chamada pelos botões físicos (botoes_manual.py) para o evento aparecer nos logs do site."""
    registrar_status(f"[Botão físico] {mensagem}")

def configurar_leitor_rfid(leitor):
    """Recebe o leitor RFID ja aberto pela main.py (a porta serial so pode ter um dono)."""
    global LEITOR_RFID
    LEITOR_RFID = leitor


def caminho_tag_info():
    configurado = Path(TAG_INFO_CSV)
    if configurado.parent.exists():
        return configurado
    return Path(__file__).resolve().parent / "tag_info.csv"


def atualizar_pesos():
    #Nao acessa o HX711: usa o ultimo peso lido pelo loop principal (bl.ler_peso),
    #para o site nao disputar a balanca com o ciclo do cocho.
    peso1, idade1 = bl.ultimo_peso(1)
    peso2, idade2 = bl.ultimo_peso(2)
    with STATUS_LOCK:
        STATUS["peso1"] = peso1
        STATUS["peso2"] = peso2
        STATUS["idade_peso1"] = idade1
        STATUS["idade_peso2"] = idade2


def ler_balancas_continuamente(intervalo=1):
    """So para quando o web_app roda sozinho: sem o loop da main.py ninguem le as balancas."""
    while True:
        with HARDWARE_LOCK:
            bl.ler_peso(1)
            bl.ler_peso(2)
        time.sleep(intervalo)

def estado_motor(numero):
    direcao, velocidade = motor._obter_estado_ativo(numero)
    return "parado" if velocidade <= 0 else f"{direcao} ({velocidade})"


def status_atual():
    atualizar_pesos()
    with STATUS_LOCK:
        resultado = dict(STATUS)
        resultado["motor1"] = estado_motor(1)
        resultado["motor2"] = estado_motor(2)
        resultado["logs"] = list(LOGS)
    with CADASTRO_LOCK:
        resultado["cadastro"] = {"estado": CADASTRO["estado"], "tag": CADASTRO["tag"]}
    calibracao_em_andamento()  #cancela calibracoes esquecidas
    with CALIBRACAO_LOCK:
        resultado["calibracao"] = {str(numero): estado for numero, estado in CALIBRACAO.items()}
    return resultado


def calibracao_em_andamento():
    """True se alguma balanca esta no meio da calibracao. A main.py usa isso para nao
    refazer a tara automatica com o peso de calibracao em cima da balanca."""
    cancelar = []
    with CALIBRACAO_LOCK:
        for numero, estado in CALIBRACAO.items():
            if estado and time.monotonic() - CALIBRACAO_INICIO[numero] > CALIBRACAO_TEMPO_MAXIMO:
                CALIBRACAO[numero] = None
                cancelar.append(numero)
        em_andamento = any(CALIBRACAO.values())
    for numero in cancelar:
        registrar_status(f"Calibração da balança {numero} cancelada por inatividade.")
    return em_andamento


def zerar_balanca(numero):
    """1o passo da calibracao (roda em thread): mede a tara com a balanca vazia."""
    try:
        with HARDWARE_LOCK:
            bl.calibrar_balanca(numero)
    except Exception as erro:
        with CALIBRACAO_LOCK:
            CALIBRACAO[numero] = None
        registrar_status(f"Erro ao zerar a balança {numero}: {erro}")
        return
    with CALIBRACAO_LOCK:
        CALIBRACAO[numero] = "aguardando_peso"
    registrar_status(
        f"Balança {numero} zerada. Coloque o peso conhecido e clique em Calibrar balança {numero} de novo."
    )


def ler_bruto_mediana(numero, quantidade=LEITURAS_FATOR):
    """Mediana de varias leituras brutas validas: uma leitura so pode vir com ruido ou saturada."""
    config = BALANCAS[numero]
    leituras = []
    for _ in range(quantidade * 2):
        try:
            leituras.append(bl.read_count(config["DT"], config["SCK"]))
        except (TimeoutError, ValueError):
            continue
        if len(leituras) >= quantidade:
            break
    if len(leituras) < quantidade // 2:
        raise RuntimeError("poucas leituras válidas do HX711")
    return statistics.median(leituras)


@app.get("/api/status")
def api_status():
    return jsonify(status_atual())


@app.post("/api/calibrar/<int:numero>")
def api_calibrar(numero):
    if numero not in BALANCAS:
        return jsonify(ok=False, erro="Balança inválida."), 400
    dados = request.get_json(silent=True) or {}
    try:
        peso_conhecido = float(dados.get("peso", 0))
    except (TypeError, ValueError):
        peso_conhecido = 0
    if peso_conhecido <= 0:
        return jsonify(ok=False, erro="Informe um peso conhecido maior que zero."), 400

    with CALIBRACAO_LOCK:
        estado = CALIBRACAO[numero]
        if estado is None:
            CALIBRACAO[numero] = "zerando"
            CALIBRACAO_INICIO[numero] = time.monotonic()
    if estado is None:
        registrar_status(f"Zerando a balança {numero}: mantenha a balança sem peso.")
        threading.Thread(target=zerar_balanca, args=(numero,), daemon=True).start()
        return jsonify(
            ok=True, etapa="tara",
            mensagem=f"Retire o peso da balança {numero}. Depois coloque o peso conhecido e clique novamente.",
        )
    if estado == "zerando":
        return jsonify(ok=False, erro=f"Aguarde: a balança {numero} ainda está sendo zerada."), 409

    try:
        with HARDWARE_LOCK:
            leitura_bruta = ler_bruto_mediana(numero)
            tara = BALANCAS[numero]["tara"]
            fator = (leitura_bruta - tara) / peso_conhecido
            if fator == 0:
                raise ValueError("fator calculado igual a zero (o peso está na balança?)")
            BALANCAS[numero]["fator"] = fator
            leitura = bl.calculo_peso(tara, leitura_bruta, fator)
    except Exception as erro:
        registrar_status(f"Erro ao calibrar a balança {numero}: {erro}. Clique de novo para tentar outra vez.")
        return jsonify(ok=False, erro=str(erro)), 500
    with CALIBRACAO_LOCK:
        CALIBRACAO[numero] = None
    registrar_status(f"Balança {numero} calibrada (fator {fator:.3f}).")
    return jsonify(ok=True, leitura=leitura, fator=fator)


@app.post("/api/motor/<int:numero>")
def api_motor(numero):
    if numero not in (1, 2):
        return jsonify(ok=False, erro="Motor inválido."), 400
    dados = request.get_json(silent=True) or {}
    direcao = dados.get("direcao", "horario")
    velocidade = int(dados.get("velocidade", 150))
    try:
        with HARDWARE_LOCK:
            motor._definir_estado_manual(numero, direcao, velocidade)
    except Exception as erro:
        registrar_status(f"Erro ao ligar o motor {numero}: {erro}")
        return jsonify(ok=False, erro=str(erro)), 500
    registrar_status(f"Motor {numero} ligado ({direcao}, velocidade {velocidade}).")
    return jsonify(ok=True)


@app.post("/api/motor/<int:numero>/parar")
def api_parar_motor(numero):
    if numero not in (1, 2):
        return jsonify(ok=False, erro="Motor inválido."), 400
    try:
        with HARDWARE_LOCK:
            motor._definir_estado_manual(numero, "parado", 0)
            motor._liberar_controle_manual(numero)
    except Exception as erro:
        registrar_status(f"Erro ao parar o motor {numero}: {erro}")
        return jsonify(ok=False, erro=str(erro)), 500
    registrar_status(f"Motor {numero} parado.")
    return jsonify(ok=True)


# ---------- Cadastro de ovelhas pelo RFID ----------

def ler_tags_cadastradas():
    """Retorna {tag_id: nome} do tag_info.csv."""
    with caminho_tag_info().open("r", encoding="utf-8", newline="") as arquivo:
        return {
            str(registro.get("tag_id", "")).strip().upper(): registro.get("nome", "")
            for registro in csv.DictReader(arquivo)
        }


def salvar_ovelha_csv(tag_id, nome, peso, valor):
    caminho = caminho_tag_info()
    campos = ["tag_id", "tipo", "valor", "nome", "peso", "mestra"]
    with caminho.open("r", encoding="utf-8", newline="") as arquivo:
        registros = list(csv.DictReader(arquivo))
    registros.append({
        "tag_id": tag_id,
        "tipo": "percentual",
        "valor": valor,
        "nome": nome,
        "peso": peso,
        "mestra": "False",
    })
    with caminho.open("w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=campos, extrasaction="ignore")
        escritor.writeheader()
        escritor.writerows(registros)


def encerrar_cadastro(mensagem):
    """Fecha a sessao de cadastro (uma unica vez) e libera o cocho para a alimentacao."""
    with CADASTRO_LOCK:
        if CADASTRO["estado"] == "inativo":
            return False
        CADASTRO["parar"].set()
        CADASTRO.update(estado="inativo", tag=None, parar=None)
        USO_COCHO.release()
    registrar_status(mensagem)
    return True


def sessao_cadastro(parar):
    """Thread do cadastro: le tags ate achar uma nova, ou ate o cadastro ser finalizado."""
    ultima_tag_avisada = None  #evita repetir o aviso enquanto a mesma tag fica perto do leitor
    while not parar.is_set():
        with CADASTRO_LOCK:
            estado = CADASTRO["estado"]
            ocioso = time.monotonic() - CADASTRO["ultima_atividade"]
        if ocioso > CADASTRO_TEMPO_MAXIMO:
            encerrar_cadastro("Cadastro encerrado por inatividade. Alimentação retomada.")
            return
        if estado != "aguardando_tag":
            parar.wait(0.3)
            continue

        tag = rfid.normalizar_tag_id(rfid.ler_tags(LEITOR_RFID, timeout=1, verbose=False))
        if not tag or parar.is_set():
            continue

        try:
            cadastradas = ler_tags_cadastradas()
        except Exception as erro:
            registrar_status(f"Erro ao ler o tag_info.csv: {erro}")
            parar.wait(2)
            continue

        if tag in cadastradas:
            if tag != ultima_tag_avisada:
                registrar_status(
                    f"Tag {tag} já cadastrada ({cadastradas[tag]}). "
                    "Aproxime outra tag ou clique em Finalizar cadastro."
                )
                ultima_tag_avisada = tag
            continue

        ultima_tag_avisada = tag
        with CADASTRO_LOCK:
            if parar.is_set():
                return
            CADASTRO.update(estado="tag_nova", tag=tag, ultima_atividade=time.monotonic())
        registrar_status(f"Tag nova reconhecida ({tag}). Digite o nome, o peso e o valor do animal.")


@app.post("/api/cadastro/iniciar")
def api_cadastro_iniciar():
    if LEITOR_RFID is None:
        return jsonify(ok=False, erro="Leitor RFID não disponível. Verifique a conexão USB do leitor."), 503
    with CADASTRO_LOCK:
        if CADASTRO["estado"] != "inativo":
            return jsonify(ok=True)
        if not USO_COCHO.acquire(blocking=False):
            return jsonify(ok=False, erro="Há um animal no cocho. Aguarde o fim da alimentação para cadastrar."), 409
        parar = threading.Event()
        CADASTRO.update(estado="aguardando_tag", tag=None, ultima_atividade=time.monotonic(), parar=parar)
    registrar_status("Cadastro iniciado. Alimentação pausada. Aproxime a tag do animal do leitor.")
    threading.Thread(target=sessao_cadastro, args=(parar,), daemon=True).start()
    return jsonify(ok=True)


@app.post("/api/cadastro/salvar")
def api_cadastro_salvar():
    dados = request.get_json(silent=True) or {}
    nome = str(dados.get("nome", "")).strip()
    try:
        peso = float(dados.get("peso", ""))
        valor = float(dados.get("valor", ""))
    except (TypeError, ValueError):
        return jsonify(ok=False, erro="Peso e valor precisam ser números."), 400
    if not nome or peso <= 0 or valor <= 0:
        return jsonify(ok=False, erro="Informe o nome, e peso e valor maiores que zero."), 400

    with CADASTRO_LOCK:
        if CADASTRO["estado"] != "tag_nova":
            return jsonify(ok=False, erro="Nenhuma tag nova reconhecida. Aproxime a tag do leitor."), 409
        tag = CADASTRO["tag"]
        try:
            if tag in ler_tags_cadastradas():
                raise ValueError(f"a tag {tag} já está cadastrada")
            salvar_ovelha_csv(tag, nome, peso, valor)
        except Exception as erro:
            registrar_status(f"Erro ao cadastrar {nome}: {erro}")
            return jsonify(ok=False, erro=str(erro)), 500
        CADASTRO.update(estado="aguardando_tag", tag=None, ultima_atividade=time.monotonic())

    registrar_status(
        f"{nome} cadastrada (tag {tag}, {peso:g} kg, valor {valor:g}). "
        "Aproxime a próxima tag ou clique em Finalizar cadastro."
    )
    return jsonify(ok=True, tag=tag)


@app.post("/api/cadastro/finalizar")
def api_cadastro_finalizar():
    encerrar_cadastro("Cadastro finalizado. Alimentação retomada.")
    return jsonify(ok=True)


@app.post("/api/reiniciar")
def api_reiniciar():
    try:
        subprocess.Popen(["sudo", "reboot", "0"])
    except Exception as erro:
        registrar_status(f"Erro ao reiniciar a Raspberry Pi: {erro}")
        return jsonify(ok=False, erro=str(erro)), 500
    registrar_status("Reiniciando a Raspberry Pi...")
    return jsonify(ok=True)


@app.get("/")
def pagina():
    return render_template("index.html")


if __name__ == "__main__":
    try:
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BOARD)
        for numero, configuracao in BALANCAS.items():
            bl.setup_balanca(configuracao["DT"], configuracao["SCK"])
        motor.setup_todos_os_motores()
        registrar_status("Site pronto.")
    except Exception as erro:
        registrar_status(f"Hardware não inicializado: {erro}")

    configurar_leitor_rfid(rfid.iniciar_leitor())
    threading.Thread(target=ler_balancas_continuamente, daemon=True).start()

    app.run(host="0.0.0.0", port=PORTA_SITE)
