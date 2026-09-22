"""Lê o histórico de conversas e mede o que ele diz sobre como o agente deve se comportar.

O dataset não entra na solução como exemplo a imitar. Ele entra como evidência: os
números abaixo justificam cada guardrail do agente. Rode com:

    PYTHONPATH=. .venv/bin/python analise/dataset.py
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import itertools
import json
import re
from pathlib import Path

import pyarrow.parquet as pq

RAIZ = Path(__file__).resolve().parent.parent
PADRAO_COTACAO_HUMANA = re.compile(r"plano (\w+) por R\$ ([\d.,]+)/mes")
PADROES_DE_DADO_PESSOAL = {
    "CPF": re.compile(r"\d{3}\.\d{3}\.\d{3}-\d{2}"),
    "CEP": re.compile(r"\b\d{5}-\d{3}\b"),
    "e-mail": re.compile(r"[\w.]+@[\w.]+"),
    "telefone": re.compile(r"\+55 \d{2} 9\d{4}-\d{4}"),
    "placa": re.compile(r"\b[A-Z]{3}\d[A-Z]\d{2}\b"),
}
CAMPOS_QUE_A_API_PEDE = ("plano_id", "idade", "veiculo_ano", "cep", "data_inicio")


def carregar_conversas(caminho: Path) -> dict[str, list[dict]]:
    tabela = pq.read_table(caminho).to_pydict()
    conversas: dict[str, list[dict]] = collections.defaultdict(list)
    for indice in range(len(tabela["conversation_id"])):
        conversas[tabela["conversation_id"][indice]].append({coluna: tabela[coluna][indice] for coluna in tabela})
    for mensagens in conversas.values():
        mensagens.sort(key=lambda mensagem: mensagem["message_index"])
    return dict(conversas)


def precos_possiveis(planos: dict) -> set[float]:
    bases = [plano["base_mensal"] for plano in planos["planos"]]
    regras = planos["regras"]
    por_idade = [faixa["multiplicador"] for faixa in regras["faixa_etaria"] if not faixa.get("recusar")]
    por_veiculo = [faixa["multiplicador"] for faixa in regras["idade_veiculo"] if not faixa.get("recusar")]
    por_regiao = [1.0, regras["regiao_cep"]["multiplicador"]]
    return {
        round(base * idade * veiculo * regiao, 2)
        for base, idade, veiculo, regiao in itertools.product(bases, por_idade, por_veiculo, por_regiao)
    }


def limite_de_idade(planos: dict) -> int:
    recusa = next(faixa for faixa in planos["regras"]["faixa_etaria"] if faixa.get("recusar"))
    return recusa["idade_min"]


def limite_de_anos_do_veiculo(planos: dict) -> int:
    recusa = next(faixa for faixa in planos["regras"]["idade_veiculo"] if faixa.get("recusar"))
    return recusa["anos_min"]


def analisar(conversas: dict[str, list[dict]], planos: dict, ano_de_referencia: int) -> dict:
    autorizados = precos_possiveis(planos)
    idade_de_recusa = limite_de_idade(planos)
    anos_de_recusa = limite_de_anos_do_veiculo(planos)

    cotacoes_humanas: collections.Counter[float] = collections.Counter()
    fora_da_tabela = 0
    mencionam_carencia = 0
    mencionam_pro_rata = 0
    recusaveis_por_idade = 0
    recusaveis_por_veiculo = 0
    cotadas_mesmo_recusaveis = 0
    com_dado_pessoal = collections.Counter()
    desfechos = collections.Counter()

    for mensagens in conversas.values():
        desfechos[mensagens[0]["conversation_outcome"]] += 1
        idade = mensagens[0]["lead_idade_informada"]
        ano_do_veiculo = int(mensagens[0]["veiculo_texto"].split()[-1])
        idade_recusavel = idade >= idade_de_recusa
        veiculo_recusavel = (ano_de_referencia - ano_do_veiculo) >= anos_de_recusa
        recusaveis_por_idade += idade_recusavel
        recusaveis_por_veiculo += veiculo_recusavel

        tipos_vistos = set()
        cotou = False
        for mensagem in mensagens:
            corpo = mensagem["message_body"]
            achado = PADRAO_COTACAO_HUMANA.search(corpo)
            if achado:
                cotou = True
                preco = float(achado.group(2).replace(",", "."))
                cotacoes_humanas[preco] += 1
                fora_da_tabela += preco not in autorizados
            minusculo = corpo.lower()
            mencionam_carencia += "carenc" in minusculo or "carênc" in minusculo
            mencionam_pro_rata += "pro-rata" in minusculo or "proporcional" in minusculo
            for rotulo, padrao in PADROES_DE_DADO_PESSOAL.items():
                if padrao.search(corpo):
                    tipos_vistos.add(rotulo)
        if cotou and (idade_recusavel or veiculo_recusavel):
            cotadas_mesmo_recusaveis += 1
        for rotulo in tipos_vistos:
            com_dado_pessoal[rotulo] += 1

    return {
        "conversas": len(conversas),
        "mensagens": sum(len(mensagens) for mensagens in conversas.values()),
        "desfechos": dict(desfechos),
        "precos_reais_possiveis": len(autorizados),
        "faixa_de_preco_real": (min(autorizados), max(autorizados)),
        "cotacoes_humanas": sum(cotacoes_humanas.values()),
        "precos_distintos_citados": sorted(cotacoes_humanas),
        "cotacoes_fora_da_tabela": fora_da_tabela,
        "mensagens_que_citam_carencia": mencionam_carencia,
        "mensagens_que_citam_pro_rata": mencionam_pro_rata,
        "recusaveis_por_idade": recusaveis_por_idade,
        "recusaveis_por_veiculo": recusaveis_por_veiculo,
        "cotadas_mesmo_sendo_recusaveis": cotadas_mesmo_recusaveis,
        "conversas_com_dado_pessoal": dict(com_dado_pessoal),
        "campos_que_a_api_pede": list(CAMPOS_QUE_A_API_PEDE),
    }


def imprimir(medidas: dict) -> None:
    conversas = medidas["conversas"]
    def proporcao(parte: int) -> str:
        return f"{parte} de {conversas} ({parte / conversas:.0%})"

    print(f"Histórico: {conversas} conversas, {medidas['mensagens']} mensagens.")
    print(f"Desfechos: {medidas['desfechos']}.\n")

    menor, maior = medidas["faixa_de_preco_real"]
    print("1. O preço que o vendedor humano cita não existe na tabela")
    print(f"   A tabela real permite {medidas['precos_reais_possiveis']} preços, de R$ {menor:.2f} a R$ {maior:.2f}.")
    print(f"   Os humanos citam {len(medidas['precos_distintos_citados'])} valores: {medidas['precos_distintos_citados']}.")
    print(f"   Cotações humanas fora da tabela: {medidas['cotacoes_fora_da_tabela']} de {medidas['cotacoes_humanas']}.")
    print("   Consequência: usar o histórico como few-shot ensina o agente a inventar preço.\n")

    print("2. Ninguém menciona as regras que mudam o que o cliente paga e recebe")
    print(f"   Mensagens que citam carência: {medidas['mensagens_que_citam_carencia']}.")
    print(f"   Mensagens que citam pró-rata: {medidas['mensagens_que_citam_pro_rata']}.")
    print("   Consequência: o agente precisa dizer as duas coisas, porque o histórico não ensina.\n")

    print("3. Boa parte do histórico cota perfis que a regra recusa")
    print(f"   Lead acima do limite de idade: {proporcao(medidas['recusaveis_por_idade'])}.")
    print(f"   Veículo acima do limite de anos: {proporcao(medidas['recusaveis_por_veiculo'])}.")
    print(f"   Conversas que receberam preço mesmo sendo recusáveis: {proporcao(medidas['cotadas_mesmo_sendo_recusaveis'])}.")
    print("   Consequência: quem decide aceitação é a API, nunca o agente.\n")

    print("4. O histórico coleta muito mais dado pessoal do que a cotação usa")
    for rotulo, quantas in sorted(medidas["conversas_com_dado_pessoal"].items(), key=lambda par: -par[1]):
        print(f"   {rotulo}: {proporcao(quantas)}.")
    print(f"   A API de cotação pede apenas: {', '.join(medidas['campos_que_a_api_pede'])}.")
    print("   Consequência: o agente não pede CPF, e-mail, telefone nem placa. Nada disso cota.")


def main() -> int:
    analisador = argparse.ArgumentParser(description="Mede o histórico de conversas do desafio.")
    analisador.add_argument("--dataset", default=str(RAIZ / "dataset" / "conversations.parquet"))
    analisador.add_argument("--planos", default=str(RAIZ / "quote-service" / "data" / "plans.json"))
    analisador.add_argument("--json", help="Grava as medidas cruas neste arquivo.")
    argumentos = analisador.parse_args()

    planos = json.loads(Path(argumentos.planos).read_text(encoding="utf-8"))
    medidas = analisar(carregar_conversas(Path(argumentos.dataset)), planos, dt.date.today().year)
    imprimir(medidas)
    if argumentos.json:
        Path(argumentos.json).write_text(json.dumps(medidas, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nMedidas cruas em {argumentos.json}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
