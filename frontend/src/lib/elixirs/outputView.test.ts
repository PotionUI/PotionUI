import { expect, test } from 'vitest';
import type { Mountable, OverlayRegistry } from './overlays';
import type { SendToRow } from './outputView';
import type { OutputEntry } from './types';
import { eligibleRows, extraSectionsFor, historyGroups, matchEntry, navigationFor, openOutputView, rowChecked } from './outputView';

const entry = (generation: string, file: string, extra: Partial<OutputEntry> = {}): OutputEntry => ({ kind: 'history', generation_id: generation, file_id: file, file_index: Number(file) % 2, media_type: 'image', ...extra });
const outputs: OutputEntry[] = [entry('g1', '0'), entry('g1', '1'), entry('g2', '2'), { kind: 'library', item_id: 'u1', media_type: 'image' }, entry('g3', '4')];
const rows: SendToRow[] = [{ id: 'a', label: 'A' }, { id: 'b', label: 'B' }];

test('outputs group by generation in order, library files left out', () => {
	expect(historyGroups(outputs)).toStrictEqual([
		{ generationId: 'g1', index: 0 },
		{ generationId: 'g2', index: 2 },
		{ generationId: 'g3', index: 4 }
	]);
	expect(historyGroups(null)).toStrictEqual([]);
});

test('navigation knows its place and both ends', () => {
	expect(navigationFor(outputs, 'g1')).toStrictEqual({ groups: historyGroups(outputs), at: 0, hasPrevious: false, hasNext: true, position: { index: 1, total: 3 } });
	expect(navigationFor(outputs, 'g3').hasNext).toBe(false);
	expect(navigationFor(outputs, 'g2').hasPrevious).toBe(true);
	expect(navigationFor(outputs, 'nope').position).toBe(null);
});

test('the file on screen is matched by file id, then by index', () => {
	expect(matchEntry(outputs, { generationId: 'g1', fileIndex: 0, fileId: 1 })).toBe(outputs[1]);
	expect(matchEntry(outputs, { generationId: 'g1', fileIndex: 0, fileId: null })).toBe(outputs[0]);
	expect(matchEntry(outputs, { generationId: 'g2', fileIndex: 5, fileId: 99 })).toBe(null);
	expect(matchEntry(outputs, null)).toBe(null);
});

test('without a predicate every connection is offered', () => {
	expect(eligibleRows({ rows }, { file_id: '1' }).map((row) => row.id)).toStrictEqual(['a', 'b']);
	expect(eligibleRows(null, { file_id: '1' })).toStrictEqual([]);
});

test('connections that refuse the file on screen are hidden for that file only', () => {
	const sendTo = { rows, isEligible: (rowId: string, item: OutputEntry) => rowId === 'a' || item.file_id === '2' };
	expect(eligibleRows(sendTo, { file_id: '1' }).map((row) => row.id)).toStrictEqual(['a']);
	expect(eligibleRows(sendTo, { file_id: '2' }).map((row) => row.id)).toStrictEqual(['a', 'b']);
});

test('selection comes from isSelected, falling back to a plain flag', () => {
	const item = { file_id: '1' };
	expect(rowChecked({ isSelected: (rowId) => rowId === 'a' }, rows[0], item)).toBe(true);
	expect(rowChecked({ isSelected: (rowId) => rowId === 'a' }, rows[1], item)).toBe(false);
	expect(rowChecked({}, { id: 'c', label: 'C', selected: true }, item)).toBe(true);
});

test('the Send to section is plain data and reports toggles with the entry', () => {
	const toggles: unknown[] = [];
	const sendTo = {
		rows: [...rows, { id: 'c', label: 'C', locked: true }],
		isEligible: (rowId: string) => rowId !== 'b',
		isSelected: (rowId: string) => rowId === 'a',
		onToggle: (rowId: string, item: OutputEntry, checked: boolean) => toggles.push([rowId, item.file_id, checked])
	};
	const sections = extraSectionsFor(sendTo, outputs[0]);
	expect(sections.length).toBe(1);
	expect(sections[0].rows.map(({ id, label, checked, disabled }) => ({ id, label, checked, disabled }))).toStrictEqual([
		{ id: 'a', label: 'A', checked: true, disabled: false },
		{ id: 'c', label: 'C', checked: false, disabled: true }
	]);
	sections[0].rows[0].onToggle(false);
	expect(toggles).toStrictEqual([['a', '0', false]]);
	expect(JSON.stringify(sections).includes('function')).toBe(false);
});

test('no Send to section without a file, a choice or an eligible connection', () => {
	expect(extraSectionsFor({ rows }, null)).toStrictEqual([]);
	expect(extraSectionsFor(null, outputs[0])).toStrictEqual([]);
	expect(extraSectionsFor({ rows, isEligible: () => false }, outputs[0])).toStrictEqual([]);
});

let registry: OverlayRegistry = {};

function fakeHost() {
	const calls = { mounted: [] as any[], updated: [] as any[], unmounted: 0 };
	const make = (name: string): Mountable => ({
		mount: (el, props) => {
			calls.mounted.push({ name, el, props });
			return { name };
		},
		update: (handle, props) => calls.updated.push({ handle, props }),
		unmount: () => {
			calls.unmounted += 1;
		}
	});
	const holders: any[] = [];
	(globalThis as any).document = {
		activeElement: null,
		body: { appendChild: (el: any) => (holders.push(el), el) },
		contains: () => false,
		querySelectorAll: () => [],
		querySelector: () => null,
		createElement: () => ({ remove() {} })
	};
	registry = { GenerationDetailsModal: make('GenerationDetailsModal'), MediaPreviewModal: make('MediaPreviewModal') };
	return calls;
}

test('a generated file opens in the history details modal on its own file', () => {
	const calls = fakeHost();
	const view = openOutputView({ outputs, index: 2, sendTo: { rows, onToggle() {} } }, registry)!;
	const { name, props } = calls.mounted[0];
	expect(name).toBe('GenerationDetailsModal');
	expect(props.generationId).toBe('g2');
	expect(props.initialFileIndex).toBe(0);
	expect(props.position).toStrictEqual({ index: 2, total: 3 });
	expect(props.hasPrevious).toBe(true);
	expect(props.hasNext).toBe(true);
	expect(props.extraSections[0].id).toBe('send-to');
	view.close();
	expect(calls.unmounted).toBe(1);
});

test('stepping walks generations and repoints the modal', async () => {
	const calls = fakeHost();
	openOutputView({ outputs, index: 0 }, registry);
	const { props } = calls.mounted[0];
	expect(props.onNavigate(1)).toBe(true);
	const update = calls.updated.at(-1).props;
	expect(update.generationId).toBe('g2');
	expect(update.position).toStrictEqual({ index: 2, total: 3 });
	expect(props.onNavigate(-1)).toBe(true);
	expect(calls.updated.at(-1).props.generationId).toBe('g1');
	expect(calls.updated.at(-1).props.initialFileIndex).toBe(-1);
	expect(props.onNavigate(-1)).toBe(false);
});

test('the Send to section follows the file the modal reports', () => {
	const calls = fakeHost();
	const sendTo = { rows, isSelected: (rowId: string, item: OutputEntry) => rowId === 'a' && item.file_id === '1', onToggle() {} };
	openOutputView({ outputs, index: 0, sendTo }, registry);
	const { props } = calls.mounted[0];
	expect(props.extraSections[0].rows[0].checked).toBe(false);
	props.onFileChange({ generationId: 'g1', fileIndex: 1, fileId: 1 });
	expect(calls.updated.at(-1).props.extraSections[0].rows[0].checked).toBe(true);
	props.onFileChange({ generationId: 'g1', fileIndex: 7, fileId: 77 });
	expect(calls.updated.at(-1).props.extraSections).toStrictEqual([]);
});

test('a report for another generation is ignored', () => {
	const calls = fakeHost();
	openOutputView({ outputs, index: 0 }, registry);
	const before = calls.updated.length;
	calls.mounted[0].props.onFileChange({ generationId: 'g3', fileIndex: 0, fileId: 4 });
	expect(calls.updated.length).toBe(before);
});

test('new sendTo data from the editor reaches the open modal', () => {
	const calls = fakeHost();
	const view = openOutputView({ outputs, index: 0, sendTo: { rows: [rows[0]], onToggle() {} } }, registry)!;
	view.update({ sendTo: { rows, onToggle() {} } });
	expect(calls.updated.at(-1).props.extraSections[0].rows.length).toBe(2);
});

test('a library file opens in the media preview', () => {
	const calls = fakeHost();
	openOutputView({ outputs, index: 3 }, registry);
	expect(calls.mounted[0].name).toBe('MediaPreviewModal');
	expect(calls.mounted[0].props.kind).toBe('image');
});

test('nothing opens without outputs or without the host modal', () => {
	fakeHost();
	expect(openOutputView({ outputs: [] }, registry)).toBe(null);
	registry = {};
	expect(openOutputView({ outputs, index: 0 }, registry)).toBe(null);
});
