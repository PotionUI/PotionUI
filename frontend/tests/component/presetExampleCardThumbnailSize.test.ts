// @vitest-environment jsdom
//
// A preset example card is a thumbnail tile - it must never request the
// preset's full-size media (CMB card: don't load a full image/video when a
// small render will do). A video item additionally must render its poster
// frame, not the video file itself, with a play badge marking it as video.
import { describe, it, expect, vi, afterEach } from 'vitest';

const getPresetAssetURL = vi.fn(
	(presetId: string, path: string, size?: string) => `URL:${presetId}:${path}:${size ?? 'full'}`
);

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetAssetURL }
}));

const { default: PresetExampleCard } = await import(
	'$lib/components/preset/PresetExampleCard.svelte'
);
const { createClassComponent } = await import('svelte/legacy');

function mountCard(item: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: PresetExampleCard as never,
		target,
		props: { presetId: 'preset-1', presetName: 'My Preset', item }
	});
	return {
		target,
		component,
		img: () => target.querySelector('img'),
		video: () => target.querySelector('video'),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mountCard> | undefined;

afterEach(() => {
	getPresetAssetURL.mockClear();
	mounted?.destroy();
	mounted = undefined;
});

describe('PresetExampleCard thumbnail sizing', () => {
	it('requests a small render for an image example, not the full original', () => {
		mounted = mountCard({ src: 'public/example.png', caption: 'Example' });

		expect(getPresetAssetURL).toHaveBeenCalledWith('preset-1', 'public/example.png', 'small');
		expect(mounted.img()?.getAttribute('src')).toBe('URL:preset-1:public/example.png:small');
	});

	it('renders a poster image, never a <video> element, for a video example', () => {
		mounted = mountCard({ src: 'public/clip.mp4', caption: 'Clip' });

		expect(getPresetAssetURL).toHaveBeenCalledWith('preset-1', 'public/clip.mp4', 'small');
		expect(mounted.video()).toBeNull();
		expect(mounted.img()?.getAttribute('src')).toBe('URL:preset-1:public/clip.mp4:small');
	});

	it('shows a play badge over the poster for a video example', () => {
		mounted = mountCard({ src: 'public/clip.webm' });

		expect(mounted.target.querySelectorAll('svg').length).toBeGreaterThan(0);
	});

	it('shows no play badge for an image example', () => {
		mounted = mountCard({ src: 'public/example.png' });

		expect(mounted.target.querySelectorAll('svg').length).toBe(0);
	});
});
