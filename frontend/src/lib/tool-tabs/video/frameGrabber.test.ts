import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const log = vi.hoisted(() => ({ seeks: [] as number[], gates: [] as Array<() => void>, created: 0, loads: 0 }));

vi.mock('$lib/media/seekVideo', () => ({
	seekVideo: (_video: unknown, time: number) =>
		new Promise<void>((resolve) => {
			log.seeks.push(time);
			log.gates.push(resolve);
		})
}));

import { acquireFrameGrabber, createFrameGrabber } from './frameGrabber';

function fakeElement(tag: string) {
	if (tag === 'video') {
		log.created += 1;
		return {
			readyState: 4,
			videoWidth: 160,
			videoHeight: 90,
			removeAttribute() {},
			load: () => (log.loads += 1)
		};
	}
	return {
		width: 0,
		height: 0,
		getContext: () => ({ drawImage() {}, imageSmoothingQuality: '' }),
		toBlob: (done: (blob: Blob) => void) => done(new Blob(['x'])),
		toDataURL: () => 'data:'
	};
}

beforeEach(() => {
	log.seeks = [];
	log.gates = [];
	log.created = 0;
	log.loads = 0;
	vi.stubGlobal('document', { createElement: fakeElement });
	vi.stubGlobal(
		'FileReader',
		class {
			result: string | null = null;
			onload: (() => void) | null = null;
			readAsDataURL() {
				this.result = 'data:image/jpeg;base64,x';
				queueMicrotask(() => this.onload?.());
			}
		}
	);
});

afterEach(() => vi.unstubAllGlobals());

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

describe('createFrameGrabber', () => {
	it('grabs one frame at a time, in the order they were asked for', async () => {
		const grabber = createFrameGrabber('/a.mp4', 48);
		const signal = new AbortController().signal;
		const first = grabber.grab(1, signal);
		const second = grabber.grab(2, signal);
		await tick();
		expect(log.seeks).toEqual([1]);
		log.gates[0]();
		await first;
		await tick();
		expect(log.seeks).toEqual([1, 2]);
		log.gates[1]();
		expect(await second).toContain('data:image/jpeg');
	});

	it('skips a queued grab that was aborted before its turn', async () => {
		const grabber = createFrameGrabber('/a.mp4', 48);
		const stale = new AbortController();
		const first = grabber.grab(1, new AbortController().signal);
		const dropped = grabber.grab(2, stale.signal);
		const kept = grabber.grab(3, new AbortController().signal);
		await tick();
		stale.abort();
		log.gates[0]();
		await first;
		await expect(dropped).rejects.toThrow();
		await tick();
		expect(log.seeks).toEqual([1, 3]);
		log.gates[1]();
		await kept;
	});
});

describe('acquireFrameGrabber', () => {
	it('shares one decoder per clip and tears it down with the last release', () => {
		const one = acquireFrameGrabber('/shared.mp4');
		const two = acquireFrameGrabber('/shared.mp4');
		expect(log.created).toBe(1);
		one.dispose();
		one.dispose();
		expect(log.loads).toBe(0);
		two.dispose();
		expect(log.loads).toBe(1);
		const again = acquireFrameGrabber('/shared.mp4');
		expect(log.created).toBe(2);
		again.dispose();
	});

	it('keeps clips apart', () => {
		const a = acquireFrameGrabber('/a.mp4');
		const b = acquireFrameGrabber('/b.mp4');
		expect(log.created).toBe(2);
		a.dispose();
		b.dispose();
	});

	it('serves grabs from both holders through the one queue with their own sizes', async () => {
		const strip = acquireFrameGrabber('/q.mp4');
		const tiles = acquireFrameGrabber('/q.mp4');
		const signal = new AbortController().signal;
		const a = strip.grab(1, signal, { width: 40, height: 20 });
		const b = tiles.grab(2, signal);
		await tick();
		expect(log.seeks).toEqual([1]);
		log.gates[0]();
		await a;
		await tick();
		expect(log.seeks).toEqual([1, 2]);
		log.gates[1]();
		await b;
		strip.dispose();
		tiles.dispose();
	});
});
