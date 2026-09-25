// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';

const { mount, unmount, flushSync } = await import('svelte');
const { default: StageBeat } = await import('$lib/components/video-director/console/StageBeat.svelte');
const { deriveStageModel } = await import('$lib/components/video-director/stage-rail/stageModel');

import type { VideoDirectorValue, ChainSegment, DirectorCapabilities } from '$lib/types/videoDirector';
import type { StageShotModel } from '$lib/components/video-director/stage-rail/stageModel';

function chainSegment(id: string, prompt: string, duration: number): ChainSegment {
	return {
		id,
		prompt,
		prompt_segments: prompt ? [{ id: `${id}-p0`, content: prompt, chips: {}, type: 'content', enabled: true }] : [],
		duration,
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

function docWith(globalPrompt: string, shotPrompt: string, negativePrompt: string): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: globalPrompt,
		global_prompt_segments: globalPrompt
			? [{ id: 'g0', content: globalPrompt, chips: {}, type: 'content', enabled: true }]
			: [],
		negative_prompt: negativePrompt,
		negative_prompt_segments: negativePrompt
			? [{ id: 'n0', content: negativePrompt, chips: {}, type: 'content', enabled: true }]
			: [],
		simple: { duration: 5, fps: 24, start_image: null, first_frame: null, last_frame: null },
		timeline: {
			fps: 24,
			shots: [{ id: 'shot-1', duration: 5, continue_from_previous: false, segments: [], keyframes: [], audio: [], ic_lora: [] }]
		},
		chain: {
			fps: 16,
			segments: [chainSegment('chain-1', shotPrompt, 5)],
			continuation: { overlap_frames: 0, stitch: true },
			keyframes: [],
			audio: []
		}
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

function mountStageBeat(doc: VideoDirectorValue) {
	const caps = wanCaps();
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
		}
	});
	cleanup = () => unmount(instance);
	return target;
}

describe('StageBeat -- resolved prompt preview', () => {
	it('renders the shared ResolvedPromptPreview instead of the editor-built-in resolved panel', async () => {
		const target = mountStageBeat(docWith('a sweeping vista', 'a lighthouse at dusk', 'blurry, warped hands'));
		await settle();

		expect(target.textContent).not.toContain('What the model receives');

		const resolvedButton = Array.from(target.querySelectorAll('button')).find((button) =>
			button.textContent?.includes('Resolved prompt')
		) as HTMLButtonElement | undefined;
		expect(resolvedButton).not.toBeUndefined();
		expect(resolvedButton!.textContent).toContain('a lighthouse at dusk');

		resolvedButton!.click();
		await settle();

		expect(target.textContent).toContain('a sweeping vista');

		const negativeTab = Array.from(target.querySelectorAll('[role="tab"]')).find(
			(tab) => tab.textContent?.trim() === 'Negative'
		) as HTMLButtonElement | undefined;
		expect(negativeTab).not.toBeUndefined();
		negativeTab!.click();
		await settle();

		expect(target.textContent).toContain('blurry, warped hands');
	});
});
