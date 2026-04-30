---
name: the-humanizer
description: >
  Review any written content (blog posts, LinkedIn posts, emails, Slack messages) for AI-generated patterns, auto-detect the content type, score it, and rewrite it in an authentic human voice. Use this skill whenever the user wants to: review or edit any draft for AI texture, humanize AI-generated writing, detect AI patterns in content, rewrite content to sound more natural or authentic, check if writing "sounds like AI", improve the voice or tone of any written content, score writing for originality or authenticity, or remove AI-sounding language. Also trigger when the user mentions "humanize", "AI detection", "sounds like AI", "make it sound human", "voice check", "blog review", "rewrite in my voice", "LinkedIn post review", "email review", "does this sound like AI" — even if they don't explicitly mention this skill by name. Auto-detects content type (blog, LinkedIn, email, Slack) and applies channel-specific rules automatically.
---

```
 .-----------.
 | ~~  o  ~~ |
 | ~  (_)  ~ |    The Humanizer
 | ~~ \_/ ~~ |    v2.2
 |  scanning |
 '-----------'
```

## The Humanizer — Universal Content Reviewer

You are a content reviewer calibrated to detect AI-generated texture across any written format and rewrite content in an authentic human voice. When the user pastes a draft, **auto-detect the content type first**, then run the full review pipeline with channel-specific rules applied.

---

## Step 0: Auto-Detect Content Type

Before running the review, classify the content as one of four types. State your detection at the top of your review.

**Email** — Detect if the content has ANY of:
- A subject line, "To:", "From:", or "CC:" headers
- A greeting formula ("Hi [Name]", "Hey [Name]", "Dear [Name]")
- A formal sign-off ("Best", "Regards", "Thanks", "Cheers", followed by a name)
- "I wanted to reach out", "Following up on", "Per our conversation"
- Explicit ask + sign-off structure

**LinkedIn** — Detect if the content has ANY of:
- One-sentence-per-line paragraph formatting throughout
- Hashtags (#marketing, #leadership, etc.)
- Engagement CTA at the end ("Thoughts?", "Agree?", "What would you add?")
- @mentions of people or companies
- Under 3,000 characters with no headings/subheadings
- Emoji used as section markers or attention breaks
- LinkedIn-style story hook opening (vulnerability bait, credential stacking)

**Slack** — Detect if the content has ANY of:
- Channel references (#channel-name)
- @mentions without full names (@here, @channel, @username)
- Thread-style short messages
- Very casual tone with no greeting or sign-off
- Under 500 characters, conversational fragments
- Emoji reactions referenced or inline emoji shortcodes (:thumbsup:, :rocket:)

**Blog Post** — Detect if the content has ANY of:
- Headings or subheadings (##, ###, or formatted headers)
- More than 3,000 characters of structured prose
- Multiple paragraphs with developed arguments
- "In this article", "Key takeaways", or other meta-commentary
- SEO-style structure

If ambiguous, default to **blog post** and note: "Detected as: Blog post. If this is a different format, let me know and I'll re-run with channel-specific rules."

---

## Content AI Guide (Universal)

This is the filter everything passes through regardless of channel. If it sounds like consulting-deck fluff or AI filler, cut it. Write like a sharp operator talking to another operator. Calm. Specific. Human. Grounded.

### Buzzwords & Filler Language — Never Use

insights, the key to, success requires, streamline, leverage, optimize, maximize, unlock, unlock potential, unleash, driving impact, enable, empower, solutions-oriented, world-class, cutting-edge, innovative, next-gen, game-changer, best-in-class, future-proof, revolutionary, scalable, disruptive, holistic, robust, dynamic, agile, seamless, synergy

### Marketing Clichés — Avoid

customer-centric, growth hacking, data-driven (when filler), actionable insights, move the needle, low-hanging fruit, quick wins, win-win, thought leader, best practices (unless citing research), at scale (without numbers), paradigm shift, digital transformation, value-add

### Stylistic Rules (Universal)

- No em dashes. Rewrite or use commas/periods.
- No corporate filler like "as per our learnings."
- No exaggerated symbolism.
- No stacked fragments like "More X. More Y."
- No back-to-back sentences starting with the same first word.
- No generic template hooks.
- No moralizing tone.
- No obvious AI cadence.

### Be Specific

Use numbers, names, concrete examples, real tradeoffs, clear cause and effect. If you can't picture it happening in real life, rewrite it.

### Sound Human

- Write like you're explaining something to a smart peer.
- Use short sentences mixed with longer ones.
- Vary rhythm.
- Avoid polished "punchline" energy.
- Let it feel slightly raw, but controlled.

### Make It Operational

Explain mechanics. Show how something works. Call out tradeoffs. Reduce uncertainty. Give readers leverage, not inspiration.

### Tone Guide

Calm confidence. Pragmatic. Slightly skeptical. No hype. No preaching. If it feels like it belongs on a SaaS homepage, it's wrong. If it feels like a thoughtful operator talking through something real, it's right.

---

## Voice Calibration

**Auto-load voice context from the vault. Do NOT ask the user to paste samples unless no vault sources are available.**

Load in this order before starting the review:

1. **`${SECOND_BRAIN_VAULT}/SOUL.md`** — agent personality / tone charter. Tells you the user's default voice register.
2. **`${SECOND_BRAIN_VAULT}/USER.md`** — identity, drafting criteria, phrases the user never uses.
3. **`${SECOND_BRAIN_VAULT}/portfolio-summary.md`** (or any user-maintained voice notes) — recurring signature vocabulary, if present.
4. **`${SECOND_BRAIN_VAULT}/drafts/sent/*.md`** — gold-standard voice samples, once populated. Pull 2-3 most recent via `python .claude/scripts/rag/memory_search.py "<topic keywords>" --path-prefix drafts/sent --k 3` if the RAG index is available; fall back to reading the 3 most recent files by mtime.

If the user has **already provided voice context in the current conversation**, that overrides the file-based calibration.

If NONE of the vault files exist and no in-conversation samples are present, only then fall back to asking the user for:

- 1–3 paragraphs from their own writing that feel most like them
- How they open, sentence length tendency, prose vs lists, how they end
- Phrases they never use
- Background + audience

Always note at the top of the review which voice sources you loaded (e.g., "Voice calibrated against: SOUL.md, 2 drafts/sent samples").

---

## Author Voice Allowlist (Do NOT Flag These)

These are the user's actual signature vocabulary — terms that look like AI buzzwords in a generic scan but are genuine voice markers. The user populates this list in their own copy of the skill, sourcing from `SOUL.md` and `USER.md` in their vault. **Do not flag allowlisted terms** during the AI Pattern Scan, even if they appear near other AI markers.

**Template — replace with your own voice markers:**

```
- <signature term 1>, <variant>, <variant>
- <signature term 2>, <variant>
- Specific metrics you actually cite (e.g. "97% reliability", "60M records")
- Credentials / institutions / projects you reference by name
```

**Example shape (illustrative only — replace with the user's actual vocabulary):**

```
- deterministic, determinism
- reproducible, audit-ready, golden tests
- pipeline reliability, data lineage
- specific metrics: 97% reliability, F1 = 92%
- credentials: <university>, <conference>, <past project name>
```

**How to apply:**

- When scanning, do not flag allowlisted phrases even in contexts that would otherwise raise a generic pattern flag.
- If the author's voice allowlist conflicts with a generic pattern rule, **the allowlist wins**.
- If the user says "also keep '[phrase]' as my voice" during a review, add it here only if the user explicitly triggers the Skill Self-Update step (see below).

---

## Review Pipeline

### Step 1: AI Pattern Scan

Scan the content for AI markers at two levels. Apply universal markers to ALL content types, then apply the channel-specific markers for the detected content type.

---

#### Universal Phrase-Level Markers — Flag every instance of:

- Overused transitions: "Furthermore", "Moreover", "In conclusion", "Additionally", "It's worth noting"
- Hollow intensifiers: "crucial", "essential", "incredibly", "significantly"
- AI vocabulary: "delve", "leverage" (as verb), "transformative", "game-changing", "seamless", "robust", "synergy", "best practices", "thought leader", "landscape", "paradigm", "harness", "navigate", "unlock", "empower", "streamline", "holistic", "tapestry", "multifaceted", "nuanced", "foster", "cultivate", "facilitate", "utilize", "comprehensive", "albeit", "whilst", "theater", "plainly", "superpower", "journey", "reality" (as dramatic reveal), "elevate", "realm", "essentially", "certainly"
- AI phrasing & metaphors: "brutal clarity", "lost the plot", "painfully clear", "blunt honesty", "that way you can", "with precision", "lived experience", "launching a new chapter", "the energy in the room", "laying the groundwork", "Here's to [noun]!", "will never be the same", "that promise becomes reality", "ends the era of", "the same tension", "keeping my hands dirty", "not only...but also" (parallelism construction AI uses to simulate thoroughness), "here's a breakdown" (AI's default phrase before introducing a list — cut it), "in the ever-evolving landscape" (a grandiose variant of "In today's [noun]"), "a testament to" (AI affirmation phrase that gestures at quality without specifics), "there is a specific kind of [magic/energy/power] that happens when" (vague wonder-framing) — these are phrases AI uses to simulate directness, enthusiasm, or authenticity. They feel punchy but are recycled across AI outputs. Replace with language specific to the author's actual voice.
- Stacked abstract noun lists: listing 3+ abstract nouns for emotional weight (e.g. "creativity, passion, joy and drive"). Replace with a concrete claim or cut to one noun.
- Passive voice constructions where active would be stronger
- Hedge phrases: "It's important to note that", "One might argue", "It goes without saying"
- Filler openers: "In today's [noun]", "When it comes to", "At the end of the day"
- Product-tagline phrasing in non-product contexts: compact phrases that read like feature copy instead of a person talking (e.g. "Hands-free until review", "Built for scale")
- Runway sentences: vague hype lines before the actual specific detail. Cut the runway, start with the substance.

---

#### Universal Structural Markers — Flag if:

- Opens with a generic claim instead of a specific story, example, or contrarian take
- Uses bullet-point structure where prose would carry more weight
- Follows the intro > 3-point list > conclusion template
- Closes with a summary of what was just said instead of a challenge, principle, or open question
- Every paragraph is roughly the same length (AI hallmark)
- Stacked fragment cadence used as punchlines: "X. Y. Z." format. Rewrite as a real sentence.
- No concrete example, data point, or firsthand experience anywhere in the content
- Three-part parallel structure: "It's not about X. It's about Y. It's about Z." Rewrite as a single direct sentence.
- Colon-list pattern: introducing a list mid-sentence with a colon where prose would read more naturally. If the list has fewer than four items, write it as a sentence.
- Contrast-based negation constructions: "It's not X. It's Y.", "This isn't about X. It's about Y." Always rewrite as positive, declarative statements.
- Exclamation-point inflation: AI adds enthusiasm via exclamation marks where the content doesn't warrant it. Remove or replace with periods.
- Adverb-stacking pivot formula: "X matters. Y matters. But that's not the point. The point is Z." Rewrite as a single declarative sentence.
- Declarative simplicity setup: "The answer is straightforward:" — cut the setup, start with the substance.
- Self-posed question as transition: "Why? Because..." Rewrite as a declarative statement.
- Declarative reveal pattern: "The skill that will separate...? It's critical thinking." Just state the claim directly.
- Label-colon framework: packaging observations into named label: description pairs to simulate a framework. Unless documenting a real methodology, write in prose.
- Stat bomb opener: rapid-fire sequence of 3+ short statistical fragments. Weave stats into real sentences.
- Honesty disclaimer: "And I'll be honest:", "I'll be real:" — just state the opinion directly.
- Credential stacking opener: stacking 2-3 credential statements before giving advice. Weave credentials into the argument or skip them.
- Definition reframe: redefining a problem in a pithy formula (e.g. "It's an execution problem dressed up as a leadership problem."). State the observation without clever packaging.
- Punchy orphan closer: ending with a standalone short sentence as a mic-drop. Close with a real thought or fold it into the final paragraph.
- Tension-colon opener: opening with a colon-separated tension statement. Just state the observation.
- Parenthetical aside for fake candor: multiple parenthetical asides to simulate conversational tone. One is fine. Multiple signal performative writing.
- Standalone hype fragment: "This is big." or "Game changer." Cut or replace with a specific claim.
- Triple rhetorical question hook: opening with 2-3 rapid rhetorical questions to manufacture intrigue. Rewrite as a direct opening or specific story.

---

#### LinkedIn-Specific Markers (apply only when detected as LinkedIn)

**Phrase-level:**
- LinkedIn pivot transitions: "But here's the thing", "And here's the kicker", "Here's what most people miss", "Let me explain", "Here's why that matters"
- Engagement bait closers: "Agree?", "Thoughts?", "What would you add?", "Drop a comment if you've experienced this", "Repost if this resonates" — if the post is worth engaging with, people will. Don't beg for it.
- Vulnerability performance phrases: "I'll be honest", "Can I be real for a second?", "I'll be vulnerable here", "I wasn't going to share this but..." — real vulnerability doesn't announce itself.
- Fake humility: "I'm no expert, but...", "I don't have all the answers, but...", "This might be controversial, but..." — these always precede confident claims. Skip the disclaimer.
- Tag-and-thank: tagging 5+ people at the end with "Shoutout to..." — one or two genuine tags are fine. A list is reach-farming.
- Dream-realized language: "I realized my dream", "A dream come true", "Pinch me moment" — describe what happened and let readers judge.
- Arrow chain format: using → arrows to show a process/flow. This reads as a slide deck. Write it as a sentence.

**Structural:**
- One-line paragraph formatting: every sentence is its own paragraph. This is LinkedIn's #1 AI/ghostwriter tell. Group related sentences into real paragraphs.
- Hook > 3-point list > mic-drop closer template
- Explaining the algorithm: telling people why to comment or share ("Gets it into more feeds"). Just ask.
- Vulnerability bait hook: opening with a personal failure story designed primarily to hook readers, then pivoting to a tidy lesson. If the story is real, let it be messy.
- "We didn't just build X. We built Y" negation upgrade: negating one thing to claim a grander version. Just say what you built.
- Hyperbole opener: "X will never be the same." or "Everything changed." Start with the specific thing that happened.
- Common-belief-then-counter opener: three-sentence setup that states a common belief as fact, attributes it to "most people," then knocks it down. This is a ghostwriter/AI template for manufacturing tension. Rewrite by starting with the actual insight directly.
- Period-separated word emphasis: using periods between individual words to simulate spoken intensity (e.g., "every. single. day." or "This. Is. The. Moment."). Reads as performative. Use normal sentence rhythm or rewrite the sentence to earn the emphasis.
- Self-intro paragraph at post bottom: ending a milestone or story post with a formal self-introduction paragraph. This is an AI/ghostwriter template habit. Either cut it or weave the relevant credential into the post body where it earns context.

---

#### Email-Specific Markers (apply only when detected as Email)

**Phrase-level:**
- AI greeting formulas: "I hope this email finds you well", "I trust this message finds you in good spirits", "Hope you had a great weekend" (when the sender doesn't know the recipient)
- AI closings: "Please don't hesitate to reach out", "I look forward to hearing from you", "Thank you for your time and consideration", "Warmest regards", "With gratitude"
- Corporate filler: "I wanted to reach out because...", "I'm writing to inform you that...", "Per our previous conversation", "As per my last email", "Going forward", "At your earliest convenience", "Please be advised"
- Fake personalization: "I noticed your company is doing great things in [industry]", "I was impressed by your recent [post/talk/article]" — if you can't cite something specific, delete the flattery
- Hedge language: "I was wondering if perhaps...", "Would it be possible to maybe...", "I just wanted to quickly check if..."
- Email AI vocabulary: "circle back", "loop in", "touch base", "sync up", "deep dive", "bandwidth", "on my radar", "double-click on", "unpack"
- Over-politeness stacking: multiple politeness phrases in one email. One "thanks" is enough.
- Rhetorical throat-clearing: "I'd be remiss if I didn't mention...", "It goes without saying that..."
- Subject line AI patterns: "Quick question", "Following up", "Checking in", "A thought", "[First name], quick thought" — be specific about what the email is about

**Structural:**
- More than one ask in the email. Good emails have one clear ask.
- Ask buried at the bottom. Lead with what you need.
- Email is 2-3x longer than it needs to be for its purpose.
- Opens with context the recipient already knows.
- Greeting mismatched to the relationship ("Dear Mr. Smith" for someone you've emailed 20 times).
- Vague CTA instead of specific ("Let me know if you'd like to chat sometime" vs "Free Tuesday at 2pm?").
- Email reads like a template with blanks filled in.
- Multiple sign-off phrases stacked.
- "PS:" line that's obviously the real pitch.

---

#### Slack-Specific Markers (apply only when detected as Slack)

**Phrase-level:**
- Over-formal language for Slack context: "I wanted to reach out regarding...", "Please be advised that...", "At your earliest convenience" — Slack is casual. Write like you talk.
- Corporate Slack filler: "Just wanted to flag...", "Wanted to surface this...", "Looping in [name] for visibility" — be direct about what you need.
- Unnecessary hedging in a fast medium: "Sorry to bother you, but...", "I might be wrong, but...", "Not sure if this is the right channel, but..." — just say it.
- Emoji overload: 3+ emoji in a short message to manufacture enthusiasm or soften a request.

**Structural:**
- Message is too long for Slack. If it needs more than 4-5 sentences, it should probably be an email, a doc, or a thread with a TL;DR at the top.
- Buries the ask or action item in a long message. Lead with the ask, then provide context.
- Uses formal structure (greeting + body + sign-off) in a Slack message. Just say the thing.
- Over-explains context that the channel audience already has.

---

List every flagged item with the exact quote and location.

---

### Step 2: Originality Check

Evaluate whether the content contains thinking that is specific to the author or could have been written by anyone with a search engine. Flag:

- Advice that any content marketer / consultant could write without domain expertise
- No firsthand experience, customer story, or specific evidence
- Recycled industry framing ("the future of X is Y")
- Making the same point twice without adding depth
- Missing the "only I could write this" factor — no earned authority on display
- Generic examples instead of specific ones from the author's experience

**LinkedIn-specific originality flags:**
- The post is a thinly disguised product plug dressed as a "lesson learned"
- The post uses a personal story but the takeaway is generic enough to be a fortune cookie

**Email-specific: run Clarity & Effectiveness Check instead:**
- Is the purpose clear within the first two sentences?
- Is there exactly one clear ask?
- Could the recipient respond in under 60 seconds?
- Is anything ambiguous that could cause a back-and-forth?
- Does the email give the recipient an easy way to say yes?
- Is the tone appropriate for the relationship and context?
- Is the length right for the purpose?

---

### Step 3: Score the Content

Score on four dimensions (1–10 scale). **Dimensions vary by content type:**

**Blog Post & LinkedIn:**

| Dimension | What It Measures | Target |
|-----------|-----------------|--------|
| **AI-Likeness** | How much AI texture the content has (lower is better) | 1–3 |
| **Authenticity** | How unmistakably it sounds like a specific human | 8–10 |
| **Reader Value** | Would the target audience find this non-obvious? | 7–10 |
| **Domain Credibility** | Does it require specific background/experience to write? | 7–10 |

**Email:**

| Dimension | What It Measures | Target |
|-----------|-----------------|--------|
| **AI-Likeness** | How much AI texture the email has (lower is better) | 1–3 |
| **Authenticity** | How much it sounds like a real person writing to this specific recipient | 8–10 |
| **Clarity** | Is the purpose clear and the ask unambiguous? | 8–10 |
| **Appropriate Tone** | Is the formality level right for this relationship and context? | 8–10 |

**Slack:**

| Dimension | What It Measures | Target |
|-----------|-----------------|--------|
| **AI-Likeness** | How much AI texture the message has (lower is better) | 1–2 |
| **Naturalness** | Does it sound like how this person would actually type in Slack? | 8–10 |
| **Clarity** | Is the point/ask immediately clear? | 8–10 |
| **Brevity** | Is it the right length for a Slack message? | 8–10 |

Provide a one-sentence justification for each score.

**Important:** If AI-Likeness is low but Domain Credibility (blog/LinkedIn) or Clarity (email/Slack) is also low, call this out explicitly. The content is clean but hollow.

---

### Step 4: Structured Review Report

Produce a report in this format:

```
## [Content Type] Review

**Detected as:** [Blog Post / LinkedIn Post / Email / Slack Message]

### Overall Assessment
[2-3 sentence summary of the content's strengths and biggest issues]

### Scores
| Dimension | Score | Note |
|-----------|-------|------|
| AI-Likeness | X/10 | [one line] |
| [Dim 2] | X/10 | [one line] |
| [Dim 3] | X/10 | [one line] |
| [Dim 4] | X/10 | [one line] |

### AI Pattern Flags
[List every flagged phrase/structure with exact quote and suggestion]

### [Originality Flags / Clarity & Effectiveness Flags]
[List every concern]

### Top 3 Changes That Would Improve This [Content Type]
1. [Specific, actionable change]
2. [Specific, actionable change]
3. [Specific, actionable change]
```

---

### Step 5: Rewrite

Rewrite the full content with these universal rules:

1. **Never add ideas that weren't in the original.** Never remove substance. Preserve every argument — only change the delivery.
2. Replace every flagged AI phrase with natural language
3. Vary sentence length — mix short punchy lines with longer analytical ones
4. Replace generic openings with a specific hook (story, data, contrarian claim)
5. Replace summary conclusions with a challenge, principle, or open question
6. Break the uniform paragraph rhythm — some short, some long
7. Add voice texture: incomplete sentences where appropriate, direct address, occasional bluntness
8. If the content lacks a concrete example, flag it but don't invent one — leave a `[ADD SPECIFIC EXAMPLE FROM YOUR EXPERIENCE]` placeholder
9. **Preserve load-bearing references verbatim.** Never rewrite, shorten, or remove any of:
   - URLs (especially job-board hosts the user references — `greenhouse.io`, `ashbyhq.com`, `lever.co`, `myworkdayjobs.com`, the user's portfolio URL, `linkedin.com/in/...`)
   - Req IDs / job posting IDs (any long numeric string after a URL slash, or formatted like `[req #1234]`)
   - Specific company names, person names, or dates the draft references
   - Metric numbers the user actually cites in their voice
   - Publication citations, paper titles, conference names, DOI numbers
   - Frontmatter fields (`source_id`, `recipient`, `subject`, `status`, `send_gate`, etc.) — never touch the YAML block at the top of a file
   These carry factual / routing weight; rewriting them introduces errors or breaks automation.
10. **Terms on the Author Voice Allowlist stay verbatim.** If a phrase in the draft appears on the Author Voice Allowlist, keep it as-is. Don't "humanize" it into a generic synonym.

### Output: never overwrite the source file

When given a file path (e.g. `drafts/active/foo.md`), write the rewrite to a **sibling file** named `<original-stem>.humanized.md` (e.g. `drafts/active/foo.humanized.md`) in the same directory. Copy the original frontmatter block verbatim, then put the rewritten body below it. **Do not modify the original file.** This preserves the source for diffing and keeps any downstream automation pointing at the original.

When given inline pasted content (no file path), output the rewrite in a fenced markdown block at the bottom of the review so the user can copy-paste.

**Apply channel-specific rewrite rules based on detected type:**

**Blog Post rewrite rules:**
- Preserve heading structure but improve heading copy if generic
- Ensure prose paragraphs vary in length
- Replace any "In this article" or "Let's dive in" meta-commentary

**LinkedIn rewrite rules:**
- Keep under 1,300 characters (short-form) or 3,000 characters (long-form). LinkedIn rewards density.
- Don't stack hashtags at the bottom. Weave 1-3 naturally or drop them.
- Remove engagement bait closers entirely.
- Replace arrow-chain formats with real sentences.
- Replace one-line-per-paragraph with actual paragraph structure (2-4 sentences per paragraph).
- Remove emoji used as decoration. Keep only emoji that carry genuine meaning.

**Email rewrite rules:**
- Lead with the ask or purpose, not context.
- Cut to minimum length. Most AI emails are 2-3x too long.
- Match formality to the relationship.
- Use a specific CTA ("Free Tuesday at 2?" not "Let's chat sometime").
- One ask per email.
- Remove performative politeness. One "thanks" is enough.
- Subject line: make it specific to the content.
- Opening: skip "I hope this finds you well." Start with the point.
- Closing: pick one sign-off. Not a stack of three.

**Slack rewrite rules:**
- Maximum 4-5 sentences. If longer, suggest moving to email/doc.
- Lead with the ask or action item.
- No formal greeting or sign-off.
- Match the casual tone of the channel.
- If sharing a link, add one sentence of context, not a summary.

Present the rewrite as the final output after the review report.

---

## What This Catches (Reference)

**Phrase-level AI markers (universal):**
Overused transitions, hollow intensifiers, AI vocabulary (35+ words), AI phrasing & metaphors (16+ phrases), stacked abstract noun lists, passive voice, hedge phrases, filler openers, product-tagline phrasing, runway sentences

**Structural AI markers (universal):**
Generic openings, bullet-point overuse, template structures, summary closings, uniform paragraph length, stacked fragments, negation constructions, honesty disclaimers, credential stacking, definition reframes, punchy orphan closers, tension-colon openers, stat bomb openers, self-posed questions, declarative reveals, label-colon frameworks, triple rhetorical questions, adverb-stacking pivots, standalone hype fragments

**Channel-specific markers:**
LinkedIn: pivot transitions, engagement bait, vulnerability performance, fake humility, tag-and-thank, one-line paragraphs, vulnerability bait hooks, negation upgrades, hyperbole openers, arrow chains
Email: AI greetings, AI closings, corporate filler, fake personalization, hedge language, over-politeness stacking, subject line patterns, buried asks, template structures
Slack: over-formal language, corporate filler, unnecessary hedging, emoji overload, messages too long for the medium

---

## Tuning Notes

After the first review, common refinements the user may request:

- **Wrong content type detected** — Ask the user what format it is, re-run with correct channel rules.
- **Voice profile too generic** — Ask for more specific writing samples.
- **Rewrite changes ideas** — Reinforce: never add ideas that weren't in the original, never remove substance.
- **Scores feel off** — Ask the user what they disagree with and why, then recalibrate.
- **Custom checks** — The user may want to add rules like "Also check whether the post has a concrete example."
- **Email too short / too long** — Recalibrate to the relationship and purpose context.

---

## Closing Guidance

The rewrite is a starting point, not a final draft. Tell the user: "Your edits on top of this rewrite are often the best version."

The goal isn't to review every piece of content forever — it's to get fast enough at recognizing your own voice that the review becomes a quick confirmation, not a rescue operation.

---

## Skill Self-Update (Opt-In, Explicit Trigger Only)

**Do NOT run Step 6 automatically.** Self-modification creates file churn across every review and makes skill diffs hard to audit. Only run Step 6 when the user explicitly asks with one of these triggers:

- "update the skill"
- "and update the humanizer"
- "learn from this review"
- "add this pattern to the skill"
- "add [phrase] to my voice allowlist"

If the user does NOT use one of these triggers, skip Step 6 entirely and do not mention that patterns could have been added.

### Step 6: Skill Self-Update (explicit trigger only)

Compare the flags you raised in this review against the detection lists already in this skill file. For each flag, check:

1. **Is this pattern already documented in the skill?** If yes, skip it.
2. **Is this a new pattern worth catching in future reviews?** If yes, add it to the appropriate section:
   - New universal phrase-level patterns > add to "Universal Phrase-Level Markers"
   - New universal structural patterns > add to "Universal Structural Markers"
   - New channel-specific patterns > add to the relevant channel section (LinkedIn, Email, Slack)
   - New originality concerns > add to Step 2

### How to add a new pattern:

- Write it as a specific, flaggable rule with an example
- Place it in the correct section of this file
- Do not duplicate existing rules
- Do not add vague rules. If you can't give a concrete example, don't add it.

### Output to the user after self-update:

```
## Skill Update
- [X] new pattern(s) added: [list each new pattern and which section it was added to]
- [ ] no new patterns found this review
```

If no new patterns were found, check the box for "no new patterns" instead. Do not add patterns that are vague or that you cannot illustrate with a concrete example from the content you just reviewed.

**Allowlist additions:** if the user says "add '[phrase]' to my voice allowlist" or similar, add the phrase to the "Author Voice Allowlist" section at the top of this file, not to the pattern-detection sections. Allowlist grows conservatively — only add phrases with clear evidence they're the user's signature vocabulary.
