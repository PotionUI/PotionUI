import { logger, getErrorMessage } from '$lib/utils/logger';
import { writable, derived, get } from 'svelte/store';
import { api } from '$lib/services/api/index';

export interface KeybindingAction {
	actionId: string;
	key: string | null;
	modifiers: string;
	label: string;
	category: string;
	context: string;
	description?: string;
	enabled: boolean;
	isCustom: boolean;
	handler?: () => void;
}

interface KeybindingsState {
	bindings: KeybindingAction[];
	helpPanelOpen: boolean;
	loaded: boolean;
}

const initialState: KeybindingsState = {
	bindings: [],
	helpPanelOpen: false,
	loaded: false
};

function createKeybindingsStore() {
	const { subscribe, set, update } = writable<KeybindingsState>(initialState);

	// Handlers registered before their binding has loaded (e.g. a layout-persistent
	// component's onMount runs before the layout kicks off loadBindings). We stash
	// them here and re-attach on load so early registrations aren't lost.
	const pendingHandlers = new Map<string, () => void>();

	return {
		subscribe,

		async loadBindings() {
			try {
				const response = await api.getKeybindings();
				if (response.success && response.data) {
					const apiBindings = response.data.keybindings || response.data;
					const bindings: KeybindingAction[] = apiBindings.map((b: any) => ({
						actionId: b.action_id,
						key: b.key,
						modifiers: b.modifiers || '',
						label: b.label,
						category: b.category,
						context: b.context,
						description: b.description || undefined,
						enabled: b.enabled,
						isCustom: b.is_custom,
						handler: undefined
					}));
					update((state) => ({
						...state,
						bindings: bindings.map((nb) => {
							const existing = state.bindings.find((eb) => eb.actionId === nb.actionId);
							const handler = existing?.handler ?? pendingHandlers.get(nb.actionId);
							return handler ? { ...nb, handler } : nb;
						}),
						loaded: true
					}));
				}
			} catch (err) {
				logger.error('Failed to load keybindings:', err);
			}
		},

		registerHandler(actionId: string, handler: () => void) {
			pendingHandlers.set(actionId, handler);
			update((state) => ({
				...state,
				bindings: state.bindings.map((b) =>
					b.actionId === actionId ? { ...b, handler } : b
				)
			}));
		},

		unregisterHandler(actionId: string) {
			pendingHandlers.delete(actionId);
			update((state) => ({
				...state,
				bindings: state.bindings.map((b) =>
					b.actionId === actionId ? { ...b, handler: undefined } : b
				)
			}));
		},

		async updateBinding(actionId: string, key: string | null, modifiers: string) {
			try {
				await api.updateKeybinding(actionId, key, modifiers);
				await this.loadBindings();
			} catch (err) {
				logger.error('Failed to update keybinding:', err);
			}
		},

		async resetBinding(actionId: string) {
			try {
				await api.resetKeybinding(actionId);
				await this.loadBindings();
			} catch (err) {
				logger.error('Failed to reset keybinding:', err);
			}
		},

		async resetAll() {
			try {
				await api.resetAllKeybindings();
				await this.loadBindings();
			} catch (err) {
				logger.error('Failed to reset all keybindings:', err);
			}
		},

		openHelp() {
			update((state) => ({ ...state, helpPanelOpen: true }));
		},

		closeHelp() {
			update((state) => ({ ...state, helpPanelOpen: false }));
		},

		toggleHelp() {
			update((state) => ({ ...state, helpPanelOpen: !state.helpPanelOpen }));
		},

		getBinding(actionId: string): KeybindingAction | undefined {
			const state = get({ subscribe });
			return state.bindings.find((b) => b.actionId === actionId);
		},

		reset() {
			set(initialState);
		}
	};
}

export const keybindingsStore = createKeybindingsStore();

/** Format a binding's modifiers+key into the display form used by the shortcuts
 * modal and shortcut hints elsewhere (e.g. "Ctrl+K", "⌘+/"). Single shared
 * formatter so every surface renders a binding identically. */
export function formatKeyCombo(modifiers: string, key: string): string {
	const parts: string[] = [];
	if (modifiers) {
		for (const mod of modifiers.split(',')) {
			const trimmed = mod.trim();
			if (trimmed === 'ctrl') parts.push('Ctrl');
			else if (trimmed === 'shift') parts.push('Shift');
			else if (trimmed === 'alt') parts.push('Alt');
			else if (trimmed === 'meta') parts.push('⌘');
		}
	}
	parts.push(key.length === 1 ? key.toUpperCase() : key);
	return parts.join('+');
}

export const isHelpOpen = derived(keybindingsStore, ($store) => $store.helpPanelOpen);

export const keybindingsByCategory = derived(keybindingsStore, ($store) => {
	const grouped: Record<string, KeybindingAction[]> = {};
	for (const binding of $store.bindings) {
		if (!grouped[binding.category]) {
			grouped[binding.category] = [];
		}
		grouped[binding.category].push(binding);
	}
	return grouped;
});

/** actionId -> formatted shortcut (e.g. "Ctrl+K"), for every enabled binding
 * that has a key. Buttons throughout the app read this to feed a Tooltip's
 * `kbd` prop — a missing entry (unloaded, keyless, or disabled) naturally
 * hides the chip since `kbd={$shortcutLabels['x']}` is then `undefined`. */
export const shortcutLabels = derived(keybindingsStore, ($store) => {
	const labels: Record<string, string> = {};
	for (const binding of $store.bindings) {
		if (binding.key && binding.enabled) {
			labels[binding.actionId] = formatKeyCombo(binding.modifiers, binding.key);
		}
	}
	return labels;
});
