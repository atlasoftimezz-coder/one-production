"""Agent mode (plan P5): the chat runs the real Claude Code / Codex agents in the project folder.

    policy.py         what may run without asking (user's choice: free inside the project)
    studio.py         One Production's own tools offered to the agent (voiceover, voices, ...)
    claude_agent.py   Claude logins and API keys, through the official Claude Agent SDK
    codex_agent.py    Codex logins, through the official `codex app-server`
    runs.py           one live run per project: event queue, approval cards, Stop
"""
