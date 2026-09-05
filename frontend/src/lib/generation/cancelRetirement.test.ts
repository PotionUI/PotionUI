// @vitest-environment jsdom
// jsdom is needed only for the real-WebSocketService fixtures below.
import { describe, it, expect, beforeEach } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { WebSocketService } from '$lib/services/websocket';
import { ensureSubscribed, isSubscriptionRequested, releaseSubscription } from '$lib/generation/restore/subscriptions';
import {
	setGenerationOutputs,
	peekGenerationOutputs,
	isGenerationOutputsRetired,
	resetGenerationOutputsRetirementForTests
} from '$lib/generation/messages/generationOutputs';
import type { DirectorRunState } from '$lib/types/tabs';
import { retireConfirmedCancellations, applyConfirmedCancellations } from './cancelRetirement';

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function currentTab(tabId: string) {
	return get(tabsStore).tabs.find((t) => t.id === tabId)!;
}

function seedDirectorRun(tabId: string, shotId: string, run: DirectorRunState, links: Record<string, string[]>) {
	const tab = currentTab(tabId);
	tabsStore.updateTab(tabId, {
		directorRuns: { ...(tab.directorRuns || {}), [shotId]: run },
		directorRunLinks: { ...(tab.directorRunLinks || {}), ...links }
	});
}

function baseRun(overrides: Partial<DirectorRunState> = {}): DirectorRunState {
	return {
		generationId: 'gen-1',
		status: 'generating',
		progress: 0.5,
		finishedAt: null,
		posterUrl: null,
		inputsHash: 'hash-a',
		...overrides
	};
}

/** A real WebSocketService (never connected) + the page's own unified
 *  unsubscribe wrapper, wired the same way routes/generate/+page.svelte
 *  wires `unsubscribeGeneration`. */
function makeOwner() {
	const ws = new WebSocketService('ws://test.invalid/ws/generation', null);
	const unsubscribe = (generationId: string): void => {
		releaseSubscription(ws, generationId);
		ws.unsubscribe(generationId);
	};
	return { ws, unsubscribe };
}

function listenerCount(ws: WebSocketService, generationId: string): number {
	return (ws as unknown as { subscriptions: Map<string, Set<unknown>> }).subscriptions.get(generationId)?.size ?? 0;
}

describe('retireConfirmedCancellations', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetGenerationOutputsRetirementForTests();
	});

	it('retires a successfully-cancelled generation: cache dropped, subscription bookkeeping and socket listener gone, a later cancelled event is a no-op', () => {
		const tabId = defaultTabId();
		const { ws, unsubscribe } = makeOwner();

		// The page's own tab-state update (clearing activeGenerationId/queue)
		// has already run by the time this is called -- simulate that here.
		tabsStore.updateTab(tabId, { activeGenerationId: null });
		setGenerationOutputs('gen-1', {
			images: [],
			videos: [{ url: '/gen-1.mp4', originalUrl: '/gen-1.mp4' } as any],
			audios: [],
			meshes: []
		});
		ensureSubscribed(ws, 'gen-1', () => ws.subscribe('gen-1', () => {}));
		expect(listenerCount(ws, 'gen-1')).toBe(1);

		retireConfirmedCancellations(['gen-1'], { tabsStore, unsubscribe });

		expect(peekGenerationOutputs('gen-1')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
		expect(isGenerationOutputsRetired('gen-1')).toBe(true);
		expect(isSubscriptionRequested(ws, 'gen-1')).toBe(false);
		expect(listenerCount(ws, 'gen-1')).toBe(0);

		// A generation_cancelled event for 'gen-1' arriving late (the backend's
		// own terminal broadcast, after the manual cancel already retired it)
		// must not resurrect anything -- no tab claims it any more, so the
		// dispatcher's own orphaned-terminal path re-retires (harmless/no-op).
		dispatchGenerationMessage(
			{ type: 'generation_cancelled', data: { id: 'gen-1' } } as any,
			{ unsubscribe }
		);
		expect(peekGenerationOutputs('gen-1')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
		expect(isGenerationOutputsRetired('gen-1')).toBe(true);
		expect(listenerCount(ws, 'gen-1')).toBe(0);
	});

	it('a queue-clear with two confirmed ids and one unconfirmed retires only the confirmed two', () => {
		const tabId = defaultTabId();
		const { ws, unsubscribe } = makeOwner();
		tabsStore.updateTab(tabId, {
			generation: { ...currentTab(tabId).generation, queue: [] }
		});

		for (const id of ['gen-a', 'gen-b', 'gen-c']) {
			setGenerationOutputs(id, {
				images: [],
				videos: [{ url: `/${id}.mp4`, originalUrl: `/${id}.mp4` } as any],
				audios: [],
				meshes: []
			});
			ensureSubscribed(ws, id, () => ws.subscribe(id, () => {}));
		}

		// Only gen-a and gen-b came back in the backend's `cancelled` list --
		// gen-c stays live (still pending/running server-side).
		retireConfirmedCancellations(['gen-a', 'gen-b'], { tabsStore, unsubscribe });

		for (const id of ['gen-a', 'gen-b']) {
			expect(isGenerationOutputsRetired(id)).toBe(true);
			expect(peekGenerationOutputs(id)).toEqual({ images: [], videos: [], audios: [], meshes: [] });
			expect(listenerCount(ws, id)).toBe(0);
		}
		expect(isGenerationOutputsRetired('gen-c')).toBe(false);
		expect(peekGenerationOutputs('gen-c').videos).toHaveLength(1);
		expect(listenerCount(ws, 'gen-c')).toBe(1);
	});

	it('a failed/rejected cancel request retires nothing (called with an empty list, as the page does on catch)', () => {
		const tabId = defaultTabId();
		const { ws, unsubscribe } = makeOwner();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		setGenerationOutputs('gen-1', {
			images: [],
			videos: [{ url: '/gen-1.mp4', originalUrl: '/gen-1.mp4' } as any],
			audios: [],
			meshes: []
		});
		ensureSubscribed(ws, 'gen-1', () => ws.subscribe('gen-1', () => {}));

		retireConfirmedCancellations([], { tabsStore, unsubscribe });

		expect(currentTab(tabId).activeGenerationId).toBe('gen-1');
		expect(isGenerationOutputsRetired('gen-1')).toBe(false);
		expect(peekGenerationOutputs('gen-1').videos).toHaveLength(1);
		expect(isSubscriptionRequested(ws, 'gen-1')).toBe(true);
		expect(listenerCount(ws, 'gen-1')).toBe(1);
	});

	it('resolves a cancelled Director shot to failed through the same reducer the live generation_cancelled handler uses, and retires the generation', () => {
		const tabId = defaultTabId();
		const { ws, unsubscribe } = makeOwner();
		tabsStore.updateTab(tabId, { activeGenerationId: null });
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-1', status: 'generating' }), {
			'gen-1': ['shot-1']
		});
		ensureSubscribed(ws, 'gen-1', () => ws.subscribe('gen-1', () => {}));

		retireConfirmedCancellations(['gen-1'], { tabsStore, unsubscribe });

		const tab = currentTab(tabId);
		expect(tab.directorRuns!['shot-1'].status).toBe('failed');
		expect(tab.directorRunLinks?.['gen-1']).toBeUndefined();
		expect(isGenerationOutputsRetired('gen-1')).toBe(true);
		expect(listenerCount(ws, 'gen-1')).toBe(0);
	});

	it('never resolves a shot resubmitted under a new generation id (identity guard) even though the OLD id is being cancelled', () => {
		const tabId = defaultTabId();
		const { ws, unsubscribe } = makeOwner();
		// 'gen-old' is being cancelled, but the shot was already resubmitted
		// under 'gen-new' -- directorRunLinks still maps BOTH (see
		// directorRuns.ts's header), but the run itself now tracks 'gen-new'.
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-new', status: 'queued' }), {
			'gen-old': ['shot-1'],
			'gen-new': ['shot-1']
		});

		retireConfirmedCancellations(['gen-old'], { tabsStore, unsubscribe });

		expect(currentTab(tabId).directorRuns!['shot-1']).toEqual(
			expect.objectContaining({ generationId: 'gen-new', status: 'queued' })
		);
	});

	it('does not retire an id another tab still claims', () => {
		const tabId = defaultTabId();
		const { unsubscribe } = makeOwner();
		tabsStore.addTabWithData('Second tab', { activeGenerationId: 'gen-shared' });
		setGenerationOutputs('gen-shared', { images: [], videos: [], audios: [], meshes: [] });

		retireConfirmedCancellations(['gen-shared'], { tabsStore, unsubscribe });

		// A different (still-open) tab claims 'gen-shared' as its own active
		// generation -- retirement must leave its cache alone.
		expect(isGenerationOutputsRetired('gen-shared')).toBe(false);
	});
});

describe('applyConfirmedCancellations', () => {
	beforeEach(() => {
		tabsStore.reset();
	});

	it('a normal single-tab cancel clears the active display and removes the queue entry', () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-1',
			generation: {
				...currentTab(tabId).generation,
				isGenerating: true,
				currentGeneration: { id: 'gen-1', generation_id: 'gen-1', status: 'running' },
				currentProgress: { step: 'sampling', progress: 0.5 },
				queue: [{ generation_id: 'gen-1', queue_position: null, status: 'running' }]
			}
		});

		applyConfirmedCancellations(tabsStore, tabId, ['gen-1']);

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.isGenerating).toBe(false);
		expect(tab.generation.currentGeneration).toBeNull();
		expect(tab.generation.currentProgress).toBeNull();
		expect(tab.generation.queue).toEqual([]);
	});

	it('writes into the ORIGINAL tab, not whichever tab is active by completion time (deferred single cancel)', () => {
		const tabA = defaultTabId();
		const tabB = tabsStore.addTabWithData('Tab B', {
			activeGenerationId: 'gen-b',
			generation: {
				...currentTab(tabA).generation,
				isGenerating: true,
				currentGeneration: { id: 'gen-b', generation_id: 'gen-b', status: 'running' }
			}
		});
		tabsStore.updateTab(tabA, { activeGenerationId: 'gen-a' });
		// The user switched tabs while the cancel request for tab A's
		// generation was still in flight.
		tabsStore.setActiveTab(tabB);

		applyConfirmedCancellations(tabsStore, tabA, ['gen-a']);

		expect(currentTab(tabA).activeGenerationId).toBeNull();
		// Tab B -- now the active tab -- is completely untouched.
		const tab = currentTab(tabB);
		expect(tab.activeGenerationId).toBe('gen-b');
		expect(tab.generation.isGenerating).toBe(true);
		expect(tab.generation.currentGeneration?.id).toBe('gen-b');
	});

	it('writes into the ORIGINAL tab for a deferred queue-clear too', () => {
		const tabA = defaultTabId();
		const tabB = tabsStore.addTabWithData('Tab B', {
			generation: {
				...currentTab(tabA).generation,
				queue: [{ generation_id: 'gen-b', queue_position: 0, status: 'pending' }]
			}
		});
		tabsStore.updateTab(tabA, {
			generation: {
				...currentTab(tabA).generation,
				queue: [{ generation_id: 'gen-a', queue_position: 0, status: 'pending' }]
			}
		});
		tabsStore.setActiveTab(tabB);

		applyConfirmedCancellations(tabsStore, tabA, ['gen-a']);

		expect(currentTab(tabA).generation.queue).toEqual([]);
		expect(currentTab(tabB).generation.queue).toEqual([
			{ generation_id: 'gen-b', queue_position: 0, status: 'pending' }
		]);
	});

	it('is a no-op, not a fallback write elsewhere, when the target tab was closed while the request was in flight', () => {
		const tabA = defaultTabId();
		// Tab B has its OWN distinguishing live generation -- a fallback to
		// "some other tab" instead of a true no-op would corrupt it.
		const tabB = tabsStore.addTabWithData('Tab B', {
			activeGenerationId: 'gen-b',
			generation: {
				...currentTab(tabA).generation,
				isGenerating: true,
				currentGeneration: { id: 'gen-b', generation_id: 'gen-b', status: 'running' },
				queue: [{ generation_id: 'gen-b-queued', queue_position: 0, status: 'pending' }]
			}
		});
		const beforeB = currentTab(tabB);
		tabsStore.removeTab(tabA);

		expect(() => applyConfirmedCancellations(tabsStore, tabA, ['gen-1'])).not.toThrow();

		const state = get(tabsStore);
		expect(state.tabs.find((t) => t.id === tabA)).toBeUndefined();
		expect(state.tabs).toHaveLength(1);
		expect(currentTab(tabB)).toEqual(beforeB);
	});

	it('keeps a newer generation the tab has since adopted -- only the old confirmed id is removed from the queue', () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-new',
			generation: {
				...currentTab(tabId).generation,
				isGenerating: true,
				currentGeneration: { id: 'gen-new', generation_id: 'gen-new', status: 'running' },
				currentProgress: { step: 'sampling', progress: 0.2 },
				queue: [{ generation_id: 'gen-old', queue_position: null, status: 'running' }]
			}
		});

		// 'gen-old' is the id the original cancel request targeted -- by the
		// time it resolves, this tab has already moved on to 'gen-new'.
		applyConfirmedCancellations(tabsStore, tabId, ['gen-old']);

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-new');
		expect(tab.generation.isGenerating).toBe(true);
		expect(tab.generation.currentGeneration?.id).toBe('gen-new');
		expect(tab.generation.currentProgress).toEqual({ step: 'sampling', progress: 0.2 });
		expect(tab.generation.queue).toEqual([]);
	});

	it('removes only the confirmed ids from a queue with unconfirmed entries', () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			generation: {
				...currentTab(tabId).generation,
				queue: [
					{ generation_id: 'gen-a', queue_position: 0, status: 'pending' },
					{ generation_id: 'gen-b', queue_position: 1, status: 'pending' },
					{ generation_id: 'gen-c', queue_position: 2, status: 'pending' }
				]
			}
		});

		applyConfirmedCancellations(tabsStore, tabId, ['gen-a', 'gen-b']);

		expect(currentTab(tabId).generation.queue).toEqual([
			{ generation_id: 'gen-c', queue_position: 2, status: 'pending' }
		]);
	});

	it('changes nothing when the confirmed-id set is empty (a failed/rejected API response)', () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-1',
			generation: {
				...currentTab(tabId).generation,
				isGenerating: true,
				currentGeneration: { id: 'gen-1', generation_id: 'gen-1', status: 'running' },
				queue: [{ generation_id: 'gen-2', queue_position: 0, status: 'pending' }]
			}
		});
		const before = currentTab(tabId);

		applyConfirmedCancellations(tabsStore, tabId, []);

		expect(currentTab(tabId)).toEqual(before);
	});
});
