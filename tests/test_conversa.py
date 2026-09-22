from __future__ import annotations

import datetime as dt

import httpx

from autoseguro.cliente_http_resiliente import PoliticaResiliencia
from autoseguro.cofre_pii import CofrePii
from autoseguro.dtos import EstadoConversa, MotivoEncaminhamento, PlanoId
from autoseguro.feature_flags import FeatureFlags
from autoseguro.guarda_de_preco import valores_citados
from autoseguro.montagem import montar_conversa
from autoseguro.provedores import ChamadaFerramenta, ProvedorRoteirizado, RespostaModelo
from autoseguro.trilha import Evento
from tests.conftest import cotacao_valida

POLITICA_RAPIDA = PoliticaResiliencia(tentativas_maximas=3, espera_inicial_segundos=0.0, jitter_maximo_segundos=0.0)

MENSAGEM_DO_LEAD = "tenho 34 anos, Onix 2021, cep 01310-100, meu cpf e 529.982.247-25"


def chamada(nome: str, **argumentos) -> ChamadaFerramenta:
    return ChamadaFerramenta(identificador=f"toolu_{nome}", nome=nome, argumentos=argumentos)


def roteiro_ate_a_cotacao(cep: str) -> list[RespostaModelo]:
    return [
        RespostaModelo(
            texto="",
            chamadas=(
                chamada(
                    "registrar_dados_do_lead",
                    idade=34,
                    veiculo_ano=2021,
                    cep=cep,
                    plano_id="completo",
                    data_inicio="2026-10-17",
                ),
            ),
        ),
        RespostaModelo(texto="", chamadas=(chamada("cotar_plano"),)),
        RespostaModelo(
            texto="Fica em R$ 209,90 por mes. Roubo e furto valem apos 30 dias. "
            "O primeiro pagamento e de R$ 101,56 pelos 15 dias restantes."
        ),
    ]


def montar(provedor, respostas, api_falsa, flags=None):
    falsa, transporte = api_falsa(respostas)
    conversa = montar_conversa(
        base_url="http://api.local",
        provedor=provedor,
        politica_http=POLITICA_RAPIDA,
        hoje=dt.date(2026, 10, 1),
        semente_pii="fixa",
        transporte=transporte,
        flags=flags or FeatureFlags(),
    )
    conversa.iniciar()
    return falsa, conversa


def test_conversa_feliz_cota_e_registra_tudo_na_trilha(api_falsa):
    token_cep = CofrePii(semente="fixa").mascarar("01310-100")
    provedor = ProvedorRoteirizado(roteiro=roteiro_ate_a_cotacao(token_cep))
    falsa, conversa = montar(provedor, [httpx.Response(200, json=cotacao_valida())], api_falsa)

    resposta = conversa.responder(MENSAGEM_DO_LEAD)

    assert conversa.estado is EstadoConversa.COTACAO_APRESENTADA
    assert conversa.encaminhamento is None
    assert "209,90" in resposta
    assert conversa.lead.plano_id is PlanoId.COMPLETO
    assert falsa.corpos_enviados[-1]["cep"] == "01310-100"

    emitidas = conversa.trilha.eventos_do_tipo(Evento.COTACAO_EMITIDA)
    assert len(emitidas) == 1
    assert emitidas[0]["premio_mensal"] == 209.90
    assert emitidas[0]["cotacao_id"].startswith("cot_")


def test_o_dado_pessoal_do_lead_nunca_aparece_na_trilha(api_falsa):
    token_cep = CofrePii(semente="fixa").mascarar("01310-100")
    provedor = ProvedorRoteirizado(roteiro=roteiro_ate_a_cotacao(token_cep))
    _, conversa = montar(provedor, [httpx.Response(200, json=cotacao_valida())], api_falsa)
    conversa.responder(MENSAGEM_DO_LEAD)

    trilha_inteira = " ".join(str(linha) for linha in conversa.trilha.linhas)
    assert "529.982.247-25" not in trilha_inteira
    assert "01310-100" not in trilha_inteira
    assert "[CPF:" in trilha_inteira


def test_o_modelo_tambem_nao_ve_o_dado_pessoal(api_falsa):
    token_cep = CofrePii(semente="fixa").mascarar("01310-100")
    provedor = ProvedorRoteirizado(roteiro=roteiro_ate_a_cotacao(token_cep))
    _, conversa = montar(provedor, [httpx.Response(200, json=cotacao_valida())], api_falsa)
    conversa.responder(MENSAGEM_DO_LEAD)

    assert "529.982.247-25" not in " ".join(provedor.recebidas)
    assert "[CPF:" in " ".join(provedor.recebidas)


def test_api_fora_do_ar_encaminha_e_nenhum_preco_chega_ao_lead(api_falsa):
    token_cep = CofrePii(semente="fixa").mascarar("01310-100")
    provedor = ProvedorRoteirizado(roteiro=roteiro_ate_a_cotacao(token_cep))
    respostas = [httpx.Response(503, json={"error": "upstream_unavailable"}) for _ in range(3)]
    falsa, conversa = montar(provedor, respostas, api_falsa)

    resposta = conversa.responder(MENSAGEM_DO_LEAD)

    assert conversa.estado is EstadoConversa.ENCAMINHADA
    assert conversa.encaminhamento.motivo is MotivoEncaminhamento.COTACAO_INDISPONIVEL
    assert valores_citados(resposta) == set()
    assert falsa.chamadas_ao_quote == 3
    assert len(conversa.trilha.eventos_do_tipo(Evento.PRECO_BLOQUEADO)) == 1


def test_recusa_por_risco_encaminha_com_o_motivo_da_seguradora(api_falsa):
    token_cep = CofrePii(semente="fixa").mascarar("01310-100")
    provedor = ProvedorRoteirizado(
        roteiro=[
            RespostaModelo(
                texto="",
                chamadas=(
                    chamada(
                        "registrar_dados_do_lead",
                        idade=80,
                        veiculo_ano=2021,
                        cep=token_cep,
                        plano_id="completo",
                        data_inicio="2026-10-17",
                    ),
                ),
            ),
            RespostaModelo(texto="", chamadas=(chamada("cotar_plano"),)),
            RespostaModelo(texto="Infelizmente esse perfil nao e aceito."),
        ]
    )
    respostas = [httpx.Response(422, json={"error": "cotacao_recusada", "motivo": "Idade acima do limite (75)."})]
    falsa, conversa = montar(provedor, respostas, api_falsa)

    conversa.responder("tenho 80 anos")

    assert conversa.encaminhamento.motivo is MotivoEncaminhamento.RISCO_RECUSADO
    assert "Idade acima do limite" in conversa.encaminhamento.resumo_para_humano
    assert falsa.chamadas_ao_quote == 1


def test_pedido_de_desconto_vai_para_o_time_comercial(api_falsa):
    provedor = ProvedorRoteirizado(
        roteiro=[
            RespostaModelo(
                texto="",
                chamadas=(
                    chamada("encaminhar_para_humano", motivo="negociacao_de_preco", observacao="Quer 20% off."),
                ),
            ),
            RespostaModelo(texto="Vou chamar alguem do comercial."),
        ]
    )
    _, conversa = montar(provedor, [httpx.Response(200, json=cotacao_valida())], api_falsa)

    conversa.responder("da um desconto ai?")

    assert conversa.encaminhamento.motivo is MotivoEncaminhamento.NEGOCIACAO_DE_PRECO
    assert conversa.estado is EstadoConversa.ENCAMINHADA


def test_conversa_travada_encaminha_depois_de_quatro_turnos_sem_avancar(api_falsa):
    provedor = ProvedorRoteirizado(roteiro=[RespostaModelo(texto="Nao entendi, pode repetir?") for _ in range(8)])
    _, conversa = montar(provedor, [httpx.Response(200, json=cotacao_valida())], api_falsa)

    for _ in range(3):
        conversa.responder("oi")
        assert conversa.encaminhamento is None
    conversa.responder("oi")

    assert conversa.encaminhamento.motivo is MotivoEncaminhamento.QUALIFICACAO_TRAVADA


def test_agente_desligado_pela_flag_nao_chama_o_modelo(api_falsa):
    provedor = ProvedorRoteirizado(roteiro=[RespostaModelo(texto="nao deveria chegar aqui")])
    _, conversa = montar(
        provedor, [httpx.Response(200, json=cotacao_valida())], api_falsa, flags=FeatureFlags(agente_habilitado=False)
    )

    resposta = conversa.responder("oi")

    assert provedor.recebidas == []
    assert conversa.estado is EstadoConversa.ENCAMINHADA
    assert "consultor" in resposta


def test_depois_de_descartada_a_conversa_para_de_notificar(api_falsa):
    provedor = ProvedorRoteirizado(roteiro=[RespostaModelo(texto="oi")])
    _, conversa = montar(provedor, [httpx.Response(200, json=cotacao_valida())], api_falsa)
    avisos: list[str] = []
    conversa.adicionar_ouvinte(lambda modelo: avisos.append(modelo.estado.value))

    conversa.responder("oi")
    quantos_antes = len(avisos)
    conversa.descartar()
    conversa.notificar_ouvintes()

    assert len(avisos) == quantos_antes
