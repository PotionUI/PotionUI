// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import type { VideoDirectorValue } from '../../src/lib/types/videoDirector';

vi.mock('$lib/services/api/index', () => ({
	api: {
		getModels: vi.fn().mockResolvedValue({ success: true, data: { models: [], total: 0 } }),
		getPresetModels: vi.fn().mockResolvedValue({ success: true, data: { models: [], total: 0 } }),
		getModelById: vi.fn().mockResolvedValue({ success: false }),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getModelDownloadStatus: vi.fn(),
		startModelDownload: vi.fn(),
		setOnAuthExpired: vi.fn(),
		getToken: vi.fn(() => null),
		getBaseURL: vi.fn(() => 'http://localhost')
	}
}));

const { default: StageIcLora } = await import('../../src/lib/components/video-director/stage-rail/StageIcLora.svelte');
const { createClassComponent } = await import('svelte/legacy');

function baseDoc(): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: {
			fps: 24,
			shots: [{ id: 'shot-1', duration: 5, continue_from_previous: false, segments: [], keyframes: [], audio: [], ic_lora: [] }]
		},
		chain: { fps: 16, segments: [], continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
	};
}

function mount(props: { doc: VideoDirectorValue; onDoc?: (next: VideoDirectorValue) => void }) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: StageIcLora as never,
		target,
		props: {
			doc: props.doc,
			timelineShotId: 'shot-1',
			formData: null,
			presetId: 'preset-1',
			onDoc: props.onDoc ?? (() => {})
		}
	});
	return {
		target,
		text: () => target.textContent ?? '',
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
	vi.clearAllMocks();
});

describe('StageIcLora, 0 entries', () => {
	it('renders the empty hint and the dashed Add button, no entry card and no fixed-height clip well', () => {
		mounted = mount({ doc: baseDoc() });

		expect(mounted.target.querySelector('.icl-card')).toBeNull();
		expect(mounted.target.querySelector('.icl-well')).toBeNull();

		const addBtn = mounted.target.querySelector<HTMLButtonElement>('.add-icl');
		expect(addBtn).not.toBeNull();
		expect(addBtn?.textContent?.trim()).toContain('Add IC-LoRA');

		expect(mounted.text()).toContain('Add one to get started');
	});

	it('clicking Add IC-LoRA mints one entry onto the shot', () => {
		const doc = baseDoc();
		let latest: VideoDirectorValue | undefined;
		mounted = mount({ doc, onDoc: (next) => (latest = next) });

		mounted.target.querySelector<HTMLButtonElement>('.add-icl')!.click();

		expect(latest?.timeline.shots[0].ic_lora).toHaveLength(1);
		expect(latest?.timeline.shots[0].ic_lora[0].id).toBe('ic-lora-1');
	});
});

describe('StageIcLora, 1 entry', () => {
	function docWithEntry(): VideoDirectorValue {
		const doc = baseDoc();
		doc.timeline.shots[0].ic_lora = [
			{ id: 'ic-lora-1', lora: { model: 'model:MDL-A', strength: 0.8 }, ref_media: null, strength: 0.42 }
		];
		return doc;
	}

	it('renders exactly one card with its title and header Remove button, and no floating strength/remove', () => {
		mounted = mount({ doc: docWithEntry() });

		const cards = mounted.target.querySelectorAll('.icl-card');
		expect(cards).toHaveLength(1);
		expect(mounted.target.querySelector('.icl-well')).toBeNull();

		expect(mounted.text()).toContain('IC-LoRA');
		expect(mounted.text()).toContain('1');
		expect(mounted.text()).toContain('0.42');

		const removeBtn = mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Remove IC-LoRA"]');
		expect(removeBtn).not.toBeNull();
	});

	it('never shows the picker\'s big "No LoRAs added" box for an entry with no LoRA chosen yet', () => {
		const doc = baseDoc();
		doc.timeline.shots[0].ic_lora = [{ id: 'ic-lora-1', lora: null, ref_media: null, strength: 1 }];
		mounted = mount({ doc });

		expect(mounted.text()).not.toContain('No LoRAs added');
		expect(mounted.text()).toContain('Choose IC-LoRA');
	});

	it('clicking the header Remove button drops the entry from the shot', () => {
		let latest: VideoDirectorValue | undefined;
		mounted = mount({ doc: docWithEntry(), onDoc: (next) => (latest = next) });

		mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Remove IC-LoRA"]')!.click();

		expect(latest?.timeline.shots[0].ic_lora).toHaveLength(0);
	});
});
