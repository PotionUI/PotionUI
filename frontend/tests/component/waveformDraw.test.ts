import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { mountComponent } from './helpers/mount';
import Waveform from '$lib/components/Waveform.svelte';

interface Recorder {
	canvas: HTMLCanvasElement;
	calls: string[];
	fillStyle: string;
}

let recorders: Recorder[] = [];

class FakeAudio extends EventTarget {
	currentTime = 0;
	duration = 10;
	paused = true;
}

function install() {
	recorders = [];
	vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
		const existing = recorders.find((entry) => entry.canvas === this);
		if (existing) return existing as never;
		const recorder: Recorder = { canvas: this, calls: [], fillStyle: '' };
		const proxy = new Proxy(recorder, {
			get(target, key: string) {
				if (key in target) return (target as unknown as Record<string, unknown>)[key];
				return (...args: unknown[]) => {
					target.calls.push(key === 'drawImage' ? `drawImage:${args.length}` : key);
				};
			},
			set(target, key: string, value) {
				(target as unknown as Record<string, unknown>)[key] = value;
				return true;
			}
		});
		recorders.push(proxy);
		return proxy as never;
	});
}

beforeEach(() => {
	vi.stubGlobal(
		'ResizeObserver',
		class {
			observe() {}
			disconnect() {}
		}
	);
	vi.spyOn(Element.prototype, 'getBoundingClientRect').mockReturnValue({ left: 0, top: 0, width: 300, height: 80, right: 300, bottom: 80, x: 0, y: 0, toJSON: () => ({}) } as DOMRect);
	install();
});

afterEach(() => {
	document.body.innerHTML = '';
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

describe('Waveform drawing', () => {
	it('builds the bars once per size and only blits them while the progress moves', async () => {
		const audio = new FakeAudio();
		mountComponent(Waveform as never, { audioElement: audio as never });
		flushSync();
		const stage = recorders.find((entry) => entry.canvas.classList.contains('waveform-canvas'));
		expect(stage).toBeDefined();
		const layers = recorders.filter((entry) => entry !== stage);
		expect(layers).toHaveLength(2);
		const built = layers.map((entry) => entry.calls.filter((call) => call === 'roundRect' || call === 'rect').length);
		expect(built[0]).toBeGreaterThan(50);
		expect(stage!.calls.includes('beginPath')).toBe(false);

		stage!.calls.length = 0;
		for (let i = 1; i <= 20; i++) {
			audio.currentTime = i * 0.4;
			audio.dispatchEvent(new Event('seeked'));
		}
		expect(stage!.calls.filter((call) => call.startsWith('drawImage'))).toHaveLength(40);
		expect(stage!.calls.includes('beginPath')).toBe(false);
		expect(layers.map((entry) => entry.calls.filter((call) => call === 'roundRect' || call === 'rect').length)).toEqual(built);
	});

	it('draws the played colour only up to the playhead', async () => {
		const audio = new FakeAudio();
		audio.currentTime = 5;
		mountComponent(Waveform as never, { audioElement: audio as never });
		flushSync();
		const stage = recorders.find((entry) => entry.canvas.classList.contains('waveform-canvas'))!;
		const draws = stage.calls.filter((call) => call.startsWith('drawImage:'));
		expect(draws).toContain('drawImage:9');
	});
});
