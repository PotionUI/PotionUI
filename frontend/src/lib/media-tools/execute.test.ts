import { describe, it, expect, vi, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const goto = vi.fn();
vi.mock('$app/navigation', () => ({ goto: (...args: unknown[]) => goto(...args) }));

vi.mock('$lib/services/api/mediaTools', () => ({
	startMediaToolRun: vi.fn(),
	getMediaToolRun: vi.fn(),
	cancelMediaToolRun: vi.fn()
}));

import { toasts } from '$lib/stores/toast';
import { resetMediaToolCatalog, toolById } from './catalog';
import { announceOutcome, executeMediaTool, summaryOf } from './execute';
import { SAVED_TO_LIBRARY_ANNOUNCE } from './announce';
import { createMediaToolRuns, type RunsApi } from './runs';
import type { MediaToolRun, ToolDTO } from './types';

const item = { id: 'r2', filename: 'u.mp4', original_filename: 'clip-speed.mp4', media_type: 'video', url: '/u.mp4' };

function completed(overrides: Partial<MediaToolRun> = {}): MediaToolRun {
	return { run_id: 'run-1', status: 'completed', progress: 1, label: '', items: [item], replaced: false, error: null, ...overrides };
}

function setup(result: MediaToolRun = completed()) {
	const start = vi.fn().mockResolvedValue({ run_id: 'run-1' });
	const getRun = vi.fn().mockResolvedValue(result);
	const api: RunsApi = { start, get: getRun, cancel: vi.fn() };
	return { runs: createMediaToolRuns(api), start };
}

const sources = [{ id: 'row-1', kind: 'video' as const, url: '/a.mp4', filename: 'a.mp4' }];

function toastMessages() {
	return get(toasts).map((entry) => `${entry.title}|${entry.message}`);
}

beforeEach(() => {
	goto.mockReset();
	vi.useRealTimers();
	for (const entry of get(toasts)) toasts.remove(entry.id);
	resetMediaToolCatalog();
});

describe('executeMediaTool', () => {
	it('starts one run with the steps, inputs and mode it was given and keys it by source', async () => {
		const { runs, start } = setup();
		const handle = await executeMediaTool({
			label: 'Speed',
			steps: [{ tool: 'speed', params: { speed: 2 } }],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'replace',
			runs
		});

		expect(start).toHaveBeenCalledWith({
			steps: [{ tool: 'speed', params: { speed: 2 } }],
			inputs: [{ item_id: 'row-1' }],
			output: { mode: 'replace' }
		});
		expect(Object.keys(get(runs))).toEqual(['row-1']);
		expect(get(runs)['row-1']).toMatchObject({ toolId: 'speed', label: 'Speed', outputMode: 'replace' });
		await handle.done;
	});

	it('resolves with the items once the run completes', async () => {
		const { runs } = setup();
		const handle = await executeMediaTool({
			label: 'Speed',
			steps: [{ tool: 'speed', params: {} }],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'new',
			runs
		});
		await expect(handle.done).resolves.toEqual({ status: 'completed', items: [item], replaced: false });
	});

	it('lets a refused request reject before anything is registered', async () => {
		const { runs, start } = setup();
		const refusal = { response: { status: 400 } };
		start.mockRejectedValue(refusal);
		await expect(
			executeMediaTool({ label: 'Speed', steps: [{ tool: 'speed', params: {} }], sources, refs: [], mode: 'new', runs })
		).rejects.toBe(refusal);
		expect(get(runs)).toEqual({});
	});

	it('remembers the params of a finished single-step run for the next dialog', async () => {
		const tool = {
			id: 'speed',
			label: 'Speed',
			icon: 'x',
			accepts: ['video'],
			produces: 'same',
			cardinality: 'one_to_one',
			params: [],
			requires: {},
			selection: null,
			scopes: ['library'],
			editor: null,
			preview: false,
			component: null,
			runnable: true,
			source: 'core',
			last_params: null
		} as unknown as ToolDTO;
		resetMediaToolCatalog([tool]);
		const { runs } = setup();
		const handle = await executeMediaTool({
			label: 'Speed',
			steps: [{ tool: 'speed', params: { speed: 3 } }],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'new',
			runs
		});
		await handle.done;
		expect(toolById('speed')?.last_params).toEqual({ speed: 3 });
	});

	it('leaves the catalog alone after a multi-step run, which the server does not remember either', async () => {
		resetMediaToolCatalog([{ id: 'rotate', last_params: null } as unknown as ToolDTO]);
		const { runs } = setup();
		const handle = await executeMediaTool({
			label: 'Crop',
			steps: [
				{ tool: 'rotate', params: { degrees: 90 } },
				{ tool: 'crop', params: { x: 0, y: 0, width: 10, height: 10 } }
			],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'new',
			runs
		});
		await handle.done;
		expect(toolById('rotate')?.last_params).toBeNull();
	});

	it('stays quiet unless asked to announce', async () => {
		const { runs } = setup();
		const handle = await executeMediaTool({
			label: 'Speed',
			steps: [{ tool: 'speed', params: {} }],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'new',
			runs
		});
		await handle.done;
		expect(toastMessages()).toEqual([]);
	});

	it('announces the start and the result when asked to', async () => {
		const { runs } = setup();
		const handle = await executeMediaTool({
			label: 'Speed',
			steps: [{ tool: 'speed', params: {} }],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'new',
			announce: true,
			runs
		});
		expect(toastMessages()).toEqual(['Edit started|Speed · Saves as a new item.']);
		await handle.done;
		expect(toastMessages()).toEqual(['Edit started|Speed · Saves as a new item.', 'Saved as new|clip-speed.mp4 is in your library.']);
	});
});

describe('announceOutcome', () => {
	it('says an original was replaced', () => {
		announceOutcome({ status: 'completed', items: [item], replaced: true });
		expect(toastMessages()).toEqual(['Original replaced|clip-speed.mp4 was updated.']);
	});

	it('counts several new items', () => {
		announceOutcome({ status: 'completed', items: [item, item, item], replaced: false });
		expect(toastMessages()).toEqual(['Saved as new|3 items are in your library.']);
	});

	it('reports a failure with its reason', () => {
		announceOutcome({ status: 'failed', message: 'The encoder stopped', code: null });
		expect(get(toasts)[0]).toMatchObject({ type: 'error', title: 'Edit failed', message: 'The encoder stopped' });
	});

	it('says nothing was saved after a cancel', () => {
		announceOutcome({ status: 'cancelled' });
		expect(toastMessages()).toEqual(['Edit cancelled|Nothing was saved.']);
	});
});

const summary = { message: 'Saved hero to the model folder.', links: [{ label: 'Open models', href: '/models?model=m-1' }] };

describe('a run that saved somewhere other than the Library', () => {
	it('shows its own summary with a link instead of a Library message', () => {
		announceOutcome({ status: 'completed', items: [], replaced: false, summary }, SAVED_TO_LIBRARY_ANNOUNCE);
		const [toast] = get(toasts);
		expect(toastMessages()).toEqual(['Done|Saved hero to the model folder.']);
		expect(toast.action?.label).toBe('Open models');
		toast.action?.onClick();
		expect(goto).toHaveBeenCalledWith('/models?model=m-1');
	});

	it('shows the summary even when the host keeps success quiet', () => {
		announceOutcome({ status: 'completed', items: [], replaced: false, summary }, {}, true);
		expect(toastMessages()).toEqual(['Done|Saved hero to the model folder.']);
	});

	it('shows the summary for a run started without announcements', async () => {
		const { runs } = setup(completed({ items: [], summary }));
		const handle = await executeMediaTool({
			label: 'Save elsewhere',
			steps: [{ tool: 'acme.save', params: {} }],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'new',
			runs
		});
		await expect(handle.done).resolves.toMatchObject({ status: 'completed', items: [], summary });
		expect(toastMessages()).toEqual(['Done|Saved hero to the model folder.']);
	});

	it.each(['/\\evil.com', 'https://x', '//x', 'javascript:alert(1)'])('drops a link that leaves the app: %s', (href) => {
		announceOutcome({ status: 'completed', items: [], replaced: false, summary: { message: 'Saved.', links: [{ label: 'Open', href }] } });
		const [toast] = get(toasts);
		expect(toast.message).toBe('Saved.');
		expect(toast.action).toBeUndefined();
	});

	it('keeps the Library message when the run also made items', () => {
		announceOutcome({ status: 'completed', items: [item], replaced: false, summary });
		expect(toastMessages()).toEqual(['Saved as new|clip-speed.mp4 is in your library.']);
	});
});

describe('a cancel that races with completion', () => {
	function setupCancel(answer: MediaToolRun) {
		const start = vi.fn().mockResolvedValue({ run_id: 'run-1' });
		const getRun = vi.fn().mockResolvedValue({ run_id: 'run-1', status: 'running', progress: 0.9, label: 'Saving', items: [], replaced: false, error: null });
		const cancel = vi.fn().mockResolvedValue(answer);
		return createMediaToolRuns({ start, get: getRun, cancel });
	}

	async function runAndCancel(answer: MediaToolRun) {
		const runs = setupCancel(answer);
		const handle = await executeMediaTool({
			label: 'Speed',
			steps: [{ tool: 'speed', params: {} }],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'replace',
			announce: true,
			runs
		});
		await new Promise((resolve) => setTimeout(resolve, 0));
		await runs.cancel('row-1');
		return handle.done;
	}

	it('announces the replaced original and never says nothing was saved', async () => {
		const outcome = await runAndCancel(completed({ replaced: true }));

		expect(outcome).toMatchObject({ status: 'completed', replaced: true });
		expect(toastMessages()).toContain('Original replaced|clip-speed.mp4 was updated.');
		expect(get(toasts).some((entry) => entry.title === 'Edit cancelled')).toBe(false);
	});

	it('says the edit was cancelled when the answer says it was', async () => {
		const outcome = await runAndCancel({ ...completed(), status: 'cancelled', items: [] });

		expect(outcome).toEqual({ status: 'cancelled' });
		expect(toastMessages()).toContain('Edit cancelled|Nothing was saved.');
	});
});

describe('announce settings', () => {
	function run(announce: Parameters<typeof executeMediaTool>[0]['announce'], result?: MediaToolRun) {
		const { runs } = setup(result);
		return executeMediaTool({
			label: 'Speed',
			steps: [{ tool: 'speed', params: {} }],
			sources,
			refs: [{ item_id: 'row-1' }],
			mode: 'new',
			announce,
			runs
		}).then((handle) => handle.done);
	}

	it('puts a link on the result toast and retitles it when the host gives an action', async () => {
		await run(SAVED_TO_LIBRARY_ANNOUNCE);

		const success = get(toasts).filter((entry) => entry.type === 'success');
		expect(success).toHaveLength(1);
		expect(success[0].title).toBe('Saved to Library');
		expect(success[0].message).toBe('clip-speed.mp4 is in your library.');
		expect(success[0].action?.label).toBe('Open Library');
		success[0].action!.onClick();
		expect(goto).toHaveBeenCalledWith('/library');
	});

	it('still says the edit started, once, with that setting', async () => {
		await run(SAVED_TO_LIBRARY_ANNOUNCE);
		expect(get(toasts).filter((entry) => entry.title === 'Edit started')).toHaveLength(1);
	});

	it('adds no link to the default toast', async () => {
		await run(true);
		const success = get(toasts).filter((entry) => entry.type === 'success');
		expect(success).toHaveLength(1);
		expect(success[0].title).toBe('Saved as new');
		expect(success[0].action).toBeUndefined();
	});

	it('says nothing about the start or the success when only failures are announced', async () => {
		await run('failures');
		expect(toastMessages()).toEqual([]);
	});

	it('still reports a failure when only failures are announced', async () => {
		const failed: MediaToolRun = { run_id: 'run-1', status: 'failed', progress: 0, label: '', items: [], replaced: false, error: { code: 'x', message: 'The encoder stopped' } };
		await run('failures', failed);
		expect(get(toasts)).toHaveLength(1);
		expect(get(toasts)[0]).toMatchObject({ type: 'error', message: 'The encoder stopped' });
	});

	it('says nothing at all when announcing is off', async () => {
		const failed: MediaToolRun = { run_id: 'run-1', status: 'failed', progress: 0, label: '', items: [], replaced: false, error: { code: 'x', message: 'The encoder stopped' } };
		await run(false, failed);
		expect(toastMessages()).toEqual([]);
	});

	it('says nothing on success with quiet success, and no start toast', async () => {
		await run({ quietSuccess: true });
		expect(toastMessages()).toEqual([]);
	});

	it('names the tool, keeps the reason as the title and offers Try again when a retry is given', async () => {
		const failed: MediaToolRun = { run_id: 'run-1', status: 'failed', progress: 0, label: '', items: [], replaced: false, error: { code: 'x', message: 'The encoder stopped' } };
		const retry = vi.fn();
		await run({ quietSuccess: true, retry }, failed);

		const [toast] = get(toasts);
		expect(toast).toMatchObject({ type: 'error', message: 'Speed failed. Your settings are kept.', title: 'The encoder stopped' });
		expect(toast.action?.label).toBe('Try again');
		toast.action!.onClick();
		expect(retry).toHaveBeenCalledTimes(1);
	});

	it('is quiet about a cancel with no retry on offer', async () => {
		const cancelled: MediaToolRun = { run_id: 'run-1', status: 'cancelled', progress: 0, label: '', items: [], replaced: false, error: null };
		await run({ quietSuccess: true, retry: vi.fn() }, cancelled);

		const [toast] = get(toasts);
		expect(toast).toMatchObject({ type: 'info', title: 'Edit cancelled' });
		expect(toast.action).toBeUndefined();
	});

	it('does not put the link on an original that was replaced', async () => {
		await run(SAVED_TO_LIBRARY_ANNOUNCE, completed({ replaced: true }));
		const success = get(toasts).filter((entry) => entry.type === 'success');
		expect(success[0].title).toBe('Original replaced');
		expect(success[0].action).toBeUndefined();
	});
});

describe('summaryOf', () => {
	it('is null for a completed outcome that carries no items and no summary', () => {
		expect(summaryOf({ status: 'completed' } as never)).toBeNull();
	});

	it('returns the summary of a completed outcome with an empty item list', () => {
		const summary = { message: 'Done', links: [] };
		expect(summaryOf({ status: 'completed', items: [], summary } as never)).toBe(summary);
	});

	it('is null when the outcome has items', () => {
		expect(summaryOf({ status: 'completed', items: [item], summary: { message: 'x', links: [] } } as never)).toBeNull();
	});
});
