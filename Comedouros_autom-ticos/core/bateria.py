"""Leitura da tensao da bateria pelo ADS1112 (I2C), em uma thread propria.

Especificacao da placa (ads_orientacao.txt):
  - ADS1112 no barramento /dev/i2c-1, endereco 0x48, alimentado pelos 3,3 V da Raspberry
    (SDA pino 3, SCL pino 5, GND pino 6, VDD pino 1). O 3,3 V NAO e referencia de calculo.
  - Configuracao: um unico byte 0x0C enviado direto, sem ponteiro de registrador
    (AIN0-AIN1, conversao continua, 15 SPS, 16 bits, ganho 1, faixa +/-2,048 V).
  - Leitura: 3 bytes (MSB, LSB, configuracao/status). Bit 7 do status = 0 -> resultado novo.
    (status & 0x7F) diferente de 0x0C -> reconfigurar.
  - Tensao: v_adc = raw * 2,048 / 32768 ; v_bateria = v_adc * BATERIA_MULTIPLICADOR (9,260252,
    que ja inclui o divisor e a calibracao da montagem).
  - Erro I2C: nao mostrar leitura antiga como atual, esperar 2 s e reconfigurar.
  - |raw| >= 32760: perto da saturacao, leitura nao confiavel.

O loop principal do cocho nao espera por nada daqui: o site e o Telegram so leem o
ultimo resultado guardado em ESTADO (com a trava _LOCK).
"""
import logging
import threading
import time
from collections import deque

from config import (
    BATERIA_ALERTA_PCT,
    BATERIA_BARRAMENTO_I2C,
    BATERIA_CURVA,
    BATERIA_ENDERECO,
    BATERIA_ESPERA_APOS_CARGA,
    BATERIA_INTERVALO_LEITURA,
    BATERIA_JANELA_MEDIA,
    BATERIA_MULTIPLICADOR,
    BATERIA_REARMAR_PCT,
    BATERIA_TENSAO_CARREGANDO,
    BATERIA_TENSAO_FIM_CARGA,
)

try:
    from smbus2 import SMBus, i2c_msg
except ImportError:  #sem a biblioteca o resto do sistema continua funcionando
    SMBus = None
    i2c_msg = None

CONFIG_ADS1112 = 0x0C          #AIN0-AIN1, continua, 15 SPS, 16 bits, ganho 1
REFERENCIA_V = 2.048           #referencia interna do ADS1112 (nao usar os 3,3 V)
ESPERA_APOS_CONFIGURAR = 0.25
ESPERA_APOS_ERRO = 2.0
LIMITE_SATURACAO = 32760
#15 SPS: um resultado novo a cada ~67 ms; se o status disser "ja lido", espera um pouco e tenta de novo
ESPERA_RESULTADO_NOVO = 0.07
TENTATIVAS_RESULTADO_NOVO = 3

_LOCK = threading.Lock()
ESTADO = {
    "estado": "aguardando",    #aguardando | ok | erro | saturado | indisponivel
    "tensao": None,            #media das ultimas leituras (V)
    "tensao_instantanea": None,
    "percentual": None,        #durante a carga: ultimo valor medido em repouso (ou None)
    "fase": "repouso",         #repouso | carregando | estabilizando (carga acabou, tensao ainda alta)
    "baixa": False,            #True enquanto o alerta de bateria baixa estiver ativo
    "mensagem": "aguardando a primeira leitura",
    "momento": None,           #time.monotonic() da ultima leitura valida
}


class ErroADS1112(Exception):
    """Status do ADS1112 diferente do configurado: precisa reconfigurar."""


def _configurar(bus):
    bus.i2c_rdwr(i2c_msg.write(BATERIA_ENDERECO, [CONFIG_ADS1112]))
    time.sleep(ESPERA_APOS_CONFIGURAR)


def _ler_raw(bus, parar):
    """Retorna o valor bruto (int com sinal, 16 bits) de um resultado novo, ou None se nao chegou."""
    for _ in range(TENTATIVAS_RESULTADO_NOVO):
        msg = i2c_msg.read(BATERIA_ENDERECO, 3)
        bus.i2c_rdwr(msg)
        msb, lsb, status = list(msg)
        if (status & 0x7F) != CONFIG_ADS1112:
            raise ErroADS1112(f"configuração lida 0x{status & 0x7F:02X} diferente da configuração 0x{CONFIG_ADS1112:02X}")
        if status & 0x80:  #bit 7 = 1: resultado ja lido, aguarda a proxima conversao
            if parar.wait(ESPERA_RESULTADO_NOVO):
                return None
            continue
        raw = (msb << 8) | lsb
        if raw >= 0x8000:
            raw -= 0x10000
        return raw
    return None


def tensao_de_raw(raw):
    v_adc = raw * (REFERENCIA_V / 32768.0)
    return v_adc * BATERIA_MULTIPLICADOR


def percentual_de_tensao(tensao):
    """Interpola a porcentagem na BATERIA_CURVA [(tensao, %), ...], limitada a 0-100."""
    curva = sorted(BATERIA_CURVA)
    if tensao <= curva[0][0]:
        return float(curva[0][1])
    if tensao >= curva[-1][0]:
        return float(curva[-1][1])
    for (v0, p0), (v1, p1) in zip(curva, curva[1:]):
        if v0 <= tensao <= v1:
            return p0 + (p1 - p0) * (tensao - v0) / (v1 - v0)
    return float(curva[-1][1])


#Painel solar carregando: a tensao nos bornes sobe (13,5-14,4 V) e nao indica a carga real.
#Enquanto carrega, e por BATERIA_ESPERA_APOS_CARGA depois, a % fica no ultimo valor em repouso.
#Com o sol nascendo a tensao sobe devagar e passa pela faixa de 100% antes de chegar no limite de
#carga: por isso a % congelada e a menor dos ultimos HISTORICO_REPOUSO_S segundos em repouso.
HISTORICO_REPOUSO_S = 300
_CARGA = {"fase": "repouso", "fim": 0.0, "pct_repouso": None, "historico": deque()}


def _atualizar_fase(media, agora):
    """Atualiza a fase de carga pela media da tensao e retorna a fase atual."""
    fase_anterior = _CARGA["fase"]
    if media >= BATERIA_TENSAO_CARREGANDO:
        _CARGA["fase"] = "carregando"
    elif fase_anterior == "carregando" and media < BATERIA_TENSAO_FIM_CARGA:
        _CARGA["fase"] = "estabilizando"
        _CARGA["fim"] = agora
    elif fase_anterior == "estabilizando" and agora - _CARGA["fim"] >= BATERIA_ESPERA_APOS_CARGA:
        _CARGA["fase"] = "repouso"
    if _CARGA["fase"] != fase_anterior:
        if _CARGA["fase"] == "carregando" and _CARGA["historico"]:
            _CARGA["pct_repouso"] = min(pct for _, pct in _CARGA["historico"])
        _CARGA["historico"].clear()
        textos = {
            "carregando": f"Bateria carregando ({media:.2f} V). Porcentagem congelada até a carga terminar.",
            "estabilizando": f"Carga da bateria encerrada ({media:.2f} V). Aguardando a tensão estabilizar.",
            "repouso": f"Bateria em repouso ({media:.2f} V). Porcentagem voltou a ser calculada.",
        }
        print(f"[BATERIA] {textos[_CARGA['fase']]}")
    return _CARGA["fase"]


def _atualizar(**campos):
    with _LOCK:
        ESTADO.update(campos)


def status_bateria():
    """Copia do ultimo estado, com a idade da leitura em segundos (para o site)."""
    with _LOCK:
        resultado = dict(ESTADO)
    momento = resultado.pop("momento")
    resultado["idade"] = None if momento is None else round(time.monotonic() - momento, 1)
    return resultado


def _verificar_alerta(percentual, tensao, ao_ficar_baixa):
    """Histerese: avisa uma vez ao chegar em BATERIA_ALERTA_PCT e so rearma acima de BATERIA_REARMAR_PCT."""
    with _LOCK:
        baixa = ESTADO["baixa"]
        disparar = not baixa and percentual <= BATERIA_ALERTA_PCT
        rearmar = baixa and percentual >= BATERIA_REARMAR_PCT
        if disparar:
            ESTADO["baixa"] = True
        elif rearmar:
            ESTADO["baixa"] = False
    if disparar:
        print(f"[BATERIA] Bateria baixa: {percentual:.0f}% ({tensao:.2f} V)")
        if ao_ficar_baixa:
            try:
                ao_ficar_baixa(percentual, tensao)
            except Exception:
                logging.exception("Erro ao avisar bateria baixa")
    elif rearmar:
        print(f"[BATERIA] Bateria recarregada: {percentual:.0f}% ({tensao:.2f} V). Alerta rearmado.")


def rodar_leitura_bateria(parar, ao_ficar_baixa=None):
    """Loop da thread da bateria. `ao_ficar_baixa(percentual, tensao)` e chamada uma vez por descarga."""
    if SMBus is None:
        _atualizar(estado="indisponivel", mensagem="biblioteca smbus2 não instalada")
        print("[BATERIA] smbus2 não instalada: leitura da bateria desativada (pip install smbus2).")
        return

    janela = deque(maxlen=max(1, int(BATERIA_JANELA_MEDIA)))
    ultimo_erro = None  #so imprime quando o erro muda, para nao encher o terminal a cada 2 s
    bus = None

    while not parar.is_set():
        try:
            if bus is None:
                bus = SMBus(BATERIA_BARRAMENTO_I2C)
                _configurar(bus)

            raw = _ler_raw(bus, parar)
            if raw is None:
                parar.wait(BATERIA_INTERVALO_LEITURA)
                continue

            if raw >= LIMITE_SATURACAO or raw <= -LIMITE_SATURACAO:
                janela.clear()
                _atualizar(
                    estado="saturado", tensao=None, tensao_instantanea=None, percentual=None,
                    mensagem=f"entrada do ADC perto da saturação (raw {raw}); leitura não confiável",
                )
                if ultimo_erro != "saturado":
                    print(f"[BATERIA] Aviso: ADC perto da saturação (raw {raw}). Leitura descartada.")
                    ultimo_erro = "saturado"
                parar.wait(BATERIA_INTERVALO_LEITURA)
                continue

            if ultimo_erro:
                print("[BATERIA] Leitura da bateria normalizada.")
                ultimo_erro = None

            tensao = tensao_de_raw(raw)
            janela.append(tensao)
            #media das ultimas leituras: a queda de tensao quando os motores ligam nao faz a % pular
            media = sum(janela) / len(janela)
            agora = time.monotonic()
            fase = _atualizar_fase(media, agora)
            if fase == "repouso":
                percentual = percentual_de_tensao(media)
                _CARGA["pct_repouso"] = percentual
                historico = _CARGA["historico"]
                historico.append((agora, percentual))
                while historico[0][0] < agora - HISTORICO_REPOUSO_S:
                    historico.popleft()
            else:
                percentual = _CARGA["pct_repouso"]  #tensao de carga nao serve para calcular a %
            _atualizar(
                estado="ok", tensao=round(media, 3), tensao_instantanea=round(tensao, 3),
                percentual=None if percentual is None else round(percentual, 1),
                fase=fase, mensagem="", momento=agora,
            )
            #so decide o alerta em repouso e com a janela cheia (evita alarme falso logo apos ligar)
            if fase == "repouso" and len(janela) == janela.maxlen:
                _verificar_alerta(percentual, media, ao_ficar_baixa)
            parar.wait(BATERIA_INTERVALO_LEITURA)

        except (OSError, ErroADS1112) as erro:
            texto = f"Erro na leitura do ADS1112 ({BATERIA_ENDERECO:#04x}, i2c-{BATERIA_BARRAMENTO_I2C}): {erro}"
            if texto != ultimo_erro:
                print(f"[BATERIA] {texto}. Nova tentativa a cada {ESPERA_APOS_ERRO:g} s.")
                ultimo_erro = texto
            janela.clear()
            #nao mostra leitura antiga como atual
            _atualizar(estado="erro", tensao=None, tensao_instantanea=None, percentual=None, mensagem=str(erro))
            if bus is not None:
                try:
                    bus.close()
                except OSError:
                    pass
                bus = None  #reabre o barramento e reconfigura o ADC na proxima volta
            parar.wait(ESPERA_APOS_ERRO)

    if bus is not None:
        try:
            bus.close()
        except OSError:
            pass


if __name__ == "__main__":
    #Teste isolado: python3 bateria.py (Ctrl+C para sair)
    parar = threading.Event()
    threading.Thread(
        target=rodar_leitura_bateria,
        args=(parar, lambda pct, v: print(f"*** ALERTA (Telegram): {pct:.0f}% {v:.2f} V ***")),
        daemon=True,
    ).start()
    try:
        while True:
            time.sleep(0.5)
            s = status_bateria()
            if s["estado"] == "ok":
                pct = "—" if s["percentual"] is None else f"{s['percentual']:.0f}"
                print(f"Bateria: {s['tensao_instantanea']:.3f} V (média {s['tensao']:.3f} V) | {pct} % | {s['fase']}")
            else:
                print(f"Bateria: {s['estado']} - {s['mensagem']}")
    except KeyboardInterrupt:
        parar.set()
        print("Encerrado.")
