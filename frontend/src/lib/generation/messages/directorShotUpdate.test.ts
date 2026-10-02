import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { parseDirectorShotUpdate, withDirectorShotUpdate } from './directorRuns';
import type { DirectorRunState } from '$lib/types/tabs';

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

function seedThreeShots(): string {
	const tabId = get(tabsStore).tabs[0].id;
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, {
		directorRuns: { a: baseRun(), b: baseRun(), c: baseRun() },
		directorRunLinks: { 'gen-1': ['a', 'b', 'c'] },
		generation: { ...tab.generation, currentGeneration: { generation_id: 'gen-1' } }
	});
	return tabId;
}

function send(message: Record<string, unknown>) {
	dispatchGenerationMessage({ generation_id: 'gen-1', ...message } as any, { unsubscribe: vi.fn() });
}

function runs(tabId: string) {
	return get(tabsStore).tabs.find((t) => t.id === tabId)!.directorRuns!;
}

describe('parseDirectorShotUpdate', () => {
	it('reads a full update', () => {
		expect(
			parseDirectorShotUpdate({ shot_id: 'b', status: 'done', progress: 1, message: null, output_url: '/clips/b.mp4', output_path: 'generations/d/g/b.mp4' })
		).toEqual({ shotId: 'b', status: 'done', progress: 1, message: null, outputUrl: '/clips/b.mp4', outputPath: 'generations/d/g/b.mp4', nsfw: false });
	});

	it('clamps progress and trims the reason', () => {
		expect(parseDirectorShotUpdate({ shot_id: 'b', status: 'generating', progress: 4, message: '  ' })).toMatchObject({
			progress: 1,
			message: null
		});
		expect(parseDirectorShotUpdate({ shot_id: 'b', status: 'failed', message: ' Out of credit ' })?.message).toBe('Out of credit');
	});

	it('rejects a message without a shot or with an unknown status', () => {
		expect(parseDirectorShotUpdate({ status: 'done' })).toBeNull();
		expect(parseDirectorShotUpdate({ shot_id: 'b', status: 'exploded' })).toBeNull();
		expect(parseDirectorShotUpdate({ shot_id: '', status: 'done' })).toBeNull();
	});
});

describe('withDirectorShotUpdate', () => {
	const update = (status: any, extra: Record<string, unknown> = {}) =>
		parseDirectorShotUpdate({ shot_id: 'b', status, ...extra })!;

	it('ignores a shot the tab has no run for', () => {
		const tab = { directorRuns: { a: baseRun() } };
		expect(withDirectorShotUpdate(tab, update('done'), 5, 'gen-1')).toEqual(tab.directorRuns);
	});

	it('ignores an update for another generation', () => {
		const tab = { directorRuns: { b: baseRun({ generationId: 'gen-2' }) } };
		expect(withDirectorShotUpdate(tab, update('done'), 5, 'gen-1').b.status).toBe('queued');
	});

	it('moves a shot through generating to done with its clip', () => {
		let tab: { directorRuns: Record<string, DirectorRunState> } = { directorRuns: { b: baseRun() } };
		tab = { directorRuns: withDirectorShotUpdate(tab, update('generating', { progress: 0.4 }), 1, 'gen-1') };
		expect(tab.directorRuns.b).toMatchObject({ status: 'generating', progress: 0.4, reported: true });
		tab = { directorRuns: withDirectorShotUpdate(tab, update('done', { output_url: '/clips/b.mp4', output_path: 'g/b.mp4' }), 9, 'gen-1') };
		expect(tab.directorRuns.b).toMatchObject({ status: 'done', progress: 1, finishedAt: 9, posterUrl: '/clips/b.mp4', outputPath: 'g/b.mp4' });
	});

	it('keeps the saved path and marks a flagged clip so it is never shown raw', () => {
		const next = withDirectorShotUpdate(
			{ directorRuns: { b: baseRun() } },
			update('done', { output_url: '/clips/b.mp4', output_path: 'g/b.mp4', nsfw: true }),
			9,
			'gen-1'
		);
		expect(next.b).toMatchObject({ status: 'done', posterUrl: '/clips/b.mp4', outputPath: 'g/b.mp4', flagged: true });
	});

	it('keeps the earlier thumbnail when a done update has no url', () => {
		const next = withDirectorShotUpdate({ directorRuns: { b: baseRun({ posterUrl: '/old.mp4' }) } }, update('done', { output_url: null }), 9, 'gen-1');
		expect(next.b.posterUrl).toBe('/old.mp4');
	});

	it('records the reason a shot failed', () => {
		const next = withDirectorShotUpdate({ directorRuns: { b: baseRun() } }, update('failed', { message: 'The provider refused this prompt.' }), 3, 'gen-1');
		expect(next.b).toMatchObject({ status: 'failed', message: 'The provider refused this prompt.' });
	});

	it('gives a failed shot without a reason a plain sentence', () => {
		const next = withDirectorShotUpdate({ directorRuns: { b: baseRun() } }, update('failed'), 3, 'gen-1');
		expect(next.b.message).toBe('This shot could not be made.');
	});

	it('shows skipped and cancelled shots as failed with a reason', () => {
		const skipped = withDirectorShotUpdate({ directorRuns: { b: baseRun() } }, update('skipped'), 3, 'gen-1');
		expect(skipped.b).toMatchObject({ status: 'failed', message: 'Not made because an earlier shot did not finish.' });
		const cancelled = withDirectorShotUpdate({ directorRuns: { b: baseRun() } }, update('cancelled'), 3, 'gen-1');
		expect(cancelled.b).toMatchObject({ status: 'failed', message: 'Stopped before it finished.' });
	});

	it('keeps a finished shot when a later skipped arrives for it', () => {
		const done = withDirectorShotUpdate({ directorRuns: { b: baseRun() } }, update('done', { output_url: '/clips/b.mp4' }), 3, 'gen-1');
		const after = withDirectorShotUpdate({ directorRuns: done }, update('skipped'), 4, 'gen-1');
		expect(after.b.status).toBe('done');
	});
});

describe('a multi-shot run through the message handlers', () => {
	beforeEach(() => {
		const first = get(tabsStore).tabs[0].id;
		tabsStore.updateTab(first, { directorRuns: {}, directorRunLinks: {} });
	});

	it('shows each shot on its own while whole-run progress keeps coming', () => {
		const tabId = seedThreeShots();
		send({ type: 'director_shot_update', shot_id: 'a', status: 'done', output_url: '/clips/a.mp4' });
		send({ type: 'director_shot_update', shot_id: 'b', status: 'generating', progress: 0.3 });
		send({ type: 'generation_status', status: 'running', progress: 0.5 });
		expect(runs(tabId).a).toMatchObject({ status: 'done', posterUrl: '/clips/a.mp4' });
		expect(runs(tabId).b).toMatchObject({ status: 'generating', progress: 0.3 });
		expect(runs(tabId).c.status).toBe('queued');
	});

	it('does not overwrite a shot clip with the stitched film poster', () => {
		const tabId = seedThreeShots();
		send({ type: 'director_shot_update', shot_id: 'a', status: 'done', output_url: '/clips/a.mp4' });
		send({
			type: 'gallery_update',
			videos: [{ path: '/api/media/generations/gen-1/0.mp4' }],
			video_urls_list: [{ path: '/api/media/generations/gen-1/0.mp4' }]
		});
		expect(runs(tabId).a.posterUrl).toBe('/clips/a.mp4');
	});

	it('keeps the finished shot and marks the rest failed with the reason when the run errors', () => {
		const tabId = seedThreeShots();
		send({ type: 'director_shot_update', shot_id: 'a', status: 'done', output_url: '/clips/a.mp4' });
		send({ type: 'director_shot_update', shot_id: 'b', status: 'failed', message: 'The provider refused this prompt.' });
		send({ type: 'director_shot_update', shot_id: 'c', status: 'skipped' });
		send({ type: 'generation_error', message: 'Shot 2 failed.' });
		expect(runs(tabId).a).toMatchObject({ status: 'done', posterUrl: '/clips/a.mp4' });
		expect(runs(tabId).b).toMatchObject({ status: 'failed', message: 'The provider refused this prompt.' });
		expect(runs(tabId).c.status).toBe('failed');
	});

	it('gives shots that never reported the run error as their reason', () => {
		const tabId = seedThreeShots();
		send({ type: 'generation_error', message: 'Provider is down.' });
		expect(runs(tabId).a).toMatchObject({ status: 'failed', message: 'Provider is down.' });
	});

	it('marks the unfinished shots as stopped when the run is cancelled', () => {
		const tabId = seedThreeShots();
		send({ type: 'director_shot_update', shot_id: 'a', status: 'done', output_url: '/clips/a.mp4' });
		send({ type: 'generation_cancelled' });
		expect(runs(tabId).a.status).toBe('done');
		expect(runs(tabId).b).toMatchObject({ status: 'failed', message: 'Stopped before it finished.' });
	});

	it('finishes every shot when the stitched film arrives', () => {
		const tabId = seedThreeShots();
		for (const id of ['a', 'b', 'c']) send({ type: 'director_shot_update', shot_id: id, status: 'done', output_url: `/clips/${id}.mp4` });
		send({ type: 'generation_complete', data: { id: 'gen-1' } });
		expect(['a', 'b', 'c'].map((id) => runs(tabId)[id].status)).toEqual(['done', 'done', 'done']);
		expect(runs(tabId).b.posterUrl).toBe('/clips/b.mp4');
	});

	it('ignores an update for a shot the run does not cover', () => {
		const tabId = seedThreeShots();
		send({ type: 'director_shot_update', shot_id: 'zzz', status: 'done' });
		expect(Object.keys(runs(tabId))).toEqual(['a', 'b', 'c']);
	});
});
