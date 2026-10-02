// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		getHistoryTools: vi.fn().mockResolvedValue({ success: true, data: [] }),
		listUploads: vi
			.fn()
			.mockResolvedValue({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } }),
		editMediaItem: vi.fn(),
		extractMediaFrame: vi.fn(),
		listLibraryItems: vi.fn().mockResolvedValue({ success: true, data: { items: [], total: 0 } }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } })
	}
}));

vi.mock('$lib/utils/storage', () => ({
	storage: { get: vi.fn().mockReturnValue(null), set: vi.fn(), remove: vi.fn() }
}));

vi.mock('$lib/components/form-fields/mediaLoaderProbe', () => ({
	probeMediaFile: vi.fn().mockResolvedValue({})
}));

const { default: MediaLoaderField } = await import(
	'$lib/components/form-fields/MediaLoaderField.svelte'
);
const { createClassComponent } = await import('svelte/legacy');
const { tick, flushSync } = await import('svelte');
const { default: StageReferencesTab } = await import(
	'$lib/components/video-director/console/StageReferencesTab.svelte'
);

function mountField(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: MediaLoaderField as any, target, props });
	return { target, component };
}

const SERVED_SCHEMA = {
	type: 'media',
	title: 'References',
	name: 'references',
	audience: 'simple',
	merge_from: ['older_clips', 'older_tracks'],
	accept: 'video/x-matroska,audio/mp4,audio/mpeg,image/webp,video/webm,image/png,image/jpeg,video/mp4',
	multiple: true,
	accepted_types: ['image', 'video', 'audio'],
	max_items_by_kind: { image: 9, video: 3, audio: 3 }
};

function item(kind: 'image' | 'video' | 'audio', id: string) {
	const ext = { image: 'png', video: 'mp4', audio: 'wav' }[kind];
	return {
		path: `/pool/${id}.${ext}`,
		relative_path: `uploads/${id}.${ext}`,
		url: `/u/${id}.${ext}`,
		name: `${id}.${ext}`,
		type: kind
	};
}

const POOL = [item('image', 'a'), item('video', 'v'), item('image', 'b'), item('audio', 't'), item('image', 'c')];

beforeEach(() => {
	document.body.innerHTML = '';
});

describe('a mixed media field in the shape the form endpoint serves it', () => {
	it('renders one inspector over one strip split into three kind groups', async () => {
		const { target } = mountField({ name: 'references', config: SERVED_SCHEMA, value: POOL, onChange: vi.fn() });
		await tick();
		await tick();

		expect(target.querySelector('[data-face]')?.getAttribute('data-face')).toBe('strip');
		expect(target.querySelectorAll('[data-media-inspector]')).toHaveLength(1);
		expect(target.querySelectorAll('[data-media-strip]')).toHaveLength(1);
		const kinds = Array.from(target.querySelectorAll<HTMLElement>('[data-media-group]')).map((el) => el.dataset.mediaGroup);
		expect(kinds).toEqual(['image', 'video', 'audio']);
		const counts = Array.from(target.querySelectorAll('[data-media-group-count]')).map((el) => el.textContent?.trim());
		expect(counts).toEqual(['3/9', '1/3', '1/3']);
		expect(target.querySelector('[data-media-count]')?.textContent?.trim()).toBe('5/15');
	});

	it('draws the kinds that hold nothing as groups with an empty add box', async () => {
		const { target } = mountField({
			name: 'references',
			config: SERVED_SCHEMA,
			value: [item('image', 'a'), item('image', 'b')],
			onChange: vi.fn()
		});
		await tick();
		await tick();

		expect(target.querySelectorAll('[data-media-inspector]')).toHaveLength(1);
		const kinds = Array.from(target.querySelectorAll<HTMLElement>('[data-media-group]')).map((el) => el.dataset.mediaGroup);
		expect(kinds).toEqual(['image', 'video', 'audio']);
		expect(target.querySelectorAll('[data-media-group="video"] [data-media-tile]')).toHaveLength(0);
		expect(target.querySelectorAll('[data-media-group="video"] [data-media-add="video"]')).toHaveLength(1);
		expect(target.querySelectorAll('[data-media-group="audio"] [data-media-add="audio"]')).toHaveLength(1);
		expect(target.querySelector('[data-media-folded]')).toBeNull();
	});
});

describe('the Director references tab over the same single field', () => {
	const specs = [
		{ field: 'references', kind: 'image', label: 'Pictures', token: '<Picture @>' },
		{ field: 'references', kind: 'video', label: 'Videos', token: '<Video @>' },
		{ field: 'references', kind: 'audio', label: 'Audio', token: '<Audio @>' }
	];
	const caps = { references: 'per_shot', referenceFields: ['references'], segmentRouting: true };
	const marker = (id: string) => `@[references:${id}]`;

	function doc(content: string) {
		return {
			schema_version: 1,
			mode: 'director',
			global_prompt: '',
			global_prompt_segments: [],
			negative_prompt: '',
			negative_prompt_segments: [],
			simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
			timeline: { fps: 24, shots: [] },
			chain: {
				fps: 24,
				segments: [
					{
						id: 's1',
						prompt: content,
						prompt_segments: [{ id: 'p1', content }],
						duration: 5,
						loras: null,
						keyframe: null,
						keyframe_strength: 1,
						last_keyframe: null,
						last_keyframe_strength: 1,
						sub_type_override: null,
						steps: null,
						cfg: null
					}
				],
				continuation: { overlap_frames: 0, stitch: true },
				keyframes: [],
				audio: []
			}
		};
	}

	function mountTab(content: string) {
		const target = document.createElement('div');
		document.body.appendChild(target);
		createClassComponent({
			component: StageReferencesTab as never,
			target,
			props: {
				doc: doc(content),
				caps,
				formData: { references: POOL },
				shotId: 's1',
				promptResources: specs,
				onDoc: vi.fn()
			}
		});
		flushSync();
		return target;
	}

	it('lists the whole pool as one used list and one unused list, numbered per kind within the shot', () => {
		const target = mountTab(`${marker('uploads/b.png')} ${marker('uploads/v.mp4')} ${marker('uploads/t.wav')}`);

		const used = Array.from(target.querySelectorAll<HTMLElement>('[data-testid="shot-reference-used"]'));
		expect(target.querySelectorAll('[data-testid="shot-references-used"]')).toHaveLength(1);
		expect(target.querySelectorAll('[data-testid="shot-references-unused"]')).toHaveLength(1);
		const handles = used.map((el) => el.textContent?.match(/(Picture|Video|Audio) \d+/)?.[0]);
		expect(handles).toEqual(['Video 1', 'Picture 1', 'Audio 1']);
		expect(target.querySelectorAll('[data-testid="shot-reference-unused"]')).toHaveLength(2);
	});
});
