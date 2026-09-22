from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum


class PlanoId(str, Enum):
    ESSENCIAL = "essencial"
    COMPLETO = "completo"
    PREMIUM = "premium"


class StatusCotacao(str, Enum):
    APROVADA = "aprovada"
    RECUSADA_POR_RISCO = "recusada_por_risco"
    PEDIDO_INVALIDO = "pedido_invalido"
    INDISPONIVEL = "indisponivel"


class MotivoEncaminhamento(str, Enum):
    COTACAO_INDISPONIVEL = "cotacao_indisponivel"
    RISCO_RECUSADO = "risco_recusado"
    PEDIDO_EXPLICITO = "pedido_explicito"
    NEGOCIACAO_DE_PRECO = "negociacao_de_preco"
    QUALIFICACAO_TRAVADA = "qualificacao_travada"
    FORA_DE_ESCOPO = "fora_de_escopo"


class EstadoConversa(str, Enum):
    QUALIFICANDO = "qualificando"
    COTANDO = "cotando"
    COTACAO_APRESENTADA = "cotacao_apresentada"
    ENCAMINHADA = "encaminhada"
    ENCERRADA = "encerrada"


class TipoRemetente(str, Enum):
    LEAD = "lead"
    AGENTE = "agente"


@dataclass
class LeadDTO:
    idade: int | None = None
    veiculo_ano: int | None = None
    cep: str | None = None
    plano_id: PlanoId | None = None
    data_inicio: dt.date | None = None

    CAMPOS_OBRIGATORIOS = ("idade", "veiculo_ano", "cep", "plano_id", "data_inicio")

    @property
    def campos_faltantes(self) -> tuple[str, ...]:
        return tuple(campo for campo in self.CAMPOS_OBRIGATORIOS if getattr(self, campo) is None)

    @property
    def qualificado(self) -> bool:
        return not self.campos_faltantes


@dataclass(frozen=True)
class CotacaoRequestDTO:
    plano_id: PlanoId
    idade: int
    veiculo_ano: int
    cep: str | None = None
    data_inicio: dt.date | None = None

    def para_json(self) -> dict:
        corpo: dict = {
            "plano_id": self.plano_id.value,
            "idade": self.idade,
            "veiculo_ano": self.veiculo_ano,
        }
        if self.cep:
            corpo["cep"] = self.cep
        if self.data_inicio:
            corpo["data_inicio"] = self.data_inicio.isoformat()
        return corpo


@dataclass(frozen=True)
class CarenciaDTO:
    coberturas: tuple[str, ...]
    dias: int
    observacao: str


@dataclass(frozen=True)
class PrimeiroPagamentoDTO:
    dias_no_mes: int
    dias_cobrados: int
    valor: float


@dataclass(frozen=True)
class CotacaoDTO:
    cotacao_id: str
    plano_id: PlanoId
    plano_nome: str
    premio_mensal: float
    franquia: int
    coberturas: tuple[str, ...]
    multiplicadores: dict[str, float]
    carencia: CarenciaDTO
    moeda: str
    primeiro_pagamento: PrimeiroPagamentoDTO | None = None
    tentativas_ate_responder: int = 1

    @staticmethod
    def de_json(corpo: dict, cotacao_id: str, tentativas: int) -> "CotacaoDTO":
        carencia = corpo["carencia"]
        pro_rata = corpo.get("primeiro_pagamento_pro_rata")
        return CotacaoDTO(
            cotacao_id=cotacao_id,
            plano_id=PlanoId(corpo["plano_id"]),
            plano_nome=corpo["plano_nome"],
            premio_mensal=corpo["premio_mensal"],
            franquia=corpo["franquia"],
            coberturas=tuple(corpo["coberturas"]),
            multiplicadores=dict(corpo["multiplicadores"]),
            carencia=CarenciaDTO(
                coberturas=tuple(carencia["coberturas"]),
                dias=carencia["dias"],
                observacao=carencia["observacao"],
            ),
            moeda=corpo["moeda"],
            primeiro_pagamento=(
                PrimeiroPagamentoDTO(
                    dias_no_mes=pro_rata["dias_no_mes"],
                    dias_cobrados=pro_rata["dias_cobrados"],
                    valor=pro_rata["valor_primeiro_pagamento"],
                )
                if pro_rata
                else None
            ),
            tentativas_ate_responder=tentativas,
        )


@dataclass(frozen=True)
class ResultadoCotacao:
    status: StatusCotacao
    cotacao: CotacaoDTO | None = None
    motivo: str | None = None
    tentativas: int = 0
    duracao_ms: int = 0

    @property
    def aprovada(self) -> bool:
        return self.status is StatusCotacao.APROVADA and self.cotacao is not None


@dataclass(frozen=True)
class Encaminhamento:
    motivo: MotivoEncaminhamento
    resumo_para_humano: str
    lead: LeadDTO
    ultima_cotacao: ResultadoCotacao | None = None


@dataclass
class Mensagem:
    mensagem_id: str
    remetente: TipoRemetente
    texto: str
    momento: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))
