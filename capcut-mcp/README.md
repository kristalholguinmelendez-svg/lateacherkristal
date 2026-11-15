# CapCut Helper — custom MCP connector

CapCut has **no public API**, so no connector can open, edit or export your
CapCut projects. This MCP server does the next best thing: it lets Claude
prepare the files you import into CapCut for your English lessons.

| Tool | What it does |
|------|--------------|
| `script_to_srt` | Turns a narration script into auto-timed `.srt` captions. Pass `translations` for bilingual English/Spanish subtitles. |
| `make_srt` | Builds an `.srt` from captions with exact times. |
| `shift_srt` | Moves all captions earlier or later. |
| `lesson_shot_list` | Writes a shot list + narration script for a short vocabulary video (9:16 by default). |
| `list_saved_files` / `read_saved_file` | Gets back files the server saved. |

In CapCut, import captions with **Text → Captions → Import** (desktop) or
**Captions → Import captions** (mobile).

## Option A — use it in Claude Code or Claude Desktop (local, easiest)

```bash
cd capcut-mcp
pip install .
claude mcp add capcut -- capcut-mcp          # Claude Code
```

For Claude Desktop, add to `claude_desktop_config.json`:

```json
{ "mcpServers": { "capcut": { "command": "capcut-mcp" } } }
```

## Option B — add it as a custom connector on claude.ai

claude.ai needs a public **HTTPS** address, so host the server first, for
example on Render, Railway, Fly.io or Google Cloud Run, using the included
`Dockerfile` (it listens on port 8000 at the path `/mcp`).

Then in claude.ai: **Settings → Connectors → Add custom connector**, and
enter `https://<your-host>/mcp`.

> The server has no login. It only creates caption and script text, but
> anyone with the URL could use it, so don't share the address publicly.

## Development

```bash
pip install -e '.[dev]'
pytest
MCP_TRANSPORT=http PORT=8000 capcut-mcp      # run the HTTP version locally
```
