import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { generationTabs, type GenerationTab, type GenerationState } from '$lib/types/tabs';
import {
	setGenerationOutputs,
	isGenerationOutputsRetired,
	resetGenerationOutputsRetirementForTests
} from './messages/generationOutputs';
import {
	clearDeletedGeneration,
	deleteTabGeneration,
	isGenerationLive,
	tabDeleteTarget,
	type DeleteTabGenerationDeps
} from './deleteTabGeneration';

const IMAGE = { url: '/api/media/generations/gen-1/1.png', originalUrl: '/api/media/generations/gen-1/1.png' };

function tabById(tabId: string): GenerationTab {
	return generationTabs(get(tabsStore).tabs).find((tab) => tab.id === tabId)!;
}

function firstTabId(): string {
	return generationTabs(get(tabsStore).tabs)[0].id;
}

function seed(tabId: string, generation: Partial<GenerationState>, activeGenerationId: string | null = null) {
	const tab = tabById(tabId);
	tabsStore.updateTab(tabId, { activeGenerationId, generation: { ...tab.generation, ...generation } });
}

function seedCompleted(tabId: string, id = 'gen-1') {
	seed(tabId, {
		currentGeneration: { id, status: 'completed' },
		batchImages: [IMAGE, IMAGE],
		batchVideos: [],
		batchAudios: [],
		artifacts: [{ artifact_type: 'seed', artifact_data: { seed: 1 } }],
		workbenchIndex: 1,
		workbenchTotal: 2
	});
}

function seedRunning(tabId: string, id = 'gen-1') {
	seed(
		tabId,
		{
			isGenerating: true,
			currentGeneration: { id, status: 'running' },
			currentProgress: { step: 'sampling', progress: 0.4 },
			batchImages: [IMAGE],
			queue: [{ generation_id: id, queue_position: null, status: 'running' }]
		},
		id
	);
}

function deps(overrides: Partial<DeleteTabGenerationDeps> = {}) {
	const calls: string[] = [];
	const value: DeleteTabGenerationDeps = {
		tabsStore,
		unsubscribe: vi.fn(),
		cancel: vi.fn(async (id: string) => {
			calls.push(`cancel:${id}`);
			return { success: true, data: { cancel_notice: null } };
		}),
		remove: vi.fn(async (id: string) => {
			calls.push(`remove:${id}`);
			return { success: true, data: { message: 'ok' } };
		}),
		...overrides
	};
	return { deps: value, calls };
}

function expectEmptyWorkbench(tab: GenerationTab) {
	expect(tab.generation.currentGeneration).toBeNull();
	expect(tab.generation.isGenerating).toBe(false);
	expect(tab.generation.currentProgress).toBeNull();
	expect(tab.generation.batchImages).toEqual([]);
	expect(tab.generation.batchVideos).toEqual([]);
	expect(tab.generation.batchAudios).toEqual([]);
	expect(tab.generation.batchMeshes).toEqual([]);
	expect(tab.generation.artifacts).toEqual([]);
	expect(tab.generation.workbenchIndex).toBe(0);
	expect(tab.generation.workbenchTotal).toBe(0);
	expect(tab.generation.cancelledGenerationId ?? null).toBeNull();
	expect(tab.generation.cancelNotice ?? null).toBeNull();
}

beforeEach(() => {
	tabsStore.reset();
	resetGenerationOutputsRetirementForTests();
});

describe('which generation the tab would delete', () => {
	it('is nothing for a missing tab or a tab with no generation', () => {
		expect(tabDeleteTarget(null)).toBeNull();
		expect(tabDeleteTarget(undefined)).toBeNull();
		expect(tabDeleteTarget(tabById(firstTabId()))).toBeNull();
	});

	it('is the generation the workbench shows', () => {
		const tabId = firstTabId();
		seedCompleted(tabId, 'gen-7');
		expect(tabDeleteTarget(tabById(tabId))).toEqual({ tabId, generationId: 'gen-7' });
	});

	it('reads generation_id when the shown generation has no id', () => {
		const tabId = firstTabId();
		seed(tabId, { currentGeneration: { generation_id: 'gen-8', status: 'completed' } });
		expect(tabDeleteTarget(tabById(tabId))).toEqual({ tabId, generationId: 'gen-8' });
	});

	it('is the queued generation the tab owns before anything is shown', () => {
		const tabId = firstTabId();
		seed(tabId, { currentGeneration: null }, 'gen-queued');
		expect(tabDeleteTarget(tabById(tabId))).toEqual({ tabId, generationId: 'gen-queued' });
	});
});

describe('whether the generation is still queued or running', () => {
	it('is not for a completed or failed result', () => {
		const tabId = firstTabId();
		seedCompleted(tabId);
		expect(isGenerationLive(tabById(tabId), 'gen-1')).toBe(false);
		seed(tabId, { currentGeneration: { id: 'gen-1', status: 'failed' } });
		expect(isGenerationLive(tabById(tabId), 'gen-1')).toBe(false);
	});

	it('is for the generation the tab owns, one in its queue, or one shown as pending or running', () => {
		const tabId = firstTabId();
		seed(tabId, { currentGeneration: null }, 'gen-1');
		expect(isGenerationLive(tabById(tabId), 'gen-1')).toBe(true);
		seed(tabId, { currentGeneration: null, queue: [{ generation_id: 'gen-2', queue_position: 1, status: 'pending' }] });
		expect(isGenerationLive(tabById(tabId), 'gen-2')).toBe(true);
		for (const status of ['pending', 'queued', 'running']) {
			seed(tabId, { currentGeneration: { id: 'gen-3', status }, queue: [] });
			expect(isGenerationLive(tabById(tabId), 'gen-3')).toBe(true);
		}
	});
});

describe('deleting the tab generation', () => {
	it('deletes a finished generation without cancelling it and leaves the workbench empty', async () => {
		const tabId = firstTabId();
		seedCompleted(tabId);
		const { deps: d, calls } = deps();
		await deleteTabGeneration({ tabId, generationId: 'gen-1' }, d);
		expect(calls).toEqual(['remove:gen-1']);
		expectEmptyWorkbench(tabById(tabId));
	});

	it('cancels a running generation first, then deletes it, then empties the workbench', async () => {
		const tabId = firstTabId();
		seedRunning(tabId);
		const { deps: d, calls } = deps();
		await deleteTabGeneration({ tabId, generationId: 'gen-1' }, d);
		expect(calls).toEqual(['cancel:gen-1', 'remove:gen-1']);
		const tab = tabById(tabId);
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.queue).toEqual([]);
		expectEmptyWorkbench(tab);
	});

	it('empties the workbench instead of showing the cancel notice when the server sends one', async () => {
		const tabId = firstTabId();
		seedRunning(tabId);
		const { deps: d } = deps({
			cancel: vi.fn(async () => ({ success: true, data: { cancel_notice: 'Stopped after the current step' } }))
		});
		await deleteTabGeneration({ tabId, generationId: 'gen-1' }, d);
		expectEmptyWorkbench(tabById(tabId));
	});

	it('keeps a generation the tab adopted between the cancel and the delete', async () => {
		const tabId = firstTabId();
		seedRunning(tabId, 'gen-1');
		const { deps: d } = deps({
			remove: vi.fn(async () => {
				seedRunning(tabId, 'gen-2');
				return { success: true };
			})
		});
		await deleteTabGeneration({ tabId, generationId: 'gen-1' }, d);
		const tab = tabById(tabId);
		expect(tab.activeGenerationId).toBe('gen-2');
		expect(tab.generation.currentGeneration).toEqual({ id: 'gen-2', status: 'running' });
		expect(tab.generation.isGenerating).toBe(true);
	});

	it('cancels a generation that is only queued before deleting it', async () => {
		const tabId = firstTabId();
		seedCompleted(tabId, 'gen-old');
		seed(tabId, { queue: [{ generation_id: 'gen-next', queue_position: 2, status: 'pending' }] });
		const { deps: d, calls } = deps();
		await deleteTabGeneration({ tabId, generationId: 'gen-next' }, d);
		expect(calls).toEqual(['cancel:gen-next', 'remove:gen-next']);
		const tab = tabById(tabId);
		expect(tab.generation.queue).toEqual([]);
		expect(tab.generation.currentGeneration).toEqual({ id: 'gen-old', status: 'completed' });
		expect(tab.generation.batchImages).toHaveLength(2);
	});

	it('does not delete anything when the cancel is refused, and leaves the tab as it was', async () => {
		const tabId = firstTabId();
		seedRunning(tabId);
		const before = tabById(tabId);
		const { deps: d, calls } = deps({ cancel: vi.fn(async () => ({ success: false, message: 'Already finished' })) });
		await expect(deleteTabGeneration({ tabId, generationId: 'gen-1' }, d)).rejects.toThrow('Already finished');
		expect(calls).toEqual([]);
		expect(d.remove).not.toHaveBeenCalled();
		expect(tabById(tabId)).toEqual(before);
	});

	it('does not delete anything when the cancel request fails', async () => {
		const tabId = firstTabId();
		seedRunning(tabId);
		const failure = new Error('network down');
		const { deps: d } = deps({ cancel: vi.fn(async () => Promise.reject(failure)) });
		await expect(deleteTabGeneration({ tabId, generationId: 'gen-1' }, d)).rejects.toBe(failure);
		expect(d.remove).not.toHaveBeenCalled();
		expect(tabById(tabId).generation.batchImages).toHaveLength(1);
	});

	it('keeps the result on screen when the delete fails, so the error is not hidden behind an empty workbench', async () => {
		const tabId = firstTabId();
		seedCompleted(tabId);
		const blocked = Object.assign(new Error('Request failed'), {
			response: { data: { detail: { blocking_runs: [{ elixir_id: 'e', run_id: 'r', number: 1 }] } } }
		});
		const { deps: d } = deps({ remove: vi.fn(async () => Promise.reject(blocked)) });
		await expect(deleteTabGeneration({ tabId, generationId: 'gen-1' }, d)).rejects.toBe(blocked);
		const tab = tabById(tabId);
		expect(tab.generation.currentGeneration).toEqual({ id: 'gen-1', status: 'completed' });
		expect(tab.generation.batchImages).toHaveLength(2);
	});

	it('throws when the server answers the delete with success false', async () => {
		const tabId = firstTabId();
		seedCompleted(tabId);
		const { deps: d } = deps({ remove: vi.fn(async () => ({ success: false, message: 'Not yours' })) });
		await expect(deleteTabGeneration({ tabId, generationId: 'gen-1' }, d)).rejects.toThrow('Not yours');
		expect(tabById(tabId).generation.batchImages).toHaveLength(2);
	});

	it('leaves a newer generation the tab moved on to untouched', async () => {
		const tabId = firstTabId();
		seedCompleted(tabId, 'gen-1');
		const { deps: d } = deps({
			remove: vi.fn(async () => {
				seedCompleted(tabId, 'gen-2');
				return { success: true };
			})
		});
		await deleteTabGeneration({ tabId, generationId: 'gen-1' }, d);
		const tab = tabById(tabId);
		expect(tab.generation.currentGeneration).toEqual({ id: 'gen-2', status: 'completed' });
		expect(tab.generation.batchImages).toHaveLength(2);
	});

	it('clears only the tab it was asked for, even after the user switched tabs', async () => {
		const tabId = firstTabId();
		seedCompleted(tabId, 'gen-1');
		tabsStore.addTab();
		const otherId = get(tabsStore).activeTabId;
		seedCompleted(otherId, 'gen-9');
		const { deps: d } = deps();
		await deleteTabGeneration({ tabId, generationId: 'gen-1' }, d);
		expectEmptyWorkbench(tabById(tabId));
		expect(tabById(otherId).generation.currentGeneration).toEqual({ id: 'gen-9', status: 'completed' });
		expect(tabById(otherId).generation.batchImages).toHaveLength(2);
	});

	it('still deletes when the tab was closed while the dialog was open', async () => {
		const tabId = firstTabId();
		seedCompleted(tabId, 'gen-1');
		tabsStore.addTab();
		tabsStore.removeTab(tabId);
		const { deps: d, calls } = deps();
		await deleteTabGeneration({ tabId, generationId: 'gen-1' }, d);
		expect(calls).toEqual(['remove:gen-1']);
	});

	it('drops the cached outputs of the deleted generation', async () => {
		const tabId = firstTabId();
		seedCompleted(tabId, 'gen-1');
		setGenerationOutputs('gen-1', { images: [IMAGE], videos: [], audios: [], meshes: [] } as never);
		const { deps: d } = deps();
		await deleteTabGeneration({ tabId, generationId: 'gen-1' }, d);
		expect(isGenerationOutputsRetired('gen-1')).toBe(true);
		expect(d.unsubscribe).toHaveBeenCalledWith('gen-1');
	});
});

describe('emptying the workbench after a delete', () => {
	it('is a no-op for a tab that no longer exists', () => {
		const before = get(tabsStore);
		clearDeletedGeneration(tabsStore, 'no-such-tab', 'gen-1');
		expect(get(tabsStore)).toEqual(before);
	});

	it('only drops the queue entry when the tab shows something else', () => {
		const tabId = firstTabId();
		seedCompleted(tabId, 'gen-1');
		seed(tabId, { queue: [{ generation_id: 'gen-2', queue_position: 1, status: 'pending' }] });
		clearDeletedGeneration(tabsStore, tabId, 'gen-2');
		const tab = tabById(tabId);
		expect(tab.generation.queue).toEqual([]);
		expect(tab.generation.currentGeneration).toEqual({ id: 'gen-1', status: 'completed' });
	});
});
