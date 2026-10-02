// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { flushSync } from 'svelte';

const getPresetAssetURL = vi.fn(
	(presetId: string, path: string, size?: string) => `URL:${presetId}:${path}:${size ?? 'full'}`
);

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetAssetURL }
}));

const { default: PresetCoverMedia } = await import('$lib/components/preset/PresetCoverMedia.svelte');
const { createClassComponent } = await import('svelte/legacy');

type Observer = {
	callback: IntersectionObserverCallback;
	options?: IntersectionObserverInit;
	target?: Element;
};
let observers: Observer[] = [];
let media: { reduced: boolean; touch: boolean };
let play: ReturnType<typeof vi.fn>;
let pause: ReturnType<typeof vi.fn>;

beforeEach(() => {
	observers = [];
	media = { reduced: false, touch: false };
	getPresetAssetURL.mockClear();
	play = vi.fn(() => Promise.resolve());
	pause = vi.fn();
	HTMLMediaElement.prototype.play = play as never;
	HTMLMediaElement.prototype.pause = pause as never;
	window.matchMedia = ((query: string) => ({
		matches: query.includes('prefers-reduced-motion') ? media.reduced : media.touch,
		media: query,
		onchange: null,
		addListener() {},
		removeListener() {},
		addEventListener() {},
		removeEventListener() {},
		dispatchEvent: () => false
	})) as never;
	(globalThis as { IntersectionObserver: unknown }).IntersectionObserver = class {
		entry: Observer;
		constructor(callback: IntersectionObserverCallback, options?: IntersectionObserverInit) {
			this.entry = { callback, options };
			observers.push(this.entry);
		}
		observe(target: Element) {
			this.entry.target = target;
		}
		disconnect() {}
		unobserve() {}
	};
});

afterEach(() => {
	document.body.innerHTML = '';
});

function mount(props: Record<string, unknown>, host = false) {
	const outer = document.createElement('div');
	if (host) outer.setAttribute('data-cover-host', '');
	document.body.appendChild(outer);
	const target = document.createElement('div');
	outer.appendChild(target);
	const component = createClassComponent({
		component: PresetCoverMedia as never,
		target,
		props: { presetId: 'p1', presetName: 'Preset', variant: 'small', ...props }
	});
	flushSync();
	return {
		outer,
		img: () => target.querySelector('img'),
		video: () => target.querySelector('video') as HTMLVideoElement | null,
		destroy: () => component.$destroy()
	};
}

function intersect(index: number, ratio: number) {
	const entry = observers[index];
	entry.callback(
		[{ isIntersecting: ratio > 0, intersectionRatio: ratio, target: entry.target } as never],
		{} as never
	);
	flushSync();
}

const nearViewObserver = () => observers.findIndex((o) => o.options?.rootMargin);
const mostlyInViewObserver = () => observers.findIndex((o) => o.options?.threshold);

describe('PresetCoverMedia', () => {
	it('renders an image cover as an img with the sized asset URL', () => {
		const m = mount({ cover: 'public/cover.png' });
		expect(m.img()?.getAttribute('src')).toBe('URL:p1:public/cover.png:small');
		expect(m.video()).toBeNull();
		expect(observers).toHaveLength(0);
	});

	it('renders a video cover as a muted looping video with the first-frame poster', () => {
		const m = mount({ cover: 'public/cover.webm' });
		const video = m.video()!;
		expect(video).not.toBeNull();
		expect(video.muted).toBe(true);
		expect(video.loop).toBe(true);
		expect(video.getAttribute('playsinline')).not.toBeNull();
		expect(video.getAttribute('preload')).toBe('metadata');
		expect(video.getAttribute('poster')).toBe('URL:p1:public/cover.webm:small');
		expect(m.img()).toBeNull();
	});

	it('does not load the video file until it is near the viewport', () => {
		const m = mount({ cover: 'public/cover.webm' });
		expect(m.video()!.getAttribute('src')).toBeNull();
		intersect(nearViewObserver(), 1);
		expect(m.video()!.getAttribute('src')).toBe('URL:p1:public/cover.webm:full');
	});

	it('plays on hover and pauses on leave', () => {
		const m = mount({ cover: 'public/cover.webm' }, true);
		intersect(nearViewObserver(), 1);
		play.mockClear();
		m.outer.dispatchEvent(new Event('pointerenter'));
		flushSync();
		expect(play).toHaveBeenCalledTimes(1);
		pause.mockClear();
		m.outer.dispatchEvent(new Event('pointerleave'));
		flushSync();
		expect(pause).toHaveBeenCalled();
	});

	it('plays while the host holds focus', () => {
		const m = mount({ cover: 'public/cover.webm' }, true);
		intersect(nearViewObserver(), 1);
		play.mockClear();
		m.outer.dispatchEvent(new Event('focusin'));
		flushSync();
		expect(play).toHaveBeenCalledTimes(1);
		m.outer.dispatchEvent(new Event('focusout'));
		flushSync();
		expect(pause).toHaveBeenCalled();
	});

	it('does not play on hover while offscreen', () => {
		const m = mount({ cover: 'public/cover.webm' }, true);
		m.outer.dispatchEvent(new Event('pointerenter'));
		flushSync();
		expect(play).not.toHaveBeenCalled();
	});

	it('on touch devices plays when at least half in view and ignores hover', () => {
		media.touch = true;
		const m = mount({ cover: 'public/cover.webm' }, true);
		intersect(nearViewObserver(), 1);
		play.mockClear();
		m.outer.dispatchEvent(new Event('pointerenter'));
		flushSync();
		expect(play).not.toHaveBeenCalled();

		intersect(mostlyInViewObserver(), 0.3);
		expect(play).not.toHaveBeenCalled();
		intersect(mostlyInViewObserver(), 0.6);
		expect(play).toHaveBeenCalledTimes(1);
		pause.mockClear();
		intersect(mostlyInViewObserver(), 0.1);
		expect(pause).toHaveBeenCalled();
	});

	it('shows only the still and never plays with reduced motion', () => {
		media.reduced = true;
		const m = mount({ cover: 'public/cover.webm' }, true);
		expect(m.video()).toBeNull();
		expect(m.img()?.getAttribute('src')).toBe('URL:p1:public/cover.webm:small');
		m.outer.dispatchEvent(new Event('pointerenter'));
		flushSync();
		expect(play).not.toHaveBeenCalled();
	});

	it('under reduced motion shows a paused video when the still fails, and never plays it', () => {
		media.reduced = true;
		const onfail = vi.fn();
		const m = mount({ cover: 'public/cover.webm', onfail }, true);
		m.img()!.dispatchEvent(new Event('error'));
		flushSync();
		expect(onfail).not.toHaveBeenCalled();
		expect(m.img()).toBeNull();
		const video = m.video()!;
		expect(video).not.toBeNull();
		expect(video.getAttribute('preload')).toBe('metadata');
		intersect(nearViewObserver(), 1);
		expect(video.getAttribute('src')).toBe('URL:p1:public/cover.webm:full');
		m.outer.dispatchEvent(new Event('pointerenter'));
		flushSync();
		expect(play).not.toHaveBeenCalled();
	});

	it('falls back to the still when the video fails to load', () => {
		const m = mount({ cover: 'public/cover.webm' });
		m.video()!.dispatchEvent(new Event('error'));
		flushSync();
		expect(m.video()).toBeNull();
		expect(m.img()?.getAttribute('src')).toBe('URL:p1:public/cover.webm:small');
	});

	it('reports failure when the still image fails', () => {
		const onfail = vi.fn();
		const m = mount({ cover: 'public/cover.png', onfail });
		m.img()!.dispatchEvent(new Event('error'));
		expect(onfail).toHaveBeenCalledTimes(1);
	});

	it('applies the hover zoom class to image and video covers', () => {
		expect(mount({ cover: 'public/cover.png' }).outer.querySelector('[data-cover-media]')!.classList.contains('media-zoom')).toBe(true);
		expect(mount({ cover: 'public/cover.webm' }).outer.querySelector('[data-cover-media]')!.classList.contains('media-zoom')).toBe(true);
	});

	it('omits the zoom class when zoom is off', () => {
		const m = mount({ cover: 'public/cover.png', zoom: false });
		expect(m.outer.querySelector('[data-cover-media]')!.classList.contains('media-zoom')).toBe(false);
	});

	it('treats an already sized video URL as a still', () => {
		const m = mount({ cover: 'http://x/api/media/presets/p/public/cover.webm?size=small' });
		expect(m.video()).toBeNull();
		expect(m.img()?.getAttribute('src')).toBe('http://x/api/media/presets/p/public/cover.webm?size=small');
	});
});
