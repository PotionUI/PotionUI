import { expect, test } from 'vitest';
import { brewName, brewNumber, canonical, elapsedOf, formatElapsed, isActive, isCurrentRun, laneStatus, runElapsed, stepState, isPolling, nodeRunView, nodeStatusLabel, outgoingCheckpoints, runLabel, runTone, toggleKey } from './runs';
import type { NodeLike, OutputEntry, Run } from './types';

function run(overrides: Partial<Run> = {}): Run {
	return {
		status: 'running',
		graph_edges: [{ id: 'e1', source: 'photo', target: 'video' }],
		node_states: { photo: { status: 'running' }, video: { status: 'waiting' } },
		checkpoints: {},
		invocations: [],
		...overrides
	};
}

const photo: NodeLike = { id: 'photo' };
const video: NodeLike = { id: 'video' };

test('run labels, tones and polling follow the status', () => {
	expect(runLabel(run())).toBe('Brewing');
	expect(runLabel(run({ status: 'awaiting_selection' }))).toBe('Waiting for your choice');
	expect(runTone(run({ status: 'needs_attention' }))).toBe('danger');
	expect(isActive(run({ status: 'awaiting_selection' }))).toBe(true);
	expect(isPolling(run({ status: 'awaiting_selection' }))).toBe(false);
	expect(isActive(run({ status: 'completed' }))).toBe(false);
	expect(runLabel(null)).toBe('');
});

test('a running node reports the average progress of its latest attempts', () => {
	const view = nodeRunView(
		run({
			invocations: [
				{ node_id: 'photo', edge_id: null, input_key: '-', ordinal: 0, attempt: 1, status: 'failed', progress: null },
				{ node_id: 'photo', edge_id: null, input_key: '-', ordinal: 0, attempt: 2, status: 'running', progress: 0.4 },
				{ node_id: 'photo', edge_id: null, input_key: 'x', ordinal: 1, attempt: 1, status: 'submitted', progress: 0.6 }
			]
		}),
		photo
	)!;

	expect(view.status).toBe('running');
	expect(view.progress).toBe(50);
	expect(view.label).toBe('Generating · 0 of 2');
	expect(view.failedCount).toBe(0);
});

test('a finished node with an open choice asks for it and shows its outputs', () => {
	const view = nodeRunView(
		run({
			status: 'awaiting_selection',
			node_states: { photo: { status: 'done', manifest: { images: [{ kind: 'history', generation_id: 'g1', file_id: 'f1' }] } } },
			checkpoints: { e1: { confirmed: null, eligible: [{ generation_id: 'g1', file_id: 'f1' }], revision: 1 } }
		}),
		photo
	)!;

	expect(view.status).toBe('choose');
	expect(view.tone).toBe('signal');
	expect(view.outputs.length).toBe(1);
	expect(outgoingCheckpoints(run({ checkpoints: { e1: { confirmed: null } } }), 'photo').length).toBe(1);
	expect(outgoingCheckpoints(run(), 'video').length).toBe(0);
});

test('failures are counted with their reasons and nodes outside the run have no view', () => {
	const view = nodeRunView(
		run({
			status: 'needs_attention',
			node_states: { photo: { status: 'failed' } },
			invocations: [
				{ node_id: 'photo', input_key: 'a', ordinal: 0, attempt: 1, status: 'failed', error: 'Out of memory' },
				{ node_id: 'photo', input_key: 'b', ordinal: 1, attempt: 1, status: 'interrupted', error: 'The server restarted' }
			]
		}),
		photo
	)!;

	expect(view.label).toBe('Failed · 0 of 2 made');
	expect(view.errors).toStrictEqual([
		{ message: 'Out of memory', count: 1 },
		{ message: 'The server restarted', count: 1 }
	]);
	expect(nodeRunView(run(), { id: 'other' })).toBe(null);
	expect(nodeRunView(null, video)).toBe(null);
});

test('choices keep the order the files were made in', () => {
	const eligible = [{ id: 'a' }, { id: 'b' }, { id: 'c' }];
	expect(canonical(['c', 'a'], eligible, (entry) => entry.id)).toStrictEqual(['a', 'c']);
	expect(toggleKey(['a'], 'b')).toStrictEqual(['a', 'b']);
	expect(toggleKey(['a', 'b'], 'a')).toStrictEqual(['b']);
});

test('the same failure on several generations is shown once with a count', () => {
	const view = nodeRunView(
		run({
			node_states: { photo: { status: 'failed' } },
			invocations: [
				{ node_id: 'photo', input_key: 'a', ordinal: 0, attempt: 1, status: 'failed', error: 'This session has 3 shots.' },
				{ node_id: 'photo', input_key: 'b', ordinal: 1, attempt: 1, status: 'failed', error: 'This session has 3 shots.' }
			]
		}),
		photo
	)!;

	expect(view.errors).toStrictEqual([{ message: 'This session has 3 shots.', count: 2 }]);
	expect(view.failedCount).toBe(2);
});

test('generations still to come are counted for placeholder tiles', () => {
	const view = nodeRunView(
		run({
			invocations: [
				{ node_id: 'photo', input_key: 'a', ordinal: 0, attempt: 1, status: 'completed', outputs: [{ file_id: 'f1' }] },
				{ node_id: 'photo', input_key: 'b', ordinal: 1, attempt: 1, status: 'running', progress: 0.2 },
				{ node_id: 'photo', input_key: 'c', ordinal: 2, attempt: 1, status: 'pending' },
				{ node_id: 'photo', input_key: 'd', ordinal: 3, attempt: 1, status: 'failed', error: 'x' }
			]
		}),
		photo
	)!;

	expect(view.pending).toBe(2);
	expect(view.outputs.length).toBe(1);
});

test('node status lines say what the node is doing in plain words', () => {
	expect(nodeStatusLabel('waiting', { source: 'Product photo' })).toBe('Waiting for Product photo');
	expect(nodeStatusLabel('waiting')).toBe('Waiting for its input');
	expect(nodeStatusLabel('running', { done: 2, total: 3 })).toBe('Generating · 2 of 3');
	expect(nodeStatusLabel('running', { total: 1 })).toBe('Generating');
	expect(nodeStatusLabel('done', { outputs: 3 })).toBe('Done · 3 files');
	expect(nodeStatusLabel('done', { outputs: 1 })).toBe('Done · 1 file');
	expect(nodeStatusLabel('done', { outputs: 3, noun: 'video' })).toBe('Done · 3 videos');
	expect(nodeStatusLabel('failed', { done: 1, total: 2 })).toBe('Failed · 1 of 2 made');
	expect(nodeStatusLabel('failed', { total: 1 })).toBe('Failed');
});

test('a waiting node names the node it waits for', () => {
	const view = nodeRunView(run(), video, { source: 'Product photo' })!;

	expect(view.label).toBe('Waiting for Product photo');
});

test('elapsed time runs while generating and stops when the node is done', () => {
	const running = nodeRunView(
		run({
			invocations: [
				{ node_id: 'photo', input_key: 'a', ordinal: 0, attempt: 1, status: 'completed', submitted_at: '2026-10-01T10:00:00Z', finished_at: '2026-10-01T10:00:40Z' },
				{ node_id: 'photo', input_key: 'b', ordinal: 1, attempt: 1, status: 'running', submitted_at: '2026-10-01T10:00:05Z' }
			]
		}),
		photo
	);
	expect(elapsedOf(running, Date.parse('2026-10-01T10:01:15Z'))).toBe('01:15');

	const done = nodeRunView(
		run({
			node_states: { photo: { status: 'done' } },
			invocations: [
				{ node_id: 'photo', input_key: 'a', ordinal: 0, attempt: 1, status: 'completed', submitted_at: '2026-10-01T10:00:00Z', finished_at: '2026-10-01T10:03:34Z' }
			]
		}),
		photo
	);
	expect(elapsedOf(done, Date.parse('2026-10-01T11:00:00Z'))).toBe('03:34');
	expect(elapsedOf(nodeRunView(run(), video), Date.now())).toBe('');
	expect(formatElapsed(3725000)).toBe('1:02:05');
	expect(formatElapsed(-1)).toBe('');
});

test('a connection row shows the estimate, then what is ready, then what was sent', () => {
	expect(laneStatus(null, 'e1', 'up to 3 images')).toStrictEqual({ label: 'up to 3 images', tone: 'neutral' });
	const waiting = run({ status: 'awaiting_selection', checkpoints: { e1: { confirmed: null, eligible: [{}, {}, {}] } } });
	expect(laneStatus(waiting, 'e1', 'up to 3 images')).toStrictEqual({ label: '3 ready', tone: 'signal' });
	const sent = run({ checkpoints: { e1: { confirmed: ['a', 'b'], eligible: [{}, {}, {}] } } });
	expect(laneStatus(sent, 'e1')).toStrictEqual({ label: '2 sent', tone: 'success' });
	const stopped = run({ status: 'cancelled', checkpoints: { e1: { confirmed: null, eligible: [{}] } } });
	expect(laneStatus(stopped, 'e1').tone).toBe('neutral');
});

test('the run strip names brews by number and each step in a word', () => {
	const runs = [{ id: 'r3', scope: 'graph' }, { id: 'r2', scope: 'node' }, { id: 'r1', scope: 'graph' }];

	expect(brewNumber(runs, 'r3')).toBe(3);
	expect(brewName(runs, 'r1')).toBe('Brew 1');
	expect(brewName(runs, 'r2')).toBe('Node run 2');
	expect(brewName(runs, 'missing')).toBe('');
	const paged = [{ id: 'r312', number: 312, scope: 'elixir' }, { id: 'r311', number: 311, scope: 'node' }];
	expect(brewNumber(paged, 'r311')).toBe(311);
	expect(brewName(paged, 'r312')).toBe('Brew 312');
	expect(brewName(paged, 'r311')).toBe('Node run 311');
	expect(stepState(null)).toStrictEqual({ word: 'Not brewed', tone: 'neutral' });
	expect(stepState({ status: 'choose' })).toStrictEqual({ word: 'Your choice', tone: 'signal' });
	expect(stepState({ status: 'failed' })).toStrictEqual({ word: 'Failed', tone: 'danger' });
	expect(runElapsed({ created_at: '2026-10-01T10:00:00Z', finished_at: '2026-10-01T10:03:34Z' }, 0)).toBe('03:34');
	expect(runElapsed({ created_at: '2026-10-01T10:00:00Z' }, Date.parse('2026-10-01T10:01:13Z'))).toBe('01:13');
	expect(runElapsed(null, 0)).toBe('');
});

test('a late answer only applies to the brew still on screen', () => {
	expect(isCurrentRun({ id: 'r1' }, 'r1')).toBe(true);
	expect(isCurrentRun({ id: 'r2' }, 'r1')).toBe(false);
	expect(isCurrentRun(null, 'r1')).toBe(false);
	expect(isCurrentRun({ id: 'r1' }, undefined)).toBe(false);
});

test('a finished step names what it made when all outputs are one kind', () => {
	const entry = (file_id: string, media_type: string) => ({ kind: 'history', generation_id: 'g1', file_id, media_type });
	const done = (outputs: OutputEntry[]) => nodeRunView(run({ status: 'completed', node_states: { photo: { status: 'done', manifest: { images: outputs } } } }), photo)!.label;
	expect(done([entry('a', 'image'), entry('b', 'image')])).toBe('Done · 2 images');
	expect(done([entry('a', 'video')])).toBe('Done · 1 video');
	expect(done([entry('a', 'image'), entry('b', 'video')])).toBe('Done · 2 files');
});
