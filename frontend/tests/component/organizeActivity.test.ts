// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';

const api = vi.hoisted(() => ({
	getOrganizeActivity: vi.fn(),
	undoOrganizeRun: vi.fn()
}));
const confirmDialog = vi.hoisted(() => vi.fn());
const toasts = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() }));

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return { ...actual, api: { ...actual.api, ...api } };
});
vi.mock('$lib/stores/confirm', () => ({ confirmDialog }));
vi.mock('$lib/stores/toast', () => ({ toasts }));

const { default: ActivityList } = await import('../../src/lib/components/organize/ActivityList.svelte');

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: ReturnType<typeof mount> | null = null;
let target: HTMLElement;

function run(over: Record<string, unknown> = {}) {
	return {
		id: 'run1',
		rule_id: 'r1',
		rule_name: 'Krea landscapes',
		rule_deleted: false,
		subject: 'generation',
		kind: 'backfill',
		status: 'completed',
		started_at: '2026-10-05T09:12:00+00:00',
		finished_at: '2026-10-05T09:12:09+00:00',
		matched: 214,
		applied: 198,
		undone: 0,
		can_undo: true,
		changes: {
			collections: [{ id: 'c1', name: 'Landscapes', count: 198, live: 198, created: true }],
			tags: [{ id: 't1', name: 'landscape', count: 120, live: 120 }],
			other: []
		},
		...over
	};
}

beforeEach(() => {
	target = document.createElement('div');
	document.body.appendChild(target);
	api.getOrganizeActivity.mockReset();
	api.undoOrganizeRun.mockReset();
	confirmDialog.mockReset();
	api.getOrganizeActivity.mockResolvedValue({ success: true, data: { runs: [run()], next_before: null } });
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target.remove();
});

function undoButton(): HTMLButtonElement | undefined {
	return [...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Undo') as HTMLButtonElement | undefined;
}

describe('ActivityList', () => {
	it('lists runs with what was added in plain words', async () => {
		instance = mount(ActivityList, { target, props: { subject: 'generation' } });
		await settle();
		expect(api.getOrganizeActivity).toHaveBeenCalledWith({ subject: 'generation', rule_id: undefined, limit: 50, before: undefined });
		const text = target.textContent ?? '';
		expect(text).toContain('Krea landscapes');
		expect(text).toContain('Added 198 items to Landscapes (new collection)');
		expect(text).toContain('Tagged 120 items "landscape"');
		expect(text).toContain('Existing items');
	});

	it('undoes a run after confirmation and marks it undone', async () => {
		confirmDialog.mockResolvedValue(true);
		api.undoOrganizeRun.mockResolvedValue({
			success: true,
			data: { undone: 198, items: 99, skipped: 0, run: run({ status: 'undone', can_undo: false, undone: 198 }) }
		});
		const onundone = vi.fn();
		instance = mount(ActivityList, { target, props: { subject: 'generation', onundone } });
		await settle();
		undoButton()!.click();
		await settle();
		expect(confirmDialog).toHaveBeenCalledTimes(1);
		expect(api.undoOrganizeRun).toHaveBeenCalledWith('run1');
		expect(onundone).toHaveBeenCalled();
		expect(undoButton()).toBeUndefined();
		expect(target.textContent).toContain('Undone');
		expect(toasts.success).toHaveBeenCalledWith('Undid this run for 99 items');
	});

	it('does nothing when the confirmation is declined', async () => {
		confirmDialog.mockResolvedValue(false);
		instance = mount(ActivityList, { target, props: { subject: 'generation' } });
		await settle();
		undoButton()!.click();
		await settle();
		expect(api.undoOrganizeRun).not.toHaveBeenCalled();
		expect(undoButton()).toBeDefined();
	});

	it('offers no undo for a run that is already undone and names deleted rules', async () => {
		api.getOrganizeActivity.mockResolvedValue({
			success: true,
			data: { runs: [run({ status: 'undone', can_undo: false, rule_deleted: true })], next_before: null }
		});
		instance = mount(ActivityList, { target, props: { subject: 'generation' } });
		await settle();
		expect(undoButton()).toBeUndefined();
		expect(target.textContent).toContain('deleted rule');
	});

	it('pages older runs with the cursor', async () => {
		api.getOrganizeActivity
			.mockResolvedValueOnce({ success: true, data: { runs: [run()], next_before: 'run1' } })
			.mockResolvedValueOnce({ success: true, data: { runs: [run({ id: 'run0', rule_name: 'Older rule' })], next_before: null } });
		instance = mount(ActivityList, { target, props: { subject: 'generation' } });
		await settle();
		([...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Show older') as HTMLButtonElement).click();
		await settle();
		expect(api.getOrganizeActivity).toHaveBeenLastCalledWith({ subject: 'generation', rule_id: undefined, limit: 50, before: 'run1' });
		expect(target.textContent).toContain('Older rule');
	});

	it('shows an empty state with no runs', async () => {
		api.getOrganizeActivity.mockResolvedValue({ success: true, data: { runs: [], next_before: null } });
		instance = mount(ActivityList, { target, props: { subject: 'generation' } });
		await settle();
		expect(target.textContent).toContain('No activity yet');
	});
});
