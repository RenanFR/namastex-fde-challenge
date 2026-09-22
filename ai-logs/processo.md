# Processo: como este agente foi construído com IA

Registro do que aconteceu, na ordem em que aconteceu.

---

## 1. Auditar antes de executar

Antes de rodar qualquer linha do repositório do desafio, varri o código: `grep` por
`eval`, `exec`, `subprocess`, `socket`, `requests`, `base64`, `pickle`; conferi que
`.git/hooks` estava vazio, que não havia submódulo nem GitHub Action, que o `uv.lock`
não apontava para índice fora do PyPI, e que o `conversations.parquet` era Parquet de
verdade (magic `PAR1` nas duas pontas) sem URL, comando ou tentativa de prompt injection
no conteúdo das mensagens.

Nada suspeito. Mas rodar código de terceiro sem olhar é como assinar contrato sem ler, e
um FDE recebe repositório de cliente o tempo todo.

## 2. Medir o dataset antes de desenhar

Em vez de abrir o editor, primeiro perguntei ao dataset o que ele tinha a dizer. Escrevi
`analise/dataset.py` para cruzar as cotações dos vendedores humanos com a tabela de preços
real de `plans.json`.

O resultado mudou o projeto inteiro:

```
precos reais possiveis: 72 | faixa 119.9 a 1025.14
precos que o vendedor humano cita: [129.9, 159.9, 189.9, 219.9, 259.9, 299.9, 349.9, 389.9]
quantos desses existem na tabela real: []
total de cotacoes humanas no dataset: 2500
mensagens que mencionam carencia: 0
mensagens que mencionam pro-rata: 0
```

Zero de 2.500 batem. E 751 conversas (30%) receberam preço apesar de o lead ou o veículo
estarem fora das regras de aceitação.

Conclusão que virou a tese da solução: **o histórico é um anti-exemplo.** Ele não entra
como few-shot, entra como evidência do que precisa ser travado. Essa decisão saiu de uma
medição, não de intuição, e teria sido difícil de tomar lendo as conversas no olho.

## 3. Validar a API real antes de escrever a camada que fala com ela

Subi a `/quote` com `QUOTE_FAILURE_RATE=0` e sondei os cantos: recusa por idade, recusa por
veículo, CEP de alto risco, pró-rata de entrada no meio do mês. Confirmei que o mapeamento
dos DTOs batia com o corpo real antes de construir qualquer coisa em cima.

Foi assim que apareceu o caso extremo de R$ 1.025,14 (Premium, 22 anos, carro de 2015, CEP
de alto risco), que virou massa de teste.

## 4. A trilha de auditoria pegou o meu próprio código mentindo

Escrevi um roteiro de demonstração para rodar o agente sem chave de API. No texto final do
roteiro coloquei "Fica em R$ 241,39 por mes".

Rodei, funcionou, parecia certo. Aí abri a trilha JSONL:

```json
{"evento": "cotacao_emitida", "cotacao_id": "cot_da29a2ee", "premio_mensal": 209.9}
{"evento": "mensagem_do_agente", "texto": "... Fica em R$ 241,39 por mes ..."}
```

A API tinha devolvido R$ 209,90. O meu roteiro inventou R$ 241,39, exatamente o pecado que
a solução inteira existe para impedir.

Duas coisas saíram daí. Primeiro, a rastreabilidade provou o valor dela em vinte segundos de
uso real. Segundo, ficou claro que **o prompt pedir para não inventar preço não é garantia
de nada**: nasceu `guarda_de_preco.py`, que confere todo número com forma de dinheiro contra
a cotação que a API devolveu e barra a mensagem se houver intruso.

Rodando a demonstração contra a API caída, a trava dispara e o evento fica registrado:

```json
{"evento": "preco_bloqueado", "valores_nao_autorizados": [101.56, 209.9, 3000.0],
 "texto_barrado": "Saiu a sua cotacao do plano Completo. Fica em R$ 209,90 por mes..."}
```

## 5. Separar o que o modelo decide do que o código decide

Primeira versão deixava o modelo decidir quando chamar um humano. Refiz: o modelo virou
**sensor**, chamando `encaminhar_para_humano` com um motivo que é enum, e a decisão passou
para `politica_encaminhamento.py`, determinística e testável sem LLM.

Motivo: critério de desistência é o que separa um agente de um chatbot, e precisa ser
auditável e defensável numa reunião, não uma frase de prompt que ninguém consegue provar.

Os motivos de infraestrutura (API fora do ar, recusa por risco, qualificação travada) o
modelo nem enxerga: o fluxo detecta sozinho. Isso também evita que o modelo mascare uma
falha de sistema como se fosse escolha dele.

## 6. O CEP forçou o desenho do cofre de PII

O plano inicial era mascarar todo dado pessoal antes de mandar para o modelo. Aí esbarrei
num conflito: o CEP é dado pessoal **e** insumo de preço, porque o prefixo define um agravo
de 1,30.

Solução: token estável e reversível. O modelo vê `[CEP:1a2b]`, a trilha guarda o token, e o
valor real só é revelado dentro do repositório, na hora de montar o corpo da requisição. Há
teste garantindo que a API recebe `07123-456` enquanto o lead em memória carrega só o token.

Na mesma linha: a API de cotação não usa CPF, e-mail, telefone nem placa, então o agente não
pede nenhum dos quatro, embora o vendedor humano do histórico peça CPF em 100% das conversas.

## 7. A primeira execução com o modelo real achou dois problemas que o dublê escondia

Todo o desenvolvimento rodou num provedor roteirizado, com turnos congelados e custo zero.
Isso testa o fluxo, mas não testa o que um modelo de verdade decide fazer. Na primeira
execução com Opus 5 apareceram duas coisas que o dublê não tinha como mostrar.

**Falso positivo na trava de preço.** O agente montou o cardápio dos três planos com as
franquias de R$ 4.500, R$ 3.000 e R$ 1.500, e a trava barrou a mensagem inteira, porque
ainda não havia cotação aprovada. Mas aqueles números vieram de `/planos`, que também é a
API. A regra estava grosseira demais: ela media "existe cotação?" quando o que importa é
"este número veio da API?". A versão final separa os dois estados, cardápio liberado antes
de cotar e blackout total depois de uma cotação que falhou.

**Promessa que o produto não cumpre.** Na execução contra a API caída, o agente escreveu
"vou tentar de novo em instantes". Ele não faz isso: não roda em background, e naquele
momento a conversa já estava sendo passada para uma pessoa. O retorno da ferramenta passou
a dizer explicitamente que um humano assume, e o prompt ganhou a regra de não prometer
retomada, aviso futuro nem verificação.

Depois das duas correções, a resposta do modelo à queda da API ficou assim:

> Não vou chutar número: preço só vale se sair do sistema. Seus dados já estão registrados
> e um especialista nosso assume daqui pra te passar o valor do Completo com início em 17/10.
>
> Só pra você já ir sabendo: roubo e furto só passam a valer depois da carência de 30 dias.
> E como você começa dia 17/10, o primeiro pagamento é proporcional aos dias restantes do mês.

Ele entregou informação útil sem citar uma cifra sequer, que era exatamente o comportamento
que eu queria e não conseguiria garantir só com prompt.

Custo das execuções reais: US$ 0,12 por conversa completa no Opus 5.

## 8. Testar o README, não só o código

Depois de escrever a documentação, rodei os comandos dela. O `pip install -e ".[dev]"` que eu
tinha documentado quebrou: o `pyproject.toml` não tinha `build-system` nem descoberta de
pacotes, e o setuptools se perdia entre as pastas do repositório.

Corrigido. Documentação que não foi executada é hipótese.

---

## Onde a IA ajudou mais, e onde não ajudou

**Ajudou muito** na varredura inicial de segurança, na análise do dataset (escrever o script
de medição foi mais rápido do que abrir o Parquet à mão), em produzir a suíte de testes com
cobertura larga, e em manter consistência de nomenclatura e de estilo de log ao longo de
doze módulos.

**Não substituiu decisão.** As escolhas que dão forma à solução, tirar o dataset do prompt,
tirar o handoff do modelo, criar a trava de preço, tokenizar o CEP em vez de descartá-lo,
saíram de olhar um número ou um log concreto e decidir. A IA acelerou a execução e produziu
a evidência; a régua de "isto é defensável?" continuou sendo minha.

O momento mais útil da sessão foi quando a ferramenta me mostrou que o **meu próprio** código
de exemplo estava inventando preço. Boa infraestrutura de observabilidade pega o erro de quem
a construiu.

O segundo mais útil foi descobrir que o dublê determinístico, que me deixou desenvolver de
graça, também me escondeu dois defeitos. Ele testa o fluxo, não o julgamento. Rodar contra o
modelo de verdade não é a etapa de validação final, é parte do ciclo, e quanto antes melhor.
