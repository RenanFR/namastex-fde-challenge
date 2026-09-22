"""Trava a publicação se houver segredo ou dado pessoal seu no repositório.

O desafio pede que as conversas com IA sejam publicadas junto do código, num repositório
público. Transcript de ferramenta de IA é justamente onde chave e dado pessoal vazam sem
ninguém perceber. Rode isto antes de publicar:

    .venv/bin/python scripts/verificar_segredos.py

Sai com código 1 se achar qualquer coisa. Nenhum valor encontrado é impresso inteiro.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTAS_IGNORADAS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".idea", ".vscode"}
EXTENSOES_BINARIAS = {".parquet", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".whl", ".so", ".dylib"}
PASTAS_COM_DADO_SINTETICO = {"dataset", "tests", "exemplos", "execucoes", "analise"}
PASTA_DOS_LOGS_DE_IA = "ai-logs"

SEGREDOS: dict[str, re.Pattern[str]] = {
    "chave da Anthropic": re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    "chave da OpenAI": re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"),
    "chave de acesso da AWS": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "token do GitHub": re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{20,}\b"),
    "token do Slack": re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b"),
    "chave privada": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    "token Bearer": re.compile(r"Bearer\s+ey[A-Za-z0-9_\-]{20,}\."),
    "variável de chave preenchida": re.compile(r"(?:API_KEY|SECRET|TOKEN|PASSWORD)\s*[=:]\s*[\"']?[A-Za-z0-9/_\-]{16,}"),
}

DADOS_PESSOAIS: dict[str, re.Pattern[str]] = {
    "CPF": re.compile(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b"),
    "e-mail": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "telefone": re.compile(r"(?<![\w])(?:\+55[\s-])?(?:\(\d{2}\)|\d{2})[\s-]9\d{4}[\s-]\d{4}(?![\w])"),
}


class Achado:
    def __init__(self, arquivo: Path, linha: int, rotulo: str, trecho: str, grave: bool):
        self.arquivo = arquivo
        self.linha = linha
        self.rotulo = rotulo
        self.trecho = trecho
        self.grave = grave

    def __str__(self) -> str:
        gravidade = "SEGREDO" if self.grave else "dado pessoal"
        return f"{gravidade}: {self.rotulo} em {self.arquivo}:{self.linha} -> {self.trecho}"


def _ocultar(valor: str) -> str:
    if len(valor) <= 8:
        return "*" * len(valor)
    return f"{valor[:3]}...{valor[-2:]} ({len(valor)} caracteres)"


def _interessa(caminho: Path) -> bool:
    if any(parte in PASTAS_IGNORADAS for parte in caminho.parts):
        return False
    return caminho.suffix.lower() not in EXTENSOES_BINARIAS


def _e_log_de_ia(caminho: Path, raiz: Path) -> bool:
    return caminho.relative_to(raiz).parts[:1] == (PASTA_DOS_LOGS_DE_IA,)


def _tem_dado_sintetico(caminho: Path, raiz: Path) -> bool:
    partes = caminho.relative_to(raiz).parts
    return bool(partes) and partes[0] in PASTAS_COM_DADO_SINTETICO


def _ignorados_pelo_git(raiz: Path, candidatos: list[Path]) -> set[Path]:
    """O que o git ignora nunca chega ao repositório público, então não bloqueia a publicação."""
    if not (raiz / ".git").exists() or not candidatos:
        return set()
    entrada = "\n".join(str(caminho.relative_to(raiz)) for caminho in candidatos)
    try:
        resultado = subprocess.run(
            ["git", "check-ignore", "--stdin"],
            cwd=raiz,
            input=entrada,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    return {raiz / linha for linha in resultado.stdout.splitlines() if linha}


def varrer(raiz: Path) -> tuple[list[Achado], int]:
    candidatos = [
        caminho
        for caminho in sorted(raiz.rglob("*"))
        if caminho.is_file() and _interessa(caminho) and caminho.resolve() != Path(__file__).resolve()
    ]
    ignorados = _ignorados_pelo_git(raiz, candidatos)

    achados: list[Achado] = []
    for caminho in candidatos:
        if caminho in ignorados:
            continue
        try:
            conteudo = caminho.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        for numero, linha in enumerate(conteudo.splitlines(), start=1):
            for rotulo, padrao in SEGREDOS.items():
                for achado in padrao.findall(linha):
                    achados.append(Achado(caminho.relative_to(raiz), numero, rotulo, _ocultar(str(achado)), True))
            if _tem_dado_sintetico(caminho, raiz):
                continue
            for rotulo, padrao in DADOS_PESSOAIS.items():
                for achado in padrao.findall(linha):
                    achados.append(
                        Achado(
                            caminho.relative_to(raiz),
                            numero,
                            rotulo,
                            _ocultar(str(achado)),
                            _e_log_de_ia(caminho, raiz),
                        )
                    )
    return achados, len(ignorados)


def main() -> int:
    analisador = argparse.ArgumentParser(description="Procura segredo e dado pessoal antes de publicar.")
    analisador.add_argument("--raiz", default=str(RAIZ))
    analisador.add_argument(
        "--tudo-e-grave",
        action="store_true",
        help="Trata dado pessoal fora do ai-logs como bloqueio também.",
    )
    argumentos = analisador.parse_args()

    raiz = Path(argumentos.raiz).resolve()
    achados, quantos_ignorados = varrer(raiz)
    graves = [achado for achado in achados if achado.grave or argumentos.tudo_e_grave]
    avisos = [achado for achado in achados if achado not in graves]

    for achado in avisos:
        print(f"aviso: {achado}", file=sys.stderr)
    for achado in graves:
        print(str(achado), file=sys.stderr)

    if graves:
        print(f"\n{len(graves)} ocorrência(s) bloqueiam a publicação. Limpe antes de subir.", file=sys.stderr)
        return 1
    print(
        f"Nenhum segredo encontrado no que vai ser publicado em {raiz}.\n"
        f"{quantos_ignorados} arquivo(s) fora da varredura por estarem no .gitignore. "
        f"{len(avisos)} aviso(s) de dado pessoal em pasta de amostra."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
