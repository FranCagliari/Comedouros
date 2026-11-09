// ---------- Avisos (substituem os alert) ----------

function avisar(mensagem, tipo = 'ok', duracao = 4000) {
    const aviso = document.createElement('div');
    aviso.className = `aviso ${tipo}`;
    aviso.textContent = mensagem;
    document.getElementById('avisos').appendChild(aviso);
    setTimeout(() => {
        aviso.classList.add('saindo');
        setTimeout(() => aviso.remove(), 300);
    }, duracao);
}

// ---------- Chamadas a API ----------

// Desabilita o botao enquanto a requisicao nao volta, para evitar clique duplo
async function post(url, data = {}, botao = null) {
    if (botao) botao.disabled = true;
    try {
        const resposta = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data),
        });
        const corpo = await resposta.json();
        if (!resposta.ok) {
            avisar(corpo.erro || 'Erro', 'erro', 6000);
        }
        return corpo;
    } catch (erro) {
        avisar('Sem conexão com o cocho', 'erro', 6000);
        return { ok: false };
    } finally {
        if (botao) botao.disabled = false;
    }
}

async function motor(numero, botao) {
    const resposta = await post(`/api/motor/${numero}`, {}, botao);
    if (resposta.ok) avisar(`Motor ${numero} ligado`);
    atualizar();
}

async function parar(numero, botao) {
    const resposta = await post(`/api/motor/${numero}/parar`, {}, botao);
    if (resposta.ok) avisar(`Motor ${numero} parado`);
    atualizar();
}

// Calibracao em tres etapas (pesos definidos no config.py, ex: 0.1, 0.2 e 0.3 kg):
// o mesmo botao inicia a calibracao e registra cada peso; o outro botao cancela
async function calibrar(numero, botao) {
    const resposta = await post(`/api/calibrar/${numero}`, {}, botao);
    if (resposta.concluida) {
        avisar(`Balança ${numero} calibrada (fator ${resposta.fator.toFixed(3)})`);
        if (!resposta.salvo) avisar('O fator não pôde ser salvo no config.py', 'erro', 8000);
    } else if (resposta.mensagem) {
        avisar(resposta.mensagem, 'ok', 8000);
    }
    atualizar();
}

async function cancelarCalibracao(numero, botao) {
    const resposta = await post(`/api/calibrar/${numero}/cancelar`, {}, botao);
    if (resposta.ok) avisar(`Calibração da balança ${numero} cancelada`);
    atualizar();
}

// Refaz a tara na hora, sem esperar a tara automatica do loop principal
async function tarar(numero, botao) {
    const texto = botao.textContent;
    botao.textContent = 'Retarando…';
    const resposta = await post(`/api/tarar/${numero}`, {}, botao);
    botao.textContent = texto;
    if (resposta.ok) avisar(`Balança ${numero} retarada`);
    atualizar();
}

// calibracao: { estado: null | "aguardando_peso" | "lendo", etapa, pesos }
function mostrarCalibracao(numero, calibracao) {
    const { estado = null, etapa = 0, pesos = [] } = calibracao || {};
    const bloco = document.getElementById(`calibracao${numero}`);
    const dica = document.getElementById(`calibracaoDica${numero}`);
    const botao = document.getElementById(`btnCalibrar${numero}`);
    const total = pesos.length;
    const peso = pesos[etapa];
    bloco.dataset.estado = estado || '';
    if (estado === 'lendo') {
        dica.textContent = `Etapa ${etapa + 1} de ${total}: lendo a balança ${numero}… não mexa no peso.`;
        botao.textContent = 'Lendo…';
    } else if (estado === 'aguardando_peso') {
        dica.textContent = `Etapa ${etapa + 1} de ${total}: coloque o peso de ${peso} kg na balança ${numero} e clique em Registrar peso.`;
        botao.textContent = `Registrar peso (${peso} kg)`;
    } else {
        dica.textContent = `Balança ${numero}: clique em Calibrar balança ${numero} e siga as ${total || 3} etapas (${pesos.join(', ')} kg).`;
        botao.textContent = `Calibrar balança ${numero}`;
    }
}

// ---------- Cadastro de ovelhas (RFID) ----------

async function iniciarCadastro(botao) {
    const resposta = await post('/api/cadastro/iniciar', {}, botao);
    if (resposta.ok) atualizar();
}

function editandoOvelha() {
    return document.getElementById('cardCadastro').dataset.estado === 'tag_cadastrada';
}

function limparFormulario() {
    document.getElementById('formOvelha').reset();
    atualizarTipoRacao();
}

// Editando um animal ja cadastrado, "Finalizar cadastro" salva o formulario (via salvarOvelha)
async function finalizarCadastro(botao) {
    if (editandoOvelha()) {
        document.getElementById('formOvelha').requestSubmit();
        return;
    }
    const resposta = await post('/api/cadastro/finalizar', {}, botao);
    if (resposta.ok) {
        limparFormulario();
        atualizar();
    }
}

// Descarta a edicao em andamento e encerra o cadastro
async function cancelarCadastro(botao) {
    const resposta = await post('/api/cadastro/cancelar', {}, botao);
    if (resposta.ok) {
        limparFormulario();
        avisar('Cadastro cancelado: nada foi alterado');
        atualizar();
    }
}

async function salvarOvelha(evento) {
    evento.preventDefault();
    const formulario = evento.target;
    const nome = document.getElementById('nome').value;
    const peso = document.getElementById('peso').value;
    const tipo = document.getElementById('tipo').value;
    const valor = document.getElementById('valor').value;
    if (editandoOvelha()) {
        const botao = document.getElementById('btnFinalizarCadastro');
        const resposta = await post('/api/cadastro/finalizar', { nome, peso, tipo, valor }, botao);
        if (resposta.ok) {
            avisar(`Informações de ${nome} atualizadas`);
            limparFormulario();
            atualizar();
        }
        return;
    }
    const botao = document.getElementById('btnSalvarOvelha');
    const resposta = await post('/api/cadastro/salvar', { nome, peso, tipo, valor }, botao);
    if (resposta.ok) {
        avisar(`${nome} cadastrada`);
        formulario.reset();
        atualizarTipoRacao();
        atualizar();
    }
}

// Quantidade de Racao: fixa em kg ou porcentagem do peso do animal
function atualizarTipoRacao() {
    const percentual = document.getElementById('tipo').value === 'percentual';
    const peso = parseFloat(document.getElementById('peso').value);
    const valor = parseFloat(document.getElementById('valor').value);
    document.getElementById('rotuloValor').textContent =
        percentual ? 'Quantidade de Ração (% do peso)' : 'Quantidade de Ração (kg)';
    let dica = percentual
        ? 'Porcentagem do peso do animal, ex: 2 = 2% do peso. Acompanha o peso medido na balança.'
        : 'Quantidade de ração por refeição, em kg.';
    if (percentual && peso > 0 && valor > 0) {
        dica += ` Hoje: ${(peso * valor / 100).toFixed(3)} kg.`;
    }
    document.getElementById('dicaRacao').textContent = dica;
}

let estadoCadastroAnterior = null;
let tagCadastroAnterior = null;

// Coloca no formulario os valores salvos no tag_info.csv do animal ja cadastrado
function preencherFormulario(dados) {
    document.getElementById('nome').value = dados.nome || '';
    document.getElementById('peso').value = dados.peso || '';
    document.getElementById('tipo').value = dados.tipo === 'percentual' ? 'percentual' : 'fixo';
    document.getElementById('valor').value = dados.valor || '';
    atualizarTipoRacao();
}

// estado: "inativo" | "aguardando_tag" | "tag_nova" | "tag_cadastrada" (editando um animal ja cadastrado)
function mostrarCadastro(cadastro) {
    const card = document.getElementById('cardCadastro');
    const texto = document.getElementById('cadastroTexto');
    const estado = cadastro ? cadastro.estado : 'inativo';
    const tag = cadastro ? cadastro.tag : null;
    card.dataset.estado = estado;

    const editando = estado === 'tag_cadastrada';
    document.getElementById('tituloCadastro').textContent = editando ? 'Editar informações' : 'Cadastrar ovelha';

    // preenche so ao entrar na edicao, para nao apagar o que a pessoa esta digitando a cada atualizacao
    if (editando && (estadoCadastroAnterior !== 'tag_cadastrada' || tagCadastroAnterior !== tag)) {
        preencherFormulario(cadastro.dados || {});
    } else if (!editando && estadoCadastroAnterior === 'tag_cadastrada') {
        limparFormulario();  // edicao encerrada (finalizada, cancelada ou por inatividade)
    }

    const liberarCampos = estado === 'tag_nova' || editando;
    for (const id of ['nome', 'peso', 'tipo', 'valor', 'btnSalvarOvelha']) {
        document.getElementById(id).disabled = !liberarCampos;
    }

    if (estado === 'aguardando_tag') {
        texto.textContent = 'Aguardando tag… Aproxime a tag do animal do leitor.';
    } else if (estado === 'tag_nova') {
        texto.innerHTML = 'Tag nova reconhecida. Digite o nome, o peso e a quantidade de ração.<br>';
        const tag = document.createElement('span');
        tag.className = 'cadastro-tag';
        tag.textContent = cadastro.tag;
        texto.appendChild(tag);
    } else if (editando) {
        texto.innerHTML = 'Animal já cadastrado. Altere as informações e clique em Finalizar cadastro para salvar, ou em Cancelar cadastro para descartar.<br>';
        const etiquetaTag = document.createElement('span');
        etiquetaTag.className = 'cadastro-tag';
        etiquetaTag.textContent = cadastro.tag;
        texto.appendChild(etiquetaTag);
    } else {
        texto.textContent = 'Clique em Iniciar cadastro e aproxime a tag do leitor. A alimentação fica pausada durante o cadastro.';
    }

    // leva o cursor para o Nome quando uma tag (nova ou para editar) acaba de ser reconhecida
    if ((estado === 'tag_nova' || editando) && estadoCadastroAnterior !== estado) {
        document.getElementById('nome').focus();
    }
    estadoCadastroAnterior = estado;
    tagCadastroAnterior = tag;
}

// ---------- Lista de ovelhas cadastradas (tag_info.csv) ----------

let ovelhasCadastradas = [];

function formatarKg(kg) {
    return `${kg.toLocaleString('pt-BR', { maximumFractionDigits: 3 })} kg`;
}

// ovelha: { tag, nome, peso, tipo, valor, racao_kg, mestra } (peso/racao_kg null quando nao ha valor valido)
function linhaOvelha(ovelha) {
    const linha = document.createElement('tr');

    const nome = document.createElement('td');
    nome.className = 'ovelha-nome';
    nome.textContent = ovelha.nome || 'Sem nome';
    if (!ovelha.nome) nome.classList.add('vazio');
    if (ovelha.mestra) {
        const etiqueta = document.createElement('span');
        etiqueta.className = 'etiqueta';
        etiqueta.textContent = 'Mestra';
        nome.append(' ', etiqueta);
    }
    const tagCelular = document.createElement('div');
    tagCelular.className = 'tag-celular';
    tagCelular.textContent = ovelha.tag;
    nome.appendChild(tagCelular);

    const tag = document.createElement('td');
    tag.className = 'cadastro-tag coluna-tag';
    tag.textContent = ovelha.tag;

    const peso = document.createElement('td');
    peso.className = 'numero';
    if (ovelha.peso == null) {
        peso.textContent = 'sem peso';
        peso.classList.add('vazio');
    } else {
        peso.textContent = formatarKg(ovelha.peso);
    }

    const racao = document.createElement('td');
    racao.className = 'numero';
    const quantidade = document.createElement('div');
    const detalhe = document.createElement('div');
    detalhe.className = 'racao-detalhe';
    if (ovelha.racao_kg == null) {
        quantidade.textContent = '—';
        quantidade.className = 'vazio';
    } else {
        quantidade.textContent = formatarKg(ovelha.racao_kg);
    }
    if (ovelha.valor == null) {
        detalhe.textContent = 'quantidade inválida';
    } else if (ovelha.tipo === 'percentual') {
        detalhe.textContent = `${ovelha.valor.toLocaleString('pt-BR')}% do peso`
            + (ovelha.racao_kg == null ? ' · falta o peso' : '');
    } else {
        detalhe.textContent = 'quantidade fixa';
    }
    racao.append(quantidade, detalhe);

    linha.append(nome, tag, peso, racao);
    return linha;
}

function filtrarOvelhas() {
    const busca = document.getElementById('filtroOvelhas').value.trim().toLowerCase();
    const corpo = document.getElementById('corpoOvelhas');
    const visiveis = ovelhasCadastradas.filter((ovelha) =>
        !busca || ovelha.nome.toLowerCase().includes(busca) || ovelha.tag.toLowerCase().includes(busca));
    corpo.replaceChildren(...visiveis.map(linhaOvelha));
    if (!visiveis.length) {
        const linha = document.createElement('tr');
        const celula = document.createElement('td');
        celula.colSpan = 4;
        celula.className = 'tabela-vazia';
        celula.textContent = ovelhasCadastradas.length ? 'Nenhuma ovelha encontrada.' : 'Nenhuma ovelha cadastrada.';
        linha.appendChild(celula);
        corpo.appendChild(linha);
    }
}

async function abrirListaOvelhas(botao) {
    const dialogo = document.getElementById('dialogOvelhas');
    const resumo = document.getElementById('resumoOvelhas');
    botao.disabled = true;
    let dados;
    try {
        const resposta = await fetch('/api/ovelhas');
        dados = await resposta.json();
    } catch (erro) {
        avisar('Sem conexão com o cocho', 'erro', 6000);
        return;
    } finally {
        botao.disabled = false;
    }
    if (!dados.ok) {
        avisar(dados.erro || 'Erro ao carregar as ovelhas', 'erro', 6000);
        return;
    }
    ovelhasCadastradas = dados.ovelhas;
    const total = ovelhasCadastradas.length;
    resumo.textContent = dados.aviso
        || `${total} ${total === 1 ? 'ovelha' : 'ovelhas'} no tag_info.csv. Ração por refeição; no tipo % do peso ela acompanha o último peso medido.`;
    document.getElementById('filtroOvelhas').value = '';
    filtrarOvelhas();
    dialogo.showModal();
}

function fecharListaOvelhas() {
    document.getElementById('dialogOvelhas').close();
}

// fecha clicando fora da janela
document.getElementById('dialogOvelhas').addEventListener('click', (evento) => {
    if (evento.target === evento.currentTarget) fecharListaOvelhas();
});

async function reiniciar(botao) {
    if (!confirm('Reiniciar a Raspberry Pi? O cocho fica parado até ela voltar.')) return;
    const resposta = await post('/api/reiniciar', {}, botao);
    if (resposta.ok) avisar('Reiniciando a Raspberry Pi…', 'ok', 10000);
}

async function desligar(botao) {
    if (!confirm('Desligar a Raspberry Pi? O cocho para e só volta tirando e recolocando a fonte da tomada.')) return;
    const resposta = await post('/api/desligar', {}, botao);
    if (resposta.ok) avisar('Desligando a Raspberry Pi… Aguarde o LED verde parar de piscar antes de tirar a fonte.', 'ok', 15000);
}

// ---------- Atualizacao da tela ----------

// idade: segundos desde a ultima leitura feita pelo loop principal
function mostrarPeso(numero, peso, idade) {
    const card = document.getElementById(`cardPeso${numero}`);
    const valor = document.getElementById(`peso${numero}`);
    const rodape = document.getElementById(`idade${numero}`);

    card.classList.remove('antigo', 'erro');
    if (idade == null) {
        valor.textContent = '—';
        rodape.textContent = 'aguardando leitura';
        return;
    }
    if (peso == null) {
        card.classList.add('erro');
        valor.textContent = 'erro';
        rodape.textContent = 'falha na leitura da balança';
        return;
    }
    valor.textContent = peso.toFixed(3);
    if (idade > 5) {
        card.classList.add('antigo');
        rodape.textContent = `última leitura há ${Math.round(idade)}s`;
    } else {
        rodape.textContent = 'atualizado agora';
    }
}

// ultimaTag: null | { tag, nome, cadastrada, hora, idade } (nome buscado no tag_info.csv)
function mostrarUltimaTag(ultimaTag) {
    const card = document.getElementById('cardTag');
    const nome = document.getElementById('tagNome');
    const id = document.getElementById('tagId');
    const rodape = document.getElementById('tagRodape');

    card.classList.remove('recente', 'desconhecida');
    if (!ultimaTag) {
        nome.textContent = '—';
        id.textContent = '';
        rodape.textContent = 'nenhuma tag lida ainda';
        return;
    }
    if (!ultimaTag.cadastrada) {
        card.classList.add('desconhecida');
        nome.textContent = 'Tag não cadastrada';
    } else {
        nome.textContent = ultimaTag.nome || 'Sem nome no cadastro';
    }
    id.textContent = ultimaTag.tag;
    if (ultimaTag.idade < 10) card.classList.add('recente');
    rodape.textContent = ultimaTag.idade < 5 ? 'lida agora' : `lida às ${ultimaTag.hora}`;
}

// "parado" | "horario (150)" | "antihorario (255)"
function mostrarMotor(numero, estado) {
    const pilula = document.getElementById(`motor${numero}`);
    const ligado = estado && estado !== 'parado';
    pilula.classList.toggle('ligado', Boolean(ligado));
    if (!ligado) {
        pilula.textContent = 'Parado';
        return;
    }
    const texto = estado.replace('antihorario', 'anti-horário').replace('horario', 'horário');
    pilula.textContent = `Girando · ${texto}`;
}

function mostrarConexao(online) {
    const conexao = document.getElementById('conexao');
    conexao.dataset.estado = online ? 'online' : 'offline';
    document.getElementById('conexaoTexto').textContent = online ? 'Online' : 'Sem conexão';
}

async function atualizar() {
    let status;
    try {
        const resposta = await fetch('/api/status');
        status = await resposta.json();
    } catch (erro) {
        mostrarConexao(false);
        return;
    }
    mostrarConexao(true);

    mostrarPeso(1, status.peso1, status.idade_peso1);
    mostrarPeso(2, status.peso2, status.idade_peso2);
    mostrarUltimaTag(status.ultima_tag);
    mostrarMotor(1, status.motor1);
    mostrarMotor(2, status.motor2);
    mostrarCadastro(status.cadastro);
    const calibracao = status.calibracao || {};
    mostrarCalibracao(1, calibracao['1']);
    mostrarCalibracao(2, calibracao['2']);

    document.getElementById('situacao').textContent = status.mensagem;
}

// ---------- Terminal da Raspberry (prints, logging e journal do servico) ----------

const LINHAS_TERMINAL = 500;
let ultimoIdTerminal = 0;

function linhaTerminal({ hora, origem, nivel, texto }) {
    const linha = document.createElement('span');
    linha.className = `linha-${origem} linha-${nivel}`;
    const marcaHora = document.createElement('span');
    marcaHora.className = 'linha-hora';
    marcaHora.textContent = `${hora} `;
    linha.append(marcaHora, `${origem === 'servico' ? '[serviço] ' : ''}${texto}\n`);
    return linha;
}

// Limpa so a tela: as proximas linhas continuam chegando a partir de ultimoIdTerminal
function limparTerminal() {
    document.getElementById('terminal').textContent = 'Nenhuma saída ainda.';
}

function filtrarTerminal() {
    const soServico = document.getElementById('terminalSoServico').checked;
    document.getElementById('terminal').classList.toggle('so-servico', soServico);
}

function mostrarServico(servico) {
    const texto = document.getElementById('servico');
    if (!servico || !servico.disponivel) {
        texto.textContent = `Serviço ${servico ? servico.nome : ''} não encontrado (o programa não está rodando pelo systemd?).`;
        return;
    }
    const partes = [`${servico.nome}: ${servico.estado} (${servico.subestado})`];
    if (servico.desde) partes.push(`desde ${servico.desde}`);
    if (servico.pid && servico.pid !== '0') partes.push(`PID ${servico.pid}`);
    if (servico.memoria_mb != null) partes.push(`${servico.memoria_mb} MB`);
    if (servico.reinicios) partes.push(`${servico.reinicios} reinício(s)`);
    texto.textContent = partes.join(' · ');
}

async function atualizarTerminal() {
    let dados;
    try {
        const resposta = await fetch(`/api/terminal?desde=${ultimoIdTerminal}`);
        dados = await resposta.json();
    } catch (erro) {
        return;
    }
    const terminal = document.getElementById('terminal');
    // so rola sozinho se o usuario ja estava vendo o fim (nao atrapalha quem subiu para ler)
    const noFim = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 30;
    if (dados.recomecar) terminal.replaceChildren();
    for (const linha of dados.linhas) terminal.appendChild(linhaTerminal(linha));
    while (terminal.childElementCount > LINHAS_TERMINAL) terminal.firstElementChild.remove();
    if (!terminal.childElementCount) terminal.textContent = 'Nenhuma saída ainda.';
    else if (terminal.firstChild.nodeType === Node.TEXT_NODE) terminal.firstChild.remove();
    if (noFim) terminal.scrollTop = terminal.scrollHeight;
    ultimoIdTerminal = dados.ultimo_id;

    document.getElementById('terminalBalancas').textContent = dados.balancas || '';
    mostrarServico(dados.servico);
}

setInterval(atualizar, 1000);
atualizar();
setInterval(atualizarTerminal, 1000);
atualizarTerminal();
for (const id of ['peso', 'valor']) {
    document.getElementById(id).addEventListener('input', atualizarTipoRacao);
}