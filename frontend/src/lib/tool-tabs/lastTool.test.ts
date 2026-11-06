import { beforeEach, describe, expect, it } from 'vitest';
import { LAST_TOOL_STORAGE_KEY, lastToolFor, rememberTool } from './lastTool';

function install(): Map<string, string> {
	const store = new Map<string, string>();
	Object.defineProperty(globalThis, 'localStorage', {
		configurable: true,
		value: {
			getItem: (key: string) => store.get(key) ?? null,
			setItem: (key: string, value: string) => void store.set(key, value),
			removeItem: (key: string) => void store.delete(key)
		}
	});
	return store;
}

let store: Map<string, string>;

beforeEach(() => {
	store = install();
});

describe('lastToolFor', () => {
	it('starts on Paint for images and Trim for videos', () => {
		expect(lastToolFor('image')).toBe('paint');
		expect(lastToolFor('video')).toBe('trim');
	});

	it('remembers each kind on its own and across reads', () => {
		rememberTool('image', 'effects');
		rememberTool('video', 'speed');

		expect(lastToolFor('image')).toBe('effects');
		expect(lastToolFor('video')).toBe('speed');
		expect(JSON.parse(store.get(LAST_TOOL_STORAGE_KEY)!)).toEqual({ image: 'effects', video: 'speed' });
	});

	it('never remembers Slice, so E does not open a photo on a terminal tool', () => {
		rememberTool('image', 'effects');
		rememberTool('image', 'slice');
		expect(lastToolFor('image')).toBe('effects');
		store.set(LAST_TOOL_STORAGE_KEY, JSON.stringify({ image: 'slice' }));
		expect(lastToolFor('image')).toBe('paint');
	});

	it('ignores a tool the editor does not have and a plugin tab', () => {
		rememberTool('image', 'trim');
		rememberTool('video', 'paint');
		rememberTool('acme.stamp', 'paint');
		rememberTool('image', null);

		expect(lastToolFor('image')).toBe('paint');
		expect(lastToolFor('video')).toBe('trim');
		expect(store.has(LAST_TOOL_STORAGE_KEY)).toBe(false);
	});

	it('falls back to the default for a stored tool that no longer exists', () => {
		store.set(LAST_TOOL_STORAGE_KEY, JSON.stringify({ image: 'retired', video: 5 }));

		expect(lastToolFor('image')).toBe('paint');
		expect(lastToolFor('video')).toBe('trim');
	});

	it('survives corrupted storage and storage that throws', () => {
		store.set(LAST_TOOL_STORAGE_KEY, '{nope');
		expect(lastToolFor('image')).toBe('paint');

		Object.defineProperty(globalThis, 'localStorage', {
			configurable: true,
			value: {
				getItem: () => {
					throw new Error('blocked');
				},
				setItem: () => {
					throw new Error('blocked');
				}
			}
		});
		expect(() => rememberTool('image', 'crop')).not.toThrow();
		expect(lastToolFor('image')).toBe('paint');
	});
});
