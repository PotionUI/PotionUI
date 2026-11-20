import { beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { generationTabs, type GenerationTab } from '$lib/types/tabs';
import type { GenerationHistoryItem } from '$lib/types/history';
import { buildHistoryReuseTabData } from '$lib/utils/historyReuse';
import { resetPreviewReplayForTests, shouldApplyPreview } from '$lib/generation/messages/replayGuard';
import {
	buildOpenedTab,
	MISSING_COPY,
	openGenerationInTab,
	UNREACHABLE_COPY,
	UNSUPPORTED_COPY,
	type OpenInTabDeps
} from './openInTab';

const MCP = { kind: 'mcp', key_id: 'k1', key_name: 'claude desktop', key_current_name: 'claude desktop' };

function detail(overrides: Partial<GenerationHistoryItem> = {}): GenerationHistoryItem {
	return {
		id: 'g1',
		preset_id: 'native/SDXL/base',
		preset_name: 'SDXL',
		mode: 'txt2img',
		form_name: 'default',
		form_data: { width: 1024, height: 768, seed: 7 },
		prompt_state: { prompt: 'a lighthouse', negativePrompt: 'blur' },
		status: 'running',
		progress: 0.4,
		created_at: '2026-11-17T09:00:00+00:00',
		updated_at: '2026-11-17T09:00:00+00:00',
		files: [],
		rating: 0,
		is_favorite: false,
		origin: MCP,
		...overrides
	} as GenerationHistoryItem;
}

function makeDeps(response: unknown, extra: Partial<OpenInTabDeps> = {}) {
	const deps: OpenInTabDeps = {
		getGeneration: vi.fn(async () => {
			if (response instanceof Error) throw response;
			return response as never;
		}),
		failureHint: vi.fn(async () => 'Try a smaller size.'),
		isAdmin: () => false,
		tabs: {
			current: () => get(tabsStore),
			addTabWithData: (name, data) => tabsStore.addTabWithData(name, data),
			setActiveTab: vi.fn((id: string) => tabsStore.setActiveTab(id)),
			updateTab: (id, updates) => tabsStore.updateTab(id, updates)
		},
		attach: vi.fn(async () => true),
		notifyError: vi.fn(),
		announceReuse: vi.fn(),
		...extra
	};
	return deps;
}

const ok = (data: GenerationHistoryItem) => ({ success: true, data });
const tabsNow = () => generationTabs(get(tabsStore).tabs);
const tabById = (id: string) => tabsNow().find((t) => t.id === id)!;

beforeEach(() => {
	tabsStore.reset();
	resetPreviewReplayForTests();
});

describe('buildOpenedTab', () => {
	it('fills the tab exactly as Reuse would, plus the binding to the generation', () => {
		const source = detail();
		const plan = buildOpenedTab(source, { isAdmin: false, hint: null });
		const reuse = buildHistoryReuseTabData(source).tabData;
		const { openedFrom, activeGenerationId, ...rest } = plan.tabData;
		expect(rest).toEqual(reuse);
		expect(activeGenerationId).toBe('g1');
		expect(openedFrom).toEqual({ generationId: 'g1', kind: 'mcp', label: 'Started over MCP · claude desktop' });
		expect(plan.name).toBe('SDXL');
		expect(plan.live).toBe(true);
	});

	it('does not bind a finished generation to the live path', () => {
		const plan = buildOpenedTab(detail({ status: 'completed' }), { isAdmin: false, hint: null });
		expect(plan.tabData.activeGenerationId).toBeUndefined();
		expect(plan.live).toBe(false);
	});

	it('carries a failure with its reason, and the hint an admin can read', () => {
		const failed = detail({ status: 'failed', error_message: 'Out of memory', error_user_message: 'Not enough memory', error_contact_admin: true, error_code: 'oom', error_id: 'E-1' });
		const plan = buildOpenedTab(failed, { isAdmin: false, hint: 'Try a smaller size.' });
		expect(plan.settled?.currentGeneration).toMatchObject({
			status: 'failed',
			message: 'Not enough memory',
			hint: 'Try a smaller size.',
			errorId: 'E-1',
			errorCode: 'oom'
		});
	});

	it('keeps raw failure text from a member who is not allowed to read it', () => {
		const plan = buildOpenedTab(detail({ status: 'failed', error_message: 'Traceback /srv/app' }), { isAdmin: false, hint: null });
		expect(plan.settled?.currentGeneration).toMatchObject({ message: 'This generation failed.', errorId: null });
	});

	it('opens a cancelled one empty', () => {
		const plan = buildOpenedTab(detail({ status: 'cancelled' }), { isAdmin: false, hint: null });
		expect(plan.settled).toEqual({ isGenerating: false, currentGeneration: null });
	});
});

describe('openGenerationInTab', () => {
	it('opens a new active tab for a running generation and binds it through the attach path', async () => {
		const before = tabsNow().length;
		const active = get(tabsStore).activeTabId;
		const deps = makeDeps(ok(detail()));
		const result = await openGenerationInTab('g1', deps);
		expect(result).toMatchObject({ kind: 'opened', live: true });
		expect(tabsNow()).toHaveLength(before + 1);
		const tabId = (result as { tabId: string }).tabId;
		expect(get(tabsStore).activeTabId).toBe(tabId);
		expect(tabById(active)).toBeDefined();
		expect(tabById(tabId)).toMatchObject({
			name: 'SDXL',
			selectedPreset: 'native/SDXL/base',
			selectedMode: 'txt2img',
			selectedVariant: 'default',
			prompt: 'a lighthouse',
			activeGenerationId: 'g1',
			formData: { width: 1024, height: 768, seed: 7 }
		});
		expect(deps.attach).toHaveBeenCalledWith(tabId, ['g1']);
	});

	it('never overwrites the work in the tab that was open', async () => {
		const first = tabsNow()[0];
		tabsStore.updateTab(first.id, { prompt: 'my own work', selectedPreset: 'native/Flux/base' });
		await openGenerationInTab('g1', makeDeps(ok(detail())));
		expect(tabById(first.id)).toMatchObject({ prompt: 'my own work', selectedPreset: 'native/Flux/base' });
	});

	it('focuses the tab already following the generation instead of opening a second', async () => {
		const deps = makeDeps(ok(detail()));
		const first = await openGenerationInTab('g1', deps);
		const count = tabsNow().length;
		tabsStore.setActiveTab(tabsNow()[0].id);
		const second = await openGenerationInTab('g1', deps);
		expect(second).toEqual({ kind: 'focused', tabId: (first as { tabId: string }).tabId });
		expect(tabsNow()).toHaveLength(count);
		expect(get(tabsStore).activeTabId).toBe((first as { tabId: string }).tabId);
		expect(deps.getGeneration).toHaveBeenCalledTimes(1);
	});

	it('focuses the same tab for a finished generation opened twice', async () => {
		const deps = makeDeps(ok(detail({ status: 'completed' })));
		const first = await openGenerationInTab('g1', deps);
		const second = await openGenerationInTab('g1', deps);
		expect(second.kind).toBe('focused');
		expect((second as { tabId: string }).tabId).toBe((first as { tabId: string }).tabId);
	});

	it('opens two quick presses once', async () => {
		const deps = makeDeps(ok(detail()));
		const [a, b] = await Promise.all([openGenerationInTab('g1', deps), openGenerationInTab('g1', deps)]);
		expect(a).toEqual(b);
		expect(deps.getGeneration).toHaveBeenCalledTimes(1);
	});

	it('opens one that finished long ago from its history, with the result and no live follow', async () => {
		const files = [{ file_type: 'IMAGE', file_path: 'generations/g1/0.png', is_derived: false }];
		const deps = makeDeps(ok(detail({ status: 'completed', files: files as never })));
		const result = await openGenerationInTab('g1', deps);
		expect(result).toMatchObject({ kind: 'opened', live: false });
		const tab = tabById((result as { tabId: string }).tabId);
		expect(deps.attach).not.toHaveBeenCalled();
		expect(deps.notifyError).not.toHaveBeenCalled();
		expect(tab.activeGenerationId).toBeNull();
		expect(tab.generation.isGenerating).toBe(false);
		expect(tab.generation.currentGeneration?.status).toBe('completed');
		expect(tab.generation.batchImages).toHaveLength(1);
	});

	it('opens a failed one with its failure showing, without following it', async () => {
		const failed = detail({ status: 'failed', error_user_message: 'Not enough memory', error_contact_admin: true, error_code: 'oom' });
		const deps = makeDeps(ok(failed), { isAdmin: () => true });
		const result = await openGenerationInTab('g1', deps);
		const tab: GenerationTab = tabById((result as { tabId: string }).tabId);
		expect(deps.attach).not.toHaveBeenCalled();
		expect(deps.failureHint).toHaveBeenCalledWith('g1');
		expect(tab.generation.currentGeneration).toMatchObject({ status: 'failed', message: 'Not enough memory', hint: 'Try a smaller size.' });
	});

	it('leaves no tab behind and says so plainly when the generation is not there', async () => {
		const before = tabsNow().length;
		const deps = makeDeps(Object.assign(new Error('nope'), { response: { status: 404 } }));
		const result = await openGenerationInTab('g1', deps);
		expect(result).toEqual({ kind: 'missing' });
		expect(tabsNow()).toHaveLength(before);
		expect(deps.notifyError).toHaveBeenCalledWith(MISSING_COPY);
		expect(deps.attach).not.toHaveBeenCalled();
	});

	it('reads a not-found answer inside the response the same way', async () => {
		const deps = makeDeps({ success: false, error: 'generation_not_found' });
		expect(await openGenerationInTab('g1', deps)).toEqual({ kind: 'missing' });
	});

	it('leaves no tab behind when the server cannot be reached', async () => {
		const before = tabsNow().length;
		const deps = makeDeps(new Error('Network Error'));
		expect(await openGenerationInTab('g1', deps)).toEqual({ kind: 'unreachable' });
		expect(tabsNow()).toHaveLength(before);
		expect(deps.notifyError).toHaveBeenCalledWith(UNREACHABLE_COPY);
	});

	it.each([
		['an editor run', { kind: 'editor', edit_id: 'e1', step_id: 's1', task: 'edit_region', run_id: 'r1' }, 'editor:edit_region'],
		['a chat run', { kind: 'chat' }, 'txt2img'],
		['a tab-started run', null, 'txt2img']
	])('refuses %s and creates no tab', async (_label, origin, mode) => {
		const before = tabsNow().length;
		const deps = makeDeps(ok(detail({ origin: origin as never, mode })));
		const result = await openGenerationInTab('g1', deps);
		expect(result).toEqual({ kind: 'unsupported' });
		expect(tabsNow()).toHaveLength(before);
		expect(deps.attach).not.toHaveBeenCalled();
		expect(deps.notifyError).toHaveBeenCalledWith(UNSUPPORTED_COPY);
	});

	it('refuses a generation in an editor mode even if it claims an MCP origin', async () => {
		const deps = makeDeps(ok(detail({ mode: 'editor:edit_region' })));
		expect(await openGenerationInTab('g1', deps)).toEqual({ kind: 'unsupported' });
	});

	it('lets the replayed preview of a generation through again in the new tab', async () => {
		shouldApplyPreview('g1', 'frame', { replayed: true });
		await openGenerationInTab('g1', makeDeps(ok(detail())));
		expect(shouldApplyPreview('g1', 'frame', { replayed: true })).toBe(true);
	});
});
