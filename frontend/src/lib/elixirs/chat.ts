import { declareMode, provideContext } from '$lib/chat/pageContext';

export const ELIXIRS_CHAT_MODE = 'elixirs';
export const ELIXIR_CONTEXT_KEY = 'elixir';

export function bindElixirChat(elixirId: string): () => void {
	const undeclare = declareMode(ELIXIRS_CHAT_MODE);
	const unprovide = provideContext(ELIXIR_CONTEXT_KEY, () => ({ elixir_id: elixirId }));
	return () => {
		unprovide();
		undeclare();
	};
}
