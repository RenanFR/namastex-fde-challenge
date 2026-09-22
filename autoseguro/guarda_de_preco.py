from __future__ import annotations

import re
from dataclasses import dataclass

from .dtos import ResultadoCotacao

CANDIDATOS_MONETARIOS = re.compile(
    r"R\$\s*[\d][\d.,]*"
    r"|\b\d{1,3}(?:\.\d{3})+(?:,\d{2})?\b"
    r"|\b\d+,\d{2}\b"
)

TEXTO_SEGURO_SEM_COTACAO = (
    "Não consigo te passar valor agora porque a cotação não saiu do sistema. "
    "Prefiro não chutar um número que pode não ser o seu."
)


def _para_numero(bruto: str) -> float | None:
    limpo = bruto.replace("R$", "").strip()
    if not limpo:
        return None
    tem_ponto, tem_virgula = "." in limpo, "," in limpo
    if tem_ponto and tem_virgula:
        limpo = limpo.replace(".", "").replace(",", ".")
    elif tem_virgula:
        limpo = limpo.replace(",", ".")
    elif tem_ponto:
        limpo = limpo.replace(".", "")
    try:
        return round(float(limpo), 2)
    except ValueError:
        return None


def valores_citados(texto: str) -> set[float]:
    achados = set()
    for bruto in CANDIDATOS_MONETARIOS.findall(texto):
        numero = _para_numero(bruto)
        if numero is not None:
            achados.add(numero)
    return achados


def _da_tabela_de_planos(planos: dict | None) -> set[float]:
    if not planos:
        return set()
    autorizados: set[float] = set()
    for plano in planos.get("planos", []):
        autorizados.add(round(float(plano["base_mensal"]), 2))
        autorizados.add(float(plano["franquia"]))
    regras = planos.get("regras", {})
    carencia = regras.get("carencia")
    if carencia:
        autorizados.add(float(carencia["dias"]))
    return autorizados


def _da_cotacao(resultado: ResultadoCotacao | None) -> set[float]:
    if resultado is None or not resultado.aprovada:
        return set()
    cotacao = resultado.cotacao
    autorizados = {round(cotacao.premio_mensal, 2), float(cotacao.franquia), float(cotacao.carencia.dias)}
    if cotacao.primeiro_pagamento is not None:
        autorizados.add(round(cotacao.primeiro_pagamento.valor, 2))
        autorizados.add(float(cotacao.primeiro_pagamento.dias_cobrados))
        autorizados.add(float(cotacao.primeiro_pagamento.dias_no_mes))
    return autorizados


def cotacao_falhou(resultado: ResultadoCotacao | None) -> bool:
    return resultado is not None and not resultado.aprovada


def valores_autorizados(resultado: ResultadoCotacao | None, planos: dict | None = None) -> set[float]:
    """O que o agente pode dizer em número.

    Antes de cotar, a tabela de planos é material de venda legítimo: preço de tabela e
    franquia saíram da API e o lead precisa deles para escolher. Depois que uma tentativa de
    cotação falha, nada monetário passa, porque naquele ponto qualquer número é lido pelo
    lead como resposta ao preço dele.
    """
    if cotacao_falhou(resultado):
        return set()
    return _da_tabela_de_planos(planos) | _da_cotacao(resultado)


@dataclass(frozen=True)
class Veredito:
    aprovado: bool
    valores_nao_autorizados: tuple[float, ...] = ()


def conferir(texto: str, resultado: ResultadoCotacao | None, planos: dict | None = None) -> Veredito:
    """Impede que um valor que não saiu da API chegue ao lead.

    O prompt já manda o modelo não inventar preço, mas prompt é pedido, não garantia.
    Aqui a garantia é estrutural: todo número com cara de dinheiro no texto precisa ter
    vindo da API, senão a mensagem não sai.
    """
    citados = valores_citados(texto)
    if not citados:
        return Veredito(aprovado=True)
    intrusos = tuple(sorted(citados - valores_autorizados(resultado, planos)))
    return Veredito(aprovado=not intrusos, valores_nao_autorizados=intrusos)
