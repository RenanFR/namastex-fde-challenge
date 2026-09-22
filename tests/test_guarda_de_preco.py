from __future__ import annotations

import pytest

from autoseguro.dtos import (
    CarenciaDTO,
    CotacaoDTO,
    PlanoId,
    PrimeiroPagamentoDTO,
    ResultadoCotacao,
    StatusCotacao,
)
from autoseguro.guarda_de_preco import conferir, valores_citados

COTACAO = CotacaoDTO(
    cotacao_id="cot_teste",
    plano_id=PlanoId.COMPLETO,
    plano_nome="Completo",
    premio_mensal=209.90,
    franquia=3000,
    coberturas=("colisao", "roubo"),
    multiplicadores={},
    carencia=CarenciaDTO(coberturas=("roubo", "furto"), dias=30, observacao=""),
    moeda="BRL",
    primeiro_pagamento=PrimeiroPagamentoDTO(dias_no_mes=31, dias_cobrados=15, valor=101.56),
)
APROVADA = ResultadoCotacao(StatusCotacao.APROVADA, cotacao=COTACAO)
INDISPONIVEL = ResultadoCotacao(StatusCotacao.INDISPONIVEL, motivo="503", tentativas=3)


def test_deixa_passar_o_texto_fiel_a_cotacao():
    texto = (
        "Fica em R$ 209,90 por mes, franquia de R$ 3.000, "
        "primeiro pagamento de R$ 101,56 pelos 15 dias, carencia de 30 dias."
    )
    assert conferir(texto, APROVADA).aprovado


def test_barra_preco_que_nao_saiu_da_api_mesmo_com_cotacao_aprovada():
    veredito = conferir("Consigo fazer por R$ 189,90 pra voce.", APROVADA)
    assert not veredito.aprovado
    assert veredito.valores_nao_autorizados == (189.9,)


def test_barra_qualquer_valor_quando_a_cotacao_nao_saiu():
    veredito = conferir("Fica em R$ 209,90 por mes.", INDISPONIVEL)
    assert not veredito.aprovado
    assert veredito.valores_nao_autorizados == (209.9,)


def test_deixa_passar_texto_sem_valor_quando_a_cotacao_falhou():
    assert conferir("O sistema esta instavel, vou te passar pra um especialista.", INDISPONIVEL).aprovado


@pytest.mark.parametrize(
    "texto",
    [
        "Seu Onix 2021 esta coberto em 30 dias.",
        "Comecando dia 17 de outubro, com 15 dias no primeiro mes.",
        "Sao 3 planos disponiveis.",
    ],
)
def test_ano_prazo_e_contagem_nao_sao_confundidos_com_dinheiro(texto):
    assert conferir(texto, INDISPONIVEL).aprovado


def test_reconhece_valor_escrito_sem_o_simbolo_da_moeda():
    assert valores_citados("o valor e 209,90 por mes") == {209.90}


def test_reconhece_milhar_com_ponto():
    assert 3000.0 in valores_citados("franquia de R$ 3.000")


TABELA_DE_PLANOS = {
    "planos": [
        {"id": "essencial", "base_mensal": 119.90, "franquia": 4500},
        {"id": "completo", "base_mensal": 209.90, "franquia": 3000},
        {"id": "premium", "base_mensal": 339.90, "franquia": 1500},
    ],
    "regras": {"carencia": {"dias": 30}},
}


def test_antes_de_cotar_o_cardapio_de_planos_pode_ser_apresentado():
    texto = (
        "Sao tres opcoes: Essencial a partir de R$ 119,90 com franquia de R$ 4.500, "
        "Completo por R$ 209,90 com franquia de R$ 3.000, "
        "e Premium por R$ 339,90 com franquia de R$ 1.500."
    )
    assert conferir(texto, None, TABELA_DE_PLANOS).aprovado


def test_sem_a_tabela_em_maos_o_cardapio_e_barrado():
    veredito = conferir("O Essencial sai por R$ 119,90.", None, None)
    assert not veredito.aprovado
    assert veredito.valores_nao_autorizados == (119.9,)


def test_depois_da_cotacao_falhar_nem_o_preco_de_tabela_passa():
    veredito = conferir("O Completo custa R$ 209,90 de tabela.", INDISPONIVEL, TABELA_DE_PLANOS)
    assert not veredito.aprovado
    assert veredito.valores_nao_autorizados == (209.9,)


def test_recusa_por_risco_tambem_corta_qualquer_valor():
    recusada = ResultadoCotacao(StatusCotacao.RECUSADA_POR_RISCO, motivo="Idade acima do limite.")
    assert not conferir("Seria R$ 119,90.", recusada, TABELA_DE_PLANOS).aprovado


def test_valor_inventado_continua_barrado_mesmo_com_a_tabela():
    veredito = conferir("Consigo por R$ 99,90 pra voce.", None, TABELA_DE_PLANOS)
    assert not veredito.aprovado
    assert veredito.valores_nao_autorizados == (99.9,)
