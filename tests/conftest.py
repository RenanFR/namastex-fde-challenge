from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import httpx
import pytest

RAIZ = Path(__file__).resolve().parent.parent
PLANOS = json.loads((RAIZ / "quote-service" / "data" / "plans.json").read_text(encoding="utf-8"))


def cotacao_valida(premio: float = 209.90, com_pro_rata: bool = True) -> dict:
    corpo = {
        "plano_id": "completo",
        "plano_nome": "Completo",
        "premio_mensal": premio,
        "franquia": 3000,
        "coberturas": ["colisao", "roubo", "furto", "terceiros", "vidros"],
        "multiplicadores": {"faixa_etaria": 1.0, "idade_veiculo": 1.0, "regiao": 1.0},
        "carencia": {
            "coberturas": ["roubo", "furto"],
            "dias": 30,
            "observacao": "Coberturas de roubo e furto so passam a valer apos a carencia.",
        },
        "moeda": "BRL",
    }
    if com_pro_rata:
        corpo["primeiro_pagamento_pro_rata"] = {
            "dias_no_mes": 31,
            "dias_cobrados": 15,
            "valor_primeiro_pagamento": 101.56,
        }
    return corpo


class ApiFalsa:
    """Responde no lugar da /quote, com a sequência de respostas que o teste pedir."""

    def __init__(self, respostas: list[httpx.Response]):
        self.respostas = respostas
        self.pedidos: list[httpx.Request] = []

    def __call__(self, pedido: httpx.Request) -> httpx.Response:
        self.pedidos.append(pedido)
        if pedido.url.path == "/planos":
            return httpx.Response(200, json=PLANOS)
        if pedido.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        indice = min(len(self.pedidos) - 1, len(self.respostas) - 1)
        return self.respostas[indice] if self.respostas else httpx.Response(200, json=cotacao_valida())

    @property
    def corpos_enviados(self) -> list[dict]:
        return [json.loads(pedido.content) for pedido in self.pedidos if pedido.content]

    @property
    def chamadas_ao_quote(self) -> int:
        return sum(1 for pedido in self.pedidos if pedido.url.path == "/quote")


@pytest.fixture
def api_falsa():
    def construir(respostas: list[httpx.Response] | None = None) -> tuple[ApiFalsa, httpx.MockTransport]:
        falsa = ApiFalsa(respostas or [httpx.Response(200, json=cotacao_valida())])
        return falsa, httpx.MockTransport(falsa)

    return construir


@pytest.fixture
def hoje() -> dt.date:
    return dt.date(2026, 10, 1)
