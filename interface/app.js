const $ = id => document.getElementById(id);
const token = document.querySelector('meta[name=token]').content;
let data = null, modoTela = 'SIMULACAO', hoverIdx = null;

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const num = (v, d = 2) => v == null ? '—' : Number(v).toLocaleString('pt-BR', {minimumFractionDigits: d, maximumFractionDigits: d});
const comSinal = v => v == null ? '—' : (v > 0 ? '+' : '') + num(v);
const horaBR = t => new Date(t).toLocaleTimeString('pt-BR', {timeZone: 'America/Sao_Paulo', hour: '2-digit', minute: '2-digit'});

async function api(path, body) {
  const r = await fetch('/api/' + path, {method: body ? 'POST' : 'GET',
    headers: {'X-Token': token, 'Content-Type': 'application/json'}, body: body ? JSON.stringify(body) : undefined});
  const j = await r.json();
  if (!r.ok) throw Error(j.error || 'erro');
  return j;
}

const CONTA = {SIMULACAO: 'Simulação', PRACTICE: 'Demo', REAL: 'Real'};

function render() {
  const d = data, st = d.status, ativo = ['conectando', 'rodando', 'parando'].includes(d.phase);
  modoTela = d.mode;

  // topo
  document.querySelectorAll('#segmento button').forEach(b => {
    b.setAttribute('aria-checked', String(b.dataset.modo === d.mode));
    b.disabled = ativo;
  });
  $('pilula').dataset.tipo = st.tipo;
  $('pilula-txt').textContent = st.titulo;
  $('iniciar').disabled = ativo;
  $('parar').disabled = !ativo || d.phase === 'parando';
  $('frase-esperada').textContent = d.frase;

  // status
  $('status').dataset.tipo = st.tipo;
  $('st-titulo').textContent = st.titulo;
  $('st-texto').textContent = st.texto;
  $('st-conta').textContent = 'Conta ' + CONTA[d.mode];

  // KPIs
  const c = d.conta, h = d.hoje, p = d.perdas;
  $('saldo').textContent = c.saldo == null ? '—' : num(c.saldo);
  $('saldo-sub').textContent = c.saldo == null ? 'aparece ao conectar' : `${CONTA[d.mode]} · ${c.moeda || ''}`;
  $('lucro').textContent = comSinal(h.lucro);
  $('lucro').className = h.lucro > 0 ? 'pos' : h.lucro < 0 ? 'neg' : '';
  $('lucro-sub').textContent = c.orcamento == null ? `conta ${CONTA[d.mode]}` : `teto de perda restante ${num(c.orcamento)}`;
  $('ops').textContent = h.ops;
  $('ops-sub').innerHTML = h.ops
    ? `<span class="pos">${h.wins} ganhas</span> · <span class="neg">${h.losses} perdidas</span> · ${h.acerto == null ? '—' : num(h.acerto * 100, 0) + '% de acerto'}`
    : 'nenhuma ainda';
  $('perdas').textContent = `${p.seguidas} de ${p.max}`;
  $('medidor').className = 'medidor' + (p.seguidas >= p.max ? ' cheio' : '');
  $('medidor').innerHTML = Array.from({length: p.max}, (_, i) => `<span class="${i < p.seguidas ? 'on' : ''}"></span>`).join('');

  // lateral
  const r = d.risco;
  $('regras').innerHTML = [
    ['Entrada', num(r.entrada)], ['Stop loss', r.max_perdas > 0 ? `${r.max_perdas} seguidas` : 'desligado'], ['Simultâneas', `até ${r.simult}`],
    ...(r.sessao > 0 ? [['Para se perder', `${num(r.sessao)} na sessão`]] : []),
    ...(r.dia_valor > 0 ? [['Perda máx. no dia', num(r.dia_valor)]] : []),
    ['Stop do dia', `${num(r.stop_dia, 0)}% do saldo`], ['Teto de perda', num(r.orcamento)],
    ['Martingale', r.martingale ? 'ligado' : 'desligado'],
  ].map(([a, b]) => `<dt>${a}</dt><dd>${esc(b)}</dd>`).join('');
  $('estrategia').textContent = d.estrategia;

  // mercado
  $('pares').innerHTML = d.pares.map(x => {
    const est = x.aberto === null ? ['', 'aguardando'] : x.aberto ? ['aberto', 'Aberto'] : ['fechado', 'Fechado'];
    const s = (x.sinal || '').replace(/\s*\(.*\)\s*$/, '');
    const m = s.match(/^(CALL|PUT)\s+(\d{1,2}:\d{2})(.*)$/);
    let sig = '<span class="sub">—</span>';
    if (m) {
      const bloq = /BLOQUEADO/.test(m[3]);
      sig = `<span class="chip ${bloq ? 'bloq' : m[1].toLowerCase()}">${m[1]}${bloq ? ' · bloqueado' : ''}</span><span class="hora-sinal">${m[2]}</span>`;
    }
    return `<tr class="${x.id === d.selected ? 'sel' : ''}" data-par="${esc(x.id)}">
      <td>${esc(x.nome)}</td><td><span class="estado ${est[0]}"><i></i>${est[1]}</span></td>
      <td class="dir">${x.payout == null ? '<span class="sub">—</span>' : num(x.payout * 100, 0) + '%'}</td><td>${sig}</td></tr>`;
  }).join('');
  document.querySelectorAll('[data-par]').forEach(tr => tr.onclick = async () => {
    try { await api('select', {asset: tr.dataset.par}); data = await api('state'); render(); } catch (e) {}
  });
  $('graf-titulo').textContent = d.selected_nome;
  $('graf-sub').textContent = d.velas.length ? `velas de 1 minuto · até ${horaBR(d.velas.at(-1).t)}` : 'sem velas ainda';

  // operações
  $('ops-conta').textContent = 'conta ' + CONTA[d.mode];
  $('tabela').innerHTML = d.ops.length ? d.ops.map(o => `<tr><td>${esc(o.hora)}</td><td>${esc(o.ativo)}</td>
      <td><span class="chip ${o.lado === 'CALL' ? 'call' : 'put'}">${esc(o.lado)}</span></td>
      <td><span class="chip ${esc(o.resultado)}">${esc(o.resultado)}</span></td>
      <td class="dir ${o.lucro > 0 ? 'pos' : o.lucro < 0 ? 'neg' : ''}">${comSinal(o.lucro)}</td></tr>`).join('')
    : '<tr><td colspan="5" class="vazio">Nenhuma operação ainda nesta conta.</td></tr>';

  // log
  $('logs').innerHTML = d.logs.length ? d.logs.slice().reverse().map(l => `<div><time>${esc(l.t)}</time>${esc(l.m)}</div>`).join('')
    : '<div class="sub">Sem mensagens.</div>';

  // 2FA
  if (d.precisa_2fa && !$('dlg2fa').open) { $('codigo2fa').value = ''; $('erro2fa').textContent = ''; $('dlg2fa').showModal(); }
  if (!d.precisa_2fa && $('dlg2fa').open) $('dlg2fa').close();

  renderAuto(d);
  desenhar();
}

// ---------------- automação ----------------
const ROT = {aprovada: 'Aprovada', em_teste: 'Em teste', reprovada: 'Não aprovada', sem_avaliacao: 'Sem avaliação'};
const MERC = {op: 'Pares de mercado', otc: 'OTC'};
const pct = v => v == null ? '—' : num(v * 100, 1) + '%';
const ic = v => v ? `${num(v[0] * 100, 1)}–${num(v[1] * 100, 1)}%` : '—';
let avisosVistos = null, aplicarPendente = null, ultimoPlanos = '';

function renderAuto(d) {
  const A = d.auto, abaAuto = document.querySelector('[data-aba=auto]');
  abaAuto.hidden = !A;
  if (!A) { $('recs').innerHTML = ''; return; }

  // recomendações (painel)
  $('recs').innerHTML = A.recomendacoes.map(r => `<div class="rec">
      <span class="rec-ic">↗</span>
      <div class="rec-txt"><b>Recomendação · ${MERC[r.mercado]}: ${esc(r.para_nome)}</b><span>${esc(r.texto)}</span></div>
      <div class="rec-acoes"><button class="btn" data-ignorar="${esc(r.id)}">Ignorar</button>
      <button class="btn primario" data-aplicar="${esc(r.mercado)}|${esc(r.para)}" data-nome="${esc(r.para_nome)}">Aplicar</button></div></div>`).join('');
  $('badge-auto').hidden = !A.recomendacoes.length;
  $('badge-auto').textContent = A.recomendacoes.length;

  // fase + avaliação
  $('a-modo').textContent = 'modo ' + A.modo;
  $('a-fase').textContent = A.fase || '—';
  $('a-fase').className = 'fase-chip ' + (A.fase || '');
  $('a-fase-txt').textContent = A.fase_texto;
  $('a-contagem').textContent = A.fase ? `${A.n_op} par(es) de mercado e ${A.n_otc} OTC abertos agora` : 'Aparece quando o bot estiver rodando.';
  const av = A.avaliacao, pr = av.progresso || {};
  $('a-aval-txt').textContent = av.gerado_em
    ? `Última avaliação: ${new Date(av.gerado_em).toLocaleString('pt-BR', {timeZone: 'America/Sao_Paulo'})} (há ${num(av.idade_horas, 1)} h).`
    : 'Ainda não houve avaliação. Clique em "Reavaliar agora".';
  const rodando = av.avaliando || pr.rodando;
  $('a-prog').hidden = !rodando;
  $('a-prog-barra').style.width = (pr.total ? Math.round(100 * (pr.feito || 0) / pr.total) : 5) + '%';
  $('a-prog-txt').textContent = rodando ? `${pr.etapa || 'trabalhando'} · ${pr.msg || ''} (${pr.feito || 0}/${pr.total || '?'})`
    : (pr.erro ? 'Última tentativa falhou: ' + pr.erro : '');
  $('b-reavaliar').disabled = !!rodando;
  const rg = av.regras;
  $('a-regras').textContent = rg ? `Regras fixas para aprovar: mín. ${rg.n_min} operações em ${rg.janela_dias} dias · significativo após correção (BH ${rg.bh_q}) · `
    + `≥ ${num(rg.metade_min * 100, 1)}% em cada metade · últimos 7 dias ≥ ${num(rg.ultimos7_min * 100, 0)}% · até ${num(rg.margem_breakeven * 100, 0)} pontos abaixo do empate.` : '';

  // planos
  const htmlPlanos = ['op', 'otc'].map(m => {
    const p = A.planos[m];
    const opcoes = A.catalogo.filter(e => e.mercados.includes(m))
      .map(e => `<option value="${esc(e.chave)}" ${e.chave === p.chave ? 'selected' : ''}>${esc(e.nome)} — ${ROT[e.status[m]]}</option>`).join('');
    return `<article class="card plano"><h4>${MERC[m]}</h4>
      <div class="nome">${esc(p.nome)}</div>
      <div class="linha"><span class="chip ${p.status}">${ROT[p.status]}</span>
        ${p.rodando_agora ? (p.pode ? '<span class="chip vivo">rodando agora</span>' : '<span class="chip">esperando</span>') : '<span class="chip">fora desta fase</span>'}
        ${p.pendente ? `<span class="chip em_teste">troca pendente → ${esc(p.pendente)}</span>` : ''}</div>
      ${p.motivo && p.rodando_agora ? `<p class="motivo">${esc(p.motivo)}</p>` : ''}
      <div class="sub">Pares (${p.pares.length})</div>
      <div class="pares-lista">${p.pares.length ? p.pares.map(x => `<span>${esc(x.replace('-op', '').replace('-OTC', ' OTC'))}</span>`).join('') : '<span>—</span>'}</div>
      <div class="trocar"><select id="sel-${m}">${opcoes}</select><button class="btn" data-trocar="${m}">Aplicar</button></div></article>`;
  }).join('');
  // Só redesenha os planos se algo mudou E se você não está mexendo numa caixa de seleção
  // (antes, a tela se atualizava a cada 1,5 s e fechava a lista aberta).
  const emUso = document.activeElement && document.activeElement.closest && document.activeElement.closest('#a-planos');
  if (!emUso && htmlPlanos !== ultimoPlanos) { $('a-planos').innerHTML = htmlPlanos; ultimoPlanos = htmlPlanos; }

  // tabelas
  $('a-pares').innerHTML = A.pares.length ? A.pares.sort((x, y) => (y.elegivel - x.elegivel) || ((y.acerto || 0) - (x.acerto || 0))).map(x =>
    `<tr><td>${esc(x.ativo.replace('-op', '').replace('-OTC', ' OTC'))}</td><td>${esc((A.catalogo.find(e => e.chave === x.chave) || {}).nome || x.chave)}</td>
      <td class="dir">${num(x.n, 0)}</td><td class="dir">${pct(x.acerto)}</td><td>${ic(x.ic)}</td>
      <td class="motivo">${x.elegivel ? '<span class="chip aprovada">aprovado</span>' : esc(x.motivos.join('; '))}</td></tr>`).join('')
    : '<tr><td colspan="6" class="vazio">Sem avaliação para as estratégias dos planos (o humor não tem histórico para testar).</td></tr>';
  $('a-vivo').innerHTML = A.ao_vivo.length ? A.ao_vivo.map(x => `<tr><td>${esc(x.nome)}</td><td>${x.mercado === 'op' ? 'Mercado' : 'OTC'}</td>
      <td class="dir">${x.n}</td><td class="dir">${pct(x.acerto)}</td><td>${ic(x.ic)}</td>
      <td class="dir ${x.lucro > 0 ? 'pos' : x.lucro < 0 ? 'neg' : ''}">${comSinal(x.lucro)}</td></tr>`).join('')
    : '<tr><td colspan="6" class="vazio">Nenhuma operação nesta conta ainda.</td></tr>';
  $('a-trocas').innerHTML = A.trocas.length ? A.trocas.map(x => `<tr><td>${esc(x.em)}</td><td>${esc(x.mercado)}</td>
      <td>${esc(x.de)} → <b>${esc(x.para)}</b></td><td>${esc(x.quem)}</td></tr>`).join('')
    : '<tr><td colspan="4" class="vazio">Nenhuma troca ainda.</td></tr>';

  // avisos novos viram "toast"
  const avs = A.avisos || [];
  if (avisosVistos === null) avisosVistos = avs.length ? avs.at(-1).t : '';
  avs.filter(x => x.t > avisosVistos).forEach(x => toast(x.texto));
  if (avs.length) avisosVistos = avs.at(-1).t > avisosVistos ? avs.at(-1).t : avisosVistos;

  document.querySelectorAll('[data-ignorar]').forEach(b => b.onclick = async () => {
    try { await api('ignorar', {id: b.dataset.ignorar}); data = await api('state'); render(); } catch (e) { toast(e.message); }
  });
  document.querySelectorAll('[data-aplicar]').forEach(b => b.onclick = () => {
    const [mercado, chave] = b.dataset.aplicar.split('|'); pedirAplicar(mercado, chave, b.dataset.nome);
  });
}

// Um único "ouvinte" no quadro dos planos, ligado uma vez só: continua funcionando mesmo
// quando o quadro é redesenhado (e não depende de a tela ter se atualizado).
$('a-planos').addEventListener('click', ev => {
  const b = ev.target.closest('[data-trocar]'); if (!b) return;
  const A = data && data.auto; if (!A) return;
  const m = b.dataset.trocar, sel = $('sel-' + m), chave = sel.value;
  if (chave === A.planos[m].chave) { toast('Essa já é a estratégia atual.'); return; }
  pedirAplicar(m, chave, sel.options[sel.selectedIndex].text);
});

function pedirAplicar(mercado, chave, nome) {
  aplicarPendente = {mercado, chave};
  $('ap-texto').textContent = `${MERC[mercado]}: trocar para ${nome}.`;
  $('ap-real').hidden = data.mode !== 'REAL';
  $('ap-frase').textContent = data.frase; $('ap-input').value = ''; $('ap-erro').textContent = '';
  $('dlg-aplicar').showModal();
}
$('dlg-aplicar').addEventListener('close', async () => {
  if (document.activeElement) document.activeElement.blur();
  ultimoPlanos = '';
  if ($('dlg-aplicar').returnValue !== 'ok' || !aplicarPendente) return;
  try { const r = await api('aplicar', {...aplicarPendente, confirmation: $('ap-input').value}); toast((r && r.msg) || 'Troca pedida.'); data = await api('state'); render(); }
  catch (e) { toast(e.message); }
  aplicarPendente = null;
});
$('b-reavaliar').onclick = async () => { try { await api('reavaliar', {}); toast('Avaliação iniciada em segundo plano.'); } catch (e) { toast(e.message); } };

function toast(texto) {
  const el = document.createElement('div'); el.className = 'toast'; el.textContent = texto;
  $('toasts').appendChild(el); setTimeout(() => el.remove(), 7000);
}

document.querySelectorAll('[data-aba]').forEach(b => b.onclick = () => {
  document.querySelectorAll('[data-aba]').forEach(x => x.setAttribute('aria-selected', String(x === b)));
  $('v-painel').hidden = b.dataset.aba !== 'painel';
  $('v-auto').hidden = b.dataset.aba !== 'auto';
  desenhar();
});

// ---------------- gráfico de velas (com mira e dica ao passar o mouse) ----------------
const COR = {up: '#3ddc97', down: '#ff6b81', grade: '#18212b', eixo: '#6c7886', mira: '#3a4757', ultimo: '#5fe0e8'};
let geo = null;

function desenhar() {
  const cv = $('grafico'), box = cv.getBoundingClientRect(), dpr = devicePixelRatio || 1;
  cv.width = box.width * dpr; cv.height = box.height * dpr;
  const ctx = cv.getContext('2d'); ctx.scale(dpr, dpr);
  const w = box.width, h = box.height, rows = (data && data.velas) || [];
  ctx.font = '500 11px "JetBrains Mono", Consolas, monospace';
  geo = null;
  if (!rows.length) { ctx.fillStyle = COR.eixo; ctx.fillText('Sem velas para este par ainda.', 8, h / 2); return; }
  const L = 4, R = 70, T = 10, B = 22, pw = w - L - R, ph = h - T - B;
  let lo = Math.min(...rows.map(x => x.l)), hi = Math.max(...rows.map(x => x.h));
  const pad = (hi - lo) * 0.08 || 0.0001; lo -= pad; hi += pad;
  const y = v => T + (hi - v) / (hi - lo) * ph, x = i => L + (i + 0.5) * pw / rows.length;
  const dec = hi > 20 ? 3 : 5;
  geo = {L, R, T, B, pw, ph, n: rows.length, w, h};

  const ult = rows.at(-1).c, yUlt = y(ult);
  ctx.lineWidth = 1;
  for (let i = 0; i <= 4; i++) {
    const yy = Math.round(T + ph * i / 4) + .5;
    ctx.strokeStyle = COR.grade; ctx.beginPath(); ctx.moveTo(L, yy); ctx.lineTo(w - R, yy); ctx.stroke();
    if (Math.abs(yy - yUlt) > 14) { ctx.fillStyle = COR.eixo; ctx.fillText(num(hi - (hi - lo) * i / 4, dec), w - R + 8, yy + 4); }
  }
  const passo = Math.max(1, Math.round(rows.length / 6));
  ctx.fillStyle = COR.eixo;
  for (let i = 0; i < rows.length; i += passo) ctx.fillText(horaBR(rows[i].t), Math.max(L, x(i) - 17), h - 5);

  const bw = Math.max(1.5, pw / rows.length * 0.62);
  rows.forEach((a, i) => {
    const cor = a.c >= a.o ? COR.up : COR.down;
    ctx.globalAlpha = hoverIdx == null || hoverIdx === i ? 1 : 0.55;
    ctx.strokeStyle = ctx.fillStyle = cor;
    ctx.beginPath(); ctx.moveTo(Math.round(x(i)) + .5, y(a.h)); ctx.lineTo(Math.round(x(i)) + .5, y(a.l)); ctx.stroke();
    const top = Math.min(y(a.o), y(a.c)), alt = Math.max(1, Math.abs(y(a.o) - y(a.c)));
    ctx.beginPath(); ctx.roundRect ? ctx.roundRect(x(i) - bw / 2, top, bw, alt, Math.min(1.5, bw / 3)) : ctx.rect(x(i) - bw / 2, top, bw, alt); ctx.fill();
  });
  ctx.globalAlpha = 1;

  // último preço
  ctx.setLineDash([2, 4]); ctx.strokeStyle = 'rgba(95,224,232,.55)';
  ctx.beginPath(); ctx.moveTo(L, Math.round(yUlt) + .5); ctx.lineTo(w - R, Math.round(yUlt) + .5); ctx.stroke(); ctx.setLineDash([]);
  ctx.fillStyle = COR.ultimo;
  ctx.beginPath(); ctx.roundRect ? ctx.roundRect(w - R + 3, yUlt - 10, R - 6, 20, 5) : ctx.rect(w - R + 3, yUlt - 10, R - 6, 20); ctx.fill();
  ctx.fillStyle = '#04252a'; ctx.fillText(num(ult, dec), w - R + 8, yUlt + 4);

  // mira
  if (hoverIdx != null && rows[hoverIdx]) {
    const xx = Math.round(x(hoverIdx)) + .5;
    ctx.strokeStyle = COR.mira; ctx.setLineDash([3, 3]);
    ctx.beginPath(); ctx.moveTo(xx, T); ctx.lineTo(xx, h - B); ctx.stroke(); ctx.setLineDash([]);
  }
}

$('grafico').addEventListener('mousemove', ev => {
  if (!geo || !data) return;
  const rect = ev.target.getBoundingClientRect(), mx = ev.clientX - rect.left;
  const i = Math.floor((mx - geo.L) / (geo.pw / geo.n));
  if (i < 0 || i >= geo.n) { hoverIdx = null; $('tip').hidden = true; desenhar(); return; }
  hoverIdx = i; desenhar();
  const v = data.velas[i], dec = v.h > 20 ? 3 : 5, var_ = (v.c - v.o) / v.o * 100;
  const tip = $('tip');
  tip.innerHTML = `<b>${horaBR(v.t)}</b><br>Abertura ${num(v.o, dec)}<br>Máxima ${num(v.h, dec)}<br>Mínima ${num(v.l, dec)}<br>Fecho <b class="${v.c >= v.o ? 'pos' : 'neg'}">${num(v.c, dec)}</b> (${var_ >= 0 ? '+' : ''}${num(var_, 3)}%)`;
  tip.hidden = false;
  const left = mx + 14 + tip.offsetWidth > rect.width - geo.R ? mx - tip.offsetWidth - 14 : mx + 14;
  tip.style.left = left + 'px'; tip.style.top = '8px';
});
$('grafico').addEventListener('mouseleave', () => { hoverIdx = null; $('tip').hidden = true; desenhar(); });

// ---------------- atualização e controles ----------------
async function atualizar() {
  try { data = await api('state'); render(); }
  catch (e) {
    $('status').dataset.tipo = 'erro'; $('st-titulo').textContent = 'Interface desconectada';
    $('st-texto').textContent = 'O programa foi fechado ou travou. Abra de novo pelo atalho.';
    $('iniciar').disabled = $('parar').disabled = true;
  }
  setTimeout(atualizar, 1500);
}

document.querySelectorAll('#segmento button').forEach(b => b.onclick = async () => {
  try { await api('modo', {mode: b.dataset.modo}); data = await api('state'); render(); } catch (e) {}
});

$('iniciar').onclick = async () => {
  const mode = modoTela; let confirmation = '';
  if (mode === 'REAL') {
    $('frase').value = ''; $('confirmar').showModal();
    const res = await new Promise(r => $('confirmar').addEventListener('close', () => r($('confirmar').returnValue), {once: true}));
    if (res !== 'ok') return;
    confirmation = $('frase').value;
  }
  try { await api('start', {mode, confirmation}); data = await api('state'); render(); } catch (e) { alert(e.message); }
};
$('parar').onclick = async () => { try { await api('stop', {}); data = await api('state'); render(); } catch (e) { alert(e.message); } };
$('form2fa').onsubmit = async ev => {
  ev.preventDefault();
  try { await api('2fa', {codigo: $('codigo2fa').value}); $('dlg2fa').close(); }
  catch (e) { $('erro2fa').textContent = e.message; }
};
$('dlg2fa').addEventListener('cancel', ev => ev.preventDefault());

// editor das regras de proteção (grava no config.yaml)
const CAMPOS_CFG = ['entrada', 'simultaneas', 'perdas_seguidas', 'perda_sessao', 'perda_dia', 'stop_dia_pct', 'teto_total', 'payout_otc'];
$('b-config').onclick = () => {
  const c = (data && data.config) || {};
  CAMPOS_CFG.forEach(k => { $('c-' + k).value = k === 'payout_otc' ? Math.round((c[k] || 0) * 100) : (c[k] ?? ''); });
  $('c-reabertura').checked = !!c.reabertura;
  $('cfg-frase').textContent = (data && data.frase) || 'CONFIRMO';
  $('cfg-input').value = ''; $('cfg-erro').textContent = '';
  $('dlg-config').showModal();
};
$('form-config').addEventListener('submit', async ev => {
  if (!ev.submitter || ev.submitter.value !== 'ok') return;
  ev.preventDefault();
  const valores = {};
  CAMPOS_CFG.forEach(k => { const v = Number($('c-' + k).value); valores[k] = k === 'payout_otc' ? v / 100 : v; });
  valores.reabertura = $('c-reabertura').checked;
  try {
    const r = await api('config', {valores, confirmation: $('cfg-input').value});
    $('dlg-config').close(); toast(r.msg || 'Regras salvas.'); data = await api('state'); render();
  } catch (e) { $('cfg-erro').textContent = e.message; }
});
window.addEventListener('resize', desenhar);
const relogio = () => { $('relogio').textContent = new Date().toLocaleString('pt-BR', {timeZone: 'America/Sao_Paulo', day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit'}); };
setInterval(relogio, 1000); relogio();
atualizar();

// Arte da lateral: o cartão inclina na direção do mouse, com reflexo de luz (efeito 3D).
const arte = $('arte'), carta = $('arte-card');
if (arte && carta && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
  arte.addEventListener('mousemove', ev => {
    const r = carta.getBoundingClientRect();
    const x = (ev.clientX - r.left) / r.width - 0.5, y = (ev.clientY - r.top) / r.height - 0.5;
    carta.classList.add('ativo');
    carta.style.setProperty('--ry', `${(x * 16).toFixed(2)}deg`);
    carta.style.setProperty('--rx', `${(-y * 16).toFixed(2)}deg`);
    carta.style.setProperty('--px', `${(-x * 8).toFixed(1)}px`);
    carta.style.setProperty('--py', `${(-y * 8).toFixed(1)}px`);
    carta.style.setProperty('--gx', `${((x + 0.5) * 100).toFixed(0)}%`);
    carta.style.setProperty('--gy', `${((y + 0.5) * 100).toFixed(0)}%`);
  });
  arte.addEventListener('mouseleave', () => {
    carta.classList.remove('ativo');
    ['--rx', '--ry', '--px', '--py'].forEach(v => carta.style.removeProperty(v));
  });
}
