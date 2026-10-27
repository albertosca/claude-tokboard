# tokboard

**Veja para onde vão os seus tokens do Claude Code** - por dia, por projeto, por tipo de token e por modelo.

O tokboard é um plugin do Claude Code. Ele registra cada sessão em segundo plano. Você vê o resultado no navegador ou no terminal.

Nada sai da sua máquina.

![O dashboard HTML](images/dashboard.png)

## O problema que ele resolve

Uma contagem crua de tokens mistura duas coisas bem diferentes:

- **leitura de cache**, que é barata e quase não pesa;
- os tokens que **contam para o seu rate limit**.

O tokboard mostra os dois números, lado a lado.

Estourou o limite semanal e não sabe por quê? O tokboard divide o seu cache creation por causa e acompanha a semana atual contra a faixa em que as semanas passadas bateram no limite.

## O que você ganha

- **Uma visão de cota**, não só de volume.
- **Quatro recortes** de cada dia: por projeto, tipo de token, modelo ou branch do git.
- **Cada sessão**, agrupada por projeto, com seus subagentes.
- **Um medidor do limite semanal** e as causas do cache creation.
- **Tokens por branch** para projetos que usaram mais de uma.
- **Duas interfaces**, uma só camada de dados: uma página no navegador e um app de terminal.

## Para onde ir agora

| Você quer... | Leia |
|---|---|
| Colocar para rodar | [Instalação](install.md) |
| Ver o seu primeiro dashboard | [Primeiro contato](first-look.md) |
| Entender os números | [Tipos de token e a linha de cota](concepts.md) |
| Saber se está perto do limite semanal | [O medidor do limite semanal](weekly-meter.md) |
| Usar a página no navegador | [O painel HTML](dashboard.md) |
| Usar o app de terminal | [O app de terminal](tui.md) |
| Nomear projetos, rotular sessões | [Configurações](settings.md) |
| Saber o que fica guardado | [Seus dados](your-data.md) |
| Atualizar para uma versão nova | [Atualização](upgrading.md) |
| Resolver um problema | [FAQ](faq.md) |
