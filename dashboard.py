"""Painel no terminal (rich). Se ajusta à altura da janela do CMD."""
from __future__ import annotations

from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from risk import FUSO_BR


def montar(estado: dict, console: Console | None = None):
    console = console or Console()
    altura = max(20, console.size.height)
    cab = Text()
    conta = estado.get("conta", "?")
    cab.append(f" CONTA {conta} ", style="bold white on red" if conta == "REAL" else "bold black on green")
    cab.append(f"  saldo {estado.get('saldo', 0):.2f} {estado.get('moeda', '')}")
    cab.append(f"  | hoje {estado.get('lucro_dia', 0):+.2f}")
    cab.append(f"  | orçamento restante {estado.get('orcamento', 0):.2f}")
    cab.append(f"  | {estado.get('hora', '')}")
    est = Text()
    est.append("Estratégia: ", style="bold")
    est.append(estado.get("estrategia", "?"))
    if estado.get("validada"):
        est.append("  [VALIDADA na pesquisa]", style="bold green")
    else:
        est.append("  [NÃO VALIDADA — sem vantagem comprovada]", style="bold red")
    mon = Text("Monitor: " + estado.get("monitor", ""), style="yellow")
    status = Text("Status: " + estado.get("status", ""), style="bold cyan")

    ta = Table(title="Ativos acompanhados", expand=True)
    for c in ("Ativo", "Nota", "Payout", "Último sinal"):
        ta.add_column(c)
    for a in estado.get("ativos", []):
        ta.add_row(a["ativo"], f"{a['nota']:.0f}", f"{a['payout']:.0%}", a.get("sinal", "-"))

    linhas_ops = max(3, altura - 18 - len(estado.get("ativos", [])))
    to = Table(title="Últimas operações", expand=True)
    for c in ("Hora (BRT)", "Ativo", "Lado", "Valor", "Exp", "Resultado", "Lucro"):
        to.add_column(c)
    for o in estado.get("ops", [])[-linhas_ops:]:
        res = o["resultado"]
        cor = {"win": "green", "loose": "red", "equal": "yellow"}.get(res, "white")
        to.add_row(o["hora"].astimezone(FUSO_BR).strftime("%d/%m %H:%M"), o["ativo"], o["lado"].upper(),
                   f"{o['valor']:.2f}" + (f" G{o['gale']}" if o.get("gale") else ""), f"{o['exp']}m",
                   Text(res, style=cor), "" if o["lucro"] is None else f"{o['lucro']:+.2f}")
    rod = Text("Para parar com segurança: crie um arquivo chamado STOP na pasta do bot, ou Ctrl+C.",
               style="dim")
    return Panel(Group(cab, est, mon, status, ta, to, rod), title="Bot IQ Option — validação estatística")
