---
name: authenticity-audit
description: YouTube policy compliance checker for faceless content. Audits scripts against inauthentic content, reused content, and demonetization signals BEFORE upload. Separate from anti-AI slop (which checks writing quality) — this checks YouTube POLICY compliance.
---

# Authenticity Audit

> **PURPOSE:** Catch YouTube policy violations BEFORE you upload. This is NOT about writing quality (see the Anti-AI Slop Checklist in the master file for that). This is about whether YouTube's automated systems will flag your video as inauthentic or reused content.

---

## Why This Exists

YouTube's current policies on "inauthentic content" target mass-produced, template-based videos with no creative input. Channels built on pure automation are getting:

- **Demonetized** (ads removed from videos)
- **Monetization applications denied** for "reused content"
- **Channels terminated** under the "inauthentic content" policy
- **AdSense applications rejected** even on first attempt

This affects faceless channels more than any other format. If your scripts are indistinguishable from thousands of other AI-generated videos, YouTube will catch it.

---

## The Two Threats

### Threat 1: "Inauthentic Content" (Channel-Level)

YouTube defines inauthentic content as: content that is mass-produced with minimal creative input, often using templates or automation to generate large volumes of similar videos.

**What triggers it:**
- Multiple channels producing near-identical content
- Template-based scripts with only topic names swapped
- No unique perspective, commentary, or analysis
- AI-generated narration with no human review or editing
- Bulk publishing (5+ videos/day across multiple channels)

### Threat 2: "Reused Content" (Monetization-Level)

YouTube flags "reused content" when your video doesn't add enough original value. This blocks AdSense approval and can remove monetization from existing channels.

**What triggers it:**
- Scripts that closely mirror existing YouTube videos on the same topic
- No original research, analysis, or unique angle
- Generic narration that could apply to any video on the topic
- Visuals that are identical to other channels (common with AI video tools)
- No editorial voice or perspective in the narration

---

## Pre-Upload Audit Checklist

Run this checklist on every script BEFORE uploading to YouTube. Each item must pass.

### Uniqueness Signals (Must have at least 5 of 7)

- [ ] **Original research:** Script includes facts, quotes, or data NOT found in the first page of Google results for this topic
- [ ] **Unique angle:** The script approaches the topic from a perspective that's different from the top 5 existing videos on this topic
- [ ] **Editorial voice:** The narrator expresses opinions, makes judgments, or adds personal commentary (not just reporting facts)
- [ ] **Specific sourcing:** At least 3 specific sources are referenced or woven into the narrative (not vague "studies show")
- [ ] **Non-obvious connections:** The script draws connections between facts or events that aren't immediately obvious
- [ ] **Original structure:** The script doesn't follow the same chronological/listicle structure as the most popular video on this topic
- [ ] **Human review evidence:** You personally reviewed and edited the script — it's not a raw AI output

### VidRush-Specific Checks (If using AI video generation)

- [ ] **Reference video is human-narrated:** If you used the Reference Video technique, the source video uses a real human narrator (not AI voice). AI mimicking AI produces the most flaggable content.
- [ ] **Visual variety:** Your video won't use the exact same stock footage sequence as other VidRush videos on similar topics (check by searching your topic on YouTube)
- [ ] **Custom elements:** You added at least one custom element: custom voiceover, custom thumbnail, edited timeline, added overlays, or modified the generated output

### Red Flags (Must have ZERO)

- [ ] **No template scripting:** The script does NOT read like a fill-in-the-blank template where only the topic name changes
- [ ] **No duplicate publishing:** This exact script or a near-identical version is NOT being published on multiple channels
- [ ] **No bulk pattern:** You are NOT publishing 5+ videos per day across multiple channels with similar content
- [ ] **No verbatim copying:** No sentences are copied word-for-word from existing YouTube video transcripts

---

## Mitigation Strategies

### For VidRush Users

1. **Always use custom scripts** — VidRush's built-in script generator is convenient but produces generic content. FacelessOS scripts are significantly more unique.
2. **Use human-narrated reference videos** — This teaches VidRush a human style instead of an AI style
3. **Edit the timeline** — Even small edits (swapping a clip, adjusting a transition) signal human involvement
4. **Custom voiceover** — Recording your own VO (or using a unique ElevenLabs voice) differentiates your content from other VidRush channels
5. **Avoid trending-only content** — Pure trend-chasing produces the most overlap with other channels. Mix trending with evergreen.

### For All Faceless Channels

1. **Add editorial commentary** — Don't just report facts. React to them. "That's insane when you think about it" is human. A dry recitation of events is AI.
2. **Include original analysis** — "Here's what nobody is connecting: X happened because of Y, not because of Z" shows unique thinking
3. **Vary your structure** — Use the Variety Rotation System to ensure your scripts don't follow the same pattern every time
4. **Research beyond page 1** — If all your facts come from the first Google result, your script will sound like everyone else's
5. **Review and edit** — A script you personally edited for 15-30 minutes is dramatically more unique than a raw AI output

---

## Channel Health Indicators

Monitor these signals. If you see multiple, take action before YouTube does.

| Signal | Risk Level | Action |
|--------|-----------|--------|
| Monetization application denied for "reused content" | 🔴 HIGH | Audit all recent scripts, increase uniqueness, appeal with evidence of original work |
| Comments saying "this sounds AI" or "heard this before" | 🟡 MEDIUM | Review recent scripts for template patterns, add more editorial voice |
| Multiple videos getting same view count (suspiciously uniform) | 🟡 MEDIUM | YouTube may be suppressing distribution — vary your content more |
| "Inauthentic content" warning in YouTube Studio | 🔴 CRITICAL | Stop publishing immediately. Audit entire channel. Remove flagged videos. |
| Sudden drop in impressions across all videos | 🟡 MEDIUM | Could be algorithmic suppression — check for policy notifications |

---

## The Authenticity Spectrum

```
MOST FLAGGABLE ←————————————————————→ MOST SAFE

AI script +          FacelessOS +      FacelessOS +         FacelessOS +
AI video +           AI video +        AI video +           custom VO +
no edits +           reference video + edited timeline +    edited timeline +
bulk publish         custom script     custom elements      original research +
                                                            editorial voice
```

**Your goal:** Be as far right on this spectrum as possible. Every step right reduces your risk significantly.

---

## Output Format

When asked to run an authenticity audit, output results in this format:

```
🛡️ **Authenticity Audit**

**Uniqueness Signals:** [X/7 passing]
- ✅/❌ Original research: [status]
- ✅/❌ Unique angle: [status]
- ✅/❌ Editorial voice: [status]
- ✅/❌ Specific sourcing: [status]
- ✅/❌ Non-obvious connections: [status]
- ✅/❌ Original structure: [status]
- ✅/❌ Human review evidence: [status]

**Red Flags:** [X/4 clear]
- ✅/❌ No template scripting
- ✅/❌ No duplicate publishing
- ✅/❌ No bulk pattern
- ✅/❌ No verbatim copying

**VidRush Checks:** [if applicable]
- ✅/❌ Human-narrated reference
- ✅/❌ Visual variety
- ✅/❌ Custom elements

**Risk Level:** [LOW / MEDIUM / HIGH]
**Recommendation:** [specific action if needed]
```

---

**FacelessOS v4** — Scripts that YouTube can't flag.
