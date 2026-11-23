import os
import csv
import logging
import time
import queue
import threading
from pathlib import Path
import requests

def _carregar_tag_info():
    """Carrega as tags usando a configuração e o CSV do projeto atual."""
    try:
        from config import TAG_INFO_CSV
        csv_configurado = Path(TAG_INFO_CSV)
    except Exception:
        csv_configurado = None

    try:
        from config import TAG_INFO
        if isinstance(TAG_INFO, dict) and TAG_INFO:
            return TAG_INFO
    except Exception:
        pass

    base_dir = Path(__file__).resolve().parent
    csv_candidates = [csv_configurado, base_dir / "tag_info.csv"]

    for caminho in csv_candidates:
        if caminho is not None and caminho.exists():
            tag_info = {}
            try:
                with caminho.open("r", encoding="utf-8", newline="") as arquivo:
                    leitor = csv.DictReader(arquivo)
                    for linha in leitor:
                        tag_id = (linha.get("tag_id") or "").strip()
                        if not tag_id:
                            continue
                        nome = (linha.get("nome") or "Nome não cadastrado").strip() or "Nome não cadastrado"
                        tag_info[tag_id] = {
                            "nome": nome,
                            "tipo": linha.get("tipo"),
                            "valor": linha.get("valor"),
                            "peso": linha.get("peso"),
                            "mestra": linha.get("mestra", "").strip().lower() in {"1", "true", "yes"},
                        }
                if tag_info:
                    return tag_info
            except Exception as exc:
                logging.warning("Não foi possível carregar TAG_INFO do CSV %s: %s", caminho, exc)

    return {}


TAG_INFO = _carregar_tag_info()

from config import BOT_TOKEN, CHAT_ID

BOT_TOKEN = str(BOT_TOKEN).strip()
CHAT_ID = str(CHAT_ID).strip()


def _resolver_log_path():
    """Resolve o caminho do log preferindo o projeto atual (2.0)."""
    base_dir = Path(__file__).resolve().parent
    candidatos = [
        Path("/home/raspberry/comedouros-automaticos_2.0/core/cocho_log.txt"),
        base_dir / "cocho_log.txt",
        base_dir.parent / "cocho_log.txt",
        Path("/home/raspberry/comedouros-automaticos/core/cocho_log.txt"),
    ]
    for caminho in candidatos:
        if caminho and caminho.exists():
            return str(caminho)
    return str(base_dir / "cocho_log.txt")


LOG_FILE_PATH = _resolver_log_path()

def _obter_info_animal(tag_id):
    """Retorna os dados da tag, aceitando IDs vindos de leitores ou CSVs."""
    return TAG_INFO.get(str(tag_id).strip(), {})


def _formatar_numero(valor, casas=3):
    """Formata pesos sem falhar quando o CSV fornece texto ou valor vazio."""
    try:
        return f"{float(valor):.{casas}f}"
    except (TypeError, ValueError):
        return str(valor) if valor not in (None, "") else "N/A"


def _escapar_markdown(texto: str) -> str:
    """
    Escapa caracteres especiais para o modo MarkdownV2 do Telegram.
    """
    caracteres_a_escapar = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    texto_str = str(texto)
    for char in caracteres_a_escapar:
        texto_str = texto_str.replace(char, f"\\{char}")
    return texto_str

#mensagens esperando envio; o loop do cocho so coloca aqui e segue, quem envia e a thread rodar_envio_telegram
FILA_TELEGRAM = queue.Queue(maxsize=100)
TENTATIVAS_ENVIO_TELEGRAM = 3
#(conectar, esperar resposta) em segundos
TIMEOUT_TELEGRAM = (3, 5)

#reaproveita a conexao HTTPS entre as mensagens (so a thread do Telegram usa)
_sessao_telegram = requests.Session()


def enviar_alerta_telegram(mensagem):
    """
    Coloca a mensagem na fila do Telegram e retorna na hora, sem esperar a internet.
    O envio de verdade é feito pela thread rodar_envio_telegram.
    """
    try:
        FILA_TELEGRAM.put_nowait(mensagem)
        return True
    except queue.Full:
        logging.error("Fila do Telegram cheia; mensagem descartada.")
        return False


def rodar_envio_telegram(parar):
    """
    Loop da thread do Telegram: tira as mensagens da fila, na ordem, e envia.
    Em falha de rede tenta de novo algumas vezes; se o Telegram recusar a mensagem, descarta.
    """
    while not parar.is_set():
        try:
            mensagem = FILA_TELEGRAM.get(timeout=1)
        except queue.Empty:
            continue

        try:
            for tentativa in range(1, TENTATIVAS_ENVIO_TELEGRAM + 1):
                resultado = _postar_telegram(mensagem)
                if resultado is not None: #enviado (True) ou recusado (False): nao adianta repetir
                    break
                if tentativa < TENTATIVAS_ENVIO_TELEGRAM and parar.wait(2 * tentativa):
                    break
            else:
                logging.error("Telegram sem conexão; mensagem descartada após %s tentativas.", TENTATIVAS_ENVIO_TELEGRAM)
        finally:
            FILA_TELEGRAM.task_done()


def _postar_telegram(mensagem):
    """
    Envia a mensagem para o Telegram (bloqueia; só deve rodar na thread do Telegram).
    Retorna True se enviou, False se o Telegram recusou e None em falha de rede.
    """
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or BOT_TOKEN).strip()
    chat_id = (os.environ.get("TELEGRAM_CHAT_ID") or CHAT_ID).strip()

    if not token or not chat_id:
        faltantes = []
        if not token:
            faltantes.append("TELEGRAM_BOT_TOKEN")
        if not chat_id:
            faltantes.append("TELEGRAM_CHAT_ID")
        logging.error(
            "Telegram nao configurado; variavel(is) ausente(s): %s",
            ", ".join(faltantes),
        )
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": mensagem,
        "parse_mode": "MarkdownV2"
    }
    
    try:
        response = _sessao_telegram.post(url, data=payload, timeout=TIMEOUT_TELEGRAM)
    except requests.RequestException as e:
        logging.error(f"Erro de conexão ao tentar enviar alerta para o Telegram: {e}")
        return None

    try:
        resposta = response.json()
        if response.status_code == 200 and resposta.get("ok") is True:
            logging.info("Alerta enviado com sucesso para o Telegram.")
            return True
        else:
            logging.error(
                "Falha ao enviar alerta para o Telegram. Status: %s, Resposta: %s",
                response.status_code,
                resposta,
            )
            return False
    except ValueError as e:
        logging.error(f"Resposta inválida do Telegram (status {response.status_code}): {e}")
        return False

def notificar_subida_animal(tag_id):
    """
    Cria e envia uma notificação para quando o animal sobe na balança e é pesado.
    """
    info_animal = _obter_info_animal(tag_id)
    nome_animal = info_animal.get('nome', 'Nome não cadastrado')
    
    nome_animal_escaped = _escapar_markdown(nome_animal)
    tag_id_escaped = _escapar_markdown(tag_id)

    mensagem = (
        f" *ANIMAL NA BALANÇA* \n\n"
        f"Um animal foi identificado e pesado com sucesso\\.\n\n"
        f"*Nome do Animal:* {nome_animal_escaped}\n"
        f"*Tag do Animal:* `{tag_id_escaped}`\n"
        f"Aguardando o animal finalizar a alimentação\\."
    )
    
    return enviar_alerta_telegram(mensagem)

def notificar_bloqueio_alimentacao(tag_id):
    """
    Cria e envia uma notificação para quando um animal é identificado,
    mas não pode ser alimentado.
    """
    info_animal = _obter_info_animal(tag_id)
    nome_animal = info_animal.get('nome', 'Nome não cadastrado')
    
    nome_animal_escaped = _escapar_markdown(nome_animal)
    tag_id_escaped = _escapar_markdown(tag_id)

    mensagem = (
        f" *ANIMAL BLOQUEADO* \n\n"
        f"Um animal foi identificado na balança, mas não pode ser alimentado agora\n\n"
        f"*Motivo:* Já consumiu a porção diária\n"
        f"*Nome do Animal:* {nome_animal_escaped}\n"
        f"*Tag do Animal:* `{tag_id_escaped}`"
    )
    
    return enviar_alerta_telegram(mensagem)

def notificar_descida_animal(tag_id, tempo_permanencia):
    """
    Cria e envia uma notificação para quando o animal desce da balança.
    """
    info_animal = _obter_info_animal(tag_id)
    nome_animal = info_animal.get('nome', 'Nome não cadastrado')

    nome_animal_escaped = _escapar_markdown(nome_animal)
    tag_id_escaped = _escapar_markdown(tag_id)
    tempo_permanencia_escaped = _escapar_markdown(tempo_permanencia)

    mensagem = (
        f" *ANIMAL SAIU DA BALANÇA* \n\n"
        f"O animal finalizou a alimentação e deixou o cocho\\.\n\n"
        f"*Nome do Animal:* {nome_animal_escaped}\n"
        f"*Tag do Animal:* `{tag_id_escaped}`\n"
        f"*Tempo de Permanência:* {tempo_permanencia_escaped}\n"
    )

    return enviar_alerta_telegram(mensagem)


def notificar_discrepancia_peso(tag_id, peso_medido, ultimo_peso, discrepancia_percentual):
    """
    Cria e envia uma notificação detalhada sobre uma discrepância de peso detectada.
    """
    info_animal = _obter_info_animal(tag_id)
    nome_animal = info_animal.get('nome', 'Nome não cadastrado')

    sinal = '+' if discrepancia_percentual > 0 else ''
    emoji_status = "" if discrepancia_percentual > 0 else ""

    ultimo_peso_str = _formatar_numero(ultimo_peso, 2)
    peso_medido_str = _formatar_numero(peso_medido, 2)
    discrepancia_str = f"{sinal}{_formatar_numero(discrepancia_percentual, 2)}"

    nome_animal_escaped = _escapar_markdown(nome_animal)
    tag_id_escaped = _escapar_markdown(tag_id)
    ultimo_peso_escaped = _escapar_markdown(ultimo_peso_str)
    peso_medido_escaped = _escapar_markdown(peso_medido_str)
    discrepancia_escaped = _escapar_markdown(discrepancia_str)
    
    mensagem = (
        f" *ALERTA: DISCREPÂNCIA DE PESO* \n\n"
        f"O peso medido para o animal está fora da tolerância esperada de ±20%\.\n\n"
        f"*Nome do Animal:* {nome_animal_escaped}\n"
        f"*Tag do Animal:* `{tag_id_escaped}`\n\n"
        f"*Último Peso Válido:* `{ultimo_peso_escaped} kg`\n"
        f"*Peso Medido Agora:* `{peso_medido_escaped} kg`\n"
        f"*Discrepância:* `{discrepancia_escaped}%` {emoji_status}\n\n"
        f"A medição foi descartada por segurança\. Verifique a balança ou o posicionamento do animal\."
    )
    
    return enviar_alerta_telegram(mensagem)

def notificar_erro_tara_e_reinicio():
    """
    Cria e envia uma notificação genérica informando que o sistema
    detectou um erro de tara e reiniciou o ciclo automaticamente.
    """
    mensagem = (
        f" *ANIMAL NÃO CONFIRMADO NO COCHO* \n\n"
        f" *SISTEMA REINICIADO AUTOMATICAMENTE* \n\n"
        f"O sistema detectou um erro de tara na balança (peso negativo) e reiniciou o ciclo para correção.\n\n"
        f"A tag de um animal foi lida, mas seu peso não foi confirmado na balança dentro do tempo limite.\n\n"
        f"*{'Causa Provável:'}* O animal passou perto do leitor de tag, mas não subiu na plataforma de pesagem.\n\n"
        f"*Ação:* Uma nova tara da balança foi solicitada para garantir a precisão das próximas pesagens."
    )
    
    return enviar_alerta_telegram(mensagem)

def notificar_motor_infinito(tag_id):
    """
    Cria e envia uma notificação para quando um animal é identificado,
    mas não pode ser alimentado.
    """
    info_animal = _obter_info_animal(tag_id)
    nome_animal = info_animal.get('nome', 'Nome não cadastrado')
    
    nome_animal_escaped = _escapar_markdown(nome_animal)
    tag_id_escaped = _escapar_markdown(tag_id)

    mensagem = (
        f" *MOTOR PARADO A FORCA POR SEGURANCA* \n\n"
        f"O motor ficou girando sem fio pois nao havia racao no cocho\n\n"
        f"*Motivo:* Provavelmente nao colocaram racao\n"
        f"*Nome do Animal:* {nome_animal_escaped}\n"
        f"*Tag do Animal:* `{tag_id_escaped}`"
    )
    
    return enviar_alerta_telegram(mensagem)

def notificar_relatorio_alimentacao(dados_relatorio):
    """
    Cria e envia uma notificação para o Telegram com os detalhes do relatório de alimentação.
    Esta versão usa a função auxiliar _escapar_markdown em todas as variáveis.
    """
    nome_animal = dados_relatorio.get("nome", "Animal Desconhecido")
    tag_id = dados_relatorio.get("tag_id", "N/A")
    hora_entrada = dados_relatorio.get("hora_entrada", "N/A")
    hora_saida = dados_relatorio.get("hora_saida", "N/A")
    tempo_cocho = dados_relatorio.get("tempo_cocho", "N/A")
    peso_animal = dados_relatorio.get("peso_animal", 0)
    peso_racao = dados_relatorio.get("peso_racao", 0)

    
    nome_animal_escaped = _escapar_markdown(nome_animal)
    tag_id_escaped = _escapar_markdown(tag_id)
    hora_entrada_escaped = _escapar_markdown(hora_entrada)
    hora_saida_escaped = _escapar_markdown(hora_saida)
    tempo_cocho_escaped = _escapar_markdown(tempo_cocho)
    
    peso_animal_escaped = _escapar_markdown(_formatar_numero(peso_animal))
    peso_racao_escaped = _escapar_markdown(_formatar_numero(peso_racao))

    mensagem = (
        f" *Relatório de Alimentação* \n\n"
        f"Um animal finalizou a alimentação no cocho\n\n"
        f"*Animal:* {nome_animal_escaped}\n"
        f"*Tag:* `{tag_id_escaped}`\n\n"
        f"*Entrada:* {hora_entrada_escaped}\n"
        f"*Saída:* {hora_saida_escaped}\n"
        f"*Tempo no Cocho:* {tempo_cocho_escaped}\n\n"
        f"*Peso do Animal:* {peso_animal_escaped} kg\n"
        f"*Ração Consumida:* {peso_racao_escaped} g"
    )
    
    return enviar_alerta_telegram(mensagem)


def notificar_bateria_baixa(percentual, tensao):
    """
    Avisa que a bateria da Raspberry chegou no nivel de alerta (chamada uma vez por descarga).
    """
    percentual_escaped = _escapar_markdown(f"{percentual:.0f}")
    tensao_escaped = _escapar_markdown(f"{tensao:.2f}")

    mensagem = (
        f" *BATERIA BAIXA* \n\n"
        f"A bateria que alimenta o comedouro está com pouca carga\\.\n\n"
        f"*Nível:* {percentual_escaped}%\n"
        f"*Tensão:* {tensao_escaped} V\n\n"
        f"Recarregue ou troque a bateria para o cocho não desligar\\."
    )

    return enviar_alerta_telegram(mensagem)


async def enviar_log_telegram(caminho_arquivo=LOG_FILE_PATH):
    """
    Esta função lê um arquivo de log e o envia como um documento
    para um grupo específico no Telegram.
    """
    try:
        import telegram
    except ImportError:
        print("ERRO: pacote 'telegram' nao instalado. Instale python-telegram-bot para enviar logs.")
        return False

    print(f"Tentando enviar o arquivo de log '{caminho_arquivo}' para o Telegram...")
    
    try:
        bot = telegram.Bot(token=BOT_TOKEN)
        
        with open(caminho_arquivo, 'rb') as arquivo_log:

            await bot.send_document(chat_id=CHAT_ID, document=arquivo_log, caption=" Aqui está o log mais recente do cocho.")
        
        print("Arquivo de log enviado com sucesso para o Telegram!")
        return True

    except FileNotFoundError:
        print(f"ERRO: O arquivo de log '{caminho_arquivo}' não foi encontrado.")

        try:
            bot = telegram.Bot(token=BOT_TOKEN)
            await bot.send_message(chat_id=CHAT_ID, text=f" Falha ao enviar o log: arquivo '{caminho_arquivo}' não encontrado.")
        except Exception as e:
            print(f"Erro adicional ao tentar notificar sobre o arquivo não encontrado: {e}")
        return False
        
    except Exception as e:
        print(f"ERRO: Ocorreu um erro inesperado ao enviar o log: {e}")
        return False


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    parar = threading.Event()
    threading.Thread(target=rodar_envio_telegram, args=(parar,), daemon=True).start()

    print("Colocando na fila as mensagens de teste de SUBIDA e DESCIDA...")
    notificar_subida_animal('49C33000E280699500005014D639954A')
    notificar_descida_animal('49C33000E280699500005s014D639954A', "5m 32s")

    FILA_TELEGRAM.join() #espera a thread enviar tudo
    parar.set()
    print("Fila do Telegram vazia. Verifique o Telegram (e o log acima em caso de falha).")
