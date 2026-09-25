// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { flushSync } from 'svelte';

const { default: MediaThumb } = await import('$lib/components/media/MediaThumb.svelte');
const { createClassComponent } = await import('svelte/legacy');

let mounted: { target: HTMLElement; destroy: () => void; component: ReturnType<typeof createClassComponent> } | undefined;

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: MediaThumb as never, target, props });
	return {
		target,
		component,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('MediaThumb', () => {
	it('renders an img for an image kind', () => {
		mounted = mount({ url: '/api/media/a.png', kind: 'image', name: 'a' });
		const img = mounted.target.querySelector('img');
		expect(img?.getAttribute('src')).toBe('/api/media/a.png');
		expect(mounted.target.querySelector('video')).toBeNull();
	});

	it('renders a video element (not an img) for a video kind', () => {
		mounted = mount({ url: '/api/media/clip.mp4', kind: 'video', name: 'clip' });
		expect(mounted.target.querySelector('img')).toBeNull();
		const video = mounted.target.querySelector('video');
		expect(video?.getAttribute('src')).toBe('/api/media/clip.mp4');
		expect(video?.getAttribute('preload')).toBe('metadata');
		expect((video as HTMLVideoElement).muted).toBe(true);
	});

	it('renders an icon tile (no img, no video) for an audio kind', () => {
		mounted = mount({ url: '/api/media/voice.mp3', kind: 'audio', name: 'voice' });
		expect(mounted.target.querySelector('img')).toBeNull();
		expect(mounted.target.querySelector('video')).toBeNull();
		expect(mounted.target.querySelector('svg')).toBeTruthy();
	});

	it('infers a video kind from the url extension when kind is missing', () => {
		mounted = mount({ url: '/api/media/clip.webm' });
		expect(mounted.target.querySelector('video')).toBeTruthy();
		expect(mounted.target.querySelector('img')).toBeNull();
	});

	it('falls back to the kind icon tile when there is no url', () => {
		mounted = mount({ kind: 'audio' });
		expect(mounted.target.querySelector('img')).toBeNull();
		expect(mounted.target.querySelector('video')).toBeNull();
		expect(mounted.target.querySelector('svg')).toBeTruthy();
	});

	it('swaps to the icon tile when the image fails to load', () => {
		mounted = mount({ url: '/api/media/broken.png', kind: 'image', name: 'broken' });
		const img = mounted.target.querySelector('img')!;
		img.dispatchEvent(new Event('error'));
		flushSync();
		expect(mounted.target.querySelector('img')).toBeNull();
		expect(mounted.target.querySelector('svg')).toBeTruthy();
	});

	it('swaps to the icon tile when the video fails to load', () => {
		mounted = mount({ url: '/api/media/broken.mp4', kind: 'video', name: 'broken' });
		const video = mounted.target.querySelector('video')!;
		video.dispatchEvent(new Event('error'));
		flushSync();
		expect(mounted.target.querySelector('video')).toBeNull();
		expect(mounted.target.querySelector('svg')).toBeTruthy();
	});
});
