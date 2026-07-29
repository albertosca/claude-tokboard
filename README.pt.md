# metrics-dashboard

Um plugin do Claude Code que acompanha seu histórico de sessões e uso
de tokens, e mostra isso de duas formas: um dashboard HTML e um app de
terminal (TUI) completo. Veja no que você tem trabalhado e quanto isso
está custando, por projeto, por dia.

[Read this in English](README.md)

## O que você ganha

- **Visibilidade de custo e tokens** - tokens de input/output/cache
  detalhados por dia, por projeto e por tipo, pra você ver o que
  realmente puxa o seu uso.
- **Histórico de sessões** - toda sessão registrada com um rótulo de
  tarefa auto-gerado (opcional - veja abaixo), filtrável por projeto ou
  tag, com detalhamento de subagentes por sessão.
- **Duas formas de ver**: `/metrics` abre um dashboard HTML interativo
  no navegador; `/metrics-tui` abre um app de terminal completo
  (feito com [Textual](https://github.com/Textualize/textual)) pra
  quando um navegador não é conveniente.

## Instalação

Instale este plugin pelo sistema de plugins do Claude Code (veja a
[documentação de plugins do Claude Code](https://docs.claude.com/en/docs/claude-code)
pra saber como seu ambiente descobre e instala plugins). Depois de
instalado, `/metrics` já funciona de cara (zero dependências
externas) - `/metrics-tui` instala sozinho suas dependências
(`textual`, `textual-plotext`) num ambiente virtual dedicado, na
primeira vez que você rodar.

No Debian/Ubuntu, esse passo de auto-instalação precisa do pacote
`python3-venv`, que nem sempre já vem instalado. Se o `/metrics-tui`
falhar na primeira vez com um erro sobre `ensurepip`, rode
`sudo apt install python3-venv` (ou o `python3.X-venv` específico da
versão que a mensagem de erro citar) e tente de novo - o `/metrics`
em si não é afetado, já que não tem dependência nenhuma pra instalar.

## Rótulos de tarefa por IA (opcional)

Os dois dashboards conseguem gerar automaticamente um rótulo curto por
sessão (ex: "corrigindo bug de redirect no login") resumindo com uma
chamada leve de `claude -p`. **Isso vem desligado por padrão**
(`--label-budget 0`) - é uma chamada de API real por sessão não
cacheada, então gasta sua própria cota. Passe `--label-budget N` pro
script de qualquer um dos dois dashboards pra ligar (`N` rótulos novos
por execução; rótulos já cacheados são sempre de graça).

## Integração com tmux (opcional, detectada automaticamente)

Se você roda o Claude Code dentro do tmux, as linhas de sessão mostram
em qual janela do tmux aconteceram. Se você não usa tmux, essa coluna
simplesmente não aparece - nada pra configurar.

## Desinstalar / onde ficam os dados

Todo dado registrado fica em `~/.claude/metrics/` (`sessions.jsonl`,
`subagents.jsonl`, caches de rótulo, o `dashboard.html` gerado) -
independente do plugin em si. Desinstalar o plugin para de registrar
dados novos, mas não apaga o que já existe.
