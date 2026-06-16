# A Take-away Prompt

One slice of an AI-native workflow: not every capability needs to become a system. Some can be packaged as a **copy-paste prompt** — an editor pastes it into any chatbot (ChatGPT / Claude / Gemini) and it just works, zero install, zero backend.

Note: only **representative** dimensions and tags are shown here; the team actually uses the full 14-dimension, 400-plus-tag version (internal, not public). The point is packaging judgment into a portable prompt, not publishing the taxonomy itself.

## Film auto-tagging prompt

Hit copy top-right, paste into any chatbot, and replace the last two lines with your film's title and plot.

```text
You are a film-tagging assistant. I'll give you a film's title and plot;
pick fitting tags from the dimensions below.

[Rules]
1. A film can span multiple dimensions, with multiple tags per dimension.
2. Prefer tags from the list; if something obviously fits but isn't listed, add it and mark it "(extended)".
3. Pay special attention to the "Emotion" dimension — always pick one if there's a mood signal.
4. Give a one-line overall read first, then one line per tag: "Dimension · Tag — one-line reason".

[Dimensions and representative tags (examples, extend sensibly)]
- Genre: comedy, drama, action, romance, horror, sci-fi, crime, documentary…
- Emotion: healing, tear-jerker, mind-bending, stress-relief, heartwarming, dark, romantic, toxic-romance…
- Theme: revenge, coming-of-age, family bonds, workplace, survival, comeback…
- Setting: prison, courtroom, outer space, underwater, haunted house…
- Era: period, WWII, future, British period…
- Audience: family, teens, adults, seniors…

[Film to tag]
Title: (fill here)
Plot: (fill here)
```

## How to use it

- The plot can be a distributor blurb or any synopsis you find — the fuller it is, the better the tags.
- Want only some dimensions (say emotion + theme)? Add "only output dimensions X and Y" to the rules.
- Want it stricter? Change rule 2 to "use only tags from the list, no inventing".

## Why do it this way

The product itself has full backend auto-tagging (whitelist-validated against the complete taxonomy, written back to the library). But often an editor just wants to "quickly try-tag one film" without opening the backend. Packaging that need as a prompt pushes the AI capability **down to the chatbot in everyone's hands** — that's the spirit of an AI-native workflow: capability follows the person, instead of being locked inside one system.

Want the general principles for "writing judgment into a good prompt"? See [Working with AI](/en/collab).
