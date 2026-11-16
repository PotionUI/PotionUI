// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

const renderRecipe = vi.hoisted(() => vi.fn());

vi.mock('$lib/filters/render', () => ({
	renderRecipe,
	LutMemo: class {
		clear() {}
		get() {
			return null;
		}
	}
}));
vi.mock('$app/environment', () => ({ browser: true }));

const { PaintSession } = await import('./session');
const { recentLooks } = await import('$lib/filters/recentLooks');

class FakeImageData {
	constructor(
		public data: Uint8ClampedArray,
		public width: number,
		public height: number
	) {}
}

function look(id: string, name = id.toUpperCase()) {
	return {
		id,
		name,
		source: 'builtin' as const,
		owned: false,
		group: 'Colour',
		pluginId: null,
		description: '',
		hasLut: false,
		defaultIntensity: 100,
		steps: [],
		cube: null
	};
}

function buildSession() {
	const session = new PaintSession();
	const canvas = document.createElement('canvas');
	canvas.width = 1;
	canvas.height = 1;
	const layer = { name: 'Layer', canvas };
	(session as unknown as { doc: { layers: unknown[]; activeIndex: number } }).doc.layers = [layer];
	vi.spyOn(session, 'snapshotLayer').mockReturnValue(canvas);
	vi.spyOn(session, 'layerSelectionMask').mockReturnValue(null);
	const commit = vi.spyOn(session, 'commitPixels').mockImplementation(() => undefined);
	return { session, commit };
}

beforeEach(() => {
	vi.useFakeTimers();
	vi.stubGlobal('ImageData', FakeImageData);
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
		getImageData: () => ({ width: 1, height: 1, data: new Uint8ClampedArray(4) }),
		putImageData: () => undefined
	} as never);
	renderRecipe.mockReset();
	renderRecipe.mockReturnValue({ width: 1, height: 1, data: new Uint8ClampedArray(4) });
	recentLooks.set([]);
});

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('applying a look in the editor', () => {
	it('records the look as recent once it is committed to the layer', () => {
		const { session, commit } = buildSession();
		session.setFilter(look('ember', 'Ember'));

		session.applyFilterToLayer();

		expect(commit).toHaveBeenCalledTimes(1);
		expect(get(recentLooks)).toEqual([{ id: 'ember', name: 'Ember', group: 'Colour' }]);
	});

	it('keeps the last three looks applied, newest first', () => {
		const { session } = buildSession();
		for (const id of ['a', 'b', 'c', 'd']) {
			session.setFilter(look(id));
			session.applyFilterToLayer();
		}

		expect(get(recentLooks).map((entry) => entry.id)).toEqual(['d', 'c', 'b']);
	});

	it('does not record a look that was only previewed', () => {
		const { session } = buildSession();

		session.setFilter(look('ember'));

		expect(get(recentLooks)).toEqual([]);
	});

	it('does not record a look that failed to apply', () => {
		const { session, commit } = buildSession();
		renderRecipe.mockImplementation(() => {
			throw new Error('bad lut');
		});
		session.setFilter(look('ember'));

		session.applyFilterToLayer();

		expect(commit).not.toHaveBeenCalled();
		expect(get(recentLooks)).toEqual([]);
	});

	it('does not record a look at zero intensity, which applies nothing', () => {
		const { session, commit } = buildSession();
		session.setFilter(look('ember'));
		session.setFilterIntensity(0);

		session.applyFilterToLayer();

		expect(commit).not.toHaveBeenCalled();
		expect(get(recentLooks)).toEqual([]);
	});

	it('still applies the look when storage is unavailable', () => {
		vi.stubGlobal('localStorage', {
			getItem: () => {
				throw new DOMException('blocked', 'SecurityError');
			},
			setItem: () => {
				throw new DOMException('blocked', 'SecurityError');
			}
		});
		const { session, commit } = buildSession();
		session.setFilter(look('ember'));

		session.applyFilterToLayer();

		expect(commit).toHaveBeenCalledTimes(1);
		expect(get(recentLooks).map((entry) => entry.id)).toEqual(['ember']);
	});
});
