from __future__ import annotations

import os
from dataclasses import dataclass


def _ligada(nome: str, padrao: bool) -> bool:
    bruto = os.getenv(nome)
    if bruto is None:
        return padrao
    return bruto.strip().lower() in {"1", "true", "sim", "on"}


@dataclass(frozen=True)
class FeatureFlags:
    """Análogo do FeatureFlagsDTO do OND: o que dá para desligar sem novo deploy."""

    agente_habilitado: bool = True
    encaminhamento_automatico: bool = True
    mascarar_dados_pessoais: bool = True

    @staticmethod
    def do_ambiente() -> "FeatureFlags":
        return FeatureFlags(
            agente_habilitado=_ligada("AUTOSEGURO_AGENTE_HABILITADO", True),
            encaminhamento_automatico=_ligada("AUTOSEGURO_ENCAMINHAMENTO_AUTOMATICO", True),
            mascarar_dados_pessoais=_ligada("AUTOSEGURO_MASCARAR_PII", True),
        )
