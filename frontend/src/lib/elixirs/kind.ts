import { toasts } from '$lib/stores/toast';
import type { ToolTab } from '$lib/types/tabs';
import type { ToolKindDefinition, ToolTabWatchContext } from '$lib/tool-tabs/types';
import { ELIXIR_TOOL, elixirIdOf } from './ids';
import { isBrewingStatus } from './tabStatus';

export function elixirClosed(tab: ToolTab): void {
	const elixirId = elixirIdOf(tab);
	if (!elixirId || !isBrewingStatus(tab.status)) return;
	toasts.show('info', `${tab.name} keeps brewing. Reopen it from + or the sidebar.`, {
		action: {
			label: 'Reopen',
			onClick: () => void import('./tab').then((module) => module.openElixirTab({ id: elixirId, name: tab.name }))
		}
	});
}

export function watchElixirTabLazily(context: ToolTabWatchContext): () => void {
	let stopped = false;
	let stop: (() => void) | null = null;
	void import('./watch').then((module) => {
		if (!stopped) stop = module.watchElixirTab(context);
	});
	return () => {
		stopped = true;
		stop?.();
	};
}

export const elixirToolKind: ToolKindDefinition = {
	tool: ELIXIR_TOOL,
	label: 'Elixir',
	description: 'Chain presets, pick what continues',
	icon: 'flask',
	accepts: [],
	order: 30,
	confirmClose: false,
	renamable: false,
	phoneActions: false,
	load: () => import('$lib/components/elixirs/ElixirTab.svelte'),
	menu: () => import('$lib/components/elixirs/ElixirPicker.svelte'),
	watch: watchElixirTabLazily,
	onClosed: elixirClosed
};
