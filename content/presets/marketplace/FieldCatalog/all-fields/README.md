# Frontend Field Catalog

This custom utility preset is a visual catalog for the core frontend form renderer.
Assign it to a development account, open **Generate**, select **Frontend Field Catalog**,
and use its five tabs to inspect each supported form field without starting a model.
The gallery-only pipeline is intentionally inert.

Coverage is organized as follows:

- **Inputs:** `string`, `textbox`, `number`, `integer`, `stepper`, `slider`, `seed`,
  `boolean`, `checkbox`, `select`, and `checkbox_group`.
- **Pickers:** `resolution`, `carousel`, `image`, `video`, `audio`, `media`, `model`,
  `models`, `lora_picker`, `llm`, `camera_shot`, and `prompt_timeline`.
- **Layout:** `tabs`/`tab`, `header`, `section`, `group`, `accordion`, `gate`, and `row`.
- **Display:** `markdown` and an AlertField index.
- **Alerts:** all six declared variants (`default`, `primary`, `secondary`, `success`,
  `warning`, `danger`), explicit and label-derived titles, description fallback, single-line
  and multiline content, and a valid untitled alert.

`file` is deliberately absent: the backend registers it, but the frontend core field registry
has no `file` component entry, so it currently renders as “Unsupported field type: file”.
This catalog covers every field type that the current frontend core registry actually renders.
