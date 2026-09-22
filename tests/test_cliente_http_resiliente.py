from __future__ import annotations

import httpx
import pytest

from autoseguro.cliente_http_resiliente import (
    CircuitoAberto,
    ClienteHttpResiliente,
    EstadoCircuito,
    FalhaTransitoria,
    PoliticaResiliencia,
)

POLITICA_RAPIDA = PoliticaResiliencia(
    tentativas_maximas=3,
    espera_inicial_segundos=0.0,
    jitter_maximo_segundos=0.0,
    falhas_para_abrir_circuito=2,
    segundos_circuito_aberto=10.0,
)


def montar(respostas, politica=POLITICA_RAPIDA, relogio=None):
    sequencia = iter(respostas)

    def responder(_pedido: httpx.Request) -> httpx.Response:
        return next(sequencia)

    esperas: list[float] = []
    cliente = ClienteHttpResiliente(
        base_url="http://api.local",
        politica=politica,
        transporte=httpx.MockTransport(responder),
        dormir=esperas.append,
    )
    if relogio is not None:
        cliente.agora = relogio
    return cliente, esperas


def test_insiste_em_erro_transitorio_e_devolve_o_sucesso_seguinte():
    cliente, esperas = montar(
        [
            httpx.Response(503, json={"error": "upstream_unavailable"}),
            httpx.Response(500, json={"error": "upstream_unavailable"}),
            httpx.Response(200, json={"premio_mensal": 209.9}),
        ]
    )
    resposta = cliente.post_json("/quote", {})
    assert resposta.status == 200
    assert resposta.tentativas == 3
    assert len(esperas) == 2


def test_desiste_depois_do_limite_de_tentativas():
    cliente, _ = montar([httpx.Response(503, json={}) for _ in range(3)])
    with pytest.raises(FalhaTransitoria) as falha:
        cliente.post_json("/quote", {})
    assert falha.value.tentativas == 3


def test_nao_insiste_quando_a_recusa_e_definitiva():
    cliente, esperas = montar([httpx.Response(422, json={"error": "cotacao_recusada", "motivo": "Idade."})])
    resposta = cliente.post_json("/quote", {})
    assert resposta.status == 422
    assert resposta.tentativas == 1
    assert esperas == []


def test_timeout_conta_como_erro_transitorio():
    def estoura(_pedido: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("demorou demais")

    cliente = ClienteHttpResiliente(
        base_url="http://api.local",
        politica=POLITICA_RAPIDA,
        transporte=httpx.MockTransport(estoura),
        dormir=lambda _: None,
    )
    with pytest.raises(FalhaTransitoria) as falha:
        cliente.post_json("/quote", {})
    assert "não respondeu" in falha.value.descricao


def test_circuito_abre_apos_falhas_seguidas_e_para_de_chamar():
    agora = [0.0]
    cliente, _ = montar([httpx.Response(503, json={}) for _ in range(6)], relogio=lambda: agora[0])
    for _ in range(2):
        with pytest.raises(FalhaTransitoria):
            cliente.post_json("/quote", {})
    assert cliente.estado_circuito is EstadoCircuito.ABERTO
    with pytest.raises(CircuitoAberto):
        cliente.post_json("/quote", {})


def test_circuito_volta_a_permitir_chamada_depois_do_tempo():
    agora = [0.0]
    respostas = [httpx.Response(503, json={}) for _ in range(6)] + [httpx.Response(200, json={"ok": True})]
    cliente, _ = montar(respostas, relogio=lambda: agora[0])
    for _ in range(2):
        with pytest.raises(FalhaTransitoria):
            cliente.post_json("/quote", {})
    agora[0] = 11.0
    assert cliente.estado_circuito is EstadoCircuito.MEIO_ABERTO
    assert cliente.post_json("/quote", {}).status == 200
    assert cliente.estado_circuito is EstadoCircuito.FECHADO


def test_o_backoff_cresce_a_cada_tentativa():
    politica = PoliticaResiliencia(
        tentativas_maximas=3, espera_inicial_segundos=1.0, fator_backoff=2.0, jitter_maximo_segundos=0.0
    )
    cliente, esperas = montar([httpx.Response(503, json={}) for _ in range(3)], politica=politica)
    with pytest.raises(FalhaTransitoria):
        cliente.post_json("/quote", {})
    assert esperas == [1.0, 2.0]
