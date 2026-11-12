import { describe, expect, it } from 'vitest';
import { makeItem, makeKit } from './fixtures';
import { acceptedFrom } from './kitCounts';
import { deriveKitChip, removedItemIds, sessionChange, skippedNotes, type KitDirectory } from './kitChips';
import { snapshotOf } from './kitMarker';

const face = makeItem('image', 'face');
const voice = makeItem('audio', 'voice');
const look = makeItem('text', 'look');
const mara = makeKit('Mara', 'mara', [face, voice, look]);
const directory: KitDirectory = new Map([[mara.id, mara]]);
const key = (itemId: string | null) => ({ kitId: mara.id, itemId });

describe('deriveKitChip', () => {
	it('is pending until the kit is known', () => {
		const chip = deriveKitChip({ kitId: 'kt_ffffffff', itemId: null }, directory, null);
		expect(chip.state).toBe('pending');
	});

	it('shows the current path of an item', () => {
		const chip = deriveKitChip(key(mara.items[0].id), directory, null);
		expect(chip).toMatchObject({ state: 'ok', label: 'mara.face', kind: 'image', warns: false });
	});

	it('follows a rename because the citation holds ids', () => {
		const renamed = makeKit('Mara Lind', 'mara_lind', [{ ...face, name: 'portrait' }], { id: mara.id });
		const chip = deriveKitChip(key(face.id), new Map([[mara.id, renamed]]), null);
		expect(chip.label).toBe('mara_lind.portrait');
	});

	it('skips an item the model does not take and says why', () => {
		const chip = deriveKitChip(key(mara.items[1].id), directory, acceptedFrom(['image', 'video']), 'LTX 2.5');
		expect(chip).toMatchObject({ state: 'skipped', warns: true, struck: false });
		expect(chip.line).toBe('LTX 2.5 does not take audio. voice is skipped.');
	});

	it('never skips text', () => {
		const chip = deriveKitChip(key(mara.items[2].id), directory, acceptedFrom([]));
		expect(chip.state).toBe('ok');
	});

	it('draws a removed item struck through', () => {
		const chip = deriveKitChip(key('it_deadbeef'), directory, null);
		expect(chip).toMatchObject({ state: 'item-deleted', struck: true, warns: true });
	});

	it('draws a deleted file as skipped, not struck', () => {
		const gone = makeKit('Mara', 'mara', [makeItem('image', 'face', { missing: true })]);
		const chip = deriveKitChip({ kitId: gone.id, itemId: gone.items[0].id }, new Map([[gone.id, gone]]), null);
		expect(chip).toMatchObject({ state: 'file-deleted', struck: false, warns: true });
	});

	it('draws a deleted kit', () => {
		const chip = deriveKitChip(key(mara.items[0].id), new Map([[mara.id, null]]), null);
		expect(chip).toMatchObject({ state: 'kit-deleted', struck: true });
		const bare = deriveKitChip(key(null), new Map([[mara.id, null]]), null);
		expect(bare).toMatchObject({ state: 'kit-deleted', bare: true, struck: false });
	});

	it('counts what a bare kit hands the model', () => {
		const chip = deriveKitChip(key(null), directory, acceptedFrom(['image']));
		expect(chip).toMatchObject({ bare: true, label: 'mara', count: 2, of: 3 });
	});
});

describe('sessionChange', () => {
	const row = { kit: mara.id, snapshot: snapshotOf(mara) };

	it('says nothing when nothing changed', () => {
		expect(sessionChange(row, directory)).toBeNull();
	});

	it('says nothing without a snapshot', () => {
		expect(sessionChange({ kit: mara.id }, directory)).toBeNull();
	});

	it('reports a renamed kit, a renamed item and a removed item', () => {
		const now = makeKit('Mara', 'mara_lind', [{ ...face, name: 'portrait' }, look], { id: mara.id });
		const change = sessionChange(row, new Map([[mara.id, now]]));
		expect(change).toEqual({
			kind: 'changed',
			name: 'Mara',
			kitRenamed: { from: 'mara', to: 'mara_lind' },
			itemsRenamed: [{ from: 'face', to: 'portrait' }],
			itemsRemoved: ['voice']
		});
		expect(removedItemIds(row, new Map([[mara.id, now]]))).toEqual([voice.id]);
	});

	it('reports a deleted kit', () => {
		expect(sessionChange(row, new Map([[mara.id, null]]))).toEqual({ kind: 'deleted', name: 'Mara' });
	});

	it('waits for the directory before judging', () => {
		expect(sessionChange(row, new Map())).toBeNull();
	});
});

describe('skippedNotes', () => {
	const image = acceptedFrom(['image']);

	it('lists what a model leaves out of a bare kit, by kind', () => {
		expect(skippedNotes([{ key: key(null), accepted: image }], directory)).toEqual([
			{ kind: 'audio', kindLabel: 'audio', names: ['voice'] }
		]);
	});

	it('lists one cited item and nothing for what is taken', () => {
		expect(skippedNotes([{ key: key(mara.items[1].id), accepted: image }], directory)).toHaveLength(1);
		expect(skippedNotes([{ key: key(mara.items[0].id), accepted: image }], directory)).toEqual([]);
	});

	it('says each name once even when cited twice, and ignores unknown kits', () => {
		const cites = [
			{ key: key(null), accepted: image },
			{ key: key(mara.items[1].id), accepted: image },
			{ key: { kitId: 'kt_ffffffff', itemId: null }, accepted: image }
		];
		expect(skippedNotes(cites, directory)[0].names).toEqual(['voice']);
	});

	it('skips nothing when the model is unknown', () => {
		expect(skippedNotes([{ key: key(null), accepted: null }], directory)).toEqual([]);
	});
});
