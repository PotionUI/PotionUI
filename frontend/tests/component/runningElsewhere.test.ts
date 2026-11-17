import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { mountComponent } from './helpers/mount';
import { settle } from './helpers/settle';

const detailOf = vi.hoisted(() => ({
	current: null as unknown
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn(async () => ({ success: true, data: detailOf.current })),
		getModeLabels: vi.fn().mockResolvedValue({ success: false }),
		getClient: vi.fn(() => ({ get: vi.fn().mockRejectedValue(new Error('not mocked')) })),
		getBaseURL: () => '',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		setOnTokenRefreshed: vi.fn()
	}
}));

const { default: RunningElsewhere } = await import('../../src/routes/generate/components/RunningElsewhere.svelte');
const { ingestQueueSnapshot, resetElsewhereForTests } = await import('$lib/generation/elsewhere/store');
const { setGenerationAttachHost } = await import('$lib/generation/restore/attachHost');
const { tabsStore } = await import('$lib/stores/tabs');
const { generationTabs } = await import('$lib/types/tabs');

const MCP = { kind: 'mcp', key_id: 'k1', key_name: 'claude desktop', key_current_name: 'claude desktop' };

function queueItem(id: string, extra: Record<string, unknown> = {}) {
	return {
		generation_id: id,
		tab_id: null,
		has_tab: false,
		status: 'running',
		preset_name: 'SDXL',
		mode: 'txt2img',
		created_at: new Date(Date.now() - 130_000).toISOString(),
		started_at: new Date(Date.now() - 125_000).toISOString(),
		origin: MCP,
		progress: null,
		...extra
	};
}

function snapshot(running: unknown[], pending: unknown[] = []) {
	return { running, pending } as never;
}

function runningDetail(id = 'g1') {
	return {
		id,
		preset_id: 'native/SDXL/base',
		preset_name: 'SDXL',
		mode: 'txt2img',
		form_name: 'default',
		form_data: { width: 1024, seed: 7 },
		prompt_state: { prompt: 'a lighthouse' },
		status: 'running',
		progress: 0.4,
		created_at: '2026-11-17T09:00:00+00:00',
		updated_at: '2026-11-17T09:00:00+00:00',
		files: [],
		origin: MCP
	};
}

const trigger = () => document.body.querySelector<HTMLButtonElement>('[data-testid="running-elsewhere"]');
const rows = () => [...document.body.querySelectorAll<HTMLElement>('[data-elsewhere-row]')];

function pointerdown(el: Element) {
	el.dispatchEvent(new Event('pointerdown', { bubbles: true, cancelable: true }));
}

async function openList() {
	pointerdown(trigger()!);
	trigger()!.click();
	await settle();
}

const makeHost = () => vi.fn(async (_tabId: string, _generationIds: string[]) => {});
let host: ReturnType<typeof makeHost>;

beforeEach(() => {
	tabsStore.reset();
	resetElsewhereForTests();
	detailOf.current = runningDetail();
	host = makeHost();
	setGenerationAttachHost(host);
});

afterEach(() => {
	setGenerationAttachHost(null);
	tabsStore.reset();
	document.body.innerHTML = '';
});

describe('the running elsewhere indicator', () => {
	it('is not there when nothing is running elsewhere', async () => {
		const view = mountComponent(RunningElsewhere);
		await settle();
		expect(trigger()).toBeNull();
		ingestQueueSnapshot(snapshot([]));
		await settle();
		expect(trigger()).toBeNull();
		view.destroy();
	});

	it('ignores editor runs, chat runs and untagged ones', async () => {
		ingestQueueSnapshot(
			snapshot([
				queueItem('e1', { origin: { kind: 'editor', edit_id: 'x', step_id: null, task: 'edit_region', run_id: 'r' }, mode: 'editor:edit_region' }),
				queueItem('c1', { origin: { kind: 'chat' } }),
				queueItem('n1', { origin: null })
			])
		);
		const view = mountComponent(RunningElsewhere);
		await settle();
		expect(trigger()).toBeNull();
		ingestQueueSnapshot(snapshot([queueItem('e1', { origin: { kind: 'editor' } }), queueItem('m1')]), Date.now() + 10_000);
		await settle();
		expect(trigger()!.textContent?.trim()).toBe('1 running over MCP');
		view.destroy();
	});

	it('is not there for a generation a tab here started', async () => {
		const tabId = generationTabs(get(tabsStore).tabs)[0].id;
		ingestQueueSnapshot(snapshot([queueItem('g1', { tab_id: tabId, has_tab: true })]));
		const view = mountComponent(RunningElsewhere);
		await settle();
		expect(trigger()).toBeNull();
		view.destroy();
	});

	it('shows how many are running elsewhere', async () => {
		ingestQueueSnapshot(snapshot([queueItem('g1')]));
		const view = mountComponent(RunningElsewhere);
		await settle();
		expect(trigger()!.textContent?.trim()).toBe('1 running over MCP');
		ingestQueueSnapshot(snapshot([queueItem('g1'), queueItem('g2')]), Date.now() + 10_000);
		await settle();
		expect(trigger()!.textContent?.trim()).toBe('2 running over MCP');
		view.destroy();
	});

	it('lists each one with what it is, where it came from, how it is going and for how long', async () => {
		ingestQueueSnapshot(
			snapshot(
				[queueItem('g1', { progress: 0.42 })],
				[queueItem('g2', { status: 'pending', queue_position: 1, started_at: null, preset_name: 'Flux', mode: 'img2img', origin: { kind: 'mcp', key_id: 'k2', key_name: 'cursor', key_current_name: 'cursor' } })]
			)
		);
		const view = mountComponent(RunningElsewhere);
		await settle();
		await openList();
		expect(rows()).toHaveLength(2);
		const [first, second] = rows();
		expect(first.textContent).toContain('SDXL');
		expect(first.textContent).toContain('Txt2img');
		expect(first.textContent).toContain('Started over MCP · claude desktop');
		expect(first.querySelector('[data-elsewhere-state]')!.textContent).toBe('Running, 42%');
		expect(first.querySelector('[data-elsewhere-elapsed]')!.textContent).toMatch(/^2m 0\ds$/);
		expect(second.textContent).toContain('Flux');
		expect(second.textContent).toContain('Started over MCP · cursor');
		expect(second.querySelector('[data-elsewhere-state]')!.textContent).toBe('Waiting, #2');
		expect(first.querySelector('button')!.textContent?.trim()).toBe('Open in a tab');
		view.destroy();
	});

	it('is portaled out of the page layout', async () => {
		ingestQueueSnapshot(snapshot([queueItem('g1')]));
		const view = mountComponent(RunningElsewhere);
		await settle();
		await openList();
		expect(view.target.querySelector('[data-elsewhere-row]')).toBeNull();
		expect(document.body.querySelector('[role="menu"] [data-elsewhere-row]')).not.toBeNull();
		view.destroy();
	});

	it('says how many more there are beyond the bounded list', async () => {
		ingestQueueSnapshot(snapshot(Array.from({ length: 11 }, (_, index) => queueItem(`g${index}`))));
		const view = mountComponent(RunningElsewhere);
		await settle();
		await openList();
		expect(rows()).toHaveLength(8);
		expect(document.body.querySelector('[data-elsewhere-more]')!.textContent).toBe('and 3 more');
		view.destroy();
	});

	it('opens the generation in a new tab, follows it, drops it from the list and closes the popover', async () => {
		ingestQueueSnapshot(snapshot([queueItem('g1'), queueItem('g2')]));
		const before = generationTabs(get(tabsStore).tabs).length;
		const view = mountComponent(RunningElsewhere);
		await settle();
		await openList();
		document.body.querySelector<HTMLButtonElement>('[data-elsewhere-open="g1"]')!.click();
		await vi.waitFor(() => expect(generationTabs(get(tabsStore).tabs)).toHaveLength(before + 1));
		await settle();
		const tabs = generationTabs(get(tabsStore).tabs);
		const created = tabs[tabs.length - 1];
		expect(get(tabsStore).activeTabId).toBe(created.id);
		expect(created).toMatchObject({ name: 'SDXL', selectedMode: 'txt2img', activeGenerationId: 'g1', prompt: 'a lighthouse' });
		expect(created.openedFrom).toMatchObject({ generationId: 'g1', kind: 'mcp' });
		expect(host).toHaveBeenCalledWith(created.id, ['g1']);
		expect(document.body.querySelector('[role="menu"]')).toBeNull();
		expect(trigger()!.textContent?.trim()).toBe('1 running over MCP');
		await openList();
		expect(rows().map((row) => row.getAttribute('data-elsewhere-row'))).toEqual(['g2']);
		view.destroy();
	});

	it('opens in a sheet on a phone', async () => {
		ingestQueueSnapshot(snapshot([queueItem('g1')]));
		const view = mountComponent(RunningElsewhere, { mobile: true });
		await settle();
		expect(trigger()!.getAttribute('aria-label')).toBe('1 running over MCP');
		trigger()!.click();
		await settle();
		const sheet = document.body.querySelector('[role="dialog"][aria-label="Started over MCP"]');
		expect(sheet).not.toBeNull();
		expect(sheet!.querySelectorAll('[data-elsewhere-row]')).toHaveLength(1);
		document.body.querySelector<HTMLButtonElement>('[data-elsewhere-open="g1"]')!.click();
		await vi.waitFor(() => expect(host).toHaveBeenCalledTimes(1));
		await settle();
		expect(document.body.querySelector('[role="dialog"][aria-label="Started over MCP"]')).toBeNull();
		view.destroy();
	});
});
