# O painel HTML

Abra com `/tokboard`.

![O dashboard HTML](images/dashboard.png)

## O que tem na página

Duas abas: **Sessões** e **Panorama**.

**Sessões** tem, de cima para baixo:

1. **A linha de cota** com os chips de tipo de token e, embaixo, **duas linhas de resumo**: o que o medidor do limite semanal e as causas do cache creation encontraram, cada um numa frase.
2. **Um bloco fechado de detalhes** com os dois cartões completos. Abra quando uma linha de resumo der vontade de ver os números.
3. **A tabela de sessões**: todas as sessões, agrupadas por projeto. Um projeto que usou 2 ou mais branches diz isso na própria linha, como `main 73% · feat/x 16% · +2`: as duas maiores por tokens e quantas mais existem.

**Panorama** tem:

1. **A linha de cota**: quanto do seu rate limit o período consumiu.
2. **O gráfico de tendência**: uma barra por dia, com quatro recortes.

O bloco de detalhes tem dois cartões, cada um com as tabelas fechadas até você abri-las:

1. **O medidor do limite semanal**: veja [o medidor](weekly-meter.md).
2. **Causas do cache creation**: uma barra por janela recente dividindo partida a frio, reescrita após pausa e crescimento.

## O gráfico de tendência

Troque o recorte pelas abas:

- projeto
- tipo de token
- modelo
- branch do git

Clique numa entrada da legenda ou numa barra para destacá-la.

## Sessões

As sessões ficam agrupadas por projeto.

Cada linha mostra:

- os subagentes;
- a duração;
- o detalhamento de tokens.

Dá para filtrar por período.

## Branches

Se um projeto usou mais de uma branch do git, a linha dele na tabela de sessões mostra quanto custaram as duas maiores, como parcela dos tokens do projeto.

A lista de sessões pode se restringir a uma única branch.

## Um "≥" na frente do total

Algumas mensagens nunca receberam a entrada final de uso.

Um total com "≥" na frente quer dizer que o valor real pode ser **maior**. É um piso, não uma estimativa.

## tmux

Dentro do tmux, o hook grava o nome da janela junto com cada sessão.

O dashboard mostra esse nome. Não há nada para configurar.

## Filtros

Rode o script direto para restringir o que ele mostra:

```bash
python3 dashboard_html.py --html --days 14
```

| Flag | Efeito |
|---|---|
| `--project` | Só as sessões desse caminho de projeto. |
| `--tag` | Só as sessões com essa tag. |
| `--days N` | Só os últimos N dias. |
| `--all` | Aceita por compatibilidade. O histórico completo é o padrão, a menos que `--days` seja usado. |
