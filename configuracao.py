"""Leitura e validação do config.yaml (com valores padrão seguros)."""
from __future__ import annotations

import copy
from pathlib import Path

import yaml

RAIZ = Path(__file__).parent

PADRAO = yaml.safe_load((RAIZ / "config.exemplo.yaml").read_text(encoding="utf-8"))


def _mesclar(base: dict, novo: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (novo or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _mesclar(out[k], v)
        else:
            out[k] = v
    return out


class ErroConfig(ValueError):
    pass


def validar(cfg: dict) -> dict:
    c = cfg
    if c["conta"]["tipo"] not in ("REAL", "PRACTICE"):
        raise ErroConfig("conta.tipo deve ser REAL ou PRACTICE")
    r = c["risco"]
    if r["entrada"] <= 0 or r["max_entrada"] < r["entrada"]:
        raise ErroConfig("risco.entrada deve ser > 0 e <= risco.max_entrada")
    if not (0 < r["stop_diario_pct"] <= 100):
        raise ErroConfig("risco.stop_diario_pct deve estar entre 0 e 100")
    if float(r.get("meta_diaria_pct") or 0) < 0:
        raise ErroConfig("risco.meta_diaria_pct deve ser 0 (sem meta) ou positivo")
    if r["orcamento_teste"] <= 0:
        raise ErroConfig("risco.orcamento_teste deve ser > 0")
    m = r["martingale"]
    if m["ativo"] and not (1 <= int(m["max_gales"]) <= 3):
        raise ErroConfig("martingale.max_gales deve ser 1, 2 ou 3")
    if c["estrategia"]["fonte"] not in ("auto", "congelada", "manual"):
        raise ErroConfig("estrategia.fonte deve ser auto, congelada ou manual")
    import strategy
    fam = c["estrategia"]["manual"]["familia"]
    if fam not in strategy.FAMILIAS:
        raise ErroConfig(f"família desconhecida: {fam}. Opções: {list(strategy.FAMILIAS)}")
    exp = int(c["estrategia"]["manual"]["params"].get("exp", 1))
    if exp not in (1, 2, 3, 4, 5, 15):
        raise ErroConfig("exp deve ser 1-5 (turbo) ou 15 (binária)")
    if not c["ativos"]["lista"]:
        raise ErroConfig("ativos.lista está vazia")
    return c


def carregar(caminho: str | Path | None = None) -> dict:
    """Lê config.yaml (ou o caminho dado). Se não existir, usa o exemplo."""
    p = Path(caminho) if caminho else RAIZ / "config.yaml"
    dados = yaml.safe_load(p.read_text(encoding="utf-8")) if p.exists() else {}
    return validar(_mesclar(PADRAO, dados or {}))


# ---------------------------------------------------------------- edição pela interface
# Campos que a tela pode mudar: chave da tela -> (seção, campo, tipo, mínimo, máximo)
CAMPOS_TELA = {
    "entrada":         ("risco", "entrada", float, 0.01, 10_000),
    "simultaneas":     ("ativos", "max_ativos_simultaneos", int, 1, 10),
    "perdas_seguidas": ("risco", "max_perdas_seguidas", int, 0, 50),
    "perda_sessao":    ("risco", "perda_max_sessao", float, 0, 1_000_000),
    "perda_dia":       ("risco", "stop_diario_valor", float, 0, 1_000_000),
    "stop_dia_pct":    ("risco", "stop_diario_pct", float, 1, 100),
    "teto_total":      ("risco", "orcamento_teste", float, 0.01, 1_000_000),
    "payout_otc":      ("automacao", "payout_minimo_otc", float, 0, 1),
    "reabertura":      ("reabertura", "ativo", bool, None, None),
}


def valores_tela(cfg: dict) -> dict:
    out = {}
    for chave, (sec, campo, tipo, _mn, _mx) in CAMPOS_TELA.items():
        v = (cfg.get(sec) or {}).get(campo)
        out[chave] = bool(v) if tipo is bool else (0 if v is None else tipo(v))
    return out


def _texto_valor(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        return f"{v:.2f}" if v >= 1 or v == 0 else f"{v:g}"
    return str(v)


def _trocar_linha(linhas: list[str], secao: str, campo: str, valor) -> None:
    """Troca o valor de 'secao: campo:' no texto do YAML, mantendo o comentário da linha."""
    import re
    ini = next((i for i, l in enumerate(linhas) if re.match(rf"^{re.escape(secao)}:\s*(#.*)?$", l)), None)
    if ini is None:                                   # seção não existe: cria no fim
        linhas += ["", f"{secao}:", f"  {campo}: {_texto_valor(valor)}"]
        return
    fim = next((i for i in range(ini + 1, len(linhas)) if linhas[i] and not linhas[i][0].isspace()
                and not linhas[i].lstrip().startswith("#")), len(linhas))
    padrao = re.compile(rf"^(  {re.escape(campo)}:\s*)([^#]*?)(\s*)(#.*)?$")
    for i in range(ini + 1, fim):
        m = padrao.match(linhas[i])
        if m:
            novo = m.group(1) + _texto_valor(valor)
            if m.group(4):
                col = len(m.group(1)) + len(m.group(2)) + len(m.group(3))
                novo += " " * max(1, col - len(novo)) + m.group(4)
            linhas[i] = novo
            return
    linhas.insert(ini + 1, f"  {campo}: {_texto_valor(valor)}")


def salvar_campos(mudancas: dict, caminho: str | Path | None = None) -> dict:
    """Grava no config.yaml os campos da tela (mantendo os comentários) e devolve o config novo.
    Valida antes: se algo ficar inválido, o arquivo não muda."""
    p = Path(caminho) if caminho else RAIZ / "config.yaml"
    novos = {}
    for chave, bruto in mudancas.items():
        if chave not in CAMPOS_TELA:
            raise ErroConfig(f"campo desconhecido: {chave}")
        sec, campo, tipo, mn, mx = CAMPOS_TELA[chave]
        try:
            v = bool(bruto) if tipo is bool else tipo(float(str(bruto).replace(",", ".")))
        except (TypeError, ValueError):
            raise ErroConfig(f"valor inválido em {chave}: {bruto!r}") from None
        if tipo is not bool and not (mn <= v <= mx):
            raise ErroConfig(f"{chave} deve ficar entre {mn:g} e {mx:g}")
        novos[(sec, campo)] = v
        if chave == "entrada":                        # a trava acompanha a entrada (entrada fixa)
            novos[("risco", "max_entrada")] = v
    original = p.read_text(encoding="utf-8") if p.exists() else (RAIZ / "config.exemplo.yaml").read_text(encoding="utf-8")
    linhas = original.splitlines()
    for (sec, campo), v in novos.items():
        _trocar_linha(linhas, sec, campo, v)
    texto = "\n".join(linhas) + "\n"
    dados = yaml.safe_load(texto)
    cfg = validar(_mesclar(PADRAO, dados or {}))      # ErroConfig aqui = nada é gravado
    tmp = p.with_suffix(".tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.replace(p)
    return cfg
