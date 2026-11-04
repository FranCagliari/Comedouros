import pigpio
import time
import logging
import RPi.GPIO as GPIO
import threading
import sys

# ==========================================
# CONFIGURAÇÃO DOS PINOS FÍSICOS (BOARD)
# ==========================================

# --- Motor 1 ---
M1_R_PWM_PIN = 12    # Pino Físico 8
M1_L_PWM_PIN = 	16   # Pino Físico 10
M1_SENSOR_PIN = 18  # Pino Físico 18 (LM358)

# --- Motor 2 ---
M2_R_PWM_PIN = 8   # Pino Físico 12
M2_L_PWM_PIN = 10   # Pino Físico 16
M2_SENSOR_PIN = 22  # Pino Físico 22 (LM358)

# ==========================================
# MAPA DE CONVERSÃO (BOARD -> BCM)
# ==========================================
BOARD_TO_BCM = {
    3: 2, 5: 3, 7: 4, 8: 14, 10: 15, 11: 17, 12: 18, 13: 27, 15: 22,
    16: 23, 18: 24, 19: 10, 21: 9, 22: 25, 23: 11, 24: 8, 26: 7,
    29: 5, 31: 6, 32: 12, 33: 13, 35: 19, 36: 16, 37: 26, 38: 20, 40: 21
}

# Variáveis globais para armazenar os BCMs após o setup
M1_R_BCM, M1_L_BCM, M1_SENS_BCM = None, None, None
M2_R_BCM, M2_L_BCM, M2_SENS_BCM = None, None, None

pi = None
_motor_lock = threading.RLock()
_estado_normal = {
    1: ("parado", 0),
    2: ("parado", 0),
}
_estado_manual = {
    1: None,
    2: None,
}


def _obter_pigpio():
    global pi

    if pi is not None and pi.connected:
        return pi

    try:
        pi = pigpio.pi()
    except Exception as e:
        raise RuntimeError(f"Erro ao inicializar pigpio: {e}") from e

    if not pi.connected:
        raise RuntimeError(
            "Não foi possível conectar ao daemon pigpio. Execute 'sudo pigpiod'."
        )

    return pi


def setup_todos_os_motores():
    """Configura TODOS os pinos (Motor 1 e Motor 2) convertendo BOARD para BCM."""
    global M1_R_BCM, M1_L_BCM, M1_SENS_BCM
    global M2_R_BCM, M2_L_BCM, M2_SENS_BCM

    pigpio_conn = _obter_pigpio()

    logging.info("Configurando pinos (Mapeamento BOARD -> BCM)...")

    # Conversão Motor 1
    M1_R_BCM = BOARD_TO_BCM.get(M1_R_PWM_PIN)
    M1_L_BCM = BOARD_TO_BCM.get(M1_L_PWM_PIN)
    M1_SENS_BCM = BOARD_TO_BCM.get(M1_SENSOR_PIN)

    # Conversão Motor 2
    M2_R_BCM = BOARD_TO_BCM.get(M2_R_PWM_PIN)
    M2_L_BCM = BOARD_TO_BCM.get(M2_L_PWM_PIN)
    M2_SENS_BCM = BOARD_TO_BCM.get(M2_SENSOR_PIN)

    # Verificação de segurança
    if any(p is None for p in [M1_R_BCM, M1_L_BCM, M1_SENS_BCM, M2_R_BCM, M2_L_BCM, M2_SENS_BCM]):
        logging.error("ERRO: Um ou mais pinos definidos não são válidos no mapeamento BOARD -> BCM.")
        sys.exit()

    logging.info(f"Motor 1: R={M1_R_PWM_PIN}->GPIO{M1_R_BCM}, L={M1_L_PWM_PIN}->GPIO{M1_L_BCM}, Sens={M1_SENSOR_PIN}->GPIO{M1_SENS_BCM}")
    logging.info(f"Motor 2: R={M2_R_PWM_PIN}->GPIO{M2_R_BCM}, L={M2_L_PWM_PIN}->GPIO{M2_L_BCM}, Sens={M2_SENSOR_PIN}->GPIO{M2_SENS_BCM}")

    with _motor_lock:
        _estado_normal[1] = ("parado", 0)
        _estado_normal[2] = ("parado", 0)
        _estado_manual[1] = None
        _estado_manual[2] = None

        # Configuração dos pinos de Saída (PWM)
        for pin in [M1_R_BCM, M1_L_BCM, M2_R_BCM, M2_L_BCM]:
            pigpio_conn.set_mode(pin, pigpio.OUTPUT)
            pigpio_conn.set_PWM_frequency(pin, 8000) # 8kHz
            pigpio_conn.set_PWM_range(pin, 255)      # 0-255 range
            pigpio_conn.set_PWM_dutycycle(pin, 0)    # Começa parado

    for pin in [M1_SENS_BCM, M2_SENS_BCM]:
        pigpio_conn.set_mode(pin, pigpio.INPUT)
        pigpio_conn.set_pull_up_down(pin, pigpio.PUD_DOWN)

    logging.info("Todos os pinos configurados com sucesso.")


def _limitar_velocidade(velocidade):
    if velocidade < 0:
        return 0
    if velocidade > 255:
        return 255
    return velocidade

TEMPO_MAXIMO_GIRO = 15
_contagem_giro = {1: 0, 2: 0}

#APÓS 15 SEGUNDOS DO MOTOR MOVENDO ELE É FORÇADO A PARAR
#Usada pelo site: chamar logo depois de _definir_estado_manual, numa thread.
#Desliga so o comando manual que estava ativo na chamada; se antes disso ele for
#parado, trocado (botao fisico) ou o site mandar girar de novo, nao faz nada.
#Retorna True se precisou parar o motor.
def parar_motor(velocidade, motor_id, tempo_maximo=TEMPO_MAXIMO_GIRO):
    if velocidade <= 0:
        return False

    with _motor_lock:
        _contagem_giro[motor_id] += 1
        minha_contagem = _contagem_giro[motor_id]
        comando = _estado_manual[motor_id]

    def comando_ainda_ativo():
        return _contagem_giro[motor_id] == minha_contagem and _estado_manual[motor_id] == comando

    inicio_movimento = time.monotonic()
    while time.monotonic() - inicio_movimento < tempo_maximo:
        with _motor_lock:
            if not comando_ainda_ativo():
                return False

        time.sleep(0.1)

    with _motor_lock:
        if not comando_ainda_ativo():
            return False
        #volta ao estado normal: se o ciclo de alimentacao estiver usando o motor, ele segue no controle
        _estado_manual[motor_id] = None
        _aplicar_estado_motor(motor_id)
    return True



def controlar_motor(direcao, velocidade, motor_id):
    pigpio_conn = _obter_pigpio()
    velocidade = _limitar_velocidade(velocidade)

    BCM_L = M1_L_BCM if motor_id == 1 else M2_L_BCM
    BCM_R = M1_R_BCM if motor_id == 1 else M2_R_BCM

    if direcao == "horario":
        pigpio_conn.set_PWM_dutycycle(BCM_L, 0)
        pigpio_conn.set_PWM_dutycycle(BCM_R, velocidade)
    elif direcao == "antihorario":
        pigpio_conn.set_PWM_dutycycle(BCM_R, 0)
        pigpio_conn.set_PWM_dutycycle(BCM_L, velocidade)
    else:
        pigpio_conn.set_PWM_dutycycle(BCM_R, 0)
        pigpio_conn.set_PWM_dutycycle(BCM_L, 0)


def _aplicar_estado_motor(motor_id):
    direcao, velocidade = _estado_manual[motor_id] or _estado_normal[motor_id]
    controlar_motor(direcao, velocidade, motor_id)


def _definir_estado_normal(motor_id, direcao, velocidade=0):
    with _motor_lock:
        _estado_normal[motor_id] = (direcao, _limitar_velocidade(velocidade))
        _aplicar_estado_motor(motor_id)



#ONDE EU PODERIA APLICAR ISSO??
def _definir_estado_manual(motor_id, direcao, velocidade=255):
    with _motor_lock:
        _estado_manual[motor_id] = (direcao, _limitar_velocidade(velocidade))
        _aplicar_estado_motor(motor_id)


def _liberar_controle_manual(motor_id):
    with _motor_lock:
        _estado_manual[motor_id] = None
        _aplicar_estado_motor(motor_id)


def _obter_estado_ativo(motor_id):
    with _motor_lock:
        return _estado_manual[motor_id] or _estado_normal[motor_id]

def ler_sensor_motor(motor_id):
    """Lê o estado do sensor do Motor (LM358). Retorna 1 (HIGH) ou 0 (LOW)."""

    pinoBcm = M1_SENS_BCM if motor_id == 1 else  M2_SENS_BCM

    if pi is not None and pi.connected and pinoBcm is not None:
        estado = pi.read(pinoBcm)
     #   print(f"Sensor Motor {motor_id}: {'HIGH' if estado else 'LOW'} ({estado}) Pino:18")
        return estado
    return -1


if __name__ == "__main__":
    pass
