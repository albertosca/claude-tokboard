# Como funciona

Três partes: hooks, uma camada de dados, duas interfaces.

## O desenho

```
Claude Code
  |-- hook Stop (fim de cada turno) ----> sessions.jsonl
  |-- hook SubagentStop ----------------> subagents.jsonl
  |
  v
metrics_core  (uma camada de dados compartilhada)
  |-- /tokboard      dashboard HTML
  '-- /tokboard-tui  app de terminal
```

## 1. Os hooks

Dois hooks acrescentam uma linha a um registro local:

- depois de cada **turno**;
- depois de cada **subagente**.

Eles também registram uma linha por resposta da API e uma por rejeição de rate limit. Isso alimenta o [medidor do limite semanal](weekly-meter.md).

## 2. A camada de dados

`lib/metrics_core/` é Python puro, só biblioteca padrão.

As duas interfaces leem por ela. Então um número significa a mesma coisa em qualquer uma.

## 3. As duas interfaces

- `dashboard_html.py` escreve uma página HTML autossuficiente.
- `dashboard_tui.py` é um app de terminal em Textual.

São consumidores independentes da mesma camada.

## Como é testado

- **100% de cobertura de instruções e de ramos**, imposta. Uma linha descoberta quebra o CI.
- **A página é testada usando-a.** Os testes de renderização dirigem o dashboard gerado de verdade no jsdom: clicando, filtrando, destacando.
- **Checagens estáticas estritas.** `ruff` e `mypy --strict`.
