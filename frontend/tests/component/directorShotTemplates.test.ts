// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: {
		searchPhrasebook: vi.fn().mockResolvedValue({ success: true, data: [] }),
		toggleValueActive: vi.fn().mockResolvedValue({ success: true }),
		getFileURL: (id: string) => id,
		listGenerationMedia: vi.fn().mockResolvedValue({ success: true, data: [] }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: true, data: null }),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({ get: vi.fn(), post: vi.fn() }),
		listSegmentTemplates: vi.fn().mockResolvedValue({ success: true, data: { templates: [] } })
	}
}));

const { mount, unmount, flushSync } = await import('svelte');
const { readable } = await import('svelte/store');
const { default: StageBeat } = await import('$lib/components/video-director/console/StageBeat.svelte');
const { deriveStageModel } = await import('$lib/components/video-director/stage-rail/stageModel');

import type { VideoDirectorValue, ChainSegment, DirectorCapabilities } from '$lib/types/videoDirector';
import type { StageShotModel } from '$lib/components/video-director/stage-rail/stageModel';
import type { PresetSegmentTemplate } from '$lib/utils/presetSegmentTemplates';

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

function blankChainSegment(id: string, duration: number, overrides: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id,
		prompt: '',
		prompt_segments: [],
		duration,
		loras: null,
		keyframe: null,
		keyframe_strength: 1,
		last_keyframe: null,
		last_keyframe_strength: 1,
		sub_type_override: null,
		steps: null,
		cfg: null,
		...overrides
	};
}

function wanCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: {
			director: {
				tips: [],
				maxDuration: null,
				audio: false,
				icLora: false,
				maxKeyframes: null,
				perSegmentLoras: true,
				keyframes: 'first_only',
				maxSegments: 8,
				maxFramesPerSegment: 81,
				defaultSegmentDuration: 5,
				continuation: null,
				maxOverlapFrames: 81,
				continuationDisabled: false,
				fpsLocked: false
			}
		},
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 16,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: true,
		references: null,
		referenceFields: []
	};
}

function chainDocWithOneShot(): VideoDirectorValue {
	const doc = baseDoc();
	doc.chain = {
		fps: 16,
		segments: [blankChainSegment('chain-1', 5)],
		continuation: { overlap_frames: 16, stitch: true },
		keyframes: [],
		audio: []
	};
	return doc;
}

const presetTemplate: PresetSegmentTemplate = {
	id: 'preset:x:*:0',
	name: 'H3 prompt document',
	description: null,
	tags: [],
	segments: [
		{
			type: 'content',
			content: '',
			chips: {},
			enabled: true,
			name: 'Multimodal description',
			prefix: 'integrated_multimodal_description: '
		}
	],
	origin: 'preset'
};

async function settle() {
	for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let cleanup: (() => void) | undefined;

afterEach(() => {
	cleanup?.();
	cleanup = undefined;
	document.body.innerHTML = '';
});

function mountStageBeat(context?: Map<string, unknown>) {
	const caps = wanCaps();
	const doc = chainDocWithOneShot();
	const model = deriveStageModel(doc, caps, { kind: 'shot', id: 'chain-1' }, null).selected as StageShotModel;

	const target = document.createElement('div');
	document.body.appendChild(target);

	const instance = mount(StageBeat, {
		target,
		props: {
			model,
			doc,
			caps,
			timelineShotId: 'chain-1',
			onDoc: () => {}
		},
		context
	});
	cleanup = () => unmount(instance);
	return target;
}

async function openTemplateModal(target: HTMLElement) {
	await settle();
	const templateButton = target.querySelector<HTMLButtonElement>('button[aria-label="Templates"]');
	if (!templateButton) throw new Error('no Apply a Segment Template button found in StageBeat render');
	templateButton.click();
	await settle();
}

describe('StageBeat -- preset Segment Templates reach the shot picker', () => {
	it('a preset template provided via the presetSegmentTemplates context appears in the modal with the Preset badge', async () => {
		const context = new Map<string, unknown>([['presetSegmentTemplates', readable<PresetSegmentTemplate[]>([presetTemplate])]]);
		const target = mountStageBeat(context);

		await openTemplateModal(target);

		expect(document.body.textContent).toContain('H3 prompt document');
		expect(document.body.textContent).toContain('Preset');
	});

	it('without the context, the modal opens but does not list the preset template', async () => {
		const target = mountStageBeat();

		await openTemplateModal(target);

		expect(document.body.textContent).toContain('Apply Segment Template');
		expect(document.body.textContent).not.toContain('H3 prompt document');
	});
});
