# FAQ

## O dashboard está vazio

O tokboard só conhece os turnos que aconteceram depois da instalação. Use o Claude Code por alguns turnos e rode `/tokboard` de novo.

## `/tokboard-tui` falha na primeira execução com erro de `ensurepip`

Isso acontece no Debian e no Ubuntu.

A instalação automática precisa do pacote `python3-venv`, que nem sempre vem instalado.

Rode:

```bash
sudo apt install python3-venv
```

Ou o `python3.X-venv` da versão que o erro citar. Depois tente de novo.

O `/tokboard` não é afetado de nenhum jeito.

## O medidor do limite semanal diz "No calls stored yet"

O registro por chamada começa vazio depois de uma atualização. Preencha uma vez. Veja [o medidor](weekly-meter.md).

## Por que um total começa com "≥"?

Algumas mensagens nunca receberam a entrada final de uso. O total real pode ser maior. Veja [o painel HTML](dashboard.md).

## Por que a linha de cota é "aproximada"?

O Claude Code não publica a própria fórmula. Veja [Tipos de token e a linha de cota](concepts.md).

## Por que um projeto aparece com o nome errado?

Uma sessão é registrada sob o diretório em que rodou. Defina `project_roots`. Veja [Configurações](settings.md).

## Alguma coisa sai da minha máquina?

Não. A única exceção são os rótulos de tarefa com IA, que vêm desligados. Veja [Seus dados](your-data.md).

## Funciona fora do macOS?

Funciona. Só a abertura automática do navegador é exclusiva do macOS. Em outros sistemas, abra `~/.claude/metrics/dashboard.html`.

## Como reporto um bug ou contribuo?

Abra uma issue no [GitHub](https://github.com/albertosca/claude-tokboard). Para uma vulnerabilidade, siga o [SECURITY.md](https://github.com/albertosca/claude-tokboard/blob/main/SECURITY.md).
