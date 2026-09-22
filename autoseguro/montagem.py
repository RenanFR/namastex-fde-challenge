from __future__ import annotations

import datetime as dt
import time
import uuid
from collections.abc import Callable
from pathlib import Path

import httpx

from .cliente_http_resiliente import ClienteHttpResiliente, EventoTentativa, PoliticaResiliencia
from .cofre_pii import CofrePii
from .conversa_view_model import ConversaViewModel
from .cotacao_api_service import CotacaoApiService
from .cotacao_repository import CotacaoRepository
from .feature_flags import FeatureFlags
from .politica_encaminhamento import PoliticaEncaminhamento
from .provedores import ProvedorLLM
from .trilha import Evento, Trilha


def montar_conversa(
    base_url: str,
    provedor: ProvedorLLM,
    arquivo_trilha: Path | None = None,
    flags: FeatureFlags | None = None,
    politica_http: PoliticaResiliencia | None = None,
    hoje: dt.date | None = None,
    semente_pii: str | None = None,
    conversa_id: str | None = None,
    transporte: httpx.BaseTransport | None = None,
    ao_tentar_http: Callable[[EventoTentativa], None] | None = None,
) -> ConversaViewModel:
    """Único lugar que sabe montar o grafo de dependências da conversa."""
    identificador = conversa_id or f"conv_{uuid.uuid4().hex[:8]}"
    cofre = CofrePii(semente=semente_pii) if semente_pii else CofrePii()
    trilha = Trilha(conversa_id=identificador, arquivo=arquivo_trilha)

    def anotar_tentativa(evento: EventoTentativa) -> None:
        trilha.registrar(
            Evento.TENTATIVA_HTTP,
            caminho=evento.caminho,
            tentativa=evento.tentativa,
            de_quantas=evento.de_quantas,
            resultado=evento.resultado.value,
            status=evento.status,
            duracao_ms=evento.duracao_ms,
            espera_ate_a_proxima_ms=evento.espera_ate_a_proxima_ms,
        )
        if ao_tentar_http is not None:
            ao_tentar_http(evento)

    cliente = ClienteHttpResiliente(
        base_url=base_url,
        politica=politica_http or PoliticaResiliencia(),
        transporte=transporte,
        ao_tentar=anotar_tentativa,
        dormir=(lambda _: None) if transporte is not None else time.sleep,
    )
    return ConversaViewModel(
        repositorio=CotacaoRepository(CotacaoApiService(cliente), cofre),
        provedor=provedor,
        cofre=cofre,
        trilha=trilha,
        politica=PoliticaEncaminhamento(),
        flags=flags or FeatureFlags.do_ambiente(),
        hoje=hoje,
        conversa_id=identificador,
    )
