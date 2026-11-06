import { expect, test } from 'vitest';
import { laneLetters, laneSelection, laneTakes, nodeChoice, tileChoice, toggled, viewerSendTo } from './choice';
import type { NodeChoiceOptions } from './choice';
import type { Run, RunCheckpoint } from './types';

const file = (id: string) => ({ kind: 'history', generation_id: 'g1', file_id: id });
const key = (id: string) => `history:g1:${id}`;
const labels: Record<string, string> = { orbit: 'Orbit left', close: 'Close-up', gold: 'Gold restyle' };

type WaitingRun = Run & { checkpoints: Record<string, RunCheckpoint> };

function waitingRun(overrides: Partial<Run> = {}): WaitingRun {
	return waitingRunBase(overrides) as WaitingRun;
}

function waitingRunBase(overrides: Partial<Run>): Run {
	return {
		status: 'awaiting_selection',
		graph_edges: [
			{ id: 'e1', source: 'photo', target: 'orbit' },
			{ id: 'e2', source: 'photo', target: 'close' }
		],
		checkpoints: {
			e1: { confirmed: null, draft: [key('a'), key('b'), key('c')], eligible: [file('a'), file('b'), file('c')], revision: 1 },
			e2: { confirmed: null, draft: [key('a'), key('b'), key('c')], eligible: [file('a'), file('b'), file('c')], revision: 1 }
		},
		...overrides
	};
}

const options = (extra: Partial<NodeChoiceOptions> = {}): NodeChoiceOptions => ({ labelFor: (id) => labels[id], ...extra });

test('a finished node opens one row per connection, the first one active', () => {
	const choice = nodeChoice(waitingRun(), 'photo', options())!;

	expect(choice.choosing).toBe(true);
	expect(choice.active).toBe('e1');
	expect(choice.lanes.map((lane) => [lane.label, lane.open, lane.selected.length])).toStrictEqual([
		['Orbit left', true, 3],
		['Close-up', true, 3]
	]);
	expect(nodeChoice(waitingRun(), 'orbit', options())).toBe(null);
	expect(nodeChoice(null, 'photo', options())).toBe(null);
});

test('a sent row locks and the next open row becomes active', () => {
	const run = waitingRun();
	run.checkpoints.e1 = { ...run.checkpoints.e1, confirmed: [key('b')] };

	expect(nodeChoice(run, 'photo', options())!.active).toBe('e2');
	const choice = nodeChoice(run, 'photo', options({ active: 'e1' }))!;

	expect(choice.active).toBe('e1');
	expect(choice.lanes[0].selected).toStrictEqual([key('b')]);
	expect(choice.lanes[0].sent).toBe(true);
	expect(choice.lanes[0].open).toBe(false);
});

test('drafts made on this page win over the saved draft', () => {
	const choice = nodeChoice(waitingRun(), 'photo', options({ drafts: { e1: [key('c')] } }))!;

	expect(choice.lanes[0].selected).toStrictEqual([key('c')]);
	expect(laneSelection({ confirmed: null, draft: ['x'] }, undefined)).toStrictEqual(['x']);
	expect(laneSelection(null)).toStrictEqual([]);
});

test('an older or stopped brew shows its rows but opens none', () => {
	expect(nodeChoice(waitingRun(), 'photo', options({ readOnly: true }))!.choosing).toBe(false);
	expect(nodeChoice(waitingRun({ status: 'cancelled' }), 'photo', options())!.lanes[0].open).toBe(false);
});

test('tiles carry the active row\'s tick and a letter for every row that takes them', () => {
	const choice = nodeChoice(waitingRun(), 'photo', options({ drafts: { e2: [key('a')] } }));

	expect(tileChoice(choice, key('a'))).toStrictEqual({
		tick: { checked: true, locked: false, label: 'Orbit left' },
		letters: [
			{ letter: 'O', label: 'Orbit left', active: true },
			{ letter: 'C', label: 'Close-up', active: false }
		]
	});
	expect(tileChoice(choice, key('b')).letters.map((item) => item.letter)).toStrictEqual(['O']);
	expect(tileChoice(choice, 'library:other').tick).toBe(null);
	expect(tileChoice(null, key('a'))).toStrictEqual({ tick: null, letters: [] });
});

test('after sending, letters show only when a file went to several rows', () => {
	const run = waitingRun({ status: 'running' });
	run.checkpoints.e1 = { ...run.checkpoints.e1, confirmed: [key('a'), key('b')] };
	run.checkpoints.e2 = { ...run.checkpoints.e2, confirmed: [key('a')] };
	const choice = nodeChoice(run, 'photo', options())!;

	expect(choice.choosing).toBe(false);
	expect(tileChoice(choice, key('a')).letters.map((item) => item.letter)).toStrictEqual(['O', 'C']);
	expect(tileChoice(choice, key('b')).letters).toStrictEqual([]);
	expect(tileChoice(choice, key('a')).tick).toBe(null);
	const looking = nodeChoice(run, 'photo', options({ active: 'e1' }));
	expect(tileChoice(looking, key('b')).tick).toStrictEqual({ checked: true, locked: true, label: 'Orbit left' });
});

test('letters fall back to two characters when targets share a first letter', () => {
	expect(laneLetters(['Orbit left', 'Overhead', 'Close-up'])).toStrictEqual(['Or', 'Ov', 'C']);
	expect(laneLetters(['', 'x'])).toStrictEqual(['?', 'X']);
});

test('ticking keeps the order the files were made in', () => {
	const eligible = ['a', 'b', 'c'];
	expect(toggled(['c'], 'a', eligible)).toStrictEqual(['a', 'c']);
	expect(toggled(['a', 'c'], 'a', eligible)).toStrictEqual(['c']);
});

test('the viewer offers a connection only for files that connection can take', () => {
	const run = waitingRun({
		graph_edges: [
			{ id: 'e1', source: 'photo', target: 'orbit' },
			{ id: 'e2', source: 'photo', target: 'close' }
		]
	});
	run.checkpoints.e2 = { confirmed: null, draft: [], eligible: [file('v1')], revision: 1 };
	const toggles: unknown[] = [];
	const sendTo = viewerSendTo(nodeChoice(run, 'photo', options({ drafts: { e1: [key('a')] } })), (rowId, entry, checked) => toggles.push([rowId, entry.file_id, checked]))!;

	expect(sendTo.rows).toStrictEqual([
		{ id: 'e1', label: 'Orbit left', locked: false },
		{ id: 'e2', label: 'Close-up', locked: false }
	]);
	expect(sendTo.isEligible('e1', file('a'))).toBe(true);
	expect(sendTo.isEligible('e2', file('a'))).toBe(false);
	expect(sendTo.isEligible('e2', file('v1'))).toBe(true);
	expect(sendTo.isEligible('missing', file('a'))).toBe(false);
	expect(sendTo.isSelected('e1', file('a'))).toBe(true);
	expect(sendTo.isSelected('e1', file('b'))).toBe(false);
	sendTo.onToggle!('e1', file('b'), true);
	expect(toggles).toStrictEqual([['e1', 'b', true]]);
	expect(laneTakes(null, key('a'))).toBe(false);
});

test('the viewer shows no Send to once nothing is open', () => {
	const run = waitingRun({ status: 'completed' });
	expect(viewerSendTo(nodeChoice(run, 'photo', options()), () => {})).toBe(null);
	expect(viewerSendTo(null, () => {})).toBe(null);
});

test('a row exposes the rows that share its choice', () => {
	const run = waitingRun();
	run.checkpoints.e1 = { ...run.checkpoints.e1, linked: ['e2'] };

	const choice = nodeChoice(run, 'photo', options())!;

	expect(choice.lanes[0].linked).toStrictEqual(['e2']);
	expect(choice.lanes[1].linked).toStrictEqual([]);
});
