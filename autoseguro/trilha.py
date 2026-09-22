from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Evento(str, Enum):
    CONVERSA_INICIADA = "conversa_iniciada"
    MENSAGEM_DO_LEAD = "mensagem_do_lead"
    MENSAGEM_DO_AGENTE = "mensagem_do_agente"
    MODELO_CONSULTADO = "modelo_consultado"
    FERRAMENTA_EXECUTADA = "ferramenta_executada"
    TENTATIVA_HTTP = "tentativa_http"
    COTACAO_EMITIDA = "cotacao_emitida"
    COTACAO_NAO_SAIU = "cotacao_nao_saiu"
    PRECO_BLOQUEADO = "preco_bloqueado"
    ENCAMINHADA_PARA_HUMANO = "encaminhada_para_humano"
    CONVERSA_ENCERRADA = "conversa_encerrada"


@dataclass
class Trilha:
    """Uma linha por evento, em JSONL, para conseguir reconstruir o que aconteceu.

    O texto já chega mascarado por quem chama. Nada de dado pessoal entra aqui.
    """

    conversa_id: str
    arquivo: Path | None = None
    ao_registrar: Callable[[dict], None] | None = None
    turno: int = 0
    _linhas: list[dict] = field(default_factory=list, repr=False)

    def registrar(self, evento: Evento, **campos) -> dict:
        linha = {
            "momento": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),
            "conversa_id": self.conversa_id,
            "turno": self.turno,
            "evento": evento.value,
            **campos,
        }
        self._linhas.append(linha)
        if self.arquivo is not None:
            self.arquivo.parent.mkdir(parents=True, exist_ok=True)
            with self.arquivo.open("a", encoding="utf-8") as saida:
                saida.write(json.dumps(linha, ensure_ascii=False) + "\n")
        if self.ao_registrar is not None:
            self.ao_registrar(linha)
        return linha

    @property
    def linhas(self) -> tuple[dict, ...]:
        return tuple(self._linhas)

    def eventos_do_tipo(self, evento: Evento) -> tuple[dict, ...]:
        return tuple(linha for linha in self._linhas if linha["evento"] == evento.value)
