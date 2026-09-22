from __future__ import annotations

import datetime as dt
import json
import logging
from dataclasses import dataclass, field

from .cofre_pii import CofrePii
from .cotacao_repository import CotacaoRepository
from .dtos import LeadDTO, MotivoEncaminhamento, PlanoId, ResultadoCotacao, StatusCotacao

registrador = logging.getLogger("autoseguro.ferramentas")

MOTIVOS_QUE_O_MODELO_PODE_SINALIZAR = (
    MotivoEncaminhamento.PEDIDO_EXPLICITO,
    MotivoEncaminhamento.NEGOCIACAO_DE_PRECO,
    MotivoEncaminhamento.FORA_DE_ESCOPO,
)

FERRAMENTAS: tuple[dict, ...] = (
    {
        "name": "consultar_planos",
        "description": (
            "Devolve a tabela oficial de planos, coberturas, franquias e as regras de cotação: "
            "faixa etária, idade do veículo, agravo por região, carência e pró-rata do primeiro mês. "
            "Consulte antes de afirmar qualquer coisa sobre cobertura, franquia ou regra. "
            "Nunca responda isso de memória."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "registrar_dados_do_lead",
        "description": (
            "Grava os dados do lead conforme ele informa. Pode ser chamada várias vezes, "
            "sempre com os campos que você já tem. Dados pessoais aparecem na conversa como "
            "tokens no formato [CEP:1a2b]; repasse o token exatamente como veio, sem tentar adivinhar "
            "o valor por trás dele."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "idade": {"type": "integer", "minimum": 0, "maximum": 120, "description": "Idade do condutor em anos."},
                "veiculo_ano": {
                    "type": "integer",
                    "minimum": 1950,
                    "maximum": 2100,
                    "description": "Ano de fabricação do veículo.",
                },
                "cep": {"type": "string", "description": "CEP onde o carro dorme, normalmente um token [CEP:xxxx]."},
                "plano_id": {
                    "type": "string",
                    "enum": [plano.value for plano in PlanoId],
                    "description": "Plano escolhido pelo lead.",
                },
                "data_inicio": {
                    "type": "string",
                    "description": "Início da vigência em AAAA-MM-DD. Afeta carência e o valor do primeiro pagamento.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "cotar_plano",
        "description": (
            "Calcula a cotação no sistema da seguradora. Esta é a única forma legítima de obter um preço. "
            "Nunca estime, calcule de cabeça, arredonde ou repita um valor que não tenha saído desta ferramenta. "
            "Se ela falhar, diga que falhou."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "plano_id": {
                    "type": "string",
                    "enum": [plano.value for plano in PlanoId],
                    "description": "Use para cotar um plano diferente do registrado, quando o lead quiser comparar.",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "encaminhar_para_humano",
        "description": (
            "Sinaliza que a conversa precisa de um vendedor humano por uma razão que só você "
            "percebe lendo o lead. Falha de sistema e recusa por risco não entram aqui: essas o "
            "próprio fluxo detecta sozinho."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "motivo": {
                    "type": "string",
                    "enum": [motivo.value for motivo in MOTIVOS_QUE_O_MODELO_PODE_SINALIZAR],
                    "description": (
                        "pedido_explicito quando o lead pede uma pessoa; "
                        "negociacao_de_preco quando ele quer desconto ou condição especial; "
                        "fora_de_escopo quando o assunto não é seguro de veículo."
                    ),
                },
                "observacao": {"type": "string", "description": "Uma frase de contexto para o vendedor."},
            },
            "required": ["motivo"],
            "additionalProperties": False,
        },
    },
)


@dataclass
class ResultadoFerramenta:
    conteudo: str
    e_erro: bool = False


@dataclass
class ExecutorFerramentas:
    repositorio: CotacaoRepository
    cofre: CofrePii
    lead: LeadDTO = field(default_factory=LeadDTO)
    ultimo_resultado: ResultadoCotacao | None = None
    intencao_sinalizada: MotivoEncaminhamento | None = None
    observacao_do_modelo: str | None = None

    def executar(self, nome: str, argumentos: dict) -> ResultadoFerramenta:
        despacho = {
            "consultar_planos": self._consultar_planos,
            "registrar_dados_do_lead": self._registrar_dados_do_lead,
            "cotar_plano": self._cotar_plano,
            "encaminhar_para_humano": self._encaminhar_para_humano,
        }
        acao = despacho.get(nome)
        if acao is None:
            return ResultadoFerramenta(f"Ferramenta desconhecida: {nome}.", e_erro=True)
        return acao(argumentos)

    def _consultar_planos(self, _: dict) -> ResultadoFerramenta:
        return ResultadoFerramenta(json.dumps(self.repositorio.planos(), ensure_ascii=False))

    def _registrar_dados_do_lead(self, argumentos: dict) -> ResultadoFerramenta:
        if "idade" in argumentos:
            self.lead.idade = int(argumentos["idade"])
        if "veiculo_ano" in argumentos:
            self.lead.veiculo_ano = int(argumentos["veiculo_ano"])
        if "cep" in argumentos:
            self.lead.cep = str(argumentos["cep"]).strip()
        if "plano_id" in argumentos:
            try:
                self.lead.plano_id = PlanoId(str(argumentos["plano_id"]).lower())
            except ValueError:
                opcoes = ", ".join(plano.value for plano in PlanoId)
                return ResultadoFerramenta(f"Plano inválido. Opções: {opcoes}.", e_erro=True)
        if "data_inicio" in argumentos:
            try:
                self.lead.data_inicio = dt.date.fromisoformat(str(argumentos["data_inicio"]))
            except ValueError:
                return ResultadoFerramenta("Data de início inválida. Use o formato AAAA-MM-DD.", e_erro=True)

        faltantes = self.lead.campos_faltantes
        registrador.info(
            "Dados do lead atualizados. Ainda faltam: %s.",
            ", ".join(faltantes) if faltantes else "nada, já dá para cotar",
        )
        if faltantes:
            return ResultadoFerramenta(f"Registrado. Ainda faltam: {', '.join(faltantes)}.")
        return ResultadoFerramenta("Registrado. Todos os dados necessários estão preenchidos, pode cotar.")

    def _cotar_plano(self, argumentos: dict) -> ResultadoFerramenta:
        plano_original = self.lead.plano_id
        if "plano_id" in argumentos:
            try:
                self.lead.plano_id = PlanoId(str(argumentos["plano_id"]).lower())
            except ValueError:
                opcoes = ", ".join(plano.value for plano in PlanoId)
                return ResultadoFerramenta(f"Plano inválido. Opções: {opcoes}.", e_erro=True)

        resultado = self.repositorio.cotar(self.lead)
        self.ultimo_resultado = resultado

        if resultado.status is StatusCotacao.PEDIDO_INVALIDO:
            self.lead.plano_id = plano_original
            return ResultadoFerramenta(f"Não deu para cotar: {resultado.motivo}.", e_erro=True)
        if resultado.status is StatusCotacao.INDISPONIVEL:
            return ResultadoFerramenta(
                "O sistema de cotação não respondeu após "
                f"{resultado.tentativas} tentativa(s). Não existe preço para informar. "
                "Esta conversa já está sendo passada a um especialista humano, então não prometa "
                "tentar de novo nem pedir para o lead aguardar: quem continua é a pessoa. "
                "Explique a instabilidade, diga que não vai chutar valor e se despeça.",
                e_erro=True,
            )
        if resultado.status is StatusCotacao.RECUSADA_POR_RISCO:
            return ResultadoFerramenta(
                f"A seguradora recusou este perfil: {resultado.motivo} "
                "Não existe preço. Seja direto com o lead sobre a recusa e não prometa "
                "revisão nem exceção: um especialista humano assume a conversa a partir daqui.",
                e_erro=True,
            )
        return ResultadoFerramenta(json.dumps(self._cotacao_para_o_modelo(resultado), ensure_ascii=False))

    def _cotacao_para_o_modelo(self, resultado: ResultadoCotacao) -> dict:
        cotacao = resultado.cotacao
        corpo = {
            "cotacao_id": cotacao.cotacao_id,
            "plano": cotacao.plano_nome,
            "premio_mensal": cotacao.premio_mensal,
            "moeda": cotacao.moeda,
            "franquia": cotacao.franquia,
            "coberturas": list(cotacao.coberturas),
            "carencia": {
                "coberturas": list(cotacao.carencia.coberturas),
                "dias": cotacao.carencia.dias,
                "observacao": cotacao.carencia.observacao,
            },
            "lembrete": (
                "Informe o prêmio exatamente como está aqui e cite a carência. "
                "Se houver primeiro pagamento proporcional, explique que só o primeiro mês é menor."
            ),
        }
        if cotacao.primeiro_pagamento is not None:
            corpo["primeiro_pagamento_proporcional"] = {
                "valor": cotacao.primeiro_pagamento.valor,
                "dias_cobrados": cotacao.primeiro_pagamento.dias_cobrados,
                "dias_no_mes": cotacao.primeiro_pagamento.dias_no_mes,
            }
        return corpo

    def _encaminhar_para_humano(self, argumentos: dict) -> ResultadoFerramenta:
        try:
            motivo = MotivoEncaminhamento(str(argumentos.get("motivo", "")).lower())
        except ValueError:
            opcoes = ", ".join(item.value for item in MOTIVOS_QUE_O_MODELO_PODE_SINALIZAR)
            return ResultadoFerramenta(f"Motivo inválido. Opções: {opcoes}.", e_erro=True)
        if motivo not in MOTIVOS_QUE_O_MODELO_PODE_SINALIZAR:
            opcoes = ", ".join(item.value for item in MOTIVOS_QUE_O_MODELO_PODE_SINALIZAR)
            return ResultadoFerramenta(f"Esse motivo não é sinalizado por você. Opções: {opcoes}.", e_erro=True)

        self.intencao_sinalizada = motivo
        self.observacao_do_modelo = argumentos.get("observacao")
        registrador.info("O agente pediu ajuda humana por %s.", motivo.value)
        return ResultadoFerramenta("Encaminhamento registrado. Se despeça do lead avisando que um especialista assume.")
