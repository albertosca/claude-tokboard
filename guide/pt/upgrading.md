# Atualização

## Passo 1 - atualize

```bash
claude plugin marketplace update tokboard
claude plugin update tokboard@tokboard
```

## Passo 2 - reinicie o Claude Code

A versão nova carrega na reinicialização.

## O que mudou, versão por versão

### 2.6.0

Nada é obrigatório. O medidor do limite semanal e os gráficos ganham alguns detalhes, e os hooks gravam os mesmos arquivos de antes.

Opcional: dá para aumentar o output de uma linha de subagente pela única chamada final que a sessão pai ainda registra:

```bash
python3 scripts/backfill-subagent-last-call.py --apply
```

É uma simulação até receber `--apply`. As chamadas anteriores sem uso final continuam sendo um piso.

### 2.5.0

Os dois hooks agora gravam dois arquivos novos:

- `calls.jsonl`: uma linha por resposta da API;
- `limit-hits.jsonl`: uma linha por rejeição de rate limit.

Eles alimentam o [medidor do limite semanal](weekly-meter.md). Começam vazios.

Preencha uma vez:

```bash
python3 scripts/backfill-calls.py --apply
```

Rode primeiro a simulação, sem `--apply`. O Claude Code apaga transcrições antigas, então faça isso logo depois de atualizar.

### 2.3.0

Um total com "≥" na frente é um **piso**: algumas mensagens nunca receberam a entrada final de uso.

Para acrescentar essa marca às linhas antigas:

```bash
python3 scripts/backfill-token-dedup.py --fields incomplete_usage
```

Uma linha cuja transcrição sumiu nunca recebe a marca, em definitivo.

### 2.2.0

A contagem de ferramentas passou a ser por chamada distinta. Fica cerca de **4x maior** que antes para o mesmo trabalho. As linhas antigas mantêm as contagens com que foram gravadas.

O output de subagente gravado no Claude Code 2.1.278 ou mais novo é um limite inferior.

### 2.1.0

Os rótulos de tarefa com IA passaram a ficar em cache por idioma.

O primeiro salvamento converte o arquivo e guarda o original ao lado, como `task_labels.json.pre-i18n` ou `agent_labels.json.pre-i18n`.

Para voltar a uma versão anterior à 2.1.0, copie esses arquivos de volta por cima dos novos.
