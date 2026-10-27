# Seus dados

Resumo: eles ficam na sua máquina.

## Onde ficam

Tudo está em `~/.claude/metrics/`:

- o registro de sessões (`sessions.jsonl`);
- o registro de subagentes (`subagents.jsonl`);
- o registro por chamada (`calls.jsonl`) e as rejeições (`limit-hits.jsonl`);
- os caches de rótulos;
- o dashboard gerado.

## O que sai da sua máquina

Nada.

Há uma exceção: os **rótulos de tarefa com IA**. Se você ligar, cada sessão rotulada é enviada ao Claude para ser resumida. Eles vêm desligados.

## Desinstalando

Desinstalar para o registro novo.

O que já existe continua lá. Apague `~/.claude/metrics/` você mesmo se quiser tirar.

## Scripts de reparo

Registros antigos podem ser reparados por scripts em `scripts/`.

Eles são cuidadosos:

- são **simulações** a menos que você passe `--apply`;
- fazem **backup** do arquivo antes;
- **verificam a cópia** por sha256 antes de gravar;
- o de modelo primeiro confere a reconstrução contra registros que o hook gravou nativamente e **se recusa a rodar** se divergirem.

## Por que os registros têm duplicatas

O Claude Code repete cada resposta da API uma vez por bloco de conteúdo. Uma sessão retomada também reproduz mensagens antigas num arquivo novo.

Então os registros crus têm duplicatas por construção. O tokboard lê tudo por uma função que conta cada mensagem uma vez só.
