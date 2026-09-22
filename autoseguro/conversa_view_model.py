from __future__ import annotations

import datetime as dt
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field

from . import guarda_de_preco, prompt
from .cofre_pii import CofrePii
from .cotacao_repository import CotacaoRepository
from .dtos import Encaminhamento, EstadoConversa, LeadDTO, Mensagem, StatusCotacao, TipoRemetente
from .feature_flags import FeatureFlags
from .ferramentas import FERRAMENTAS, ExecutorFerramentas
from .politica_encaminhamento import AVISO_POR_MOTIVO, ContextoDecisao, PoliticaEncaminhamento
from .provedores import ProvedorLLM, ResultadoDeFerramenta
from .trilha import Evento, Trilha

registrador = logging.getLogger("autoseguro.conversa")

LIMITE_DE_VOLTAS_DE_FERRAMENTA = 8
AVISO_AGENTE_DESLIGADO = "Nosso atendimento automático está desligado no momento. Um consultor vai te responder."
AVISO_JA_ENCAMINHADA = "Já passei sua conversa para um especialista. Ele continua com você por aqui mesmo."


@dataclass
class ConversaViewModel:
    """Orquestrador da conversa, no papel do ViewModel do OND.

    Guarda o estado, avisa quem estiver ouvindo a cada mudança e para de notificar
    depois de descartado. A tela (a CLI) só lê daqui, nunca fala com o modelo ou com a API.
    """

    repositorio: CotacaoRepository
    provedor: ProvedorLLM
    cofre: CofrePii
    trilha: Trilha
    politica: PoliticaEncaminhamento = field(default_factory=PoliticaEncaminhamento)
    flags: FeatureFlags = field(default_factory=FeatureFlags)
    hoje: dt.date | None = None
    conversa_id: str = field(default_factory=lambda: f"conv_{uuid.uuid4().hex[:8]}")
    estado: EstadoConversa = EstadoConversa.QUALIFICANDO
    mensagens: list[Mensagem] = field(default_factory=list)
    encaminhamento: Encaminhamento | None = None
    _executor: ExecutorFerramentas | None = field(default=None, repr=False)
    _ouvintes: list[Callable[["ConversaViewModel"], None]] = field(default_factory=list, repr=False)
    _descartado: bool = field(default=False, repr=False)
    _turnos_sem_avancar: int = field(default=0, repr=False)

    @property
    def lead(self) -> LeadDTO:
        return self._exigir_executor().lead

    @property
    def encerrada(self) -> bool:
        return self.estado in (EstadoConversa.ENCAMINHADA, EstadoConversa.ENCERRADA)

    def adicionar_ouvinte(self, ouvinte: Callable[["ConversaViewModel"], None]) -> None:
        self._ouvintes.append(ouvinte)

    def notificar_ouvintes(self) -> None:
        if self._descartado:
            return
        for ouvinte in self._ouvintes:
            ouvinte(self)

    def descartar(self) -> None:
        self._descartado = True
        self._ouvintes.clear()

    def iniciar(self) -> None:
        self._executor = ExecutorFerramentas(repositorio=self.repositorio, cofre=self.cofre)
        self.provedor.iniciar(prompt.montar(self.hoje), FERRAMENTAS)
        self.trilha.registrar(
            Evento.CONVERSA_INICIADA,
            provedor=self.provedor.nome,
            encaminhamento_automatico=self.flags.encaminhamento_automatico,
        )
        self.notificar_ouvintes()

    def responder(self, texto_do_lead: str) -> str:
        if self._descartado:
            raise RuntimeError("Esta conversa já foi descartada.")
        if not self.flags.agente_habilitado:
            return self._encerrar_com(AVISO_AGENTE_DESLIGADO, EstadoConversa.ENCAMINHADA)
        if self.encerrada:
            return AVISO_JA_ENCAMINHADA

        self.trilha.turno += 1
        texto = self.cofre.mascarar(texto_do_lead) if self.flags.mascarar_dados_pessoais else texto_do_lead
        self._guardar(TipoRemetente.LEAD, texto)
        self.trilha.registrar(Evento.MENSAGEM_DO_LEAD, texto=texto)

        faltantes_antes = self.lead.campos_faltantes
        resposta = self.provedor.enviar_mensagem_do_lead(texto)

        for _ in range(LIMITE_DE_VOLTAS_DE_FERRAMENTA):
            self.trilha.registrar(
                Evento.MODELO_CONSULTADO,
                tokens_entrada=resposta.tokens_entrada,
                tokens_saida=resposta.tokens_saida,
                ferramentas_pedidas=[chamada.nome for chamada in resposta.chamadas],
            )
            if not resposta.pediu_ferramenta:
                break
            resposta = self.provedor.enviar_resultados(self._executar(resposta.chamadas))
        else:
            registrador.warning("O modelo insistiu em chamar ferramenta além do limite de voltas. Seguindo sem ela.")

        self._atualizar_progresso(faltantes_antes)
        texto_final = self._aplicar_politica(self._barrar_preco_nao_autorizado(resposta.texto))
        self._guardar(TipoRemetente.AGENTE, texto_final)
        self.trilha.registrar(Evento.MENSAGEM_DO_AGENTE, texto=texto_final, estado=self.estado.value)
        self.notificar_ouvintes()
        return texto_final

    def _barrar_preco_nao_autorizado(self, texto: str) -> str:
        veredito = guarda_de_preco.conferir(
            texto,
            self._exigir_executor().ultimo_resultado,
            self.repositorio.planos_ja_consultados,
        )
        if veredito.aprovado:
            return texto
        registrador.error(
            "O agente tentou informar %s, que não saiu da cotação. A mensagem foi barrada antes de chegar ao lead.",
            ", ".join(f"R$ {valor:.2f}" for valor in veredito.valores_nao_autorizados),
        )
        self.trilha.registrar(
            Evento.PRECO_BLOQUEADO,
            valores_nao_autorizados=list(veredito.valores_nao_autorizados),
            texto_barrado=texto,
        )
        return guarda_de_preco.TEXTO_SEGURO_SEM_COTACAO

    def _executar(self, chamadas) -> tuple[ResultadoDeFerramenta, ...]:
        executor = self._exigir_executor()
        resultados = []
        for chamada in chamadas:
            saida = executor.executar(chamada.nome, chamada.argumentos)
            self.trilha.registrar(
                Evento.FERRAMENTA_EXECUTADA,
                ferramenta=chamada.nome,
                argumentos=chamada.argumentos,
                deu_erro=saida.e_erro,
            )
            self._registrar_cotacao(chamada.nome)
            resultados.append(
                ResultadoDeFerramenta(identificador=chamada.identificador, conteudo=saida.conteudo, e_erro=saida.e_erro)
            )
        return tuple(resultados)

    def _registrar_cotacao(self, nome_da_ferramenta: str) -> None:
        if nome_da_ferramenta != "cotar_plano":
            return
        resultado = self._exigir_executor().ultimo_resultado
        if resultado is None:
            return
        if resultado.aprovada:
            self.estado = EstadoConversa.COTACAO_APRESENTADA
            self.trilha.registrar(
                Evento.COTACAO_EMITIDA,
                cotacao_id=resultado.cotacao.cotacao_id,
                plano=resultado.cotacao.plano_id.value,
                premio_mensal=resultado.cotacao.premio_mensal,
                tentativas=resultado.tentativas,
                duracao_ms=resultado.duracao_ms,
            )
        else:
            self.trilha.registrar(
                Evento.COTACAO_NAO_SAIU,
                status=resultado.status.value,
                motivo=resultado.motivo,
                tentativas=resultado.tentativas,
            )

    def _atualizar_progresso(self, faltantes_antes: tuple[str, ...]) -> None:
        executor = self._exigir_executor()
        avancou = len(executor.lead.campos_faltantes) < len(faltantes_antes)
        cotou = executor.ultimo_resultado is not None and executor.ultimo_resultado.status is StatusCotacao.APROVADA
        self._turnos_sem_avancar = 0 if (avancou or cotou) else self._turnos_sem_avancar + 1

    def _aplicar_politica(self, texto_do_modelo: str) -> str:
        executor = self._exigir_executor()
        encaminhamento = self.politica.avaliar(
            ContextoDecisao(
                lead=executor.lead,
                ultimo_resultado=executor.ultimo_resultado,
                turnos_sem_avancar=self._turnos_sem_avancar,
                intencao_sinalizada=executor.intencao_sinalizada,
            )
        )
        if encaminhamento is None or not self.flags.encaminhamento_automatico:
            return texto_do_modelo or "Desculpa, me perdi aqui. Pode repetir?"

        self.encaminhamento = encaminhamento
        self.estado = EstadoConversa.ENCAMINHADA
        registrador.info(
            "Conversa [%s] encaminhada para um humano por %s.", self.conversa_id, encaminhamento.motivo.value
        )
        self.trilha.registrar(
            Evento.ENCAMINHADA_PARA_HUMANO,
            motivo=encaminhamento.motivo.value,
            resumo=encaminhamento.resumo_para_humano,
            observacao_do_modelo=executor.observacao_do_modelo,
        )
        aviso = AVISO_POR_MOTIVO[encaminhamento.motivo]
        if not texto_do_modelo or texto_do_modelo == guarda_de_preco.TEXTO_SEGURO_SEM_COTACAO:
            return aviso
        return f"{texto_do_modelo}\n\n{aviso}".strip()

    def _encerrar_com(self, texto: str, estado: EstadoConversa) -> str:
        self.estado = estado
        self._guardar(TipoRemetente.AGENTE, texto)
        self.trilha.registrar(Evento.CONVERSA_ENCERRADA, estado=estado.value, texto=texto)
        self.notificar_ouvintes()
        return texto

    def _guardar(self, remetente: TipoRemetente, texto: str) -> None:
        self.mensagens.append(
            Mensagem(mensagem_id=f"msg_{uuid.uuid4().hex[:8]}", remetente=remetente, texto=texto)
        )

    def _exigir_executor(self) -> ExecutorFerramentas:
        if self._executor is None:
            raise RuntimeError("Chame iniciar() antes de conversar.")
        return self._executor
