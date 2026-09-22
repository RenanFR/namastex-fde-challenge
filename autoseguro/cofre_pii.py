from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from enum import Enum


class TipoDadoPessoal(str, Enum):
    EMAIL = "EMAIL"
    CPF = "CPF"
    TELEFONE = "TELEFONE"
    CEP = "CEP"
    PLACA = "PLACA"


PADROES: tuple[tuple[TipoDadoPessoal, re.Pattern[str]], ...] = (
    (TipoDadoPessoal.EMAIL, re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    (TipoDadoPessoal.CPF, re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b")),
    (TipoDadoPessoal.TELEFONE, re.compile(r"(?:\+55\s?)?\(?\d{2}\)?\s?9?\d{4}[-\s]?\d{4}\b")),
    (TipoDadoPessoal.CEP, re.compile(r"\b\d{5}-\d{3}\b|\b\d{8}\b")),
    (TipoDadoPessoal.PLACA, re.compile(r"\b[A-Z]{3}\d[A-Z]\d{2}\b|\b[A-Z]{3}-?\d{4}\b")),
)

PADRAO_TOKEN = re.compile(r"\[(EMAIL|CPF|TELEFONE|CEP|PLACA):([0-9a-f]{4})\]")


@dataclass
class CofrePii:
    """Troca dado pessoal por token antes do texto sair para o modelo ou para a trilha.

    O valor original fica só em memória, neste processo. A ponta que precisa do valor
    de verdade (o cliente HTTP da cotação, porque o CEP é insumo de preço) chama
    `revelar` no último momento.
    """

    semente: str = field(default_factory=lambda: os.urandom(8).hex())
    _por_token: dict[str, str] = field(default_factory=dict, repr=False)
    _por_valor: dict[str, str] = field(default_factory=dict, repr=False)

    def mascarar(self, texto: str) -> str:
        if not texto:
            return texto
        mascarado = texto
        for tipo, padrao in PADROES:
            mascarado = padrao.sub(lambda achado: self._token(tipo, achado.group(0)), mascarado)
        return mascarado

    def revelar(self, valor: str | None) -> str | None:
        if valor is None:
            return None
        return PADRAO_TOKEN.sub(lambda achado: self._por_token.get(achado.group(0), achado.group(0)), valor)

    def contem_dado_pessoal(self, texto: str) -> bool:
        return any(padrao.search(texto) for _, padrao in PADROES)

    @property
    def tokens_emitidos(self) -> int:
        return len(self._por_token)

    def _token(self, tipo: TipoDadoPessoal, valor: str) -> str:
        if valor in self._por_valor:
            return self._por_valor[valor]
        digest = hashlib.blake2s(f"{self.semente}:{valor}".encode(), digest_size=2).hexdigest()
        token = f"[{tipo.value}:{digest}]"
        self._por_token[token] = valor
        self._por_valor[valor] = token
        return token
