import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { generationTabs } from '$lib/types/tabs';

const STORAGE_KEY = 'potionui_tabs_state::anon';

function installStorage() {
	const store = new Map<string, string>();
	(globalThis as any).localStorage = {
		getItem: (key: string) => store.get(key) ?? null,
		setItem: (key: string, value: string) => void store.set(key, value),
		removeItem: (key: string) => void store.delete(key),
		clear: () => store.clear()
	};
}

beforeEach(() => installStorage());

afterEach(() => {
	vi.doUnmock('$app/environment');
	vi.resetModules();
});

async function reload(persistedTab: Record<string, unknown>) {
	localStorage.setItem(STORAGE_KEY, JSON.stringify({ tabs: [persistedTab], activeTabId: persistedTab.id }));
	vi.doMock('$app/environment', () => ({ browser: true }));
	vi.resetModules();
	const { tabsStore } = await import('$lib/stores/tabs');
	const reconcile = await import('$lib/generation/restore/reconcile');
	const persistence = await import('$lib/stores/tabPersistence');
	return { tabsStore, reconcile, persistence };
}

const BASE = {
	id: 'opened-1',
	name: 'SDXL',
	selectedPreset: 'native/SDXL/base',
	selectedMode: 'txt2img',
	selectedSessionId: null,
	activeGenerationId: 'g1',
	openedFrom: { generationId: 'g1', kind: 'mcp', label: 'Started over MCP · claude desktop' }
};

function runningStatus() {
	return {
		success: true,
		data: { id: 'g1', status: 'running', progress: 0.5, created_at: '2026-11-17T09:00:00Z', started_at: '2026-11-17T09:00:02Z' }
	};
}

describe('a tab opened onto a generation, after a reload', () => {
	it('keeps its binding and source through the saved tab state', async () => {
		const { tabsStore, persistence } = await reload(BASE);
		const tab = generationTabs(get(tabsStore).tabs)[0];
		expect(tab.activeGenerationId).toBe('g1');
		expect(tab.openedFrom).toEqual(BASE.openedFrom);
		expect(persistence.toPersistedTab(tab)).toMatchObject({ activeGenerationId: 'g1', openedFrom: BASE.openedFrom });
	});

	it('follows the generation again although the server never knew a tab for it', async () => {
		const { tabsStore, reconcile } = await reload(BASE);
		const api = {
			getGenerationStatus: vi.fn(async () => runningStatus() as never),
			getGenerationById: vi.fn(async () => ({ success: true, data: { files: [] } }) as never)
		};
		const onSubscribe = vi.fn();

		await reconcile.reconcileTabGenerations('opened-1', api, tabsStore, { extraCandidateIds: [], onSubscribe, subscriptionOwner: {} });

		expect(api.getGenerationStatus).toHaveBeenCalledWith('g1');
		expect(onSubscribe).toHaveBeenCalledWith('g1');
		const tab = generationTabs(get(tabsStore).tabs)[0];
		expect(tab.generation.isGenerating).toBe(true);
		expect(tab.generation.queue.map((q) => q.generation_id)).toEqual(['g1']);
		expect(tab.openedFrom?.generationId).toBe('g1');
	});

	it('takes live progress for the generation right after it follows it again', async () => {
		const { tabsStore, reconcile } = await reload(BASE);
		const api = {
			getGenerationStatus: vi.fn(async () => runningStatus() as never),
			getGenerationById: vi.fn(async () => ({ success: true, data: { files: [] } }) as never)
		};
		await reconcile.reconcileTabGenerations('opened-1', api, tabsStore, { onSubscribe: vi.fn(), subscriptionOwner: {} });
		const { dispatchGenerationMessage } = await import('$lib/stores/generation');
		dispatchGenerationMessage(
			{ type: 'generation_status', generation_id: 'g1', pipe_id: 1, status: 'running', progress: 0.7, current_step: 'Sampling' } as never,
			{ unsubscribe: vi.fn() }
		);
		const tab = generationTabs(get(tabsStore).tabs)[0];
		expect((tab.generation.currentProgress as { progress?: number } | null)?.progress).toBe(0.7);
	});

	it('shows the result when the generation finished while the page was closed', async () => {
		const { tabsStore, reconcile } = await reload(BASE);
		const api = {
			getGenerationStatus: vi.fn(async () => ({ success: true, data: { id: 'g1', status: 'completed', created_at: '2026-11-17T09:00:00Z' } }) as never),
			getGenerationById: vi.fn(async () => ({ success: true, data: { files: [{ file_type: 'IMAGE', file_path: 'generations/g1/0.png', is_derived: false }] } }) as never)
		};
		const onSubscribe = vi.fn();
		await reconcile.reconcileTabGenerations('opened-1', api, tabsStore, { onSubscribe, subscriptionOwner: {} });
		expect(onSubscribe).not.toHaveBeenCalled();
		const tab = generationTabs(get(tabsStore).tabs)[0];
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.currentGeneration?.status).toBe('completed');
		expect(tab.generation.batchImages).toHaveLength(1);
	});
});
