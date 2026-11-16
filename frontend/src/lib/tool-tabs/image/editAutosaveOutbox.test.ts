import { describe, expect, it } from 'vitest';
import { EDIT_AUTOSAVE_OUTBOX_KEY } from '$lib/stores/editAutosaveKeys';
import { createEditAutosaveOutbox } from './editAutosaveOutbox';

const SOURCE = { kind: 'upload' as const, id: 'art-1' };

function memoryStorage() {
	const data = new Map<string, string>();
	return {
		data,
		get: (key: string) => data.get(key) ?? null,
		set: (key: string, value: string) => void data.set(key, value)
	};
}

function entry(marker: number) {
	return { source: SOURCE, state: { marker } };
}

describe('the edit autosave outbox', () => {
	it('shares what one instance writes with another over the same storage', () => {
		const storage = memoryStorage();
		const writer = createEditAutosaveOutbox(storage);
		const reader = createEditAutosaveOutbox(storage);
		expect(writer.put('tab-1', entry(1))).toBe(true);
		expect(reader.pending('tab-1')).toEqual(entry(1));
		expect(reader.pendingTabIds()).toEqual(['tab-1']);
	});

	it('starts empty when the stored JSON is corrupt', () => {
		const storage = memoryStorage();
		storage.data.set(EDIT_AUTOSAVE_OUTBOX_KEY, '{not json');
		const outbox = createEditAutosaveOutbox(storage);
		expect(outbox.pendingTabIds()).toEqual([]);
		expect(outbox.pending('tab-1')).toBeNull();
	});

	it('drops stored entries that have no state', () => {
		const storage = memoryStorage();
		storage.data.set(EDIT_AUTOSAVE_OUTBOX_KEY, JSON.stringify({ pending: { good: entry(1), bad: { source: null }, worse: 4 } }));
		expect(createEditAutosaveOutbox(storage).pendingTabIds()).toEqual(['good']);
	});

	it('does not throw when the storage refuses to write and still holds the entry in memory', () => {
		const outbox = createEditAutosaveOutbox({
			get: () => null,
			set: () => {
				throw new Error('quota');
			}
		});
		expect(outbox.put('tab-1', entry(1))).toBe(false);
		expect(outbox.pending('tab-1')).toEqual(entry(1));
		expect(() => outbox.drop('tab-1')).not.toThrow();
		expect(outbox.pending('tab-1')).toBeNull();
	});

	it('does not throw when the storage refuses to read', () => {
		const outbox = createEditAutosaveOutbox({
			get: () => {
				throw new Error('blocked');
			},
			set: () => undefined
		});
		expect(outbox.pendingTabIds()).toEqual([]);
	});

	it('replaces the previous entry for a tab', () => {
		const outbox = createEditAutosaveOutbox(memoryStorage());
		outbox.put('tab-1', entry(1));
		outbox.put('tab-1', entry(2));
		expect(outbox.pending('tab-1')).toEqual(entry(2));
		expect(outbox.pendingTabIds()).toEqual(['tab-1']);
	});

	it('acknowledges only the entry that was sent', () => {
		const storage = memoryStorage();
		const outbox = createEditAutosaveOutbox(storage);
		const first = entry(1);
		outbox.put('tab-1', first);
		const sent = outbox.pending('tab-1')!;
		outbox.put('tab-1', entry(2));
		outbox.ack('tab-1', sent);
		expect(outbox.pending('tab-1')).toEqual(entry(2));
		outbox.ack('tab-1', outbox.pending('tab-1')!);
		expect(outbox.pending('tab-1')).toBeNull();
		expect(createEditAutosaveOutbox(storage).pendingTabIds()).toEqual([]);
	});

	it('lists every tab with pending work', () => {
		const outbox = createEditAutosaveOutbox(memoryStorage());
		outbox.put('tab-1', entry(1));
		outbox.put('tab-2', entry(2));
		expect(outbox.pendingTabIds().sort()).toEqual(['tab-1', 'tab-2']);
	});

	it('prunes entries of tabs that no longer exist', () => {
		const storage = memoryStorage();
		const outbox = createEditAutosaveOutbox(storage);
		outbox.put('tab-1', entry(1));
		outbox.put('tab-2', entry(2));
		outbox.prune(['tab-2', 'tab-9']);
		expect(outbox.pendingTabIds()).toEqual(['tab-2']);
		expect(createEditAutosaveOutbox(storage).pendingTabIds()).toEqual(['tab-2']);
	});
});
