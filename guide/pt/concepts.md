# Tipos de token e a linha de cota

Nem todo token custa igual. Esta página explica os quatro tipos que você vai ver.

## Os quatro tipos de token

| Tipo | O que é |
|---|---|
| **input** | O texto que você envia e que não vem do cache. |
| **output** | O texto que o Claude escreve de volta. |
| **cache read** | Contexto que o Claude reaproveitou do cache. Barato. |
| **cache creation** | Contexto gravado no cache. |

Os nomes ficam em inglês nos dois idiomas. São os termos que a API da Anthropic usa.

## Por que o total cru engana

Normalmente a leitura de cache é o maior número, de longe.

Ela também é a mais barata. Somá-la ao resto esconde o que importa.

## A linha de cota

Uma linha à parte estima quanto do seu rate limit o período consumiu.

Ela pesa os tipos assim:

- **input** e **cache creation** contam de um lado;
- **output** conta do outro;
- **cache read** fica de fora.

É o peso que a Anthropic documenta para a API dela.

## É uma aproximação

O Claude Code não publica a própria fórmula.

Por isso a linha vem marcada como **aproximada**. Use para comparar dias e projetos, não como um medidor exato.

## As camadas de 5 minutos e de 1 hora

O cache creation vem em duas camadas, uma de 5 minutos e outra de 1 hora.

O [medidor do limite semanal](weekly-meter.md) mantém as duas separadas.

## A "última barra"

O período mais recente ainda está em andamento.

O navegador mostra quanto dele já passou, hachurado. Uma semana pela metade nunca parece uma queda.

O app de terminal diz o mesmo numa linha sob o gráfico: por exemplo, "última barra: 37% da semana até agora".
