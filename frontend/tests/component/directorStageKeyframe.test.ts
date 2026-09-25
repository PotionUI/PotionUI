// @vitest-environment jsdom
import { describe, it, expect, afterEach } from 'vitest';
import type { VideoDirectorValue, DirectorCapabilities, DirectorModeCapability } from '../../src/lib/types/videoDirector';

const { default: StageKeyframe } = await import('../../src/lib/components/video-director/stage-rail/StageKeyframe.svelte');
const { deriveStageModel } = await import('../../src/lib/components/video-director/stage-rail/stageModel');
const { createClassComponent } = await import('svelte/legacy');

function baseModeCap(overrides: Partial<DirectorModeCapability> = {}): DirectorModeCapability {
	return {
		tips: [],
		maxDuration: null,
		audio: false,
		icLora: false,
		maxKeyframes: null,
		perSegmentLoras: false,
		keyframes: 'none',
		maxSegments: null,
		maxFramesPerSegment: null,
		defaultSegmentDuration: 5,
		continuation: null,
		maxOverlapFrames: null,
		continuationDisabled: false,
		fpsLocked: false,
		...overrides
	};
}

function timelineCaps(): DirectorCapabilities {
	return {
		presetModes: null,
		modes: { director: baseModeCap({ keyframes: 'anywhere', audio: true, icLora: true, maxKeyframes: 8 }) },
		enabledModes: ['director'],
		defaultDuration: 5,
		defaultFps: 24,
		maxDuration: null,
		maxFrames: null,
		segmentRouting: false,
		references: null,
		referenceFields: []
	};
}

function timelineDoc(): VideoDirectorValue {
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
			shots: [
				{
					id: 'shot-1',
					duration: 5,
					continue_from_previous: false,
					segments: [],
					keyframes: [{ id: 'kf-1', start: 1.8, role: 'free', strength: 0.85, media: { path: 'lighting_ref_1.jpg' } }],
					audio: [],
					ic_lora: []
				}
			]
		},
		chain: { fps: 16, segments: [], continuation: { overlap_frames: 0, stitch: true }, keyframes: [], audio: [] }
	};
}

function mount(props: { doc: VideoDirectorValue; caps: DirectorCapabilities; keyframeId: string; onDoc?: (next: VideoDirectorValue) => void }) {
	const stageModel = deriveStageModel(props.doc, props.caps, { kind: 'keyframe', id: props.keyframeId }, null, 'shot-1');
	if (stageModel.selected.kind !== 'keyframe') throw new Error('expected a keyframe selection');

	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: StageKeyframe as never,
		target,
		props: {
			model: stageModel.selected,
			doc: props.doc,
			caps: props.caps,
			timelineShotId: 'shot-1',
			formData: null,
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
});

describe('StageKeyframe card', () => {
	it('renders one card with a header title, role badge and time, and a header Remove button', () => {
		mounted = mount({ doc: timelineDoc(), caps: timelineCaps(), keyframeId: 'kf-1' });

		expect(mounted.target.querySelectorAll('.stage-card')).toHaveLength(1);
		expect(mounted.text()).toContain('Keyframe');
		expect(mounted.text()).toContain('Free');
		expect(mounted.text()).toContain('1.80 s');

		const removeBtn = mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Remove keyframe"]');
		expect(removeBtn).not.toBeNull();
	});

	it('never renders a plain text Remove button', () => {
		mounted = mount({ doc: timelineDoc(), caps: timelineCaps(), keyframeId: 'kf-1' });

		const textRemove = Array.from(mounted.target.querySelectorAll('button')).find((b) => b.textContent?.trim() === 'Remove');
		expect(textRemove).toBeUndefined();
	});

	it('shows Source when the keyframe has media', () => {
		mounted = mount({ doc: timelineDoc(), caps: timelineCaps(), keyframeId: 'kf-1' });

		expect(mounted.text()).toContain('Source');
		expect(mounted.text()).toContain('lighting_ref_1.jpg');
	});

	it('hides Source entirely (no dash placeholder) when the keyframe has no media', () => {
		const doc = timelineDoc();
		doc.timeline.shots[0].keyframes = [{ id: 'kf-1', start: 1.8, role: 'free', strength: 0.85, media: null }];
		mounted = mount({ doc, caps: timelineCaps(), keyframeId: 'kf-1' });

		expect(mounted.text()).not.toContain('Source');
		expect(mounted.text()).not.toContain('—');
	});

	it('renders the strength slider, disabled until media exists', () => {
		const doc = timelineDoc();
		doc.timeline.shots[0].keyframes = [{ id: 'kf-1', start: 1.8, role: 'free', strength: 0.85, media: null }];
		mounted = mount({ doc, caps: timelineCaps(), keyframeId: 'kf-1' });

		const slider = mounted.target.querySelector<HTMLInputElement>('.strength-slider');
		expect(slider).not.toBeNull();
		expect(slider?.disabled).toBe(true);
	});

	it('clicking the header Remove button drops the keyframe from the shot', () => {
		let latest: VideoDirectorValue | undefined;
		mounted = mount({ doc: timelineDoc(), caps: timelineCaps(), keyframeId: 'kf-1', onDoc: (next) => (latest = next) });

		mounted.target.querySelector<HTMLButtonElement>('button[aria-label="Remove keyframe"]')!.click();

		expect(latest?.timeline.shots[0].keyframes.some((k) => k.id === 'kf-1')).toBe(false);
	});
});
