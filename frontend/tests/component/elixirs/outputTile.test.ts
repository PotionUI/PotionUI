import { describe, it, expect, afterEach } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import OutputTile from '$lib/components/elixirs/OutputTile.svelte';

let instance: ReturnType<typeof mount> | undefined;

afterEach(() => {
	if (instance) unmount(instance);
	instance = undefined;
	document.body.innerHTML = '';
});

function render(entry: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	instance = mount(OutputTile, { target, props: { entry } });
	flushSync();
	return target;
}

function tile(target: HTMLElement): HTMLElement {
	return target.querySelector('.tile') as HTMLElement;
}

describe('OutputTile aspect ratio', () => {
	it('takes the shape of a portrait image once it loads', () => {
		const target = render({ generation_id: 'g1', file_id: 1, media_type: 'image', preview_url: '/p.png' });
		const img = target.querySelector('img') as HTMLImageElement;
		Object.defineProperty(img, 'naturalWidth', { value: 720 });
		Object.defineProperty(img, 'naturalHeight', { value: 1280 });
		img.dispatchEvent(new Event('load'));
		flushSync();
		expect(tile(target).style.aspectRatio).toBe('720 / 1280');
	});

	it('takes the shape of a video once its metadata loads', () => {
		const target = render({ generation_id: 'g2', file_id: 2, media_type: 'video', media_url: '/v.mp4' });
		const video = target.querySelector('video') as HTMLVideoElement;
		Object.defineProperty(video, 'videoWidth', { value: 1080 });
		Object.defineProperty(video, 'videoHeight', { value: 1920 });
		video.dispatchEvent(new Event('loadedmetadata'));
		flushSync();
		expect(tile(target).style.aspectRatio).toBe('1080 / 1920');
	});

	it('keeps the default shape until the media reports its size', () => {
		const target = render({ generation_id: 'g3', file_id: 3, media_type: 'image', preview_url: '/p.png' });
		expect(tile(target).style.aspectRatio).toBe('');
	});
});
