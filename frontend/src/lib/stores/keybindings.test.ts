import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const mockGetKeybindings = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		getKeybindings: (...args: unknown[]) => mockGetKeybindings(...args)
	}
}));

import { keybindingsStore, shortcutLabels, formatKeyCombo } from './keybindings';

function apiBinding(overrides: Record<string, unknown> = {}) {
	return {
		action_id: 'open_chat',
		key: 'k',
		modifiers: 'ctrl',
		label: 'Open chat',
		category: 'general',
		context: 'global',
		enabled: true,
		is_custom: false,
		...overrides
	};
}

describe('formatKeyCombo', () => {
	it('joins modifiers and an uppercased single-char key', () => {
		expect(formatKeyCombo('ctrl', 'k')).toBe('Ctrl+K');
		expect(formatKeyCombo('ctrl,shift', 'p')).toBe('Ctrl+Shift+P');
	});

	it('leaves multi-char keys as-is', () => {
		expect(formatKeyCombo('', 'Escape')).toBe('Escape');
	});
});

describe('shortcutLabels', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		keybindingsStore.reset();
	});

	it('formats enabled bindings that have a key', async () => {
		mockGetKeybindings.mockResolvedValue({
			success: true,
			data: {
				keybindings: [
					apiBinding({ action_id: 'open_chat', key: 'k', modifiers: 'ctrl' }),
					apiBinding({ action_id: 'go_generate', key: 'g', modifiers: '' }),
					apiBinding({ action_id: 'go_phrasebook', key: '4', modifiers: '' }),
					apiBinding({ action_id: 'go_prompts', key: '5', modifiers: '' })
				]
			}
		});

		await keybindingsStore.loadBindings();

		const labels = get(shortcutLabels);
		expect(labels.open_chat).toBe('Ctrl+K');
		expect(labels.go_generate).toBe('G');
		expect(labels.go_phrasebook).toBe('4');
		expect(labels.go_prompts).toBe('5');
	});

	it('omits bindings with no key', async () => {
		mockGetKeybindings.mockResolvedValue({
			success: true,
			data: { keybindings: [apiBinding({ action_id: 'go_prompts', key: null })] }
		});

		await keybindingsStore.loadBindings();

		expect(get(shortcutLabels)).not.toHaveProperty('go_prompts');
	});

	it('omits disabled bindings', async () => {
		mockGetKeybindings.mockResolvedValue({
			success: true,
			data: {
				keybindings: [apiBinding({ action_id: 'show_help', key: '?', enabled: false })]
			}
		});

		await keybindingsStore.loadBindings();

		expect(get(shortcutLabels)).not.toHaveProperty('show_help');
	});

	it('is empty before bindings load', () => {
		expect(get(shortcutLabels)).toEqual({});
	});
});

describe('keybindingsStore.reset()', () => {
	beforeEach(() => {
		vi.clearAllMocks();
		keybindingsStore.reset();
	});

	it('drops loaded bindings a different user must not keep using', async () => {
		mockGetKeybindings.mockResolvedValue({
			success: true,
			data: { keybindings: [apiBinding({ action_id: 'open_chat', key: 'j', is_custom: true })] }
		});
		await keybindingsStore.loadBindings();
		expect(get(keybindingsStore).bindings.length).toBeGreaterThan(0);
		expect(get(keybindingsStore).loaded).toBe(true);

		keybindingsStore.reset();

		const state = get(keybindingsStore);
		expect(state.bindings).toEqual([]);
		expect(state.loaded).toBe(false);
	});
});
