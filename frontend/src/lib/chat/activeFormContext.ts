/**
 * Whether the chat's outgoing `form_state.preset`/`form_state.form_data`
 * should report the Generate page's active preset/checkpoint. `contextTab`
 * is read off the shared Generate-page tab store regardless of which page
 * the chat is actually open on, so a chat opened elsewhere (History, a
 * plugin mode, ...) would otherwise leak whatever tab happens to be
 * pinned/active on the Generate page into the model's notion of "the
 * active preset" (`resolve_active_preset_id`/`resolve_active_model_id`,
 * `src/features/llm/tools/builtin/utils.py`) — including memory scope
 * resolution for `write_memory`/reflection. Only the Generate page's own
 * mode ("generation", covering "/" and "/generate") is a context the
 * shared tab store actually describes.
 */
import { DEFAULT_CHAT_MODE } from '$lib/stores/chatSession';

export function isGeneratePageContext(pageModeId: string | null): boolean {
	return pageModeId === DEFAULT_CHAT_MODE;
}
