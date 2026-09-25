// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import type { ChainSegment, DirectorCapabilities, VideoDirectorValue } from '../../src/lib/types/videoDirector';
import type { PromptResourceSpec } from '../../src/lib/utils/promptResources';

const { default: StageReferencesTab } = await import(
	'../../src/lib/components/video-director/console/StageReferencesTab.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { flushSync } = await import('svelte');

const specs: PromptResourceSpec[] = [
	{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
	{ field: 'reference_audios', kind: 'audio', label: 'Audio', token: '<Audio @>' }
];

const formData = {
	references: [
		{ path: '/pool/a.png', url: '/u/a.png', type: 'image' },
		{ path: '/pool/b.png', url: '/u/b.png', type: 'image' },
		{ path: '/pool/c.png', url: '/u/c.png', type: 'image' }
	],
	reference_audios: [{ path: '/pool/t.wav', type: 'audio' }]
};

const caps = {
	references: 'per_shot',
	referenceFields: ['references', 'reference_audios'],
	segmentRouting: true
} as unknown as DirectorCapabilities;

function shot(content: string): ChainSegment {
	return {
		id: 's1',
		prompt: content,
		prompt_segments: [
			{ id: 'p1', content },
			{ id: 'p2', content: 'off @[references:/pool/b.png]', enabled: false }
		],
		duration: 5,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		steps: null,
		cfg: null
	};
}

function doc(content: string): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: { fps: 24, shots: [] },
		chain: { fps: 24, segments: [shot(content)], continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
	} as VideoDirectorValue;
}

function mount(initial: VideoDirectorValue) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const updates: VideoDirectorValue[] = [];
	const component = createClassComponent({
		component: StageReferencesTab as never,
		target,
		props: {
			doc: initial,
			caps,
			formData,
			shotId: 's1',
			promptResources: specs,
			onDoc: (next: VideoDirectorValue) => {
				updates.push(next);
				component.$set({ doc: next });
				flushSync();
			}
		}
	});
	return {
		target,
		updates,
		used: () =>
			[...target.querySelectorAll<HTMLElement>('[data-testid="shot-reference-used"]')].map((el) => ({
				marker: el.dataset.marker,
				text: el.textContent?.replace(/\s+/g, ' ').trim()
			})),
		unused: () => [...target.querySelectorAll<HTMLElement>('[data-testid="shot-reference-unused"]')].map((el) => el.dataset.marker),
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('StageReferencesTab (per_shot)', () => {
	it('splits the pool into used and not used from the shot prompt markers, with no checkboxes', () => {
		mounted = mount(doc('@[references:/pool/c.png] meets @[references:/pool/a.png] and @[references:/pool/c.png]'));

		expect(mounted.target.querySelectorAll('input[type="checkbox"]').length).toBe(0);
		const used = mounted.used();
		expect(used.map((u) => u.marker)).toEqual(['@[references:/pool/a.png]', '@[references:/pool/c.png]']);
		expect(used[0].text).toContain('Picture 1');
		expect(used[0].text).toContain('×1');
		expect(used[1].text).toContain('Picture 2');
		expect(used[1].text).toContain('×2');
		expect(mounted.unused()).toEqual(['@[references:/pool/b.png]', '@[reference_audios:/pool/t.wav]']);
	});

	it('shows the no-reference note when the prompt cites nothing', () => {
		mounted = mount(doc('an empty street'));
		expect(mounted.used()).toEqual([]);
		expect(mounted.target.querySelector('[data-testid="shot-references-empty"]')).not.toBeNull();
		expect(mounted.unused()).toHaveLength(4);
	});

	it('Insert @ adds the marker to the shot prompt and the counts follow', () => {
		mounted = mount(doc('@[references:/pool/c.png]'));
		const row = mounted.target.querySelector<HTMLElement>('[data-marker="@[references:/pool/b.png]"][data-testid="shot-reference-unused"]');
		row?.querySelector<HTMLButtonElement>('button')?.click();
		flushSync();

		expect(mounted.updates).toHaveLength(1);
		const segments = mounted.updates[0].chain.segments[0].prompt_segments;
		expect(segments[0].content).toBe('@[references:/pool/c.png] @[references:/pool/b.png]');
		const used = mounted.used();
		expect(used.map((u) => u.marker)).toEqual(['@[references:/pool/b.png]', '@[references:/pool/c.png]']);
		expect(used[0].text).toContain('Picture 1');
		expect(used[1].text).toContain('Picture 2');
		expect(mounted.unused()).toEqual(['@[references:/pool/a.png]', '@[reference_audios:/pool/t.wav]']);
	});
});
