import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/utils/generationSounds', () => ({
	playGenerationCompleteSound: vi.fn(),
	playGenerationErrorSound: vi.fn(),
	unlockGenerationSoundContext: vi.fn()
}));

import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { generationTabs } from '$lib/types/tabs';
import { reconcileTabGenerations } from '$lib/generation/restore/reconcile';
import { resetPreviewReplayForTests } from '$lib/generation/messages/replayGuard';
import { openGenerationInTab, type OpenInTabDeps } from './openInTab';

const DETAIL = {
	id: 'g1',
	preset_id: 'native/SDXL/base',
	preset_name: 'SDXL',
	mode: 'txt2img',
	form_name: 'default',
	form_data: { seed: 7 },
	prompt_state: { prompt: 'a lighthouse' },
	status: 'running',
	origin: { kind: 'mcp', key_id: 'k1', key_name: 'claude desktop' },
	files: []
};

function setup() {
	const subscribed: string[] = [];
	const api = {
		getGenerationStatus: vi.fn(async () => ({ success: true, data: { id: 'g1', status: 'running', created_at: '2026-11-17T09:00:00Z', started_at: '2026-11-17T09:00:02Z' } }) as never),
		getGenerationById: vi.fn(async () => ({ success: true, data: { files: [] } }) as never)
	};
	const unsubscribe = vi.fn();
	const deps: OpenInTabDeps = {
		getGeneration: async () => ({ success: true, data: DETAIL }) as never,
		failureHint: async () => null,
		isAdmin: () => false,
		tabs: {
			current: () => get(tabsStore),
			addTabWithData: (name, data) => tabsStore.addTabWithData(name, data),
			setActiveTab: (id) => tabsStore.setActiveTab(id),
			updateTab: (id, updates) => tabsStore.updateTab(id, updates)
		},
		attach: async (tabId, ids) => {
			await reconcileTabGenerations(tabId, api, tabsStore, {
				extraCandidateIds: ids,
				subscriptionOwner: {},
				onSubscribe: (id) => subscribed.push(id),
				unsubscribe
			});
			return true;
		},
		notifyError: vi.fn(),
		announceReuse: vi.fn()
	};
	return { deps, subscribed, unsubscribe };
}

const tabOf = (id: string) => generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
const frame = (image: string, extra: Record<string, unknown> = {}) =>
	({ type: 'workbench_update', generation_id: 'g1', pipe_id: 1, image, ...extra }) as never;

beforeEach(() => {
	tabsStore.reset();
	resetPreviewReplayForTests();
});

describe('a tab opened onto a running generation', () => {
	it('shows progress, the replayed preview once, the live ones after it, and the result at the end', async () => {
		const { deps, subscribed, unsubscribe } = setup();
		const result = await openGenerationInTab('g1', deps);
		const tabId = (result as { tabId: string }).tabId;

		expect(subscribed).toEqual(['g1']);
		expect(tabOf(tabId).generation.isGenerating).toBe(true);

		const hooks = { unsubscribe };
		dispatchGenerationMessage(frame('/api/preview/replayed.png', { replayed: true }), hooks);
		expect((tabOf(tabId).generation.currentGeneration as any).current_image).toBe('/api/preview/replayed.png');

		dispatchGenerationMessage({ type: 'generation_status', generation_id: 'g1', pipe_id: 1, status: 'running', progress: 0.5, current_step: 'sampling' } as never, hooks);
		dispatchGenerationMessage(frame('/api/preview/live-1.png'), hooks);
		dispatchGenerationMessage(frame('/api/preview/replayed.png', { replayed: true }), hooks);
		expect((tabOf(tabId).generation.currentGeneration as any).current_image).toBe('/api/preview/live-1.png');

		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'g1' } } as never, hooks);
		const done = tabOf(tabId);
		expect(done.generation.isGenerating).toBe(false);
		expect(done.activeGenerationId).toBeNull();
		expect(done.generation.queue).toEqual([]);
		expect(unsubscribe).toHaveBeenCalledWith('g1');
		expect(done.openedFrom?.generationId).toBe('g1');
	});

	it('shows the failure when the generation fails while followed', async () => {
		const { deps, unsubscribe } = setup();
		const tabId = ((await openGenerationInTab('g1', deps)) as { tabId: string }).tabId;
		dispatchGenerationMessage(
			{ type: 'generation_error', generation_id: 'g1', message: 'Not enough memory', hint: 'Try a smaller size.', error_id: 'E-9', error_code: 'cuda_oom' } as never,
			{ unsubscribe }
		);
		expect(tabOf(tabId).generation.currentGeneration).toMatchObject({
			status: 'failed',
			message: 'Not enough memory',
			hint: 'Try a smaller size.',
			errorId: 'E-9'
		});
		expect(tabOf(tabId).generation.isGenerating).toBe(false);
	});

	it('is a new generation, not the followed one, when the member presses Generate', async () => {
		const { deps } = setup();
		const tabId = ((await openGenerationInTab('g1', deps)) as { tabId: string }).tabId;
		tabsStore.updateTab(tabId, { prompt: 'a lighthouse at dusk' });
		expect(tabOf(tabId).activeGenerationId).toBe('g1');
		expect(tabOf(tabId).openedFrom?.generationId).toBe('g1');
	});
});
