from __future__ import annotations

from dataclasses import dataclass

from .cliente_http_resiliente import ClienteHttpResiliente, RespostaHttp
from .dtos import CotacaoRequestDTO


@dataclass
class CotacaoApiService:
    """Fala HTTP com a API de cotação. Não interpreta regra de negócio."""

    cliente: ClienteHttpResiliente

    def cotar(self, pedido: CotacaoRequestDTO) -> RespostaHttp:
        return self.cliente.post_json("/quote", pedido.para_json())

    def consultar_planos(self) -> RespostaHttp:
        return self.cliente.get_json("/planos")

    def esta_no_ar(self) -> bool:
        try:
            return self.cliente.get_json("/health").corpo.get("status") == "ok"
        except Exception:
            return False
