---
title: PotionAI
order: 55
---

# PotionAI

When your administrator has configured a language model, PotionUI gains **PotionAI**, an assistant that helps you write and refine prompts, answer questions, and (when enabled) take actions for you.

The assistant only appears when at least one LLM configuration is set up. If you don't see it, ask your administrator to add one (see **Administration**).

## Chatting with the assistant

PotionAI is a panel you can open from any page: use the **AI Chat** button at the bottom of the sidebar (or its keyboard shortcut), or the **Chat** tab on a phone. Type into the "Ask the AI anything..." box and press Enter (or the send button) to talk to it. It's useful for brainstorming subjects, expanding a rough idea into a fuller prompt, or asking how to describe a look you have in mind.

The header above the conversation has a **model** button to choose which configured LLM answers when more than one is available (models that can see images can also look at attached pictures), a **mode** picker that selects how the assistant behaves (it locks once the conversation has messages), a context meter showing how many tokens the last request used, **New chat**, a **Conversation options** menu (**Rename conversation**, **Export transcript**, **Delete conversation**) and a close button.

The controls that shape each message sit in the action row along the bottom of the composer:

- **Attach image** — send an image along with your message (handy with vision-capable models), with a neighbouring toggle to auto-attach your last generated image.
- **Tools** — with tools on, the assistant can do more than talk: it can take actions such as adjusting a prompt for you, or — when you attach or ask about a phrasebook category (e.g. "check my values in @phrasebook.camera and remove the ones about cats") — listing, adding, or removing phrasebook values on your behalf; additions and removals always wait for your approval before anything changes. Ask it to enhance or expand your current prompt and it creatively rewrites it, grounded in your chosen model and community prompts — thumbs up / thumbs down on the result teaches it the style you prefer, and you can **Apply** the proposed text back into your prompt. It can also edit your Library files — "trim this clip to the first 5 seconds", "resize these to 1080 pixels wide" — and saves the result as a new Library item; replacing an original always waits for your approval. With tools off, it just replies with text. The same button opens a list of the tools available in the current mode, where you can switch individual tools off for the conversation (all are on by default).
- **Memory** — open the panel of notes the assistant keeps about your preferences so it can carry them across the conversation.
- **Pin to tab** — keep the assistant's actions aimed at a specific generation tab.

While the assistant is working you'll see a "Thinking..." indicator, and each reply shows token usage. Replies you like or dislike can be marked with a thumbs up or thumbs down, which helps the assistant learn your taste over time.

## Conversations

Your chats are kept as conversations, so you can leave and come back to a thread. The rail beside the chat lists recent conversations grouped by date; you can search them (and filter by chat type), switch between them, start one with **New conversation**, or delete ones you no longer need. With none yet the rail reads "No conversations yet".

## Built-in slash commands

Typing certain commands in the chat box triggers built-in behaviors:

- **/help** — list these slash commands.
- **/tools** — list the tools the assistant can use.

For saving ordered prompt compositions, see **Prompts Workspace**; its **Segments** tab holds reusable single building blocks and **Segment Templates** holds reusable slot layouts.
