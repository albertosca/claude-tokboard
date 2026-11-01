# The terminal app

Same data as the browser page, driven by the keyboard.

![The terminal app](images/tui.gif)

## Start it

In Claude Code, run:

```
/tokboard-tui
```

It prints a command. Paste it into **your own terminal**.

The app is full-screen. It cannot run inside Claude Code.

## Keys

| Key | Action |
|---|---|
| <kbd>g</kbd> | Go to the next tab: Sessions, Overview. |
| <kbd>t</kbd> | Change the chart mode. |
| <kbd>p</kbd> | Change the period. |
| <kbd>q</kbd> | Quit. |

## The chart modes

Press <kbd>t</kbd> to cycle through the four slicings of a day:

1. project
2. token type
3. model
4. git branch

## Sessions

The session table has a detail panel.

In the terminal the tokens-per-branch panel sits on the **Overview** tab, with the quota line and the chart; the browser page puts it on **Sessions**.

On the **Overview** tab, two lines sum up the weekly-limit meter and the cache-creation causes, and a closed block of details under them holds the full cards. Move to it and press <kbd>Enter</kbd> to open it; the tables inside start closed too.

## Small terminals

Some lines need room. The legend and the "last bar" line show up on taller terminals.

If something is missing, make the window taller.

## Language

The interface follows your terminal's locale. See [Settings](settings.md#language).
