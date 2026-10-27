# Primeiro contato

Um passeio curto pelo seu primeiro dashboard.

## Antes de começar

O tokboard só conhece os turnos que aconteceram **depois** da instalação.

Então use o Claude Code um pouco primeiro. Alguns turnos já bastam para aparecer algo.

## Passo 1 - abra o dashboard

No Claude Code, rode:

```
/tokboard
```

O navegador abre a página.

## Passo 2 - leia o gráfico de tendência

O gráfico grande no topo mostra uma barra por dia.

Cada barra é dividida em cores. Por padrão, as cores são os seus **projetos**.

## Passo 3 - troque o recorte

As abas acima do gráfico refatiam o mesmo dia:

1. por **projeto**
2. por **tipo de token**
3. por **modelo**
4. por **branch do git**

Um dia tem a mesma altura em todas as abas. Só as cores mudam.

## Passo 4 - destaque uma coisa

Clique numa entrada da legenda ou numa barra.

Aquele projeto, tipo de token ou modelo fica destacado no gráfico inteiro.

## Passo 5 - role a página

Abaixo do gráfico você encontra:

- o **medidor do limite semanal**;
- de onde vem o seu **cache creation**;
- **tokens por branch**;
- uma **tabela de sessões**, agrupada por projeto.

Cada um tem a sua página:

- [O medidor do limite semanal](weekly-meter.md)
- [O painel HTML](dashboard.md)

## Prefere o terminal?

Rode `/tokboard-tui`. Os mesmos dados, pelo teclado. Veja [O app de terminal](tui.md).
