import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get, writable } from 'svelte/store';

const state = vi.hoisted(() => ({ detail: null as unknown }));

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn(async () => ({ success: true, data: state.detail })),
		getGenerationParams: vi.fn(),
		updateGenerationTags: vi.fn(),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({
			get: vi.fn().mockResolvedValue({ data: { success: true, data: { hint: 'Try a smaller size.' } } }),
			post: vi.fn()
		})
	}
}));

vi.mock('$app/stores', () => ({ page: writable({ url: new URL('http://localhost/generate') }) }));
vi.mock('@google/model-viewer', () => ({}));
vi.mock('$lib/stores/auth', async (importOriginal) => ({
	...(await importOriginal<object>()),
	authStore: writable({ user: { account_type: 'ADMIN' } })
}));

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { default: Workbench } = await import('$lib/components/Workbench.svelte');
const { openGenerationInNewTab } = await import('$lib/generation/attach/openInTabDefault');
const { setGenerationAttachHost } = await import('$lib/generation/restore/attachHost');
const { tabsStore } = await import('$lib/stores/tabs');
const { toasts } = await import('$lib/stores/toast');
const { generationTabs } = await import('$lib/types/tabs');
const { createClassComponent } = await import('svelte/legacy');
const { settle } = await import('./helpers/settle');

function detail(over: Record<string, unknown>) {
	return {
		id: 'g1',
		preset_id: 'native/SDXL/base',
		preset_name: 'SDXL',
		mode: 'txt2img',
		form_name: 'default',
		form_data: { seed: 7 },
		prompt_state: { prompt: 'a lighthouse' },
		status: 'completed',
		progress: 1,
		created_at: '2026-11-17T09:00:00+00:00',
		updated_at: '2026-11-17T09:00:00+00:00',
		files: [],
		rating: 0,
		is_favorite: false,
		origin: { kind: 'mcp', key_id: 'k1', key_name: 'claude desktop' },
		...over
	};
}

let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void } | undefined;
const makeHost = () => vi.fn(async (_tabId: string, _generationIds: string[]) => {});
let host: ReturnType<typeof makeHost>;

beforeEach(() => {
	tabsStore.reset();
	originalRO = (globalThis as { ResizeObserver?: unknown }).ResizeObserver;
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = FakeResizeObserver;
	target = document.body.appendChild(document.createElement('div'));
	host = makeHost();
	setGenerationAttachHost(host);
	for (const toast of get(toasts)) toasts.remove(toast.id);
});

afterEach(() => {
	component?.$destroy();
	component = undefined;
	target.remove();
	setGenerationAttachHost(null);
	tabsStore.reset();
	(globalThis as { ResizeObserver?: unknown }).ResizeObserver = originalRO;
});

function showInWorkbench(tabId: string) {
	const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === tabId)!;
	component = createClassComponent({
		component: Workbench as never,
		target,
		props: { emptyLayout: 'pane', currentGeneration: tab.generation.currentGeneration, isGenerating: tab.generation.isGenerating, workbenchIndex: 0, workbenchTotal: 0 }
	}) as never;
}

describe('opening a generation in a tab', () => {
	it('shows the reason a failed generation failed, with its hint, and does not follow it', async () => {
		state.detail = detail({ status: 'failed', error_message: 'Out of GPU memory', error_user_message: 'Not enough memory', error_code: 'cuda_oom', error_id: 'E-1', error_contact_admin: false });
		const result = await openGenerationInNewTab('g1');
		expect(result.kind).toBe('opened');
		showInWorkbench((result as { tabId: string }).tabId);
		await settle();
		const text = target.textContent ?? '';
		expect(text).toContain('Generation failed');
		expect(text).toContain('Not enough memory');
		expect(text).toContain('Try a smaller size.');
		expect(host).not.toHaveBeenCalled();
	});

	it('opens one that finished long ago from its history, without following it or complaining', async () => {
		state.detail = detail({ status: 'completed', files: [{ file_type: 'IMAGE', file_path: 'generations/g1/0.png', is_derived: false }] });
		const result = await openGenerationInNewTab('g1');
		const tab = generationTabs(get(tabsStore).tabs).find((t) => t.id === (result as { tabId: string }).tabId)!;
		expect(host).not.toHaveBeenCalled();
		expect(get(toasts).filter((toast) => toast.type === 'error')).toEqual([]);
		expect(tab.generation.currentGeneration?.status).toBe('completed');
		expect(tab.generation.batchImages).toHaveLength(1);
		expect(tab.activeGenerationId).toBeNull();
	});

	it('says so plainly and leaves no half-built tab when the generation is not there', async () => {
		const { api } = await import('$lib/services/api/index');
		vi.mocked(api.getGenerationById).mockRejectedValueOnce(Object.assign(new Error('missing'), { response: { status: 404 } }));
		const before = generationTabs(get(tabsStore).tabs).length;
		const result = await openGenerationInNewTab('g1');
		expect(result.kind).toBe('missing');
		expect(generationTabs(get(tabsStore).tabs)).toHaveLength(before);
		expect(get(toasts).map((toast) => toast.message)).toContain('This generation is no longer available.');
		expect(host).not.toHaveBeenCalled();
	});

	it('follows a running one through the attach path', async () => {
		state.detail = detail({ status: 'running', progress: 0.3 });
		const result = await openGenerationInNewTab('g1');
		expect(host).toHaveBeenCalledWith((result as { tabId: string }).tabId, ['g1']);
	});
});
