import { describe, expect, it } from 'vitest';
import { makeItem, makeKit } from './fixtures';
import {
	KITS_GROUP,
	bareKitDescription,
	defaultKitField,
	itemDescription,
	itemMatches,
	kitCapableFields,
	kitFieldsOf,
	kitMatches,
	kitPickerState,
	kitRowDescription,
	loadedKits,
	groupDescription
} from './promptKits';
import type { KitDirectory } from './kitChips';

const specs = [
	{ field: 'references', kind: 'image' },
	{ field: 'references', kind: 'video' },
	{ field: 'lora', kind: 'image', source: 'models' }
];

const mara = makeKit('Mara', 'mara', [makeItem('image', 'face'), makeItem('audio', 'voice'), makeItem('text', 'look')], { label: 'Actor' });
const harbour = makeKit('Old harbour', 'old_harbour', [makeItem('image', 'lighthouse', { width: 900, height: 1200 })]);
const directory: KitDirectory = new Map([
	[mara.id, mara],
	[harbour.id, harbour]
]);
const values = { references: [{ path: 'a.png', url: '/a.png' }, { kit: mara.id }, { kit: harbour.id }, { kit: 'kt_deadbeef' }] };

describe('which fields take kits', () => {
	it('is a media field of the mode, never a source-backed bundle field', () => {
		const capable = kitCapableFields(specs);
		expect([...capable.keys()]).toEqual(['references']);
		expect(capable.get('references')?.has('image')).toBe(true);
		expect(capable.get('references')?.has('audio')).toBe(false);
	});

	it('reads the loaded rows of each field', () => {
		const [field] = kitFieldsOf(specs, values);
		expect(field.rows.map((row) => row.kit)).toEqual([mara.id, harbour.id, 'kt_deadbeef']);
		expect(defaultKitField(kitFieldsOf(specs, values))).toBe('references');
		expect(defaultKitField(kitFieldsOf(specs, {}))).toBe('references');
		expect(defaultKitField([])).toBeNull();
	});

	it('keeps only kits the directory knows', () => {
		const loaded = loadedKits(kitFieldsOf(specs, values), directory);
		expect(loaded.map((entry) => entry.kit.handle)).toEqual(['mara', 'old_harbour']);
		expect(loaded[0].usage.skipped.map((item) => item.name)).toEqual(['voice']);
	});
});

describe('kitPickerState', () => {
	const loaded = loadedKits(kitFieldsOf(specs, values), directory);

	it('is off for ordinary queries', () => {
		expect(kitPickerState(null, null, 'image', loaded).kitMode).toBe(false);
		expect(kitPickerState('references', null, 'kits.', loaded).kitMode).toBe(false);
	});

	it('follows a typed @kits. path', () => {
		expect(kitPickerState(null, null, 'kits.', loaded)).toMatchObject({ kitMode: true, drilled: null, filter: '' });
		expect(kitPickerState(null, null, 'kits.ma', loaded)).toMatchObject({ kitMode: true, drilled: null, filter: 'ma' });
		const drilled = kitPickerState(null, null, 'kits.mara.fa', loaded);
		expect(drilled.drilled?.kit.handle).toBe('mara');
		expect(drilled.filter).toBe('fa');
	});

	it('follows the Kits group opened from the list', () => {
		expect(kitPickerState(KITS_GROUP, null, 'har', loaded)).toMatchObject({ kitMode: true, drilled: null, filter: 'har' });
		expect(kitPickerState(KITS_GROUP, harbour.id, '', loaded).drilled?.kit.handle).toBe('old_harbour');
	});

	it('stays at the list when the typed handle is not a loaded kit', () => {
		expect(kitPickerState(null, null, 'kits.nobody.', loaded)).toMatchObject({ kitMode: true, drilled: null });
	});
});

describe('picker rows', () => {
	const [entry] = loadedKits(kitFieldsOf(specs, values), directory);

	it('matches a kit by name or path name, and an item by name', () => {
		expect(kitMatches(entry, 'mar')).toBe(true);
		expect(kitMatches(entry, 'zzz')).toBe(false);
		expect(itemMatches(entry.kit.items[0], 'fa')).toBe(true);
		expect(itemMatches(entry.kit.items[0], 'x')).toBe(false);
	});

	it('describes a kit row', () => {
		expect(kitRowDescription(entry)).toBe('3 items · Actor');
		expect(groupDescription(0)).toBe('No kits loaded yet');
		expect(groupDescription(2)).toBe('2 loaded');
	});

	it('says what a bare citation hands the model', () => {
		expect(bareKitDescription(entry)).toBe('Everything this model can take · 2 of 3');
	});

	it('describes an item by kind and size', () => {
		const [, , harbourEntry] = [entry, entry, loadedKits(kitFieldsOf(specs, values), directory)[1]];
		expect(itemDescription(harbourEntry.kit.items[0], harbourEntry.accepted)).toBe('Image · 900×1200');
		expect(itemDescription(entry.kit.items[1], entry.accepted)).toBe('Audio · not taken by this model');
		expect(itemDescription(entry.kit.items[2], entry.accepted)).toBe('Text · 6 characters');
		expect(itemDescription({ ...entry.kit.items[0], missing: true }, entry.accepted)).toBe('File deleted');
	});
});
