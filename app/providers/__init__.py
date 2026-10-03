"""AI providers. Each one turns a prompt into a stream of events (see base.py).

    demo           built-in echo, no account needed (lets a new user try the app)
    claude         Claude Code CLI with the user's own Claude login   (claude -p)
    codex          Codex CLI with the user's own ChatGPT login        (codex exec)
    anthropic_api  Anthropic Messages API with the user's API key

Every account is used only through its official client (plan decision D2).
"""
