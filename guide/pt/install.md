# Instalação

Dois comandos e pronto.

## Passo 1 - instale o plugin

No terminal:

```bash
claude plugin marketplace add albertosca/claude-tokboard
claude plugin install tokboard@tokboard
```

Prefere clicar? Dentro do Claude Code, `/plugin` faz o mesmo de forma interativa.

## Passo 2 - reinicie o Claude Code

O registro começa sozinho. Não há nada para configurar.

## Passo 3 - veja os seus dados

Quando quiser olhar, use um destes dois comandos:

| Comando | O que faz |
|---|---|
| `/tokboard` | Gera o dashboard HTML e abre no navegador. |
| `/tokboard-tui` | Abre o app de terminal. |

## Requisitos

- **`/tokboard`**: só a biblioteca padrão do Python. Nada para instalar.
- **`/tokboard-tui`**: `python3` 3.10 ou mais novo, com o módulo `venv`.

## Sobre a primeira execução do `/tokboard-tui`

A primeira execução cria um ambiente virtual na pasta do próprio plugin (`.venv`).

Ela instala `textual` e `textual-plotext` nele, a partir do PyPI.

Toda execução imprime o comando para colar no seu terminal. Ele fica assim, com a pasta real do plugin no lugar:

```bash
<pasta do plugin>/.venv/bin/python3 <pasta do plugin>/dashboard_tui.py
```

O app de terminal ocupa a tela toda. Por isso ele roda no seu terminal, não dentro do Claude Code.

## O navegador

No macOS o navegador abre sozinho.

Em outros sistemas, abra este arquivo:

```
~/.claude/metrics/dashboard.html
```

## Problemas?

No Debian ou Ubuntu, o `/tokboard-tui` pode falhar na primeira execução com um erro de `ensurepip`. Veja o [FAQ](faq.md).
