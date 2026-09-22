from __future__ import annotations

from autoseguro.cofre_pii import CofrePii, TipoDadoPessoal

MENSAGEM_DO_LEAD = (
    "Cpf 529.982.247-25, tenho 35 anos, cep 01310-100, "
    "email joao.silva@gmail.com, whats +55 11 98765-4321, placa ABC1D23"
)


def test_mascara_os_cinco_tipos_de_dado_pessoal():
    cofre = CofrePii(semente="fixa")
    mascarado = cofre.mascarar(MENSAGEM_DO_LEAD)
    for tipo in TipoDadoPessoal:
        assert f"[{tipo.value}:" in mascarado
    assert not cofre.contem_dado_pessoal(mascarado)


def test_idade_continua_visivel_porque_e_insumo_de_preco():
    cofre = CofrePii(semente="fixa")
    assert "35 anos" in cofre.mascarar(MENSAGEM_DO_LEAD)


def test_o_mesmo_valor_sempre_vira_o_mesmo_token():
    cofre = CofrePii(semente="fixa")
    primeira = cofre.mascarar("meu cep e 01310-100")
    segunda = cofre.mascarar("confirmando, cep 01310-100")
    assert primeira.split()[-1] == segunda.split()[-1]
    assert cofre.tokens_emitidos == 1


def test_revelar_devolve_o_valor_original_do_token():
    cofre = CofrePii(semente="fixa")
    token = cofre.mascarar("07123-456")
    assert cofre.revelar(token) == "07123-456"


def test_revelar_deixa_passar_valor_que_nunca_foi_tokenizado():
    assert CofrePii(semente="fixa").revelar("01310-100") == "01310-100"


def test_sementes_diferentes_geram_tokens_diferentes_para_o_mesmo_dado():
    um = CofrePii(semente="uma").mascarar("01310-100")
    outro = CofrePii(semente="outra").mascarar("01310-100")
    assert um != outro
