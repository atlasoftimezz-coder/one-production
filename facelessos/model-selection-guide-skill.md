---
name: model-selection-guide
description: Choose the right AI model and workflow for FacelessOS scriptwriting. Covers model comparison, plan selection, token management, research workflows, and common mistakes.
---

# Model Selection Guide

Choose the right model for your FacelessOS scriptwriting workflow.

## CRITICAL: HOW CLAUDE WEB WORKS

**In Claude web (claude.ai), each chat is locked to ONE model.**

You cannot switch from Sonnet to Opus mid-conversation. When you start a chat, you pick a model, and that chat stays on that model.

**This means:**
- "Split workflow" = SEPARATE CHATS with different models
- You copy/paste research from one chat into another
- Or use a completely different tool (Perplexity) for research

---

## RECOMMENDED MODEL FOR FACELESSOS

**Use Opus 4.6 for writing scripts.**

| Model | Recommendation | Notes |
|-------|---------------|-------|
| **Opus 4.6** | ✅ BEST for FacelessOS | Fastest, 1M context window, handles complex structures |
| **Opus 4.5** | ✅ Solid alternative | Slightly slower, some members prefer it for fewer hallucinations |
| **Sonnet 4.6** | ⚠️ For quick edits only | Faster and cheaper, but lower quality for full scripts |
| **ChatGPT/GPT-5** | ❌ Not recommended | Gets you 70% there, needs heavy revision |

**From the community:** Ed, RK, and Haris all use Opus 4.6 as their daily driver. If you notice hallucinations on a specific topic, try Opus 4.5 for that session.

---

## PLAN SELECTION

| Plan | Cost | Best For |
|------|------|----------|
| **Claude Pro** | $20/month | Getting started, 1-2 scripts per day, scripts under 20 minutes |
| **Claude Max** | $100/month | Batch scripting, long-form (45+ min), heavy daily usage |

**Member Quote:** "Claude Max is more like 50X better than Pro - not 5X."

**When to upgrade to Max:**
- Writing 3+ scripts per day
- Scripts over 30 minutes regularly
- Hitting context limits frequently
- Batch scripting across multiple channels

**Note:** API access is NOT recommended for most users. The per-token cost adds up fast. One member spent $374 in a single month on API alone. Stick with Pro or Max.

---

## RESEARCH WORKFLOWS

### Option A: Claude Research Mode (Recommended)

Claude has a built-in research feature. Enable it at the start of your conversation:

1. Start a new chat in your FacelessOS project
2. Enable Research Mode
3. Ask Claude to research your topic
4. Once research is complete, Claude automatically switches to writing mode
5. Write your script with the research context already loaded

**Why this is best:** No copy-pasting between tools. Research and writing happen in the same context with FacelessOS skills active.

### Option B: Perplexity + Claude

**Step 1: Perplexity (Free)**
- Gather facts, statistics, sources
- Build your research document
- Export key findings

**Step 2: Claude with FacelessOS**
- Paste your research findings
- Write the full script
- No token waste on research

**Why this works:** Perplexity is free and purpose-built for research. You save all your Claude tokens for writing.

### Option C: Claude Code Extension

For power users comfortable with VS Code or similar IDEs:

- Install the Claude Code extension in VS Code
- Runs through your Claude subscription (not API billing)
- More efficient token usage than the web interface
- Better for batch workflows and long sessions

---

## SIMPLE APPROACH (No Split Workflow)

If the workflows above sound complicated:

1. **Open Claude web**
2. **Select Opus 4.6**
3. **Open your FacelessOS project**
4. **Write your script**

That's it. The split workflows save tokens but add complexity. Most users get excellent results with the simple approach.

---

## WHEN TO USE EACH MODEL

### Opus 4.6 (Default Choice)
- Writing full scripts
- Long-form (45+ min)
- Research + writing in one session
- When you want the latest capabilities

### Opus 4.5
- If Opus 4.6 is hallucinating on a specific topic
- For members who prefer slightly more conservative output
- Still excellent quality, just slower

### Sonnet 4.6
- Quick edits to existing scripts
- Re-audit runs
- Budget-conscious work
- NOT for full script generation

---

## VOICEOVER PACING CUSTOMIZATION

Different TTS voices and narrators speak at different speeds. Customize your word count targets:

**Default:** FacelessOS assumes ~150 words per minute (standard narration pace).

**To customize:** Tell FacelessOS at the start of your session:
```
My voiceover rate is about [X] words per minute. Adjust all script length targets accordingly.
```

Common rates:
- Fast narration (news style): ~170 WPM
- Standard narration: ~150 WPM
- Slow, dramatic narration: ~130 WPM
- Vidrush ElevenLabs voices: varies by voice, test and measure

---

## TOKEN MANAGEMENT

### Hitting Context Limits?

1. **Start a fresh chat** for each new script (most impactful)
2. **Use Research Mode** instead of manually feeding long documents
3. **Consider Claude Max** if hitting limits more than twice per day
4. **Use Perplexity** for research-heavy topics to save Claude tokens

### Token Estimates by Script Length

| Script Length | Approximate Tokens |
|--------------|-------------------|
| 5-10 min | ~15K tokens |
| 15-30 min | ~40K tokens |
| 45-60 min | ~80K tokens |
| 1-2 hours | ~120K+ tokens |

---

## COMMON MISTAKES

### Mistake #1: Thinking you can switch models mid-chat
Each Claude web chat is locked to one model. Start a new chat to use a different model.

### Mistake #2: Using Sonnet for full scripts
Sonnet is fast and cheap but produces noticeably lower quality scripts. Use Opus for anything you plan to publish.

### Mistake #3: Using GPT instead of Claude
GPT gets you 70% there. Claude with FacelessOS gets you to 95%+. The time saved on revisions is worth the cost difference.

### Mistake #4: Staying on Pro when you're hitting limits
If you're constantly hitting context limits, Claude Max ($100/month) saves frustration and improves output quality.

### Mistake #5: Using API thinking it's cheaper
API billing is per-token. For heavy scriptwriting, Pro ($20) or Max ($100) with unlimited usage is almost always cheaper than API.

---

## QUICK DECISION

**Just starting?**
→ Claude Pro ($20/mo) + Opus 4.6

**Doing 3+ scripts/day?**
→ Claude Max ($100/mo) + Opus 4.6

**Want to save tokens?**
→ Perplexity (research) + Claude Opus 4.6 (writing)

**Scripts over 45 minutes?**
→ Claude Max + Opus 4.6 (recommended)

---

**Bottom line:** Use Opus 4.6 for FacelessOS scripts. Upgrade to Max if you're a heavy user. Enable Research Mode for research-heavy topics. The split workflow is optional.
