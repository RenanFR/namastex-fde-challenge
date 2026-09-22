from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .cliente_http_resiliente import EventoTentativa, ResultadoTentativa
from .conversa_view_model import ConversaViewModel
from .montagem import montar_conversa
from .provedor_anthropic import ProvedorAnthropic
from .provedores import ChamadaFerramenta, ProvedorLLM, ProvedorRoteirizado, RespostaModelo

CINZA = "\033[90m"
AZUL = "\033[94m"
VERDE = "\033[92m"
AMARELO = "\033[93m"
FIM = "\033[0m"


def _carregar_roteiro(caminho: Path) -> ProvedorRoteirizado:
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    turnos = [
        RespostaModelo(
            texto=turno.get("texto", ""),
            chamadas=tuple(
                ChamadaFerramenta(
                    identificador=chamada.get("id", f"toolu_{indice}"),
                    nome=chamada["nome"],
                    argumentos=chamada.get("argumentos", {}),
                )
                for indice, chamada in enumerate(turno.get("chamadas", []))
            ),
        )
        for turno in bruto["turnos"]
    ]
    return ProvedorRoteirizado(roteiro=turnos)


def _escolher_provedor(argumentos) -> ProvedorLLM:
    if argumentos.roteiro:
        return _carregar_roteiro(Path(argumentos.roteiro))
    if argumentos.provedor == "roteirizado":
        raise SystemExit("O provedor roteirizado precisa de --roteiro apontando para um arquivo de turnos.")
    if not ProvedorAnthropic.ha_credencial():
        raise SystemExit(
            "ANTHROPIC_API_KEY não está no ambiente.\n"
            "Coloque a chave num arquivo .env e carregue no shell, ou rode com --roteiro "
            "exemplos/roteiro_cotacao_feliz.json para ver o fluxo completo sem chave e sem custo."
        )
    return ProvedorAnthropic()


def _mostrar_tentativa(evento: EventoTentativa) -> None:
    if evento.resultado is ResultadoTentativa.SUCESSO:
        if evento.tentativa > 1:
            print(f"{CINZA}  [infra] {evento.caminho} respondeu na tentativa {evento.tentativa}{FIM}")
        return
    proxima = (
        f", nova tentativa em {evento.espera_ate_a_proxima_ms}ms" if evento.espera_ate_a_proxima_ms else ", desistindo"
    )
    print(
        f"{AMARELO}  [infra] {evento.caminho} tentativa {evento.tentativa}/{evento.de_quantas}: "
        f"{evento.resultado.value}{proxima}{FIM}"
    )


def _mostrar_estado(conversa: ConversaViewModel) -> None:
    faltando = conversa.lead.campos_faltantes
    resumo = ", ".join(faltando) if faltando else "nada, pronto para cotar"
    print(f"{CINZA}  [estado] {conversa.estado.value} | falta: {resumo}{FIM}")


def _rodar(conversa: ConversaViewModel, mensagens: list[str], interativo: bool) -> None:
    conversa.iniciar()
    print(f"{CINZA}conversa {conversa.conversa_id}{FIM}\n")

    for mensagem in mensagens:
        print(f"{AZUL}lead>{FIM} {mensagem}")
        print(f"{VERDE}ana >{FIM} {conversa.responder(mensagem)}\n")
        if conversa.encerrada:
            break

    while interativo and not conversa.encerrada:
        try:
            entrada = input(f"{AZUL}lead>{FIM} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not entrada:
            continue
        print(f"{VERDE}ana >{FIM} {conversa.responder(entrada)}\n")

    if conversa.encaminhamento is not None:
        print(f"{AMARELO}--- passado para o time humano ---{FIM}")
        print(conversa.encaminhamento.resumo_para_humano)
    conversa.descartar()


def main(argv: list[str] | None = None) -> int:
    analisador = argparse.ArgumentParser(
        prog="autoseguro",
        description="Agente de cotação de seguro de veículo da AutoSeguro.",
    )
    analisador.add_argument("--api", default="http://localhost:8000", help="Base da API de cotação.")
    analisador.add_argument("--provedor", choices=("anthropic", "roteirizado"), default="anthropic")
    analisador.add_argument("--roteiro", help="Arquivo de turnos para rodar sem chave de API.")
    analisador.add_argument("--conversa", help="Arquivo com uma mensagem do lead por linha.")
    analisador.add_argument("--trilha", default="execucoes/trilha.jsonl", help="Onde gravar a trilha em JSONL.")
    analisador.add_argument("--verboso", action="store_true", help="Mostra os logs da infraestrutura.")
    argumentos = analisador.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if argumentos.verboso else logging.WARNING,
        format=f"{CINZA}%(levelname)s %(name)s: %(message)s{FIM}",
        stream=sys.stderr,
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)

    mensagens: list[str] = []
    if argumentos.conversa:
        mensagens = [
            linha.strip()
            for linha in Path(argumentos.conversa).read_text(encoding="utf-8").splitlines()
            if linha.strip() and not linha.startswith("#")
        ]

    conversa = montar_conversa(
        base_url=argumentos.api,
        provedor=_escolher_provedor(argumentos),
        arquivo_trilha=Path(argumentos.trilha) if argumentos.trilha else None,
        ao_tentar_http=_mostrar_tentativa,
    )
    conversa.adicionar_ouvinte(_mostrar_estado)
    _rodar(conversa, mensagens, interativo=not argumentos.conversa)
    print(f"{CINZA}trilha gravada em {argumentos.trilha}{FIM}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
