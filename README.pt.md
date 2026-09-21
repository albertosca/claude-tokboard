🇺🇸 [English](README.md) · 🇧🇷 [Português](README.pt.md)

[![CI](https://github.com/albertosca/claude-tokboard/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/albertosca/claude-tokboard/actions/workflows/ci.yml)
[![Lint](https://github.com/albertosca/claude-tokboard/actions/workflows/lint.yml/badge.svg?branch=main)](https://github.com/albertosca/claude-tokboard/actions/workflows/lint.yml)
[![Coverage](https://raw.githubusercontent.com/albertosca/claude-tokboard/main/.github/badges/coverage.svg)](https://github.com/albertosca/claude-tokboard/blob/main/.github/badges/coverage.svg)

# tokboard

Um plugin do Claude Code que acompanha seu histórico de sessões e uso
de tokens, e mostra isso de duas formas: um dashboard HTML e um app de
terminal (TUI) completo. Veja no que você tem trabalhado e quanto isso
está custando, por projeto, por dia.

![O dashboard HTML: gráfico de barras empilhadas por projeto, com abas por tipo de token e por modelo, acima de uma tabela de sessões agrupada por projeto](assets/dashboard.png)

![A TUI: tabela de sessões com painel de detalhe, e o gráfico de tendência ciclando pelas três formas de fatiar um dia - por projeto, por tipo de token, por modelo](assets/tui.gif)

<sub>Renderizado a partir do fixture sintético de teste deste próprio repositório - os projetos se chamam `alpha`, `gamma`, `eta`; nenhum dado real de sessão.</sub>

## Notas de engenharia

Duas camadas de apresentação (um dashboard HTML e um TUI feito com
[Textual](https://github.com/Textualize/textual)) compartilham uma
única camada de dados em Python puro (`lib/metrics_core/`), então todo
número que as duas superfícies mostram é calculado uma vez só e
testado uma vez só. A suíte está em 100% de cobertura (statement e
branch) com um gate permanente (`--cov-fail-under=100` - uma única
linha sem cobertura derruba o build), e o CI roda ela junto com o
`ruff` (um conjunto amplo de regras, não só o padrão) e o
`mypy --strict` a cada push - veja [Contribuindo](#contribuindo)
abaixo pros comandos exatos, ou os workflows em si em
[`.github/workflows/`](.github/workflows/).

## O que você ganha

- **Visibilidade de custo e tokens** - tokens de input/output/cache
  detalhados por dia, por projeto e por tipo, pra você ver o que
  realmente puxa o seu uso.
- **Histórico de sessões** - toda sessão registrada com um rótulo de
  tarefa auto-gerado (opcional - veja abaixo), filtrável por projeto ou
  tag, com detalhamento de subagentes por sessão.
- **Duas formas de ver**: `/tokboard` abre um dashboard HTML interativo
  no navegador; `/tokboard-tui` abre um app de terminal completo
  (feito com [Textual](https://github.com/Textualize/textual)) pra
  quando um navegador não é conveniente.

## Instalação

Instale este plugin pelo sistema de plugins do Claude Code (veja a
[documentação de plugins do Claude Code](https://docs.claude.com/en/docs/claude-code)
pra saber como seu ambiente descobre e instala plugins). Depois de
instalado, `/tokboard` já funciona de cara (zero dependências
externas) - `/tokboard-tui` instala sozinho suas dependências
(`textual`, `textual-plotext`) num ambiente virtual dedicado, na
primeira vez que você rodar.

No Debian/Ubuntu, esse passo de auto-instalação precisa do pacote
`python3-venv`, que nem sempre já vem instalado. Se o `/tokboard-tui`
falhar na primeira vez com um erro sobre `ensurepip`, rode
`sudo apt install python3-venv` (ou o `python3.X-venv` específico da
versão que a mensagem de erro citar) e tente de novo - o `/tokboard`
em si não é afetado, já que não tem dependência nenhuma pra instalar.

## Rótulos de tarefa por IA (opcional)

Os dois dashboards conseguem gerar automaticamente um rótulo curto por
sessão (ex: "corrigindo bug de redirect no login") resumindo com uma
chamada leve de `claude -p`. **Isso vem desligado por padrão**
(`--label-budget 0`) - é uma chamada de API real por sessão não
cacheada, então gasta sua própria cota. Passe `--label-budget N` pro
script de qualquer um dos dois dashboards pra ligar (`N` rótulos novos
por execução; rótulos já cacheados são sempre de graça).

## Nomeando seus projetos (opcional)

Uma sessão é registrada com o diretório em que o Claude Code estava, que muitas vezes é um subdiretório do projeto e não a raiz dele - então uma sessão rodada de `minha-api/docs/vendor` aparece na legenda do gráfico como "vendor". O caminho sozinho não diz onde o projeto termina, e resolver contra o sistema de arquivos também não resolve: diretórios são renomeados e apagados, e o histórico sobrevive a eles.

Então você declara, em `~/.claude/metrics/config.json`:

```json
{
  "project_roots": ["/Users/username/code", "/Users/username/work"]
}
```

Um filho direto de uma raiz declarada é um projeto, por mais fundo que a sessão tenha ido. O `~` é expandido. Worktrees mantêm entrada própria, rotulada `projeto / worktree`, então você continua sabendo em qual branch do projeto o trabalho aconteceu.

Sem esse arquivo nada muda: os dashboards caem numa heurística que só consegue subir um nível, e deixam o resto como está.

Sessões e subagentes registrados a partir de 21/09/2026 também gravam a raiz do projeto que o próprio Claude Code entrega a todo hook, que é autoritativa e não precisa de configuração nenhuma - o `project_roots` é o que dá nome a tudo que foi registrado antes disso.

## Integração com tmux (opcional, detectada automaticamente)

Se você roda o Claude Code dentro do tmux, as linhas de sessão mostram
em qual janela do tmux aconteceram. Se você não usa tmux, essa coluna
simplesmente não aparece - nada pra configurar.

## Desinstalar / onde ficam os dados

Todo dado registrado fica em `~/.claude/metrics/` (`sessions.jsonl`,
`subagents.jsonl`, caches de rótulo, o `dashboard.html` gerado) -
independente do plugin em si. Desinstalar o plugin para de registrar
dados novos, mas não apaga o que já existe.

## Contribuindo

```bash
pip install -r requirements-dev.txt -r requirements-tui.txt
npm install                     # jsdom, pros testes de renderização
pytest -q                       # suíte completa, 100% de cobertura é gate
ruff check . && ruff format --check .
mypy .
```

Sem build step e sem asset compilado - `dashboard_html.py` e
`dashboard_tui.py` são os dois pontos de entrada, ambos lendo de
`lib/metrics_core/`. PRs são bem-vindos; abra uma issue antes pra
qualquer coisa além de um fix pequeno, pra alinhar a direção antes do
trabalho.

## Licença

[MIT](LICENSE)
