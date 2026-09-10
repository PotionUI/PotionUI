/**
 * Gating for the chat's outgoing `form_state.preset`/`form_state.form_data`.
 *
 * `contextTab` is read off the shared Generate-page tab store regardless of
 * which page the chat is actually open on, so reporting it unconditionally
 * would leak whatever tab happens to be pinned/active on the Generate page
 * into the model's notion of "the active preset"
 * (`resolve_active_preset_id`/`resolve_active_model_id`,
 * `src/features/llm/tools/builtin/utils.py`) — including memory scope
 * resolution for `write_memory`/reflection.
 *
 * The two fields are gated differently:
 * - `preset` follows the CHAT SESSION's own mode (`chatSession.mode`), not
 *   the page it happens to be open on: a session in the generation mode
 *   (the default — covers a fresh conversation and one restored from
 *   History) stays tied to the Generate tab's active preset wherever it's
 *   opened from, since the backend scopes memory reflection to that preset.
 *   A plugin-mode session (lora-dataset, comfyui-import, ...) never gets it.
 * - `form_data` is live form content, only meaningful while the chat is
 *   actually open on the Generate page itself, so it stays gated on the
 *   page's own mode.
 */
import { DEFAULT_CHAT_MODE } from '$lib/stores/chatSession';

export function isGeneratePageContext(pageModeId: string | null): boolean {
	return pageModeId === DEFAULT_CHAT_MODE;
}

/**
 * Whether the chat should report the Generate tab's active preset as
 * `form_state.preset`: only for a session in the generation mode. A
 * plugin-mode session must never report a preset just because the chat
 * drawer happens to be open over the Generate page. (A message can't be
 * sent before the session mode is resolved - `sendMessage` requires a
 * selected config, which `loadAllData` sets after resolving the mode.)
 */
export function isGenerationPresetContext(sessionModeId: string | null): boolean {
	return sessionModeId === DEFAULT_CHAT_MODE;
}
