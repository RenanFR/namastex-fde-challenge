from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from .provedores import ChamadaFerramenta, RespostaModelo, ResultadoDeFerramenta

registrador = logging.getLogger("autoseguro.modelo")

MODELO_PADRAO = "claude-opus-5"
ESFORCO_PADRAO = "medium"


@dataclass
class ProvedorAnthropic:
    """Cliente do modelo de verdade.

    O esforço fica em medium porque isto é uma conversa de vendas: o lead está do outro
    lado esperando, e latência alta custa mais do que a última fração de qualidade.
    """

    modelo: str = field(default_factory=lambda: os.getenv("AUTOSEGURO_MODELO", MODELO_PADRAO))
    esforco: str = field(default_factory=lambda: os.getenv("AUTOSEGURO_ESFORCO", ESFORCO_PADRAO))
    limite_de_tokens: int = 4096
    nome: str = "anthropic"
    _cliente: object | None = field(default=None, repr=False)
    _sistema: str = field(default="", repr=False)
    _ferramentas: list[dict] = field(default_factory=list, repr=False)
    _mensagens: list[dict] = field(default_factory=list, repr=False)

    @staticmethod
    def ha_credencial() -> bool:
        return bool(os.getenv("ANTHROPIC_API_KEY"))

    def iniciar(self, sistema: str, ferramentas: tuple[dict, ...]) -> None:
        import anthropic

        self._cliente = anthropic.Anthropic()
        self._sistema = sistema
        self._ferramentas = list(ferramentas)
        self._mensagens = []

    def enviar_mensagem_do_lead(self, texto: str) -> RespostaModelo:
        self._mensagens.append({"role": "user", "content": texto})
        return self._chamar()

    def enviar_resultados(self, resultados: tuple[ResultadoDeFerramenta, ...]) -> RespostaModelo:
        self._mensagens.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": resultado.identificador,
                        "content": resultado.conteudo,
                        **({"is_error": True} if resultado.e_erro else {}),
                    }
                    for resultado in resultados
                ],
            }
        )
        return self._chamar()

    def _chamar(self) -> RespostaModelo:
        if self._cliente is None:
            raise RuntimeError("O provedor precisa ser iniciado antes de conversar.")

        resposta = self._cliente.messages.create(
            model=self.modelo,
            max_tokens=self.limite_de_tokens,
            system=self._sistema,
            tools=self._ferramentas,
            output_config={"effort": self.esforco},
            messages=self._mensagens,
        )

        if resposta.stop_reason == "refusal":
            registrador.error("O modelo recusou responder esta mensagem.")
            self._mensagens.append({"role": "assistant", "content": resposta.content})
            return RespostaModelo(texto="", chamadas=())

        self._mensagens.append({"role": "assistant", "content": resposta.content})

        texto = "".join(bloco.text for bloco in resposta.content if bloco.type == "text").strip()
        chamadas = tuple(
            ChamadaFerramenta(identificador=bloco.id, nome=bloco.name, argumentos=dict(bloco.input))
            for bloco in resposta.content
            if bloco.type == "tool_use"
        )
        return RespostaModelo(
            texto=texto,
            chamadas=chamadas,
            tokens_entrada=resposta.usage.input_tokens,
            tokens_saida=resposta.usage.output_tokens,
        )
