from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class ChamadaFerramenta:
    identificador: str
    nome: str
    argumentos: dict


@dataclass(frozen=True)
class ResultadoDeFerramenta:
    identificador: str
    conteudo: str
    e_erro: bool = False


@dataclass(frozen=True)
class RespostaModelo:
    texto: str
    chamadas: tuple[ChamadaFerramenta, ...] = ()
    tokens_entrada: int = 0
    tokens_saida: int = 0

    @property
    def pediu_ferramenta(self) -> bool:
        return bool(self.chamadas)


@runtime_checkable
class ProvedorLLM(Protocol):
    """O agente fala com o modelo só por aqui.

    Cada provedor guarda o próprio histórico, porque cada um tem a sua forma de
    representar a conversa. Quem usa só manda mensagem e devolve resultado de ferramenta.
    """

    nome: str

    def iniciar(self, sistema: str, ferramentas: tuple[dict, ...]) -> None: ...

    def enviar_mensagem_do_lead(self, texto: str) -> RespostaModelo: ...

    def enviar_resultados(self, resultados: tuple[ResultadoDeFerramenta, ...]) -> RespostaModelo: ...


@dataclass
class ProvedorRoteirizado:
    """Dublê determinístico: devolve, em ordem, as respostas que o roteiro definiu.

    Serve para os testes e para rodar o agente de ponta a ponta sem chave de API e sem
    custo. Ele não tenta parecer inteligente de propósito: o que se quer testar aqui é o
    fluxo em volta do modelo, não o modelo.
    """

    roteiro: list[RespostaModelo] = field(default_factory=list)
    nome: str = "roteirizado"
    sistema: str = ""
    ferramentas: tuple[dict, ...] = ()
    recebidas: list[str] = field(default_factory=list)
    resultados_recebidos: list[ResultadoDeFerramenta] = field(default_factory=list)
    _posicao: int = 0

    def iniciar(self, sistema: str, ferramentas: tuple[dict, ...]) -> None:
        self.sistema = sistema
        self.ferramentas = ferramentas

    def enviar_mensagem_do_lead(self, texto: str) -> RespostaModelo:
        self.recebidas.append(texto)
        return self._proxima()

    def enviar_resultados(self, resultados: tuple[ResultadoDeFerramenta, ...]) -> RespostaModelo:
        self.resultados_recebidos.extend(resultados)
        return self._proxima()

    def _proxima(self) -> RespostaModelo:
        if self._posicao >= len(self.roteiro):
            return RespostaModelo(texto="(roteiro terminou)")
        resposta = self.roteiro[self._posicao]
        self._posicao += 1
        return resposta
