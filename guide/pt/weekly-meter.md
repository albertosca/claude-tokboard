# O medidor do limite semanal

Estourou o limite semanal e não sabe por quê? Este é o painel para isso.

## O que ele mostra

O medidor mostra o **cache creation** acumulado na janela semanal atual.

Ele mantém as camadas de 5 minutos e de 1 hora separadas.

E compara tudo com uma **faixa**: os valores vistos quando as semanas passadas foram rejeitadas pela primeira vez.

## É uma hipótese

A Anthropic não publica como o limite semanal é contado.

Então o medidor avisa:

- vem rotulado como hipótese;
- mostra quantos **pontos** estão por trás da faixa.

Mais rejeições passadas significam uma faixa mais confiável.

## De onde vêm os dados

Desde a versão 2.5.0, os dois hooks registram:

- uma linha por **resposta da API**, em `calls.jsonl`;
- uma linha por **rejeição de rate limit**, em `limit-hits.jsonl`.

O medidor lê esses dois arquivos.

## Sem dados?

Os arquivos começam vazios depois de uma atualização.

Preencha a partir das transcrições que o Claude Code ainda guarda:

```bash
python3 scripts/backfill-calls.py            # simulação
python3 scripts/backfill-calls.py --apply    # grava
```

A simulação imprime o dia mais antigo que ela alcança.

O Claude Code apaga transcrições antigas, então rode **uma vez, logo depois de atualizar**.

## De onde vem o cache creation

Cada janela semanal divide o cache creation em três causas:

| Causa | Significado |
|---|---|
| **Partida a frio** | Uma sessão começou do zero. |
| **Reescrita após pausa** | O cache expirou numa pausa e foi gravado de novo. |
| **Crescimento incremental** | A conversa simplesmente ficou mais longa. |

O painel também mostra quais sessões mais reescreveram depois de ficar ociosas.

## O que fazer com isso

- Muita reescrita após pausa? Pausas longas custam caro. Termine ou use `/clear` em vez de deixar uma sessão grande aberta.
- Muita partida a frio? Muitas sessões novas, cada uma pagando para montar o seu contexto.
- Quase tudo crescimento incremental? Sessões longas. Compacte mais cedo.
