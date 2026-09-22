from __future__ import annotations

import pytest

from autoseguro.dtos import LeadDTO, MotivoEncaminhamento, PlanoId, ResultadoCotacao, StatusCotacao
from autoseguro.politica_encaminhamento import AVISO_POR_MOTIVO, ContextoDecisao, PoliticaEncaminhamento

POLITICA = PoliticaEncaminhamento(turnos_maximos_sem_avancar=4)


def contexto(**ajustes) -> ContextoDecisao:
    base = {
        "lead": LeadDTO(idade=34, veiculo_ano=2021, cep="[CEP:1a2b]", plano_id=PlanoId.COMPLETO),
        "ultimo_resultado": None,
        "turnos_sem_avancar": 0,
        "intencao_sinalizada": None,
    }
    return ContextoDecisao(**{**base, **ajustes})


def test_conversa_saudavel_nao_encaminha():
    assert POLITICA.avaliar(contexto()) is None


def test_cotacao_indisponivel_encaminha():
    resultado = ResultadoCotacao(StatusCotacao.INDISPONIVEL, motivo="503", tentativas=3)
    assert POLITICA.avaliar(contexto(ultimo_resultado=resultado)).motivo is MotivoEncaminhamento.COTACAO_INDISPONIVEL


def test_recusa_por_risco_encaminha():
    resultado = ResultadoCotacao(StatusCotacao.RECUSADA_POR_RISCO, motivo="Idade acima do limite.")
    assert POLITICA.avaliar(contexto(ultimo_resultado=resultado)).motivo is MotivoEncaminhamento.RISCO_RECUSADO


def test_qualificacao_travada_encaminha_no_limite():
    assert POLITICA.avaliar(contexto(turnos_sem_avancar=3)) is None
    assert POLITICA.avaliar(contexto(turnos_sem_avancar=4)).motivo is MotivoEncaminhamento.QUALIFICACAO_TRAVADA


def test_cotacao_aprovada_nao_encaminha_sozinha():
    resultado = ResultadoCotacao(StatusCotacao.APROVADA, cotacao=None)
    assert POLITICA.avaliar(contexto(ultimo_resultado=resultado)) is None


@pytest.mark.parametrize(
    "intencao",
    [
        MotivoEncaminhamento.PEDIDO_EXPLICITO,
        MotivoEncaminhamento.NEGOCIACAO_DE_PRECO,
        MotivoEncaminhamento.FORA_DE_ESCOPO,
    ],
)
def test_intencao_do_lead_tem_prioridade_sobre_o_resto(intencao):
    resultado = ResultadoCotacao(StatusCotacao.INDISPONIVEL, motivo="503", tentativas=3)
    decidido = POLITICA.avaliar(contexto(intencao_sinalizada=intencao, ultimo_resultado=resultado))
    assert decidido.motivo is intencao


def test_todo_motivo_tem_um_aviso_para_o_lead():
    assert set(AVISO_POR_MOTIVO) == set(MotivoEncaminhamento)


def test_o_resumo_para_o_humano_diz_o_que_falta_e_o_que_aconteceu():
    resultado = ResultadoCotacao(StatusCotacao.INDISPONIVEL, motivo="o serviço respondeu 503", tentativas=3)
    resumo = POLITICA.avaliar(contexto(ultimo_resultado=resultado)).resumo_para_humano
    assert "cotacao_indisponivel" in resumo
    assert "data_inicio" in resumo
    assert "3 tentativa(s)" in resumo
