# Configurações

Tudo aqui é opcional.

O arquivo de configuração é `~/.claude/metrics/config.json`.

## Idioma

A interface fala inglês e português do Brasil.

Ela segue o locale do seu terminal. Para forçar um:

```json
{ "language": "pt" }
```

Aceita: `"en"`, `"pt"` ou uma tag como `"pt-BR"`.

## Nomeando os seus projetos

Uma sessão é registrada sob o diretório em que rodou.

Nem sempre esse é o projeto.

Liste as pastas onde os seus projetos moram:

```json
{ "project_roots": ["~/code", "~/work"] }
```

Cada pasta diretamente dentro de uma raiz listada é um projeto, não importa a profundidade em que a sessão foi.

| Sessão rodou em | Sem `project_roots` | Com `~/code` listado |
|---|---|---|
| `~/code/my-api/docs/vendor` | vendor | my-api |

As versões recentes já gravam a raiz do projeto, então isso corrige principalmente o histórico antigo.

## Rótulos de tarefa com IA

O Claude pode rotular cada sessão com um resumo curto, como "corrigindo bug de redirect do auth".

Vem **desligado por padrão**.

Por quê? Cada rótulo novo é uma chamada `claude -p` na **sua própria cota**.

Ligue com um orçamento:

```bash
/tokboard-tui --label-budget 10
```

- `N` é quantos rótulos **novos** por execução.
- Rótulos já feitos ficam em cache, e são de graça.

Os rótulos ficam em cache por idioma.

## Filtros

Os dois scripts aceitam:

| Flag | Efeito |
|---|---|
| `--project` | Só as sessões desse caminho de projeto. |
| `--tag` | Só as sessões com essa tag. |
| `--days N` | Só os últimos N dias. O histórico completo é o padrão. |

## Marcando uma sessão

Defina `CLAUDE_METRIC_TAG` antes de iniciar o Claude Code para marcar as sessões dele. Depois filtre com `--tag`.
