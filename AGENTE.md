# Agente de cotação AutoSeguro

Agente que atende um lead de seguro de veículo pelo WhatsApp: conversa, qualifica, cota
na API da seguradora e decide sozinho quando precisa de um humano.

Resposta ao desafio FDE da Namastex. O README original do desafio continua em `README.md`.

---

## A tese

**A `/quote` é a única fonte de preço que existe neste sistema.** O modelo conduz a conversa,
mas não calcula, não estima, não arredonda e não repete valor de lugar nenhum. Quando a API
não responde, o agente não inventa e também não morre: ele diz a verdade, preserva o que já
coletou e entrega o lead para um humano com contexto suficiente para continuar.

Isso não é uma instrução de prompt. É uma trava de código, descrita em
[Trava de preço](#3-trava-de-preço-o-prompt-pede-o-código-garante).

---

## Como rodar

### 1. Suba a API de cotação

```bash
docker compose up --build          # http://localhost:8000
```

Sem Docker:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
cd quote-service && ../.venv/bin/uvicorn app.main:app --port 8000
```

### 2. Rode os testes (não precisa de chave nem de API no ar)

```bash
.venv/bin/python -m pytest -q
```

### 3. Veja o agente funcionando sem chave de API e sem custo

O provedor roteirizado troca o modelo por turnos congelados. O resto do sistema é o real:
mesma API, mesmo retry, mesma trava de preço, mesma trilha.

```bash
.venv/bin/python -m autoseguro \
  --roteiro exemplos/roteiro_cotacao_feliz.json \
  --conversa exemplos/lead_cotacao_feliz.txt \
  --trilha execucoes/demo_feliz.jsonl
```

O mesmo roteiro contra uma API que nunca responde, que é o caso que separa:

```bash
QUOTE_FAILURE_RATE=1.0 docker compose up -d --build
.venv/bin/python -m autoseguro \
  --roteiro exemplos/roteiro_cotacao_feliz.json \
  --conversa exemplos/lead_cotacao_feliz.txt \
  --trilha execucoes/demo_api_fora_do_ar.jsonl
```

### 4. Rode com o modelo de verdade

Coloque sua chave num arquivo `.env` na raiz (já está no `.gitignore`):

```
ANTHROPIC_API_KEY=...
```

```bash
set -a && source .env && set +a
.venv/bin/python -m autoseguro --conversa exemplos/lead_cotacao_feliz.txt --verboso
.venv/bin/python -m autoseguro          # conversa interativa no terminal
```

Modelo e esforço saem de variável de ambiente: `AUTOSEGURO_MODELO` (padrão `claude-opus-5`)
e `AUTOSEGURO_ESFORCO` (padrão `medium`).

### 5. Antes de publicar

```bash
.venv/bin/python scripts/verificar_segredos.py
```

Ele varre só o que vai ser publicado: consulta o `git check-ignore` e pula o que está no
`.gitignore`, para não acusar o seu `.env` local e ao mesmo tempo não deixar passar uma chave
num arquivo versionado. Sai com código 1 se achar qualquer coisa, e nunca imprime o valor
encontrado por inteiro.

---

## Decisões, e por quê

### 1. O histórico entrou como evidência, não como exemplo

`analise/dataset.py` mede o dataset. Reproduza com
`.venv/bin/python analise/dataset.py`. O que ele encontra nas 2.500 conversas:

| Medida | Resultado |
|---|---|
| Preços que a tabela real permite | 72 valores, de R$ 119,90 a R$ 1.025,14 |
| Preços distintos que o vendedor humano cita | 8 valores, nenhum deles na tabela |
| Cotações humanas fora da tabela | **2.500 de 2.500** |
| Mensagens que mencionam carência | **0** |
| Mensagens que mencionam pró-rata | **0** |
| Conversas que receberam preço apesar de serem recusáveis pela regra | 751 (30%) |
| Conversas com CPF / CEP | 100% |
| Campos que a API de cotação realmente usa | `plano_id`, `idade`, `veiculo_ano`, `cep`, `data_inicio` |

A leitura é direta: **o histórico é um anti-exemplo.** Usar essas conversas como few-shot
ensinaria o agente a inventar preço com naturalidade, a omitir carência e a cotar perfis que
a seguradora recusa. Por isso o dataset não alimenta o prompt. Ele alimentou as decisões
abaixo, e cada guardrail existe por causa de uma linha daquela tabela.

### 2. Quem decide passar para o humano é código, não o modelo

`politica_encaminhamento.py` roda a cada turno, com entrada tipada e sem LLM no meio. São
seis motivos, num enum:

| Motivo | Quem detecta | Quando |
|---|---|---|
| `cotacao_indisponivel` | o fluxo | a API esgotou as tentativas ou o circuito está aberto |
| `risco_recusado` | o fluxo | a API devolveu 422, o perfil está fora das regras de aceitação |
| `qualificacao_travada` | o fluxo | 4 turnos sem coletar um dado novo e sem cotar |
| `pedido_explicito` | o modelo | o lead pediu uma pessoa |
| `negociacao_de_preco` | o modelo | o lead quer desconto, e desconto não é mandato do agente |
| `fora_de_escopo` | o modelo | o assunto não é seguro de veículo |

O modelo participa só como **sensor**: ele chama `encaminhar_para_humano` com um motivo que é
enum, nunca texto livre. Quem decide o que fazer com esse sinal é a política. Isso segue a
mesma linha de não classificar intenção comparando a mensagem com uma lista de palavras: a
decisão vem da estrutura (um tipo na borda), não do vocabulário. O sistema é monolíngue hoje,
mas a lista de palavras quebraria no dia em que não fosse.

Todo encaminhamento produz um resumo estruturado para o vendedor humano: o que já foi
coletado, o que falta, e o que aconteceu com a cotação. Sem isso o handoff é só um abandono
com outro nome.

### 3. Trava de preço: o prompt pede, o código garante

O prompt manda o modelo nunca inventar preço. Prompt é pedido, não garantia.

`guarda_de_preco.py` intercepta **toda** mensagem antes de ela chegar ao lead, extrai todo
número com forma de dinheiro e exige que cada um esteja na cotação que a API devolveu. Se não
estiver, a mensagem não sai: é substituída por um texto honesto e o incidente vai para a
trilha com o texto barrado inteiro, para auditoria.

Isso não é hipotético. Durante o desenvolvimento o roteiro de demonstração que eu mesmo
escrevi dizia R$ 241,39 quando a API tinha devolvido R$ 209,90. Quem pegou foi a trilha, e
foi por causa disso que a trava existe. Rodando a demonstração contra a API quebrada, dá para
ver o evento `preco_bloqueado` guardando a frase inventada que nunca chegou ao lead.

A detecção é por forma (`R$ 209,90`, `209,90`, `3.000`), não por vocabulário. Ano de veículo,
prazo de carência e contagem de dias não são confundidos com dinheiro, e isso está coberto em
teste.

A primeira execução com o modelo real refinou a regra. O agente montou o cardápio de planos
com as três franquias (R$ 4.500, R$ 3.000, R$ 1.500) e a trava barrou, porque ainda não havia
cotação. Falso positivo: aqueles números vieram de `/planos`, que também é a API. A regra final
tem dois estados:

- **Antes de cotar:** valem os números da tabela de planos. Preço de tabela e franquia são
  material de venda legítimo e o lead precisa deles para escolher.
- **Depois de uma tentativa de cotação que falhou ou foi recusada:** blackout total, nem o
  preço de tabela passa. Naquele ponto da conversa qualquer número é lido pelo lead como
  resposta ao preço dele.

O valor inventado continua barrado nos dois estados.

### 4. Instabilidade tratada em uma camada só

`cliente_http_resiliente.py` é o análogo do cliente HTTP autenticado que eu uso no meu app:
toda chamada externa passa por ele, e nem o agente nem a regra de negócio sabem que a API é
instável.

- **Timeout de 3 segundos.** A lentidão simulada é de 8 segundos. Esperar os 8 e depois
  responder é pior para o lead do que cortar em 3 e tentar de novo, porque uma nova tentativa
  costuma responder na hora.
- **3 tentativas com backoff exponencial e jitter.** Com 20% de falha e 10% de lentidão, a
  chance das três tentativas falharem fica em torno de 3%. O jitter existe para não sincronizar
  várias conversas na mesma janela de retentativa.
- **Retry só no que é transitório.** 5xx, 429, timeout e erro de conexão são retentados. 422 e
  400 não: uma recusa por idade é uma resposta correta da API, e insistir nela só gasta tempo
  do lead. Essa distinção é o que separa `cotacao_indisponivel` de `risco_recusado`, que levam
  o lead a lugares diferentes.
- **Circuit breaker.** Depois de 4 falhas seguidas, o cliente para de chamar por 20 segundos e
  falha rápido. Quando a API está fora do ar, fazer o lead esperar três timeouts para ouvir a
  mesma desculpa é desperdício dos dois lados.

### 5. Dado pessoal não chega ao modelo nem ao log

`cofre_pii.py` troca CPF, CEP, e-mail, telefone e placa por token estável (`[CEP:1a2b]`) antes
de a mensagem sair para o modelo ou para a trilha. O valor real fica em memória e só é revelado
no último momento, dentro do repositório, quando o corpo da requisição é montado.

O detalhe que exigiu esse desenho: **o CEP é dado pessoal e insumo de preço ao mesmo tempo.**
O prefixo determina um agravo de 1,30. Mascarar e jogar fora quebraria a cotação; mandar cru
para o modelo é expor sem necessidade. O token resolve os dois: o modelo trabalha com
`[CEP:1a2b]`, a API recebe `07123-456`, e a trilha guarda só o token. Há teste garantindo que
o CEP real chega na API mesmo o lead carregando apenas o token.

A idade continua visível de propósito. Ela é insumo direto de preço e de aceitação, o modelo
precisa raciocinar sobre ela, e sozinha não identifica ninguém.

**O agente não pede CPF, nome completo, e-mail, telefone nem placa.** A API de cotação não usa
nenhum desses campos. O vendedor humano do histórico pedia CPF em 100% das conversas sem
precisar. Coletar dado que não entra na decisão é risco sem contrapartida.

### 6. Rastreabilidade em dois canais

- **Trilha estruturada** (`execucoes/*.jsonl`): uma linha por evento, com `conversa_id`,
  `turno`, `cotacao_id`, status, número da tentativa e duração. Serve para reconstruir a
  conversa inteira e para alimentar métrica.
- **Log humano**: frases em português, para quem está lendo a saída às três da manhã
  entender o que quebrou sem decorar nome de variável.

Cada mensagem, cada chamada de ferramenta, cada tentativa HTTP e cada decisão tem id e status.

### 7. Escolha de modelo

`claude-opus-5` por padrão, com esforço `medium`. O esforço não fica no máximo porque isto é
uma conversa de vendas: o lead está do outro lado esperando, e latência alta custa mais do que
a última fração de qualidade num fluxo com ferramentas bem tipadas. Os dois são configuráveis.

O loop de tool use é manual, não o tool runner do SDK. Motivo: a política precisa ser avaliada
entre turnos, a trilha precisa registrar cada chamada e o cofre precisa destokenizar na borda.
Além disso o tool runner é beta, e um repositório que outra pessoa vai rodar não precisa dessa
dependência.

---

## Arquitetura

As camadas espelham o app que eu mantenho hoje, um Flutter com MVVM, porque a separação
resolve o mesmo problema: manter a regra de negócio longe da tela e da rede.

```
cli.py                        a tela: só lê estado e renderiza
  └─ conversa_view_model.py   orquestra o turno, guarda estado, notifica ouvintes
       ├─ provedores.py       protocolo do LLM (anthropic | roteirizado)
       ├─ ferramentas.py      o que o modelo pode fazer, com schema tipado
       ├─ guarda_de_preco.py  nenhum valor sai sem ter vindo da API
       ├─ politica_...py      quando chamar um humano
       └─ cotacao_repository.py   traduz HTTP em resultado tipado
            └─ cotacao_api_service.py   fala /quote e /planos
                 └─ cliente_http_resiliente.py   timeout, retry, circuit breaker
cofre_pii.py                  atravessa tudo: token entra, valor real só na borda
trilha.py                     JSONL de auditoria
feature_flags.py              desliga agente ou encaminhamento sem deploy
```

O estado da conversa é um enum (`qualificando`, `cotando`, `cotacao_apresentada`,
`encaminhada`, `encerrada`), não string solta. O mesmo vale para plano, status de cotação e
motivo de encaminhamento.

---

## Testes

52 testes, todos sem rede e sem chave de API. O HTTP é substituído por `httpx.MockTransport` e
o modelo pelo provedor roteirizado, então o que está sendo testado é o sistema de verdade, não
um mock do próprio sistema.

```
tests/test_cofre_pii.py                  mascaramento, estabilidade do token, reversão
tests/test_cliente_http_resiliente.py    retry, backoff, o que não se retenta, circuit breaker
tests/test_cotacao_repository.py         tradução de 200/422/503, CEP revelado na borda
tests/test_politica_encaminhamento.py    cada motivo, prioridade entre eles, resumo do handoff
tests/test_guarda_de_preco.py            preço fiel passa, inventado não, ano e prazo não confundem
tests/test_conversa.py                   conversa ponta a ponta, PII fora da trilha, handoff
```

---

## Limitações conhecidas

- **O provedor roteirizado é um dublê, não um agente.** Ele devolve turnos congelados. Serve
  para testar o fluxo em volta do modelo com custo zero, não para avaliar qualidade de conversa.
- **Estado em memória.** Uma conversa vive num processo. Para produção o `ConversaViewModel`
  precisaria ser reidratado de um armazenamento por `conversa_id`, o que muda a montagem mas
  não as camadas.
- **Um lead por vez.** Não há fila, webhook nem concorrência. O desafio pede um agente, e
  acrescentar um broker aqui seria enfeite.
- **O cofre de PII vive no processo.** Em produção o mapa token para valor precisaria de um
  armazenamento com expiração.
- **A detecção de dado pessoal é por formato.** Um CPF escrito por extenso passa. Para um
  sistema real isso pediria uma segunda camada.

---

## Log de execução

`execucoes/` guarda as trilhas das execuções gravadas:

| Arquivo | Modelo | O que mostra |
|---|---|---|
| `opus5_conversa_completa.jsonl` | Opus 5 | conversa completa até a cotação sair. 20.467 tokens de entrada, 656 de saída, US$ 0,12 |
| `opus5_api_fora_do_ar.jsonl` | Opus 5 | a mesma conversa com a API caída: 3 tentativas, nenhum valor dito, handoff com contexto |
| `demo_feliz.jsonl` | roteirizado | o mesmo caminho feliz sem chave de API |
| `demo_api_fora_do_ar.jsonl` | roteirizado | a queda da API com a trava de preço disparando (evento `preco_bloqueado`) |

Na execução com a API fora do ar, o modelo real escreveu:

> Não vou chutar número: preço só vale se sair do sistema. Seus dados já estão registrados e
> um especialista nosso assume daqui.

E ainda entregou valor sem citar cifra nenhuma, explicando a carência de 30 dias e que o
primeiro pagamento seria proporcional. O resumo que foi para o humano tem os cinco campos
coletados, o CEP como token, e o motivo técnico da falha.

Uma correção saiu daí: na primeira gravação o agente prometeu "vou tentar de novo em
instantes", coisa que ele não faz, porque não roda em background e a conversa já estava indo
para uma pessoa. O retorno da ferramenta passou a dizer isso explicitamente, e o prompt ganhou
a regra de não prometer o que o produto não faz.

---

## Uso de IA

As conversas com IA durante o desafio estão em `ai-logs/`, conforme pedido no `README.md`.
Rode `scripts/verificar_segredos.py` antes de qualquer publicação: transcript de ferramenta de
IA é onde chave e dado pessoal vazam sem ninguém notar.
