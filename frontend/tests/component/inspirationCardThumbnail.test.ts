// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { InspirationDto, InspirationMedia } from '$lib/services/api/inspirations';

const { default: InspirationCard } = await import(
	'../../src/routes/inspirations/components/InspirationCard.svelte'
);

function inspiration(media: InspirationMedia[]): InspirationDto {
	return {
		id: 'insp-1',
		title: 'Dusk',
		description: null,
		author: { id: 'u1', username: 'ada', avatar_url: null },
		media,
		params_preview: [],
		technique: null,
		created_at: '2026-01-01T00:00:00Z',
		comment_count: 0,
		save_count: 0,
		saved_by_me: false,
		source_generation_id: null
	};
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function mountCard(media: InspirationMedia[]) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(InspirationCard, {
		target,
		props: { item: inspiration(media), onOpen: () => {} }
	});
	flushSync();
	return target.querySelector('button') as HTMLButtonElement;
}

const img = () => target.querySelector('img');
const video = () => target.querySelector('video');

const imageMedia = { url: '/full.png', type: 'image', thumbnail_url: '/thumb.webp' };
const videoMedia = { url: '/clip.mp4', type: 'video', thumbnail_url: '/poster.webp' };

afterEach(() => {
	if (component) unmount(component);
	component = null;
	target?.remove();
	document.body.innerHTML = '';
});

describe('InspirationCard thumbnails', () => {
	it('shows the thumbnail for an image, decoding asynchronously', () => {
		mountCard([imageMedia]);
		expect(img()?.getAttribute('src')).toBe('/thumb.webp');
		expect(img()?.getAttribute('decoding')).toBe('async');
	});

	it('falls back to the original image after the thumbnail errors', () => {
		mountCard([imageMedia]);
		img()!.dispatchEvent(new Event('error'));
		flushSync();
		expect(img()?.getAttribute('src')).toBe('/full.png');
	});

	it('uses the original image when there is no thumbnail', () => {
		mountCard([{ url: '/full.png', type: 'image', thumbnail_url: null }]);
		expect(img()?.getAttribute('src')).toBe('/full.png');
	});

	it('shows a poster image for a video and mounts the video on hover and focus', () => {
		const card = mountCard([videoMedia]);
		expect(img()?.getAttribute('src')).toBe('/poster.webp');
		expect(video()).toBeNull();

		card.dispatchEvent(new MouseEvent('mouseenter'));
		flushSync();
		expect(video()?.getAttribute('src')).toBe('/clip.mp4');
		expect(video()?.loop).toBe(true);
		expect(video()?.autoplay).toBe(true);
		expect(img()).toBeNull();

		card.dispatchEvent(new MouseEvent('mouseleave'));
		flushSync();
		expect(video()).toBeNull();

		card.dispatchEvent(new FocusEvent('focus'));
		flushSync();
		expect(video()).not.toBeNull();
	});

	it('mounts the video straight away for a video without a thumbnail', () => {
		mountCard([{ url: '/clip.mp4', type: 'video', thumbnail_url: null }]);
		expect(video()?.getAttribute('src')).toBe('/clip.mp4');
		expect(img()).toBeNull();
	});
});
