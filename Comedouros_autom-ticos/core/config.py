import datetime as dt
import RPi.GPIO as GPIO
import pigpio


# --- Sensor de reflexivo ---
SENSOR_1_PIN = 40
SENSOR_2_PIN = 38
SENSOR_1_PRESENCA_NIVEL = GPIO.HIGH
SENSOR_2_PRESENCA_NIVEL = GPIO.HIGH
SENSOR_1_PULL_UP_DOWN = GPIO.PUD_UP
SENSOR_2_PULL_UP_DOWN = GPIO.PUD_UP
SENSOR_1_CONFIRMATION_TIME = 0.5
SENSOR_1_RELEASE_CONFIRMATION_TIME = 1.0
SENSOR_2_RELEASE_CONFIRMATION_TIME = 1.0
SENSOR_1_POLL_INTERVAL = 0.05
SENSOR_2_POLL_INTERVAL = 0.05
SENSOR_2_CONFIRMATION_TIME = 0.5
TEMPO_ESPERA_RETORNO_ANIMAL = 10 #sera que é isso?

# --- CONFIGURAÇÕES DE HARDWARE (PINOS GPIO BOARD) ---
PINO_RELE = 10
PINO_PWM = 12 
PINO_BOTAO_MANUAL_MOTOR1 = 36
PINO_BOTAO_MANUAL_MOTOR2 = 26
BOTAO_PRESSIONADO = GPIO.HIGH
BOTAO_SOLTO = GPIO.LOW
BOTAO_CALIBRAR1 = 24
BOTAO_CALIBRAR2 = 32

# --- SERVICO DO SYSTEMD ---
#Nome do servico que roda a main.py na Raspberry; o site mostra o journal dele na area de logs
SERVICO_SISTEMA = "novo_comedouro.service"

# --- CONFIGURAÇÕES DO LEITOR RFID ---
RFID_PORTA_SERIAL = "/dev/ttyUSB0"
RFID_BAUDRATE = 38400
RFID_POTENCIA_DB = 15
TAGS_RFID_IGNORADAS = {
    "0E8A3000E2801191A50400721456F326",
}

# --- CONFIGURAÇÕES DA BALANCA ---
BALANCAS = {
        1: {"DT": 15, "SCK": 13, "fator": 130776, "tara": 0},  # Balança da ração
        2: {"DT": 7, "SCK": 11, "fator": -7228.267, "tara": 0},   # Balança do animal
    }

# Pesos conhecidas usadas nos tres apertos do botao de recalibracao, em kg.
# Altere estes valores para corresponder aos pesos realmente colocados.
# Segurar o botao de calibracao por esse tempo (s) cancela a calibracao em andamento.
TEMPO_CANCELAR_CALIBRACAO = 5

PESOS_CALIBRACAO_KG = {
    1: (0.1, 0.2, 0.3),
    2: (1.0, 2.0, 3.0),
}


# --- Dados Relatorio cocho csv ---

#autorizações para acesar os serviços
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


LOCAL_RELATORIO_CSV = "/home/raspberry/comedouros_3/Comedouros/Comedouros_autom-ticos/core/relatorio_cocho.csv"
LOCAL_CREDENCIAL = "/home/raspberry/comedouros_3/Comedouros/Comedouros_autom-ticos/core/SheetsKey.json"
NOME_PLANILHA = "Relatorio_Cocho" 

# --- Dados tag_info.csv ---

TAG_INFO_CSV = "/home/raspberry/comedouros_3/Comedouros/Comedouros_autom-ticos/core/tag_info.csv"

# Dados Telegram


BOT_TOKEN = "8130076024:AAH3WOv1xJAT5ylbZRuGJmMMlkb3b2Exf18"
CHAT_ID = "-1002684732798"
LOG_FILE_PATH = "/home/raspberry/comedouros-automaticos/core/cocho_log.txt"

# --- BATERIA (ADS1112 via I2C) ---
# Placa: SDA pino 3, SCL pino 5, GND pino 6, VDD 3,3 V pino 1. Bateria no J2 (AIN0-AIN1).
BATERIA_BARRAMENTO_I2C = 1        # /dev/i2c-1
BATERIA_ENDERECO = 0x48
# Ja inclui o divisor (9,92k / 1,218k) e a calibracao feita com multimetro (12,720 V).
# Aplicado sobre v_adc em volts. Nao aplicar outro fator de divisor.
BATERIA_MULTIPLICADOR = 9.260252
BATERIA_INTERVALO_LEITURA = 0.5   # segundos entre leituras
# Leituras usadas na media (20 x 0,5 s = 10 s): a queda de tensao com os motores ligados
# nao faz a porcentagem pular nem dispara o alerta sozinha.
BATERIA_JANELA_MEDIA = 20
# Tensao (V) -> carga (%). Valores tipicos de bateria chumbo-acido 12 V em repouso.
# AJUSTAR conforme a bateria usada (ex: litio 3S: 12.6 V = 100%, 9.9 V = 0%).
BATERIA_CURVA = [
    (10.50, 0),
    (11.31, 10),
    (11.58, 20),
    (11.75, 30),
    (11.90, 40),
    (12.06, 50),
    (12.20, 60),
    (12.32, 70),
    (12.42, 80),
    (12.50, 90),
    (12.70, 100),
]
BATERIA_ALERTA_PCT = 20     # manda o aviso no Telegram ao chegar nessa carga
BATERIA_REARMAR_PCT = 30    # so avisa de novo depois de a bateria voltar acima disso (recarga)
# Carga pelo painel solar: com a tensao acima disso a bateria e considerada carregando e a % fica
# congelada no ultimo valor medido em repouso (a tensao de carga nao indica a carga real).
BATERIA_TENSAO_CARREGANDO = 13.0
BATERIA_TENSAO_FIM_CARGA = 12.9    # abaixo disso a carga terminou (histerese)
BATERIA_ESPERA_APOS_CARGA = 600    # segundos esperando a tensao assentar antes de recalcular a %






