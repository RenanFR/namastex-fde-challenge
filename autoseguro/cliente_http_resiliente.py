from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import httpx

registrador = logging.getLogger("autoseguro.http")

STATUS_QUE_MERECEM_NOVA_TENTATIVA = frozenset({408, 425, 429, 500, 502, 503, 504})


class EstadoCircuito(str, Enum):
    FECHADO = "fechado"
    ABERTO = "aberto"
    MEIO_ABERTO = "meio_aberto"


class ResultadoTentativa(str, Enum):
    SUCESSO = "sucesso"
    ERRO_TRANSITORIO = "erro_transitorio"
    ERRO_DEFINITIVO = "erro_definitivo"
    ESTOUROU_O_TEMPO = "estourou_o_tempo"
    SEM_CONEXAO = "sem_conexao"


class FalhaTransitoria(Exception):
    def __init__(self, descricao: str, tentativas: int):
        self.descricao = descricao
        self.tentativas = tentativas
        super().__init__(descricao)


class CircuitoAberto(Exception):
    def __init__(self, segundos_restantes: float):
        self.segundos_restantes = segundos_restantes
        super().__init__(f"circuito aberto por mais {segundos_restantes:.1f}s")


@dataclass(frozen=True)
class PoliticaResiliencia:
    tentativas_maximas: int = 3
    timeout_segundos: float = 3.0
    espera_inicial_segundos: float = 0.4
    fator_backoff: float = 2.0
    jitter_maximo_segundos: float = 0.25
    falhas_para_abrir_circuito: int = 4
    segundos_circuito_aberto: float = 20.0


@dataclass(frozen=True)
class RespostaHttp:
    status: int
    corpo: dict
    tentativas: int
    duracao_ms: int


@dataclass(frozen=True)
class EventoTentativa:
    caminho: str
    tentativa: int
    de_quantas: int
    resultado: ResultadoTentativa
    status: int | None
    duracao_ms: int
    espera_ate_a_proxima_ms: int


@dataclass
class ClienteHttpResiliente:
    """Análogo do AuthenticatedHttpClient do OND: toda chamada externa passa por aqui.

    A `/quote` é um sistema legado que falha e engasga de propósito. Quem trata isso
    é esta camada, não o agente e não a regra de negócio.
    """

    base_url: str
    politica: PoliticaResiliencia = field(default_factory=PoliticaResiliencia)
    ao_tentar: Callable[[EventoTentativa], None] | None = None
    sorteio: random.Random = field(default_factory=random.Random)
    agora: Callable[[], float] = time.monotonic
    dormir: Callable[[float], None] = time.sleep
    transporte: httpx.BaseTransport | None = None
    _cliente: httpx.Client | None = field(default=None, repr=False)
    _falhas_consecutivas: int = field(default=0, repr=False)
    _circuito_abre_ate: float = field(default=0.0, repr=False)

    def __post_init__(self) -> None:
        if self._cliente is None:
            self._cliente = httpx.Client(
                base_url=self.base_url,
                timeout=self.politica.timeout_segundos,
                transport=self.transporte,
            )

    @property
    def estado_circuito(self) -> EstadoCircuito:
        if self._circuito_abre_ate == 0.0:
            return EstadoCircuito.FECHADO
        if self.agora() < self._circuito_abre_ate:
            return EstadoCircuito.ABERTO
        return EstadoCircuito.MEIO_ABERTO

    def fechar(self) -> None:
        if self._cliente is not None:
            self._cliente.close()

    def post_json(self, caminho: str, corpo: dict) -> RespostaHttp:
        return self._chamar("POST", caminho, corpo)

    def get_json(self, caminho: str) -> RespostaHttp:
        return self._chamar("GET", caminho, None)

    def _chamar(self, metodo: str, caminho: str, corpo: dict | None) -> RespostaHttp:
        if self.estado_circuito is EstadoCircuito.ABERTO:
            restante = self._circuito_abre_ate - self.agora()
            registrador.warning(
                "Chamada a %s barrada: o serviço de cotação está fora do ar e volta a ser tentado em %.0fs.",
                caminho,
                restante,
            )
            raise CircuitoAberto(restante)

        ultima_descricao = "sem detalhe"
        for tentativa in range(1, self.politica.tentativas_maximas + 1):
            inicio = self.agora()
            resultado, status, corpo_resposta, ultima_descricao = self._uma_tentativa(metodo, caminho, corpo)
            duracao_ms = int((self.agora() - inicio) * 1000)

            ultima = tentativa == self.politica.tentativas_maximas
            espera = 0.0 if (ultima or resultado is ResultadoTentativa.SUCESSO) else self._espera(tentativa)
            if resultado is ResultadoTentativa.ERRO_DEFINITIVO:
                espera = 0.0

            self._avisar(caminho, tentativa, resultado, status, duracao_ms, espera)

            if resultado is ResultadoTentativa.SUCESSO:
                self._registrar_sucesso()
                return RespostaHttp(status or 200, corpo_resposta or {}, tentativa, duracao_ms)

            if resultado is ResultadoTentativa.ERRO_DEFINITIVO:
                self._registrar_sucesso()
                return RespostaHttp(status or 400, corpo_resposta or {}, tentativa, duracao_ms)

            registrador.warning(
                "Tentativa %d de %d para %s não passou (%s). %s",
                tentativa,
                self.politica.tentativas_maximas,
                caminho,
                ultima_descricao,
                "Desistindo." if ultima else f"Tentando de novo em {espera:.1f}s.",
            )
            if not ultima:
                self.dormir(espera)

        self._registrar_falha()
        raise FalhaTransitoria(ultima_descricao, self.politica.tentativas_maximas)

    def _uma_tentativa(
        self, metodo: str, caminho: str, corpo: dict | None
    ) -> tuple[ResultadoTentativa, int | None, dict | None, str]:
        assert self._cliente is not None
        try:
            resposta = self._cliente.request(metodo, caminho, json=corpo)
        except httpx.TimeoutException:
            return ResultadoTentativa.ESTOUROU_O_TEMPO, None, None, (
                f"o serviço não respondeu em {self.politica.timeout_segundos:.0f}s"
            )
        except httpx.TransportError as falha:
            return ResultadoTentativa.SEM_CONEXAO, None, None, f"não foi possível conectar ({falha.__class__.__name__})"

        try:
            corpo_resposta = resposta.json()
        except ValueError:
            corpo_resposta = {"error": "resposta_ilegivel", "texto": resposta.text[:200]}

        if resposta.status_code in STATUS_QUE_MERECEM_NOVA_TENTATIVA:
            return ResultadoTentativa.ERRO_TRANSITORIO, resposta.status_code, corpo_resposta, (
                f"o serviço respondeu {resposta.status_code}"
            )
        if resposta.status_code >= 400:
            return ResultadoTentativa.ERRO_DEFINITIVO, resposta.status_code, corpo_resposta, (
                f"o serviço recusou o pedido com {resposta.status_code}"
            )
        return ResultadoTentativa.SUCESSO, resposta.status_code, corpo_resposta, "ok"

    def _espera(self, tentativa: int) -> float:
        base = self.politica.espera_inicial_segundos * (self.politica.fator_backoff ** (tentativa - 1))
        return base + self.sorteio.uniform(0.0, self.politica.jitter_maximo_segundos)

    def _avisar(
        self,
        caminho: str,
        tentativa: int,
        resultado: ResultadoTentativa,
        status: int | None,
        duracao_ms: int,
        espera: float,
    ) -> None:
        if self.ao_tentar is None:
            return
        self.ao_tentar(
            EventoTentativa(
                caminho=caminho,
                tentativa=tentativa,
                de_quantas=self.politica.tentativas_maximas,
                resultado=resultado,
                status=status,
                duracao_ms=duracao_ms,
                espera_ate_a_proxima_ms=int(espera * 1000),
            )
        )

    def _registrar_sucesso(self) -> None:
        self._falhas_consecutivas = 0
        self._circuito_abre_ate = 0.0

    def _registrar_falha(self) -> None:
        self._falhas_consecutivas += 1
        if self._falhas_consecutivas >= self.politica.falhas_para_abrir_circuito:
            self._circuito_abre_ate = self.agora() + self.politica.segundos_circuito_aberto
            registrador.error(
                "O serviço de cotação falhou %d vezes seguidas. Parando de chamar por %.0fs para não piorar.",
                self._falhas_consecutivas,
                self.politica.segundos_circuito_aberto,
            )
