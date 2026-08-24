---
title: LLM Assistant
order: 55
---

# LLM Assistant

When your administrator has configured a language model, PotionUI gains an AI assistant that helps you write and refine prompts, answer questions, and (when enabled) take actions for you.

The assistant only appears when at least one LLM configuration is set up. If you don't see it, ask your administrator to add one (see **Administration**).

## Chatting with the assistant

The chat assistant is a panel you open while generating — on desktop it's part of the generation panel, and on mobile it's the **LLM** swipe panel. Type into the "Ask the AI anything..." box and press Enter (or the send button) to talk to it. It's useful for brainstorming subjects, expanding a rough idea into a fuller prompt, or asking how to describe a look you have in mind.

The header above the conversation carries the assistant's identity: a **Model** picker to choose which configured LLM answers when more than one is available (models marked "vision capable" can also look at images), a **Mode** picker that selects how the assistant behaves (it locks once the conversation has messages), small badges for vision (**V**) and an attached image (**IMG**), and token usage. Closing the panel is done from here too.

The controls that shape each message sit in the action row along the bottom of the composer:

- **Attach image** — send an image along with your message (handy with vision-capable models), with a neighbouring toggle to auto-attach your last generated image.
- **Tools** — with tools on, the assistant can do more than talk: it can take actions such as adjusting a prompt for you, or — when you attach or ask about a phrasebook category (e.g. "check my values in @phrasebook.camera and remove the ones about cats") — listing, adding, or removing phrasebook values on your behalf; additions and removals always wait for your approval before anything changes. Ask it to enhance or expand your current prompt and it creatively rewrites it, grounded in your chosen model and community prompts — thumbs up / thumbs down on the result teaches it the style you prefer, and you can **Apply** the proposed text back into your prompt. With tools off, it just replies with text. The same button opens a list of the tools available in the current mode, where you can switch individual tools off for the conversation (all are on by default).
- **Memory** — open the panel of notes the assistant keeps about your preferences so it can carry them across the conversation.
- **Pin to tab** — keep the assistant's actions aimed at a specific generation tab.

While the assistant is working you'll see a "Thinking..." indicator, and each reply shows token usage. Replies you like or dislike can be marked with a thumbs up or thumbs down, which helps the assistant learn your taste over time.

## Sessions

Your conversations are kept as **sessions**, so you can leave and come back to a thread. A sessions list shows recent conversations; you can switch between them, start a new one, or delete ones you no longer need. If there are none yet you'll see "No active sessions".

## Built-in slash commands

Typing certain commands in the chat box triggers built-in behaviors:

- **/help** — list these slash commands.
- **/tools** — list the tools the assistant can use.

For saving ordered prompt compositions, see **Prompts Workspace**; its **Segments** tab holds reusable single building blocks and **Segment Templates** holds reusable slot layouts.
