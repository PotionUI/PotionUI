// @vitest-environment jsdom
// jsdom (rather than the suite's default 'node') is needed only for the
// stable-subscription fixture below, which drives a REAL WebSocketService
// (its comparisons against `WebSocket.OPEN` etc. need the global jsdom
// provides) rather than a hand-rolled stand-in.
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { findTabByGenerationId, dispatchGenerationMessage } from '$lib/stores/generation';
import { WebSocketService } from '$lib/services/websocket';
import {
	reconcileTabGenerations,
	collectInFlightGenerationIds,
	isConfirmedMissing,
	resetPendingPosterRecoveriesForTests,
	clearSubscriptionOwner,
	type ReconcileApi
} from './reconcile';
import type { DirectorRunState } from '$lib/types/tabs';
import type { APIResponse, GenerationStatus } from '$lib/types/api';
import {
	setGenerationOutputs,
	peekGenerationOutputs,
	isGenerationOutputsRetired,
	resetGenerationOutputsRetirementForTests
} from '$lib/generation/messages/generationOutputs';

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function currentTab(tabId: string) {
	return get(tabsStore).tabs.find((t) => t.id === tabId)!;
}

function seedDirectorRun(
	tabId: string,
	shotId: string,
	run: DirectorRunState,
	links: Record<string, string[]>
) {
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

function statusResponse(
	overrides: Partial<GenerationStatus> & { status: GenerationStatus['status'] }
): APIResponse<GenerationStatus> {
	return {
		success: true,
		data: {
			id: overrides.id ?? 'gen-1',
			created_at: '2026-01-01T00:00:00Z',
			...overrides
		} as GenerationStatus
	};
}

function videoFile(path: string) {
	return { file_type: 'VIDEO', file_path: path, is_derived: false };
}

function notFoundError() {
	return { response: { status: 404, data: { detail: { error: 'generation_not_found' } } } };
}

function networkError() {
	return new Error('Network Error');
}

/** A test-double API facade -- resolved lazily via a map of id -> queue of
 *  responses/throws, so a test can script "fails once, then succeeds". */
function createFakeApi() {
	const statusQueue = new Map<string, Array<{ ok?: APIResponse<GenerationStatus>; err?: unknown }>>();
	const historyQueue = new Map<string, Array<{ ok?: APIResponse<any>; err?: unknown }>>();
	const statusCalls: string[] = [];
	const historyCalls: string[] = [];

	function pop<T>(queue: Map<string, T[]>, id: string): T | undefined {
		const arr = queue.get(id);
		if (!arr || arr.length === 0) return undefined;
		return arr.length > 1 ? arr.shift() : arr[0];
	}

	const api: ReconcileApi = {
		async getGenerationStatus(id) {
			statusCalls.push(id);
			const next = pop(statusQueue, id);
			if (!next) throw new Error(`no scripted status response for ${id}`);
			if (next.err) throw next.err;
			return next.ok!;
		},
		async getGenerationById(id) {
			historyCalls.push(id);
			const next = pop(historyQueue, id);
			if (!next) return { success: true, data: { files: [] } };
			if (next.err) throw next.err;
			return next.ok!;
		}
	};

	return {
		api,
		statusCalls,
		historyCalls,
		scriptStatus(id: string, ...responses: Array<{ ok?: APIResponse<GenerationStatus>; err?: unknown }>) {
			statusQueue.set(id, responses);
		},
		scriptHistory(id: string, ...responses: Array<{ ok?: APIResponse<any>; err?: unknown }>) {
			historyQueue.set(id, responses);
		}
	};
}

describe('collectInFlightGenerationIds', () => {
	it('dedupes across activeGenerationId, non-terminal directorRuns, directorRunLinks keys and queue', () => {
		const ids = collectInFlightGenerationIds({
			activeGenerationId: 'gen-1',
			directorRuns: {
				'shot-1': baseRun({ generationId: 'gen-1', status: 'generating' }),
				'shot-2': baseRun({ generationId: 'gen-2', status: 'queued' }),
				'shot-3': baseRun({ generationId: 'gen-3', status: 'done' })
			},
			directorRunLinks: { 'gen-1': ['shot-1'], 'gen-4': ['shot-4'] },
			queue: [{ generation_id: 'gen-5', queue_position: 0, status: 'pending' }]
		});
		expect(new Set(ids)).toEqual(new Set(['gen-1', 'gen-2', 'gen-4', 'gen-5']));
		// gen-1 counted once despite appearing as both activeGenerationId and a link key.
		expect(ids.filter((id) => id === 'gen-1')).toHaveLength(1);
		// gen-3's run is terminal ('done') -- never collected.
		expect(ids).not.toContain('gen-3');
	});

	it('returns an empty list for a tab with nothing in flight', () => {
		expect(collectInFlightGenerationIds({ activeGenerationId: null, directorRuns: {}, directorRunLinks: {} })).toEqual([]);
	});
});

describe('isConfirmedMissing', () => {
	it('reads a thrown 404 as confirmed missing', () => {
		expect(isConfirmedMissing(notFoundError(), null)).toBe(true);
	});

	it('reads a success:false generation_not_found response as confirmed missing', () => {
		expect(isConfirmedMissing(null, { success: false, error: 'generation_not_found' })).toBe(true);
	});

	it('reads a network error (no response) as transient, not missing', () => {
		expect(isConfirmedMissing(networkError(), null)).toBe(false);
	});

	it('reads a 500 as transient, not missing', () => {
		expect(isConfirmedMissing({ response: { status: 500 } }, null)).toBe(false);
	});

	it('reads a malformed success response as transient, not missing', () => {
		expect(isConfirmedMissing(null, { success: false, error: 'weird_error' })).toBe(false);
	});
});

describe('reconcileTabGenerations', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetPendingPosterRecoveriesForTests();
	});

	it('resolves a completed director run while offline, poster from its own output', async () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-1', status: 'generating' }), {
			'gen-1': ['shot-1']
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { ok: statusResponse({ status: 'completed' }) });
		fake.scriptHistory('gen-1', { ok: { success: true, data: { files: [videoFile('gen-1/0.mp4')] } } });

		await reconcileTabGenerations(tabId, fake.api, tabsStore);

		const tab = currentTab(tabId);
		expect(tab.directorRuns!['shot-1'].status).toBe('done');
		expect(tab.directorRuns!['shot-1'].posterUrl).toBe('/api/media/generations/gen-1/0.mp4');
		expect(tab.directorRuns!['shot-1'].finishedAt).not.toBeNull();
		expect(tab.directorRunLinks?.['gen-1']).toBeUndefined();
		expect(fake.statusCalls).toEqual(['gen-1']);
	});

	it('resolves a failed director run while offline, no poster, previous progress dropped', async () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-1', status: 'generating', progress: 0.4 }), {
			'gen-1': ['shot-1']
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { ok: statusResponse({ status: 'failed', message: 'OOM' }) });

		await reconcileTabGenerations(tabId, fake.api, tabsStore);

		const tab = currentTab(tabId);
		expect(tab.directorRuns!['shot-1'].status).toBe('failed');
		expect(tab.directorRuns!['shot-1'].posterUrl).toBeNull();
	});

	it('resolves a cancelled director run while offline the same way as failed', async () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-1', status: 'generating' }), {
			'gen-1': ['shot-1']
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { ok: statusResponse({ status: 'cancelled' }) });

		await reconcileTabGenerations(tabId, fake.api, tabsStore);

		expect(currentTab(tabId).directorRuns!['shot-1'].status).toBe('failed');
	});

	it('clears activeGenerationId and updates the shared display when the active generation is confirmed missing', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-1',
			generation: { ...currentTab(tabId).generation, isGenerating: true }
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { err: notFoundError() });

		await reconcileTabGenerations(tabId, fake.api, tabsStore);

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.isGenerating).toBe(false);
		expect(tab.generation.currentGeneration?.status).toBe('failed');
	});

	it('resolves a secondary director shot completed offline without disturbing the tab\'s own active generation', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-active',
			generation: { ...currentTab(tabId).generation, isGenerating: true }
		});
		seedDirectorRun(tabId, 'shot-2', baseRun({ generationId: 'gen-b', status: 'queued' }), {
			'gen-b': ['shot-2']
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-active', { ok: statusResponse({ status: 'running', progress: 0.2 }) });
		fake.scriptStatus('gen-b', { ok: statusResponse({ status: 'completed', id: 'gen-b' }) });
		fake.scriptHistory('gen-b', { ok: { success: true, data: { files: [videoFile('gen-b/0.mp4')] } } });

		await reconcileTabGenerations(tabId, fake.api, tabsStore);

		const tab = currentTab(tabId);
		// The secondary shot resolved to done, not left "queued".
		expect(tab.directorRuns!['shot-2'].status).toBe('done');
		expect(tab.directorRuns!['shot-2'].posterUrl).toBe('/api/media/generations/gen-b/0.mp4');
		// The tab's own active generation is untouched by the secondary shot's resolution.
		expect(tab.activeGenerationId).toBe('gen-active');
		expect(tab.generation.isGenerating).toBe(true);
	});

	it('keeps the reference and recovers on retry after a transient status-lookup failure', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const fake = createFakeApi();
		fake.scriptStatus(
			'gen-1',
			{ err: networkError() },
			{ ok: statusResponse({ status: 'completed', id: 'gen-1' }) }
		);
		fake.scriptHistory('gen-1', { ok: { success: true, data: { files: [videoFile('gen-1/0.mp4')] } } });

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		expect(fake.statusCalls).toEqual(['gen-1', 'gen-1']);
		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.currentGeneration?.status).toBe('completed');
	});

	it('leaves the reference untouched when both the initial lookup and its retry fail transiently', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { err: networkError() }, { err: networkError() });

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		expect(fake.statusCalls).toEqual(['gen-1', 'gen-1']);
		expect(currentTab(tabId).activeGenerationId).toBe('gen-1');
	});

	it('recovers the poster on a retried history lookup after the status already confirmed completed', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { ok: statusResponse({ status: 'completed', id: 'gen-1' }) });
		fake.scriptHistory(
			'gen-1',
			{ err: networkError() },
			{ ok: { success: true, data: { files: [videoFile('gen-1/0.mp4')] } } }
		);

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.currentGeneration?.current_video).toBe('/api/media/generations/gen-1/0.mp4');
		expect(fake.historyCalls).toEqual(['gen-1', 'gen-1']);
	});

	it('ignores a stale restore response once a newer submission has taken over the tab and the shot was resubmitted', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-old' });
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-old', status: 'generating' }), {
			'gen-old': ['shot-1']
		});

		let resolveStatus!: (value: APIResponse<GenerationStatus>) => void;
		const pending = new Promise<APIResponse<GenerationStatus>>((resolve) => {
			resolveStatus = resolve;
		});
		const api: ReconcileApi = {
			getGenerationStatus: vi.fn().mockReturnValue(pending),
			getGenerationById: vi.fn().mockResolvedValue({ success: true, data: { files: [videoFile('gen-old/0.mp4')] } })
		};

		const reconcilePromise = reconcileTabGenerations(tabId, api, tabsStore);

		// A new submission takes over the tab and resubmits the shot under a
		// new generation id BEFORE the old lookup resolves.
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-new',
			directorRuns: { 'shot-1': baseRun({ generationId: 'gen-new', status: 'queued' }) },
			directorRunLinks: { 'gen-old': ['shot-1'], 'gen-new': ['shot-1'] }
		});

		resolveStatus(statusResponse({ status: 'completed', id: 'gen-old' }));
		await reconcilePromise;

		const tab = currentTab(tabId);
		// The stale 'gen-old' completion must not clobber the newer submission.
		expect(tab.activeGenerationId).toBe('gen-new');
		expect(tab.directorRuns!['shot-1']).toEqual(
			expect.objectContaining({ generationId: 'gen-new', status: 'queued' })
		);
	});

	it('deduplicates lookups: a chain run whose shots share one generationId is looked up once', async () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-chain', status: 'generating' }), {
			'gen-chain': ['shot-1', 'shot-2']
		});
		seedDirectorRun(tabId, 'shot-2', baseRun({ generationId: 'gen-chain', status: 'generating' }), {});

		const fake = createFakeApi();
		fake.scriptStatus('gen-chain', { ok: statusResponse({ status: 'completed', id: 'gen-chain' }) });
		fake.scriptHistory('gen-chain', { ok: { success: true, data: { files: [videoFile('gen-chain/0.mp4')] } } });

		await reconcileTabGenerations(tabId, fake.api, tabsStore);

		expect(fake.statusCalls).toEqual(['gen-chain']);
		const tab = currentTab(tabId);
		expect(tab.directorRuns!['shot-1'].status).toBe('done');
		expect(tab.directorRuns!['shot-2'].status).toBe('done');
	});

	it('re-subscribes a still-running generation exactly once and leaves it in place', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { ok: statusResponse({ status: 'running', progress: 0.3 }) });
		const onSubscribe = vi.fn();

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { onSubscribe });

		expect(onSubscribe).toHaveBeenCalledTimes(1);
		expect(onSubscribe).toHaveBeenCalledWith('gen-1');
		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-1');
		expect(tab.generation.isGenerating).toBe(true);
	});

	it('gives a non-owning running Director shot a queue routing entry so live messages find its tab', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-active' });
		seedDirectorRun(tabId, 'shot-2', baseRun({ generationId: 'gen-b', status: 'generating' }), {
			'gen-b': ['shot-2']
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-active', { ok: statusResponse({ status: 'running', progress: 0.1 }) });
		fake.scriptStatus('gen-b', { ok: statusResponse({ status: 'running', progress: 0.4, id: 'gen-b' }) });

		// Before the fix, only activeGenerationId ever got a queue entry --
		// findTabByGenerationId had no way to route a follow-up message for
		// 'gen-b' back to this tab (it only ever finds a tab through
		// currentGeneration or generation.queue).
		expect(findTabByGenerationId('gen-b')).toBeNull();

		await reconcileTabGenerations(tabId, fake.api, tabsStore);

		expect(findTabByGenerationId('gen-b')).toBe(tabId);
		const tab = currentTab(tabId);
		expect(tab.generation.queue).toEqual(
			expect.arrayContaining([
				expect.objectContaining({ generation_id: 'gen-b', status: 'running' })
			])
		);
		// The owning display is untouched by the secondary shot's routing entry.
		expect(tab.activeGenerationId).toBe('gen-active');
	});

	it('treats a status body with no recognized status as transient, not as a terminal failure', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-1', status: 'generating' }), {
			'gen-1': ['shot-1']
		});
		const fake = createFakeApi();
		// success:true but no `status` field at all -- malformed, not missing.
		fake.scriptStatus(
			'gen-1',
			{ ok: { success: true, data: { id: 'gen-1' } } as APIResponse<GenerationStatus> },
			{ ok: { success: true, data: { id: 'gen-1' } } as APIResponse<GenerationStatus> }
		);

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		expect(fake.statusCalls).toEqual(['gen-1', 'gen-1']);
		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBe('gen-1');
		expect(tab.directorRuns!['shot-1'].status).toBe('generating');
	});

	it('recovers from a malformed status body once the retry returns a recognized status', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const fake = createFakeApi();
		fake.scriptStatus(
			'gen-1',
			{ ok: { success: true, data: { id: 'gen-1' } } as APIResponse<GenerationStatus> },
			{ ok: statusResponse({ status: 'running', progress: 0.5 }) }
		);

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		expect(fake.statusCalls).toEqual(['gen-1', 'gen-1']);
		expect(currentTab(tabId).generation.currentGeneration?.status).toBe('running');
	});

	it('never re-routes/subscribes an id whose Director link was retired by a live event mid-reconciliation', async () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-old', status: 'generating' }), {
			'gen-old': ['shot-1']
		});

		let resolveStatus!: (value: APIResponse<GenerationStatus>) => void;
		const pending = new Promise<APIResponse<GenerationStatus>>((resolve) => {
			resolveStatus = resolve;
		});
		const api: ReconcileApi = {
			getGenerationStatus: vi.fn().mockReturnValue(pending),
			getGenerationById: vi.fn().mockResolvedValue({ success: true, data: { files: [] } })
		};
		const onSubscribe = vi.fn();

		const reconcilePromise = reconcileTabGenerations(tabId, api, tabsStore, { onSubscribe });

		// A live WebSocket event resolves 'gen-old' and drops its link entirely
		// (the run moved on, e.g. re-run under a fresh id with no trace of the
		// old one) WHILE the stale status lookup is still in flight.
		tabsStore.updateTab(tabId, {
			directorRuns: {},
			directorRunLinks: {}
		});

		// The delayed lookup finally answers "still running".
		resolveStatus(statusResponse({ status: 'running', progress: 0.9, id: 'gen-old' }));
		await reconcilePromise;

		expect(onSubscribe).not.toHaveBeenCalled();
		const tab = currentTab(tabId);
		expect(tab.generation.queue.some((q) => q.generation_id === 'gen-old')).toBe(false);
		expect(findTabByGenerationId('gen-old')).toBeNull();
	});

	it('folds live-queue-snapshot ids into the same pass, re-validating rather than trusting the snapshot', async () => {
		const tabId = defaultTabId();
		// No persisted trace of 'gen-x' at all -- only the caller's live
		// snapshot (e.g. a plain, non-Director queued generation) names it.
		const fake = createFakeApi();
		fake.scriptStatus('gen-x', { ok: statusResponse({ status: 'completed', id: 'gen-x' }) });
		fake.scriptHistory('gen-x', { ok: { success: true, data: { files: [] } } });

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { extraCandidateIds: ['gen-x'] });

		// A stale snapshot claiming "still running" never gets a chance to
		// resurrect it -- reconcile's own fresh lookup found it completed, so
		// it is never added to the queue at all.
		expect(currentTab(tabId).generation.queue.some((q) => q.generation_id === 'gen-x')).toBe(false);
		expect(fake.statusCalls).toEqual(['gen-x']);
	});

	it('deduplicates an id that is both a persisted directorRunLinks key and a live-snapshot candidate', async () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-b', status: 'generating' }), {
			'gen-b': ['shot-1']
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-b', { ok: statusResponse({ status: 'running', id: 'gen-b' }) });

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { extraCandidateIds: ['gen-b'] });

		expect(fake.statusCalls).toEqual(['gen-b']);
	});

	it('never resurrects an ordinary generation whose real generation_complete lands while its status lookup is still in flight', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-a',
			generation: {
				...currentTab(tabId).generation,
				isGenerating: true,
				currentGeneration: { id: 'gen-a', generation_id: 'gen-a', status: 'running' }
			}
		});

		let resolveStatus!: (value: APIResponse<GenerationStatus>) => void;
		const pending = new Promise<APIResponse<GenerationStatus>>((resolve) => {
			resolveStatus = resolve;
		});
		const api: ReconcileApi = {
			getGenerationStatus: vi.fn().mockReturnValue(pending),
			getGenerationById: vi.fn().mockResolvedValue({ success: true, data: { files: [] } })
		};
		const onSubscribe = vi.fn();

		const reconcilePromise = reconcileTabGenerations(tabId, api, tabsStore, { onSubscribe });

		// The REAL generation_complete handler (dispatched exactly as the
		// WebSocket boundary would) finishes 'gen-a' -- clearing its active id
		// and queue entry -- WHILE the stale status lookup above is still
		// pending.
		dispatchGenerationMessage(
			{ type: 'generation_complete', data: { id: 'gen-a' } } as any,
			{ unsubscribe: vi.fn() }
		);
		expect(currentTab(tabId).activeGenerationId).toBeNull();
		expect(currentTab(tabId).generation.currentGeneration?.status).toBe('completed');

		// The belated lookup finally answers "still running".
		resolveStatus(statusResponse({ status: 'running', progress: 0.9, id: 'gen-a' }));
		await reconcilePromise;

		const tab = currentTab(tabId);
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.currentGeneration?.status).toBe('completed');
		// The stale "still running" response neither re-added a queue/routing
		// entry for 'gen-a' nor re-subscribed to it -- resurrection is exactly
		// what this guards against. (`currentGeneration` legitimately still
		// carries 'gen-a's own id after completion -- that's unrelated to
		// resurrection and not what this test is checking.)
		expect(tab.generation.queue.some((q) => q.generation_id === 'gen-a')).toBe(false);
		expect(onSubscribe).not.toHaveBeenCalled();
	});

	it('subscribes a still-running id at most once across two reconcile passes against the SAME socket, through the real WebSocketService', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const ws = new WebSocketService('ws://test.invalid/ws/generation', null);
		const received: unknown[] = [];
		const handleGenerationMessage = (message: unknown) => received.push(message);
		const onSubscribe = (generationId: string) => {
			ws.subscribe(generationId, (message) => handleGenerationMessage(message));
		};

		const fake = createFakeApi();
		fake.scriptStatus(
			'gen-1',
			{ ok: statusResponse({ status: 'running', progress: 0.1 }) },
			{ ok: statusResponse({ status: 'running', progress: 0.6 }) }
		);

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { onSubscribe, subscriptionOwner: ws });
		// A later reconnect against the SAME socket re-runs reconciliation for
		// the same still-running id.
		await reconcileTabGenerations(tabId, fake.api, tabsStore, { onSubscribe, subscriptionOwner: ws });

		// One live event arrives on the (never actually connected, but
		// otherwise real) socket.
		(ws as unknown as { onMessage(message: { type: string; generation_id: string }): void }).onMessage({
			type: 'generation_status',
			generation_id: 'gen-1'
		});

		expect(received).toHaveLength(1);
	});

	it('subscribes on a NEW socket after an SPA navigate-away-and-back, even though the tab still has the old routing entry', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const oldSocket = new WebSocketService('ws://test.invalid/ws/generation', null);

		const fake = createFakeApi();
		fake.scriptStatus(
			'gen-1',
			{ ok: statusResponse({ status: 'running', progress: 0.1 }) },
			{ ok: statusResponse({ status: 'running', progress: 0.6 }) }
		);

		// Pass 1, on the page's first mount.
		await reconcileTabGenerations(tabId, fake.api, tabsStore, {
			subscriptionOwner: oldSocket,
			onSubscribe: (id) => oldSocket.subscribe(id, () => {})
		});
		expect((oldSocket as unknown as { subscriptions: Map<string, Set<unknown>> }).subscriptions.get('gen-1')?.size).toBe(1);

		// SPA navigate away and back: onMount runs again and constructs a
		// BRAND NEW WebSocketService (createGenerationSocket()) while the
		// module-scope tabs store -- and 'gen-1's routing entry in it --
		// survives untouched.
		const newSocket = new WebSocketService('ws://test.invalid/ws/generation', null);
		await reconcileTabGenerations(tabId, fake.api, tabsStore, {
			subscriptionOwner: newSocket,
			onSubscribe: (id) => newSocket.subscribe(id, () => {})
		});

		// The routing entry from pass 1 is still there (routing/dispatch is
		// unaffected by this fix)...
		expect(currentTab(tabId).generation.queue.some((q) => q.generation_id === 'gen-1')).toBe(true);
		// ...but the NEW socket -- which starts with zero listeners of its
		// own -- still gets its own subscription for the still-live
		// generation, rather than being skipped because the OLD socket
		// already had one.
		expect((newSocket as unknown as { subscriptions: Map<string, Set<unknown>> }).subscriptions.get('gen-1')?.size).toBe(1);
	});

	it('clearSubscriptionOwner drops an owner\'s memory so a later pass against the same owner value resubscribes', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const owner = {};
		const subscribeCalls: string[] = [];
		const fake = createFakeApi();
		fake.scriptStatus(
			'gen-1',
			{ ok: statusResponse({ status: 'running' }) },
			{ ok: statusResponse({ status: 'running' }) }
		);

		await reconcileTabGenerations(tabId, fake.api, tabsStore, {
			subscriptionOwner: owner,
			onSubscribe: (id) => subscribeCalls.push(id)
		});
		expect(subscribeCalls).toEqual(['gen-1']);

		clearSubscriptionOwner(owner);

		await reconcileTabGenerations(tabId, fake.api, tabsStore, {
			subscriptionOwner: owner,
			onSubscribe: (id) => subscribeCalls.push(id)
		});
		expect(subscribeCalls).toEqual(['gen-1', 'gen-1']);
	});

	it('a retired signal applies nothing further and never subscribes on a late response', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, { activeGenerationId: 'gen-1' });
		const controller = new AbortController();
		let resolveStatus!: (value: APIResponse<GenerationStatus>) => void;
		const pending = new Promise<APIResponse<GenerationStatus>>((resolve) => {
			resolveStatus = resolve;
		});
		const api: ReconcileApi = {
			getGenerationStatus: vi.fn().mockReturnValue(pending),
			getGenerationById: vi.fn().mockResolvedValue({ success: true, data: { files: [] } })
		};
		const onSubscribe = vi.fn();

		const reconcilePromise = reconcileTabGenerations(tabId, api, tabsStore, {
			signal: controller.signal,
			subscriptionOwner: {},
			onSubscribe
		});

		controller.abort();
		resolveStatus(statusResponse({ status: 'running' }));
		await reconcilePromise;

		expect(onSubscribe).not.toHaveBeenCalled();
		expect(currentTab(tabId).generation.queue.some((q) => q.generation_id === 'gen-1')).toBe(false);
	});

	it('remembers a pending poster after two transient history failures and recovers it, without a full re-scan, on the next pass', async () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-1', status: 'generating' }), {
			'gen-1': ['shot-1']
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { ok: statusResponse({ status: 'completed', id: 'gen-1' }) });
		fake.scriptHistory('gen-1', { err: networkError() }, { err: networkError() });

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		let tab = currentTab(tabId);
		expect(tab.directorRuns!['shot-1'].status).toBe('done');
		expect(tab.directorRuns!['shot-1'].posterUrl).toBeNull();
		expect(fake.statusCalls).toEqual(['gen-1']);
		expect(fake.historyCalls).toEqual(['gen-1', 'gen-1']);

		// A later pass -- 'gen-1' is no longer tracked anywhere
		// (collectInFlightGenerationIds finds nothing), so recovery must not
		// re-poll status; it retries only the remembered history fetch.
		fake.scriptHistory('gen-1', { ok: { success: true, data: { files: [videoFile('gen-1/0.mp4')] } } });
		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		tab = currentTab(tabId);
		expect(tab.directorRuns!['shot-1'].posterUrl).toBe('/api/media/generations/gen-1/0.mp4');
		expect(fake.statusCalls).toEqual(['gen-1']);
		expect(fake.historyCalls).toEqual(['gen-1', 'gen-1', 'gen-1']);
	});

	it('gives up recovering a poster once the history fetch is confirmed 404', async () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ generationId: 'gen-1', status: 'generating' }), {
			'gen-1': ['shot-1']
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { ok: statusResponse({ status: 'completed', id: 'gen-1' }) });
		fake.scriptHistory('gen-1', { err: networkError() }, { err: networkError() });
		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		fake.scriptHistory('gen-1', { err: notFoundError() });
		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });
		expect(fake.historyCalls).toEqual(['gen-1', 'gen-1', 'gen-1']);

		// A THIRD pass must not try again -- the entry was dropped for good on
		// the confirmed-404 above, so this makes no additional history call.
		await reconcileTabGenerations(tabId, fake.api, tabsStore, { retryDelayMs: 0 });

		expect(fake.historyCalls).toEqual(['gen-1', 'gen-1', 'gen-1']);
		expect(currentTab(tabId).directorRuns!['shot-1'].posterUrl).toBeNull();
	});
});

describe('reconcileTabGenerations retires the generationOutputs cache and unsubscribes on terminal resolution', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetPendingPosterRecoveriesForTests();
		resetGenerationOutputsRetirementForTests();
	});

	it('retires a stale cache entry and unsubscribes when a generation resolves completed while offline', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-1',
			generation: { ...currentTab(tabId).generation, isGenerating: true }
		});
		// Some gallery_update arrived before the disconnect -- a live
		// generation_complete would normally retire this, but none is coming.
		setGenerationOutputs('gen-1', {
			images: [],
			videos: [{ url: '/stale.mp4', originalUrl: '/stale.mp4' } as any],
			audios: [],
			meshes: []
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-1', { ok: statusResponse({ status: 'completed', id: 'gen-1' }) });
		fake.scriptHistory('gen-1', { ok: { success: true, data: { files: [videoFile('gen-1/0.mp4')] } } });
		const unsubscribe = vi.fn();

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { unsubscribe });

		expect(unsubscribe).toHaveBeenCalledWith('gen-1');
		expect(peekGenerationOutputs('gen-1')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
		expect(isGenerationOutputsRetired('gen-1')).toBe(true);
	});

	it('retires the cache and unsubscribes when a generation is confirmed missing', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-2',
			generation: { ...currentTab(tabId).generation, isGenerating: true }
		});
		setGenerationOutputs('gen-2', {
			images: [],
			videos: [],
			audios: [{ url: '/a.wav' } as any],
			meshes: []
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-2', { err: notFoundError() });
		const unsubscribe = vi.fn();

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { unsubscribe });

		expect(unsubscribe).toHaveBeenCalledWith('gen-2');
		expect(peekGenerationOutputs('gen-2')).toEqual({ images: [], videos: [], audios: [], meshes: [] });
	});

	it('never unsubscribes an id reconciliation resolves as "keep" (still pending/running)', async () => {
		const tabId = defaultTabId();
		tabsStore.updateTab(tabId, {
			activeGenerationId: 'gen-3',
			generation: {
				...currentTab(tabId).generation,
				isGenerating: true,
				queue: [{ generation_id: 'gen-3', queue_position: null, status: 'running' }]
			}
		});
		const fake = createFakeApi();
		fake.scriptStatus('gen-3', { ok: statusResponse({ status: 'running', progress: 0.3 }) });
		const unsubscribe = vi.fn();

		await reconcileTabGenerations(tabId, fake.api, tabsStore, { unsubscribe, onSubscribe: () => {} });

		expect(unsubscribe).not.toHaveBeenCalled();
	});
});
