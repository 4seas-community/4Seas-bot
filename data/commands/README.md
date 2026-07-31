# Custom commands

Drop a `.yaml` file in this directory, send `/reload` in the group, and the command
is live. No restart, no code, no deploy. Delete the file and `/reload` again to
remove it.

## Format

One file can hold a single command (a mapping) or several (a list):

```yaml
- command: wifi                  # required. becomes /wifi
  description: Venue Wi-Fi       # shown in /help and Telegram's command menu
  reply: |                       # required. Telegram HTML
    📶 <b>Wi-Fi</b>
    Network: <code>4Seas-Guest</code>
    Password: <code>ask-an-admin</code>

  # everything below is optional, defaults shown
  admin_only: false              # true → only TELEGRAM_ADMIN_IDS get a reply
  scope: all                     # all | group | private
  parse_mode: HTML               # HTML | Markdown | MarkdownV2 | none
  disable_preview: true          # suppress link previews
  enabled: true                  # false → parsed but not registered
```

Only `command` and `reply` are required.

## Rules

| Rule | Why |
|---|---|
| Name must be `a-z 0-9 _`, 1–32 chars | Telegram rejects the whole `setMyCommands` batch otherwise — one bad name would blank the menu for every command |
| Cannot reuse a built-in name (`start help events ask faq sync report reload status`) | Overriding `/reload` with a broken config would lock you out of the only way to fix it |
| Duplicate names across files are rejected | Whichever file loaded first wins; the other is reported as an error rather than silently ignored |

## When something is wrong

`/reload` reports each bad entry with its filename and reason. **Bad entries are
skipped, not fatal** — the rest of your commands still load. A file with invalid
YAML is skipped whole; every other file still loads.

`/status` shows the live count and whether there are outstanding config errors.

## Notes

- `admin_only` and `scope: private` commands are hidden from Telegram's command
  menu — advertising a command that silently does nothing is worse than not
  listing it. They still appear in `/help` for admins.
- Replies are static text. For anything that needs live data (like `/events`),
  that's a code-level command.
- HTML supports `<b> <i> <u> <s> <code> <pre> <a href="">`. Escape literal
  `< > &` as `&lt; &gt; &amp;` or the message fails to send.
