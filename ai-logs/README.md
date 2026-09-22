# Uso de IA neste desafio

## Ferramenta

O desafio inteiro foi construído com **Claude Code** (modelo Opus 5), numa sessão só, em
pair programming: eu decidindo escopo, arquitetura e trade-offs, o modelo levantando dados,
escrevendo código e rodando verificação.

## Por que este log é curado e não o `.jsonl` cru

O `README.md` do desafio pede para copiar as sessões de `~/.claude/projects/<slug>/*.jsonl`.
No meu caso essa pasta tem **530 MB e 61 sessões** do produto que eu mantenho hoje: conversas
de negócio, dados de clientes, caminhos de credencial em cloud e contexto pessoal. O arquivo
desta sessão especificamente tem 2 MB e carrega, além do desafio, o índice de memória do meu
outro projeto.

Publicar isso num repositório público seria vazamento, não transparência. O próprio desafio
aceita a alternativa: "Copie e cole num .md mesmo, serve". Então `processo.md` é o registro
honesto do processo, escrito durante a execução e não reconstruído depois.

Se a Namastex preferir ver o material cru, **topo a sessão de tela compartilhada** que o
desafio oferece: eu abro o transcript na frente de vocês e navego pelos turnos, sem precisar
publicar o arquivo.

## Verificação

`scripts/verificar_segredos.py` varre este repositório inteiro, incluindo esta pasta, à procura
de chave de API, token, chave privada e dado pessoal. Ele sai com código 1 se achar qualquer
coisa. Rodei antes de publicar.
