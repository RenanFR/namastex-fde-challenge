from __future__ import annotations

import datetime as dt

import httpx

from autoseguro.cliente_http_resiliente import ClienteHttpResiliente, PoliticaResiliencia
from autoseguro.cofre_pii import CofrePii
from autoseguro.cotacao_api_service import CotacaoApiService
from autoseguro.cotacao_repository import CotacaoRepository
from autoseguro.dtos import LeadDTO, PlanoId, StatusCotacao
from tests.conftest import cotacao_valida

POLITICA_RAPIDA = PoliticaResiliencia(tentativas_maximas=3, espera_inicial_segundos=0.0, jitter_maximo_segundos=0.0)


def montar(api_falsa, respostas=None):
    falsa, transporte = api_falsa(respostas)
    cofre = CofrePii(semente="fixa")
    cliente = ClienteHttpResiliente(
        base_url="http://api.local", politica=POLITICA_RAPIDA, transporte=transporte, dormir=lambda _: None
    )
    return falsa, cofre, CotacaoRepository(CotacaoApiService(cliente), cofre)


def lead_completo(cep: str) -> LeadDTO:
    return LeadDTO(
        idade=34, veiculo_ano=2021, cep=cep, plano_id=PlanoId.COMPLETO, data_inicio=dt.date(2026, 10, 17)
    )


def test_cotacao_aprovada_traz_premio_carencia_e_pro_rata(api_falsa):
    _, cofre, repositorio = montar(api_falsa)
    resultado = repositorio.cotar(lead_completo(cofre.mascarar("01310-100")))
    assert resultado.status is StatusCotacao.APROVADA
    assert resultado.cotacao.premio_mensal == 209.90
    assert resultado.cotacao.carencia.coberturas == ("roubo", "furto")
    assert resultado.cotacao.carencia.dias == 30
    assert resultado.cotacao.primeiro_pagamento.valor == 101.56
    assert resultado.cotacao.cotacao_id.startswith("cot_")


def test_o_cep_real_chega_na_api_mesmo_o_lead_guardando_so_o_token(api_falsa):
    falsa, cofre, repositorio = montar(api_falsa)
    token = cofre.mascarar("07123-456")
    lead = lead_completo(token)
    assert lead.cep.startswith("[CEP:")
    repositorio.cotar(lead)
    assert falsa.corpos_enviados[-1]["cep"] == "07123-456"


def test_recusa_por_risco_nao_vira_indisponibilidade(api_falsa):
    respostas = [httpx.Response(422, json={"error": "cotacao_recusada", "motivo": "Idade acima do limite."})]
    falsa, cofre, repositorio = montar(api_falsa, respostas)
    resultado = repositorio.cotar(lead_completo(cofre.mascarar("01310-100")))
    assert resultado.status is StatusCotacao.RECUSADA_POR_RISCO
    assert resultado.motivo == "Idade acima do limite."
    assert falsa.chamadas_ao_quote == 1


def test_api_fora_do_ar_vira_indisponivel_sem_cotacao(api_falsa):
    respostas = [httpx.Response(503, json={"error": "upstream_unavailable"}) for _ in range(3)]
    falsa, cofre, repositorio = montar(api_falsa, respostas)
    resultado = repositorio.cotar(lead_completo(cofre.mascarar("01310-100")))
    assert resultado.status is StatusCotacao.INDISPONIVEL
    assert resultado.cotacao is None
    assert resultado.tentativas == 3
    assert falsa.chamadas_ao_quote == 3


def test_lead_incompleto_nem_chega_a_chamar_a_api(api_falsa):
    falsa, _, repositorio = montar(api_falsa)
    resultado = repositorio.cotar(LeadDTO(idade=34))
    assert resultado.status is StatusCotacao.PEDIDO_INVALIDO
    assert falsa.chamadas_ao_quote == 0
    assert "veiculo_ano" in resultado.motivo


def test_a_tabela_de_planos_e_buscada_uma_vez_so(api_falsa):
    falsa, _, repositorio = montar(api_falsa)
    repositorio.planos()
    repositorio.planos()
    assert sum(1 for pedido in falsa.pedidos if pedido.url.path == "/planos") == 1
