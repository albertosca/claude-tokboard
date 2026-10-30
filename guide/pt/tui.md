# O app de terminal

Os mesmos dados da página no navegador, pelo teclado.

![O app de terminal](images/tui.gif)

## Como abrir

No Claude Code, rode:

```
/tokboard-tui
```

Ele imprime um comando. Cole no **seu próprio terminal**.

O app ocupa a tela toda. Ele não roda dentro do Claude Code.

## Teclas

| Tecla | Ação |
|---|---|
| <kbd>g</kbd> | Vai para a próxima aba: Sessões, Panorama, Hipóteses. |
| <kbd>t</kbd> | Muda o modo do gráfico. |
| <kbd>p</kbd> | Muda o período. |
| <kbd>q</kbd> | Sai. |

## Os modos do gráfico

Aperte <kbd>t</kbd> para passar pelos quatro recortes de um dia:

1. projeto
2. tipo de token
3. modelo
4. branch do git

## Sessões

A tabela de sessões tem um painel de detalhe.

No terminal o painel de tokens por branch fica na aba **Panorama**, com a linha de cota e o gráfico; a página do navegador o põe em **Sessões**.

O medidor do limite semanal e as causas do cache creation ficam na aba **Hipóteses**, como na página do navegador. As tabelas começam fechadas: vá até uma e aperte <kbd>Enter</kbd> para abrir.

## Terminais pequenos

Algumas linhas precisam de espaço. A legenda e a linha da "última barra" aparecem em terminais mais altos.

Se algo sumiu, aumente a altura da janela.

## Idioma

A interface segue o locale do seu terminal. Veja [Configurações](settings.md).
