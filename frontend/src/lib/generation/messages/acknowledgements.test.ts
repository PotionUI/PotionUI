import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import { get } from 'svelte/store';
import { generationTabs } from '$lib/types/tabs';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';

function seedGeneration(): void {
	const tab = generationTabs(get(tabsStore).tabs)[0];
	tabsStore.updateTab(tab.id, {
		generation: { ...tab.generation, queue: [{ generation_id: 'gen-1', queue_position: null, status: 'running' }] }
	});
}

describe('generation message handlers and the unknown-type warning', () => {
	let warn: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		tabsStore.reset();
		seedGeneration();
		warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
	});
	afterEach(() => warn.mockRestore());

	it.each(['subscribed', 'generation_update'])('handles "%s" without warning', (type) => {
		dispatchGenerationMessage({ type, generation_id: 'gen-1' } as any, { unsubscribe: vi.fn() });
		expect(warn).not.toHaveBeenCalled();
	});

	it('still warns once about a type nothing handles', () => {
		const deps = { unsubscribe: vi.fn() };
		dispatchGenerationMessage({ type: 'totally_unknown_type', generation_id: 'gen-1' } as any, deps);
		dispatchGenerationMessage({ type: 'totally_unknown_type', generation_id: 'gen-1' } as any, deps);
		expect(warn).toHaveBeenCalledTimes(1);
		expect(warn.mock.calls[0][0]).toContain('totally_unknown_type');
	});
});
