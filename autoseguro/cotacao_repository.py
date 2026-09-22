from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from .cliente_http_resiliente import CircuitoAberto, FalhaTransitoria
from .cofre_pii import CofrePii
from .cotacao_api_service import CotacaoApiService
from .dtos import CotacaoDTO, CotacaoRequestDTO, LeadDTO, ResultadoCotacao, StatusCotacao

registrador = logging.getLogger("autoseguro.cotacao")


@dataclass
class CotacaoRepository:
    """Traduz o que a API devolveu em um resultado tipado que o agente sabe usar.

    Esta é a única fonte de preço do sistema. Nada acima desta camada calcula,
    estima ou repete um prêmio que não tenha saído daqui.
    """

    servico: CotacaoApiService
    cofre: CofrePii
    gerar_id: Callable[[], str] = lambda: f"cot_{uuid.uuid4().hex[:8]}"
    _planos_em_cache: dict | None = field(default=None, repr=False)

    @property
    def planos_ja_consultados(self) -> dict | None:
        return self._planos_em_cache

    def planos(self) -> dict:
        if self._planos_em_cache is None:
            self._planos_em_cache = self.servico.consultar_planos().corpo
        return self._planos_em_cache

    def cotar(self, lead: LeadDTO) -> ResultadoCotacao:
        if not lead.qualificado:
            faltando = ", ".join(lead.campos_faltantes)
            return ResultadoCotacao(status=StatusCotacao.PEDIDO_INVALIDO, motivo=f"faltam dados do lead: {faltando}")

        cotacao_id = self.gerar_id()
        pedido = CotacaoRequestDTO(
            plano_id=lead.plano_id,
            idade=lead.idade,
            veiculo_ano=lead.veiculo_ano,
            cep=self.cofre.revelar(lead.cep),
            data_inicio=lead.data_inicio,
        )

        try:
            resposta = self.servico.cotar(pedido)
        except CircuitoAberto as circuito:
            registrador.error(
                "Cotação [%s] não foi pedida: o serviço está fora do ar e só será tentado de novo em %.0fs.",
                cotacao_id,
                circuito.segundos_restantes,
            )
            return ResultadoCotacao(
                status=StatusCotacao.INDISPONIVEL,
                motivo="serviço de cotação fora do ar",
                tentativas=0,
            )
        except FalhaTransitoria as falha:
            registrador.error(
                "Cotação [%s] do plano %s falhou após %d tentativas porque %s.",
                cotacao_id,
                lead.plano_id.value,
                falha.tentativas,
                falha.descricao,
            )
            return ResultadoCotacao(
                status=StatusCotacao.INDISPONIVEL,
                motivo=falha.descricao,
                tentativas=falha.tentativas,
            )

        if resposta.status == 422:
            motivo = resposta.corpo.get("motivo", "perfil fora das regras de aceitação")
            registrador.info("Cotação [%s] recusada pela seguradora: %s", cotacao_id, motivo)
            return ResultadoCotacao(
                status=StatusCotacao.RECUSADA_POR_RISCO,
                motivo=motivo,
                tentativas=resposta.tentativas,
                duracao_ms=resposta.duracao_ms,
            )

        if resposta.status >= 400:
            detalhe = resposta.corpo.get("detalhe") or resposta.corpo.get("error", "pedido inválido")
            registrador.warning("Cotação [%s] foi recusada por dado inválido: %s", cotacao_id, detalhe)
            return ResultadoCotacao(
                status=StatusCotacao.PEDIDO_INVALIDO,
                motivo=str(detalhe),
                tentativas=resposta.tentativas,
                duracao_ms=resposta.duracao_ms,
            )

        cotacao = CotacaoDTO.de_json(resposta.corpo, cotacao_id, resposta.tentativas)
        registrador.info(
            "Cotação [%s] do plano %s saiu em %s %.2f por mês, na tentativa %d.",
            cotacao_id,
            cotacao.plano_nome,
            cotacao.moeda,
            cotacao.premio_mensal,
            resposta.tentativas,
        )
        return ResultadoCotacao(
            status=StatusCotacao.APROVADA,
            cotacao=cotacao,
            tentativas=resposta.tentativas,
            duracao_ms=resposta.duracao_ms,
        )
