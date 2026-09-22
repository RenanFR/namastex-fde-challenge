from __future__ import annotations

from dataclasses import dataclass

from .dtos import Encaminhamento, LeadDTO, MotivoEncaminhamento, ResultadoCotacao, StatusCotacao


@dataclass(frozen=True)
class ContextoDecisao:
    lead: LeadDTO
    ultimo_resultado: ResultadoCotacao | None
    turnos_sem_avancar: int
    intencao_sinalizada: MotivoEncaminhamento | None


AVISO_POR_MOTIVO: dict[MotivoEncaminhamento, str] = {
    MotivoEncaminhamento.COTACAO_INDISPONIVEL: (
        "Passei seus dados para um especialista, que continua com você por aqui mesmo."
    ),
    MotivoEncaminhamento.RISCO_RECUSADO: (
        "Um especialista vai olhar seu caso e falar com você por aqui mesmo."
    ),
    MotivoEncaminhamento.PEDIDO_EXPLICITO: (
        "Já passei a conversa para um especialista, com tudo o que você me contou."
    ),
    MotivoEncaminhamento.NEGOCIACAO_DE_PRECO: (
        "Desconto e condição especial são com o time comercial. Estou te encaminhando agora."
    ),
    MotivoEncaminhamento.QUALIFICACAO_TRAVADA: (
        "Vou chamar um especialista para continuar com você daqui."
    ),
    MotivoEncaminhamento.FORA_DE_ESCOPO: (
        "Isso foge do que eu resolvo por aqui, que é seguro de veículo. "
        "Vou te passar para alguém do time que te atende melhor."
    ),
}


@dataclass(frozen=True)
class PoliticaEncaminhamento:
    """Quando o agente para e chama um humano.

    A decisão é determinística de propósito: o modelo conduz a conversa, mas quem
    decide desistir é esta regra, que roda igual em todo turno e é testável sem LLM.
    O modelo participa só como sensor, sinalizando a intenção do lead por enum tipado.
    """

    turnos_maximos_sem_avancar: int = 4

    def avaliar(self, contexto: ContextoDecisao) -> Encaminhamento | None:
        motivo = self._motivo(contexto)
        if motivo is None:
            return None
        return Encaminhamento(
            motivo=motivo,
            resumo_para_humano=self._resumo(motivo, contexto),
            lead=contexto.lead,
            ultima_cotacao=contexto.ultimo_resultado,
        )

    def _motivo(self, contexto: ContextoDecisao) -> MotivoEncaminhamento | None:
        if contexto.intencao_sinalizada is not None:
            return contexto.intencao_sinalizada
        resultado = contexto.ultimo_resultado
        if resultado is not None:
            if resultado.status is StatusCotacao.RECUSADA_POR_RISCO:
                return MotivoEncaminhamento.RISCO_RECUSADO
            if resultado.status is StatusCotacao.INDISPONIVEL:
                return MotivoEncaminhamento.COTACAO_INDISPONIVEL
        if contexto.turnos_sem_avancar >= self.turnos_maximos_sem_avancar:
            return MotivoEncaminhamento.QUALIFICACAO_TRAVADA
        return None

    def _resumo(self, motivo: MotivoEncaminhamento, contexto: ContextoDecisao) -> str:
        lead = contexto.lead
        conhecido = [
            f"idade {lead.idade}" if lead.idade is not None else None,
            f"veículo {lead.veiculo_ano}" if lead.veiculo_ano is not None else None,
            f"CEP {lead.cep}" if lead.cep else None,
            f"plano {lead.plano_id.value}" if lead.plano_id else None,
            f"início em {lead.data_inicio.isoformat()}" if lead.data_inicio else None,
        ]
        coletado = ", ".join(item for item in conhecido if item) or "nada coletado ainda"
        faltando = ", ".join(lead.campos_faltantes) or "nenhum"

        linhas = [
            f"Motivo do encaminhamento: {motivo.value}.",
            f"Dados coletados: {coletado}.",
            f"Dados ainda pendentes: {faltando}.",
        ]
        resultado = contexto.ultimo_resultado
        if resultado is None:
            linhas.append("Nenhuma cotação chegou a ser pedida.")
        elif resultado.aprovada:
            cotacao = resultado.cotacao
            linhas.append(
                f"Cotação [{cotacao.cotacao_id}] já emitida: plano {cotacao.plano_nome} "
                f"por {cotacao.moeda} {cotacao.premio_mensal:.2f} por mês."
            )
        else:
            linhas.append(
                f"Última tentativa de cotação terminou em {resultado.status.value} "
                f"após {resultado.tentativas} tentativa(s). Motivo: {(resultado.motivo or 'não informado').rstrip('.')}."
            )
        return " ".join(linhas)
