import logs_terminal
logs_terminal.instalar() #antes dos outros imports: tudo que for impresso no terminal vai tambem para o site
import maquina_estados as maq_es
import time
import sensor_reflexivo as sr
import relatorio as rl
import pandas as pd
from config import *
import threading
import botoes_manual as btm
import motor as mt
import sys
import notificacoes as nt
import web_app as web

parar = threading.Event() #sistema para desligar todas as threads
INTERVALO_LEITURA_PESOS = 0.5


def rodar_ciclos(sistemaCocho):
    ultima_recal = 0   
    ultimo_log_pesos = 0
    
    while not parar.is_set(): #loop principal

        #antes só if time.monotonic() - ultimo_log_pesos >= 1:
        if time.monotonic() - ultimo_log_pesos >= INTERVALO_LEITURA_PESOS and not web.calibracao_em_andamento():
            sistemaCocho.print_pesos_reais()
            ultimo_log_pesos = time.monotonic()

        #a cada 30s; pula enquanto o site calibra, para nao tirar a tara com o peso de calibracao em cima
        if time.monotonic() - ultima_recal > 300 and not web.calibracao_em_andamento():
            sistemaCocho.recalibrar_balanca_sem_presenca() #enquanto não há vacas no cocho, o sistema fica recalibrando a balança
            ultima_recal = time.monotonic()

            #FOI TROCADO 
        if sr.confirmar_presenca_sensor('1'): #se tiver vacas no sensor 1, o ciclo se inicia
            #cadastro de ovelhas aberto no site: ele esta usando o leitor RFID, alimentacao pausada
            if not web.USO_COCHO.acquire(blocking=False):
                parar.wait(0.5)
                continue

            try:
                print("presença confirmada no sensor 1, entrando no ciclo cocho")
                resposta = sistemaCocho.executar_um_ciclo()
                print(resposta)

                #RELATORIO TELEGRAM
                if resposta:
                    nt.notificar_relatorio_alimentacao(resposta)

                #SHEETS
                if resposta and list(resposta.values())[0]:
                    sistemaCocho.relatorio_csv = pd.read_csv(LOCAL_RELATORIO_CSV)
                    rl.salvar_registro_csv(sistemaCocho.relatorio_csv, resposta)

                    #substitui o peso do animal pela medição enviada ao Sheets (-1 e -2 mantêm o último peso válido)
                    sistemaCocho.salvar_peso_animal(resposta['tag_id'], resposta.get('peso_animal'))
            finally:
                web.USO_COCHO.release()
        else:
            parar.wait(0.05) #sem animal: evita loop a 100% de CPU disputando o GIL com o site e os botoes

def notificar():
        
    while not parar.is_set():
        enviados = rl.sincronizar_csv_com_sheets()
        if enviados:
            print(f"{enviados} registros novos enviados para o Google Sheets")
        parar.wait(300)

def botao():
    estado1 = BOTAO_SOLTO
    estado2 = BOTAO_SOLTO
    estado3 = BOTAO_SOLTO
    estado4 = BOTAO_SOLTO
    while not parar.is_set():

        estado1 = btm.monitorar_botao_motor(1, estado1)
        estado2 = btm.monitorar_botao_motor(2, estado2)
        estado3 = btm.calibrar(1, estado3)
        estado4 = btm.calibrar(2, estado4)


def rodar_site():
    """Sobe o site (Flask) em paralelo. O hardware ja foi configurado por configurar_cocho()."""
    try:
        web.registrar_status("Interface pronta")
        logs_terminal.iniciar_leitura_servico() #journal do novo_comedouro.service na area de logs do site
        #use_reloader=False: o reloader do Flask so funciona na thread principal
        web.app.run(host="0.0.0.0", port=web.PORTA_SITE, debug=False, use_reloader=False, threaded=True)
    except Exception as erro:
        print(f"Erro ao iniciar o site: {erro}")



def desligar():
    """Para motores e limpa a GPIO. Best-effort, chamado uma vez no fim."""
    parar.set()
    try:
        mt._definir_estado_normal(1, "parado", 0)
        mt._definir_estado_normal(2, "parado", 0)
    except Exception:
        pass
    try:
        GPIO.cleanup()
    except Exception:
        pass
    print("Sistema finalizado.")

if __name__ == "__main__":
    sistemaCocho = maq_es.SistemaCocho()

    try:
        sistemaCocho.configurar_cocho()
        print("\nLeitura inicial das balanças:")
        sistemaCocho.print_pesos_reais()
    except KeyboardInterrupt:
        desligar()
        sys.exit()
        print(f'Sistema finalizado')

    print("\nIniciando sistema principal()... Pressione Ctrl+C para sair.")
    print("-" * 40)

    t1 = threading.Thread(target=rodar_ciclos, args=(sistemaCocho,), daemon = True)
    t2 = threading.Thread(target=notificar, args=(), daemon= True)
    t3 = threading.Thread(target=botao, args=(), daemon = True)

    #FOI ACRESCENTADO
    web.configurar_leitor_rfid(sistemaCocho.leitor_rfid) #o site usa o mesmo leitor no cadastro de ovelhas
    t4 = threading.Thread(target=rodar_site, args=(), daemon = True) #site: encerra junto com o programa

    t5 = threading.Thread(target=nt.rodar_envio_telegram, args=(parar,), daemon = True) #envia o Telegram sem travar o ciclo do cocho

# Iniciando as threads

    t1.start()
    t2.start()
    t3.start()
    t4.start()
    t5.start()
    print(f"Site disponivel em http://<ip-da-raspberry>:{web.PORTA_SITE}")

    try:
        while t1.is_alive():
            time.sleep(0.5)
    except KeyboardInterrupt:
        print(f'\nEncerrando')

    parar.set()
    t1.join(timeout=3)
    t2.join(timeout=3)
    t3.join(timeout=3)
    t4.join(timeout=3)
    t5.join(timeout=3)
    desligar()
