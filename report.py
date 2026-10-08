"""Relatórios em CSV/HTML (pesquisa e operações do bot)."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd

import stats

CSS = """<style>body{font-family:Segoe UI,Arial,sans-serif;margin:24px;color:#222;background:#fafafa}
table{border-collapse:collapse;margin:12px 0;font-size:13px}td,th{border:1px solid #ccc;padding:4px 8px}
th{background:#eee}.ok{color:#0a7a2f;font-weight:bold}.ruim{color:#b00020}.box{background:#fff;border:1px solid #ddd;
padding:12px 16px;border-radius:6px;margin:12px 0}h1{font-size:22px}h2{font-size:18px;margin-top:28px}</style>"""


def _pct(x):
    try:
        return f"{float(x):.1%}"
    except (TypeError, ValueError):
        return "-"


def html_pesquisa(caminho: Path, info: dict, resumos: list, todos: list, wf: dict, c) -> None:
    aprov = [r for r in todos if r.status == "APROVADA"]
    l = [f"<html><head><meta charset='utf-8'><title>Pesquisa de hipóteses</title>{CSS}</head><body>"]
    l.append("<h1>Pesquisa de hipóteses — pipeline de validação</h1>")
    if info.get("controle"):
        l.append(f"<div class='box'><b>CONTROLE: dados {info['controle']}</b> (sintéticos). "
                 + ("Esperado: NADA aprovado." if info["controle"] == "aleatorio"
                    else "Esperado: achar 'hora' = 14, lado CALL.") + "</div>")
    veredito = ("<span class='ok'>Há regra(s) APROVADA(S) em todas as etapas.</span> Mesmo assim, "
                "confirme ao vivo antes de aumentar a entrada.") if aprov else \
        ("<span class='ruim'>Nenhuma hipótese passou em todas as etapas.</span> "
         "Não há evidência de vantagem: a expectativa de operar é negativa.")
    l.append(f"<div class='box'><b>Veredito:</b> {veredito}</div>")
    p = info["periodos"]
    l.append(f"<p>Treino: {p['inicio'][:10]} → {p['corte_treino'][:10]} | Teste: → {p['novo_inicio'][:10]} | "
             f"Período novo (intocado): → {p['fim'][:10]}</p>")
    l.append("<p>Breakeven: " + ", ".join(f"exp {e} min = {stats.breakeven(v):.1%} (payout {v:.0%})"
                                          for e, v in sorted(c.payout.items()) if e in c.exps) + "</p>")
    l.append("<h2>Ativos</h2><table><tr><th>Ativo</th><th>De</th><th>Até</th><th>Candles</th></tr>")
    for k, (a, b, n) in info["ativos"].items():
        l.append(f"<tr><td>{k}</td><td>{a[:10]}</td><td>{b[:10]}</td><td>{n:,}</td></tr>")
    l.append("</table><h2>Resumo por família</h2><table><tr><th>Família</th><th>Testes (treino)</th>"
             "<th>≠50% no treino (p&lt;5%)</th><th>…esperado só por sorte</th><th>Passaram treino (IC&gt;breakeven)</th><th>Passaram teste</th><th>Aprovadas</th></tr>")
    for r in resumos:
        l.append(f"<tr><td>{r['familia']}</td><td>{r['testes_no_treino']}</td><td>{r.get('diferentes_de_50_no_treino', '-')}</td>"
                 f"<td>{r['falsas_esperadas_treino_5pct']:.0f}</td><td>{r['passaram_treino']}</td><td>{r['passaram_teste']}</td>"
                 f"<td class='{'ok' if r['aprovadas'] else ''}'>{r['aprovadas']}</td></tr>")
    l.append("</table>")
    cand = [r for r in todos if r.status != "reprovada no treino"]
    cand.sort(key=lambda r: ({"APROVADA": 0, "reprovada no período novo": 1, "passou no teste": 1,
                              "reprovada no teste": 2}.get(r.status, 3), -(r.teste or {}).get("acerto", 0)))
    l.append("<h2>Hipóteses que passaram no treino (e o que aconteceu depois)</h2>"
             "<table><tr><th>Família</th><th>Parâmetros</th><th>Exp</th><th>Ativo</th><th>Lado</th>"
             "<th>Treino n / acerto</th><th>Teste n / acerto [IC95%]</th><th>Novo n / acerto [IC95%]</th>"
             "<th>Status</th></tr>")
    for r in cand[:150]:
        t = r.teste or {}
        nv = r.novo or {}
        cls = "ok" if r.status == "APROVADA" else "ruim"
        l.append(f"<tr><td>{r.familia}</td><td>{html.escape(json.dumps(r.params))}</td><td>{r.exp}</td>"
                 f"<td>{r.ativo}</td><td>{'seguir/CALL' if r.lado == 1 else 'inverter/PUT'}</td>"
                 f"<td>{r.treino['n']} / {_pct(r.treino['acerto'])}</td>"
                 f"<td>{t.get('n', '-')} / {_pct(t.get('acerto'))} [{_pct(t.get('ic_baixo'))}, {_pct(t.get('ic_alto'))}]</td>"
                 f"<td>{nv.get('n', '-')} / {_pct(nv.get('acerto'))} [{_pct(nv.get('ic_baixo'))}, {_pct(nv.get('ic_alto'))}]</td>"
                 f"<td class='{cls}'>{r.status}</td></tr>")
    l.append("</table>")
    if len(cand) > 150:
        l.append(f"<p>(+{len(cand) - 150} linhas no JSON)</p>")
    if wf:
        l.append("<h2>Walk-forward das aprovadas</h2><table><tr><th>Regra</th><th>Janelas</th><th>n</th>"
                 "<th>Acerto</th><th>IC95%</th></tr>")
        for k, v in wf.items():
            ic = v.get("ic", (0, 1))
            l.append(f"<tr><td>{html.escape(k)}</td><td>{v.get('janelas')}</td><td>{v.get('n')}</td>"
                     f"<td>{_pct(v.get('acerto'))}</td><td>[{_pct(ic[0])}, {_pct(ic[1])}]</td></tr>")
        l.append("</table>")
    l.append("<h2>Como ler</h2><ul><li><b>Passou no treino</b> não significa nada sozinho: com centenas de "
             "testes, dezenas passam por sorte (compare '≠50% no treino' com '…esperado só por sorte').</li><li><b>Teste</b>: regra congelada, "
             "dados que ela nunca viu; exige IC95% inferior acima do breakeven, Benjamini-Hochberg, bootstrap por dia, "
             "sem depender de poucos dias, estável em blocos e ativos.</li><li><b>Período novo</b>: confirmação "
             "final em meses recentes, intocados.</li><li>Dados Dukascopy (BID). A IQ liquida pelo PRÓPRIO preço: "
             "confira com <code>python alinhamento.py</code>.</li></ul>")
    l.append(f"<p style='color:#777'>Duração: {info.get('duracao_s')} s</p></body></html>")
    Path(caminho).write_text("\n".join(l), encoding="utf-8")


def operacoes(db, pasta: Path, payout_padrao: float = 0.85) -> tuple[Path, Path]:
    """Exporta as operações do bot (SQLite) para CSV e HTML com estatística."""
    pasta.mkdir(parents=True, exist_ok=True)
    df = db.operacoes_df()
    csv = pasta / "operacoes.csv"
    df.to_csv(csv, index=False, encoding="utf-8-sig", sep=";", decimal=",")
    fech = df[df["resultado"].isin(["win", "loose"])] if len(df) else df
    linhas = [f"<html><head><meta charset='utf-8'><title>Operações do bot</title>{CSS}</head><body>",
              "<h1>Operações do bot</h1>"]
    if len(fech):
        w = (fech["resultado"] == "win").astype(int)
        pay = float(fech["payout"].mean()) if "payout" in fech else payout_padrao
        r = stats.resumir(w, pay)
        lucro = float(df["lucro"].fillna(0).sum())
        linhas.append(f"<div class='box'>{html.escape(r.texto())}<br>Lucro/prejuízo total: <b>{lucro:+.2f}</b><br>"
                      f"Para provar vantagem com o acerto atual: "
                      f"{stats.operacoes_para_ic_acima(r.acerto, r.breakeven) if r.acerto > r.breakeven else '∞ (acerto abaixo do breakeven)'} operações</div>")
        por_ativo = fech.assign(w=w).groupby("ativo")["w"].agg(["count", "mean"])
        linhas.append("<h2>Por ativo</h2>" + por_ativo.to_html(float_format=lambda x: f"{x:.3f}"))
    else:
        linhas.append("<p>Nenhuma operação fechada ainda.</p>")
    linhas.append("<h2>Últimas 200 operações</h2>" + (df.tail(200).to_html(index=False) if len(df) else ""))
    linhas.append("</body></html>")
    h = pasta / "operacoes.html"
    h.write_text("\n".join(linhas), encoding="utf-8")
    return csv, h
