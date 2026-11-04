import type { ToolTab } from '$lib/types/tabs';

export const ELIXIR_TOOL = 'elixir';

export interface ElixirTabState {
	elixirId: string;
}

export function elixirIdOf(tab: ToolTab | null | undefined): string | null {
	if (!tab || tab.tool !== ELIXIR_TOOL) return null;
	if (tab.key) return tab.key;
	const state = tab.state as Partial<ElixirTabState> | null;
	return typeof state?.elixirId === 'string' && state.elixirId ? state.elixirId : null;
}
