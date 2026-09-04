import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { directorShotIdsFor, withDirectorRunGenerating, withDirectorRunTerminal, withDirectorRunPoster } from './directorRuns';
import type { DirectorRunState } from '$lib/types/tabs';

// Importing '$lib/stores/generation' pulls in '$lib/generation/messages' as a
// side effect, which registers every handler under test (status, complete,
// error, gallery_update).

function defaultTabId(): string {
	return get(tabsStore).tabs[0].id;
}

function seedDirectorRun(tabId: string, shotId: string, run: DirectorRunState, links: Record<string, string[]>) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, {
		directorRuns: { ...(tab.directorRuns || {}), [shotId]: run },
		directorRunLinks: { ...(tab.directorRunLinks || {}), ...links }
	});
}

function baseRun(overrides: Partial<DirectorRunState> = {}): DirectorRunState {
	return {
		generationId: 'gen-1',
		status: 'queued',
		progress: null,
		finishedAt: null,
		posterUrl: null,
		inputsHash: 'hash-a',
		...overrides
	};
}

describe('directorShotIdsFor', () => {
	it('returns null when the generation id is undefined', () => {
		expect(directorShotIdsFor({ directorRunLinks: { 'gen-1': ['shot-1'] } }, undefined)).toBeNull();
	});

	it('returns null when the generation is not a Video Director run', () => {
		expect(directorShotIdsFor({ directorRunLinks: { 'gen-1': ['shot-1'] } }, 'gen-2')).toBeNull();
	});

	it('returns null for an empty link entry rather than an empty array', () => {
		expect(directorShotIdsFor({ directorRunLinks: { 'gen-1': [] } }, 'gen-1')).toBeNull();
	});

	it('returns the shot ids a generation covers', () => {
		expect(directorShotIdsFor({ directorRunLinks: { 'gen-1': ['shot-1', 'shot-2'] } }, 'gen-1')).toEqual([
			'shot-1',
			'shot-2'
		]);
	});
});

describe('withDirectorRunGenerating', () => {
	it('flips every covered shot to generating with the given progress, leaving others untouched', () => {
		const tab = { directorRuns: { 'shot-1': baseRun(), 'shot-2': baseRun(), 'shot-3': baseRun({ status: 'done' }) } };
		const next = withDirectorRunGenerating(tab, ['shot-1', 'shot-2'], 0.42);
		expect(next['shot-1']).toEqual({ ...baseRun(), status: 'generating', progress: 0.42 });
		expect(next['shot-2']).toEqual({ ...baseRun(), status: 'generating', progress: 0.42 });
		expect(next['shot-3']).toEqual(baseRun({ status: 'done' }));
	});

	it('ignores a shot id with no existing run rather than inventing one', () => {
		const next = withDirectorRunGenerating({ directorRuns: {} }, ['ghost-shot'], 0.5);
		expect(next).toEqual({});
	});
});

describe('withDirectorRunTerminal', () => {
	it('resolves to done, sets progress to 1 and stamps finishedAt', () => {
		const tab = { directorRuns: { 'shot-1': baseRun({ status: 'generating', progress: 0.9 }) } };
		const next = withDirectorRunTerminal(tab, ['shot-1'], 'done', 'https://example/out.mp4', 1000);
		expect(next['shot-1']).toEqual(baseRun({ status: 'done', progress: 1, finishedAt: 1000, posterUrl: 'https://example/out.mp4' }));
	});

	it('a failed resolution never writes a poster and keeps the last known progress', () => {
		const tab = { directorRuns: { 'shot-1': baseRun({ status: 'generating', progress: 0.3 }) } };
		const next = withDirectorRunTerminal(tab, ['shot-1'], 'failed', 'https://example/out.mp4', 2000);
		expect(next['shot-1']).toEqual(baseRun({ status: 'failed', progress: 0.3, finishedAt: 2000, posterUrl: null }));
	});

	it('a done resolution with no poster keeps whatever gallery_update already set', () => {
		const tab = { directorRuns: { 'shot-1': baseRun({ posterUrl: 'https://example/earlier.mp4' }) } };
		const next = withDirectorRunTerminal(tab, ['shot-1'], 'done', null, 3000);
		expect(next['shot-1'].posterUrl).toBe('https://example/earlier.mp4');
	});
});

describe('withDirectorRunPoster', () => {
	it('sets the poster without touching status/progress', () => {
		const tab = { directorRuns: { 'shot-1': baseRun({ status: 'generating', progress: 0.6 }) } };
		const next = withDirectorRunPoster(tab, ['shot-1'], 'https://example/preview.mp4');
		expect(next['shot-1']).toEqual(baseRun({ status: 'generating', progress: 0.6, posterUrl: 'https://example/preview.mp4' }));
	});
});

// ─── End-to-end through the real message handlers ──────────────────────────

describe('directorRuns wiring through generation message handlers', () => {
	beforeEach(() => tabsStore.reset());

	it('generation_status flips every covered shot to generating with its progress', () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun(), { 'gen-1': ['shot-1', 'shot-2'] });
		seedDirectorRun(tabId, 'shot-2', baseRun(), {});
		const tab0 = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		tabsStore.updateTab(tabId, { generation: { ...tab0.generation, currentGeneration: { generation_id: 'gen-1' } } });

		dispatchGenerationMessage(
			{ type: 'generation_status', generation_id: 'gen-1', status: 'running', progress: 0.5 } as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.directorRuns!['shot-1']).toMatchObject({ status: 'generating', progress: 0.5 });
		expect(tab.directorRuns!['shot-2']).toMatchObject({ status: 'generating', progress: 0.5 });
	});

	it('a generation_status for an unrelated generation id leaves directorRuns untouched', () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun(), { 'gen-1': ['shot-1'] });
		// Give the unrelated generation somewhere to route to (currentGeneration).
		const tab0 = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		tabsStore.updateTab(tabId, { generation: { ...tab0.generation, currentGeneration: { generation_id: 'gen-other' } } });

		dispatchGenerationMessage(
			{ type: 'generation_status', generation_id: 'gen-other', status: 'running', progress: 0.9 } as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.directorRuns!['shot-1']).toEqual(baseRun());
	});

	it('gallery_update sets the poster ahead of completion, without changing status', () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ status: 'generating', progress: 0.95 }), { 'gen-1': ['shot-1'] });
		const tab0 = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		tabsStore.updateTab(tabId, { generation: { ...tab0.generation, currentGeneration: { generation_id: 'gen-1' } } });

		dispatchGenerationMessage(
			{
				type: 'gallery_update',
				generation_id: 'gen-1',
				videos: [{ path: '/api/media/generations/gen-1/0.mp4' }],
				video_urls_list: [{ path: '/api/media/generations/gen-1/0.mp4' }]
			} as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.directorRuns!['shot-1']).toMatchObject({
			status: 'generating',
			posterUrl: '/api/media/generations/gen-1/0.mp4'
		});
	});

	it('generation_complete resolves every covered shot to done with a finishedAt', () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ status: 'generating', progress: 0.95 }), {
			'gen-1': ['shot-1', 'shot-2']
		});
		seedDirectorRun(tabId, 'shot-2', baseRun({ status: 'generating', progress: 0.95 }), {});
		const tab0 = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		tabsStore.updateTab(tabId, { generation: { ...tab0.generation, currentGeneration: { generation_id: 'gen-1' } } });

		dispatchGenerationMessage({ type: 'generation_complete', data: { id: 'gen-1' } } as any, { unsubscribe: vi.fn() });

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.directorRuns!['shot-1'].status).toBe('done');
		expect(tab.directorRuns!['shot-1'].progress).toBe(1);
		expect(typeof tab.directorRuns!['shot-1'].finishedAt).toBe('number');
		expect(tab.directorRuns!['shot-2'].status).toBe('done');
	});

	it('generation_error resolves every covered shot to failed', () => {
		const tabId = defaultTabId();
		seedDirectorRun(tabId, 'shot-1', baseRun({ status: 'generating', progress: 0.4 }), { 'gen-1': ['shot-1'] });
		const tab0 = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		tabsStore.updateTab(tabId, { generation: { ...tab0.generation, currentGeneration: { generation_id: 'gen-1' } } });

		dispatchGenerationMessage(
			{ type: 'generation_error', data: { id: 'gen-1', message: 'boom' } } as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.directorRuns!['shot-1'].status).toBe('failed');
		expect(typeof tab.directorRuns!['shot-1'].finishedAt).toBe('number');
	});

	it('a generation with no directorRunLinks entry never gains a directorRuns field at all', () => {
		const tabId = defaultTabId();
		const tab0 = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		tabsStore.updateTab(tabId, { generation: { ...tab0.generation, currentGeneration: { generation_id: 'gen-plain' } } });

		dispatchGenerationMessage(
			{ type: 'generation_status', generation_id: 'gen-plain', status: 'running', progress: 0.5 } as any,
			{ unsubscribe: vi.fn() }
		);

		const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
		expect(tab.directorRuns).toBeUndefined();
	});
});
