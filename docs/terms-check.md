# Terms check (plan R8) - 2026-10-06

What each provider's current terms say about how One Production uses accounts. Re-check before
changing any switching rule, and whenever a provider updates its terms. The machine-readable
verdicts the app enforces live in `app/terms.json` (shipped with the app).

## Questions (plan R8)

1. Scripted / automated use of the official CLI with a **subscription** login.
2. Holding **several accounts** of the same service.
3. **Switching accounts when a limit is reached** ("chaining" - D7).

## Anthropic (Claude logins: Free / Pro / Max)

Sources, read 2026-10-06:
- Consumer Terms of Service, effective 2025-10-08 - https://www.anthropic.com/legal/consumer-terms
- Claude Code "Legal and compliance" - https://code.claude.com/docs/en/legal-and-compliance
- Usage Policy, effective 2025-09-15 - https://www.anthropic.com/legal/aup

1. **Automated use.**
   - The Consumer Terms (s. 3) forbid accessing the Services "through automated or non-human
     means, whether through a bot, script, or otherwise", **"except when you are accessing our
     Services via an Anthropic API Key or where we otherwise explicitly permit it"**.
   - Claude Code is that explicit permission for subscribers. The Claude Code page says Pro/Max
     users are under the Consumer Terms, and "Advertised usage limits for Pro and Max plans
     assume **ordinary, individual usage** of Claude Code and the Agent SDK".
   - It also says: "Anthropic does not permit third-party developers to offer Claude.ai login
     into their own applications, or to **route requests through Free, Pro, or Max plan
     credentials on behalf of their users** ... developers may not collect, store, or
     intermediate Claude.ai credentials or session tokens - sign-in to a Claude account must
     complete through Anthropic's own flow."
   - And it says this "does not ... prevent an end user from signing in to the unmodified
     Claude Code binary with their own Claude subscription".
   - It adds that developers building products with the Agent SDK "should use API key
     authentication".
   - **How One Production fits:** it runs the unmodified Claude Code binary, and every person
     signs in through Anthropic's own flow (`claude auth login`, in a per-account folder). It
     never sees or stores the tokens, and it never acts on anyone else's behalf: friends install
     their own copy with their own accounts (D8).
   - **Risk:** heavy unattended automation on a subscription (team mode, dev dispatch, episode
     runs) is at the edge of "ordinary, individual usage". **Recommendation:** use an API-key
     account for heavy or unattended runs, and keep subscriptions for interactive work.
     *Owner's call.*
2. **Several accounts.**
   - Not forbidden as such.
   - The Usage Policy forbids using a different account to "circumvent a ban", and
     coordinating activity across accounts "to avoid detection or circumvent product
     guardrails".
   - Credentials may not be shared (Consumer Terms s. 2).
3. **Switching at a limit.**
   - Rotating between subscriptions to keep working past one account's limit circumvents the
     usage limits the plan is priced on, and support guidance and reports treat it as
     non-compliant.
   - **Verdict: not allowed.**

## OpenAI (Codex with ChatGPT logins)

Sources, read 2026-10-06:
- Terms of Use (ROW), updated 2026-03-05 - https://openai.com/policies/row-terms-of-use/. The page
  blocks automated reading, so it was quoted via https://conductatlas.com/platform/openai/terms-of-use-row/
- "Using ChatGPT agent in line with our policies" - https://openai.com/policies/using-chatgpt-agent-in-line-with-our-policies/
- Business Terms, via https://conductatlas.com/platform/openai/openai-business-terms/provision/CA-P-059977/prohibition-on-circumventing-usage-limits/

1. **Automated use.**
   - The Terms of Use forbid to "automatically or programmatically extract data or Output".
   - The Codex CLI is OpenAI's own tool for plan subscribers, used as published, so One
     Production does not extract Output any other way.
   - Same rule as Anthropic: the owner's own account only.
2. **Several accounts.** Not explicitly forbidden. Account credentials may not be shared.
3. **Switching at a limit.**
   - OpenAI's policies forbid bypassing "rate limits, restrictions" and circumventing Usage
     Limits.
   - **Verdict: not allowed.**

## Anthropic API keys

- Pay-per-use under the Commercial Terms. Automated and scripted use is what the API is for.
- Moving work to an API key when a subscription runs out is ordinary use, not chaining, and it
  is the default order D7 asks for (subscription → API key).
- Limited only by the owner's own spend caps (R10).

## Result for One Production

| Switch | Allowed? | App rule |
|---|---|---|
| Claude login → another Claude login at a limit | **No** | blocked (`accounts.switch_allowed`) |
| Codex login → another Codex login at a limit | **No** | blocked |
| Any subscription → a different service (Claude ↔ Codex) | Yes - continuing work on another product the owner also uses | allowed |
| Any subscription → the owner's API key | Yes (D7 default order) | allowed, within spend caps |

- **Subscription chaining stays disabled.** The owner's "allow chaining" setting cannot switch it
  on while `app/terms.json` marks a provider "not_allowed" (automatic test:
  `tests/p12/test_terms.py`).
- **Owner's decisions** (manual check P12):
  - [ ] read this page and confirm the verdicts;
  - [ ] decide whether heavy, unattended runs (team mode, dispatch, episodes) should use an API
        key instead of a subscription login.

*This is a reading of public terms for the owner's own use, not legal advice.*
