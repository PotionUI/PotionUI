---
title: "Reference: Assistant and language models"
order: 45
category: API
category_order: 90
---

# Reference: Assistant and language models

The chat assistant and the language-model configuration behind it.

Generated from the routes of the running application. Do not edit by hand.

## LLM

### GET /api/llm/configurations/my

**Get My LLM Configurations**

Get LLM configurations assigned to the current user.

Key permission: `read`.

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### POST /api/llm/generate

**Generate LLM Response**

Generate a text response using the configured LLM provider.

Key permission: `generate`.

JSON body, `LLMGenerateRequest`:

- `prompt` (string, required)
- `config_id` (string, optional)
- `image_data` (string, optional)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.

### GET /api/llm/user-assignments/{user_id}

**Get User LLM Assignments**

Get all LLM configurations assigned to a specific user.

Key permission: `read`.

Path parameters:

- `user_id` (string, required)

Response `200`: the standard envelope (`success`, `data`, `message`, `error`). The shape of `data` is not described in the schema.
