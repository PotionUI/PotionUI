// DIR-06 rework 2: proves the REAL round-trip channel for a Wan timing
// profile is `formData` itself, persisted and restored alongside
// `videoDirector` as one unit by the actual tab-session machinery
// (`collectTabSessionData`/`buildSessionRestoreTabPatch`) -- not a synthetic
// `chain.timingProfile` fixture. `resolveDirectorTimingProfile` deliberately
// has no document parameter any more (see its own doc comment): a restored
// session's `svi_motion_latent_count` reaches it through the SAME
// live-form-value branch a fresh session uses.
import { describe, it, expect } from 'vitest';
import type { Tab } from '$lib/types/tabs';
import { collectTabSessionData } from './sessionTabState';
import { buildSessionRestoreTabPatch } from './sessionRestore';
import { resolveDirectorTimingProfile, resolveDirectorCapabilities, buildDirectorSubmission } from './videoDirector';
import { deriveRailModel } from '../components/video-director/stage-rail/railModel';
import type { VideoDirectorValue, ChainSegment } from '$lib/types/videoDirector';

const WAN_PRESET_RAW = {
	family: 'wan',
	timing: { motion_latent_count_field: 'svi_motion_latent_count', motion_latent_count_default: 1 },
	preset_modes: ['video'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: {
			per_segment_loras: true,
			keyframes: 'first_only',
			max_segments: 8,
			max_frames_per_segment: 81,
			continuation: { source: 'tail_frames', overlap_frames: 4, stitch: true }
		}
	},
	limits: { default_duration: 5, default_fps: 16, max_duration: 60 }
};

function blankChainSegment(id: string, duration: number, overrides: Partial<ChainSegment> = {}): ChainSegment {
	return {
		id,
		prompt: `shot ${id}`,
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

function wan808080Doc(): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 'director',
		global_prompt: '',
		global_prompt_segments: [],
		negative_prompt: '',
		negative_prompt_segments: [],
		simple: { duration: 5, fps: 16, start_image: null, first_frame: null, last_frame: null },
		timeline: {
			fps: 16,
			shots: [{ id: 'shot-1', duration: 5, continue_from_previous: false, segments: [], keyframes: [], audio: [], ic_lora: [] }]
		},
		chain: {
			fps: 16,
			segments: [
				blankChainSegment('seg-a', 80 / 16, { sub_type_override: 't2v' }),
				blankChainSegment('seg-b', 80 / 16),
				blankChainSegment('seg-c', 80 / 16, { sub_type_override: 't2v' })
			],
			continuation: { overlap_frames: 4, stitch: true },
			keyframes: [],
			audio: []
		}
	};
}

function tab(overrides: Partial<Tab> = {}): Tab {
	return {
		id: 'tab-a',
		name: 'Tab A',
		selectedPreset: 'wan-preset',
		selectedMode: 'video',
		selectedSessionId: 'session-a',
		prompt: '',
		negativePrompt: '',
		formData: {},
		generation: {
			isGenerating: false,
			currentGeneration: null,
			currentProgress: null,
			pipeTimers: {},
			startedAt: null,
			totalTime: null,
			lastDurationMs: null,
			batchImages: [],
			batchVideos: [],
			batchAudios: [],
			artifacts: [],
			workbenchIndex: 0,
			workbenchTotal: 0,
			queue: [],
			submittedPromptTemplate: null
		},
		workbenchMaxHeight: '600',
		leftPanelWidth: 380,
		leftPanelCollapsed: true,
		workbenchCollapsed: false,
		layoutMode: 'three',
		promptPanelWidth: 420,
		...overrides
	} as Tab;
}

describe('a saved-and-restored Wan tab resolves the SAME timing profile through the real session machinery', () => {
	it('collectTabSessionData -> buildSessionRestoreTabPatch round-trips formData and videoDirector as one unit', () => {
		const caps = resolveDirectorCapabilities(WAN_PRESET_RAW, 'video')!;
		const originalDoc = wan808080Doc();
		const originalFormData = { svi_motion_latent_count: 2 };
		const liveTab = tab({ videoDirector: originalDoc, formData: originalFormData });

		// The numbers BEFORE saving -- what the user was looking at.
		const preSaveProfile = resolveDirectorTimingProfile(caps, liveTab.formData);
		const preSaveRail = deriveRailModel(originalDoc, caps, undefined, preSaveProfile);
		expect(preSaveRail.shots.map((s) => s.contributedFrames)).toEqual([81, 77, 81]);

		// Save (collectTabSessionData) then restore (buildSessionRestoreTabPatch)
		// -- the exact pair SessionPill.svelte / sessions.ts `loadSession` /
		// +page.svelte's auto-restore all use.
		const saved = collectTabSessionData(liveTab, 'video');
		const modeData = saved.video!;
		const restoredPatch = buildSessionRestoreTabPatch(modeData);

		// The restored formData carries the SAME sibling value the
		// orchestrator itself reads (request.form_data.get('svi_motion_latent_count')) --
		// submitted alongside (never inside) buildDirectorSubmission's own
		// video_director payload, since that field lives outside the document.
		expect(restoredPatch.formData).toEqual(originalFormData);
		expect(restoredPatch.videoDirector).toEqual(originalDoc);

		// Deriving the rail from the RESTORED state through the real
		// production resolver reproduces the exact pre-save numbers.
		const restoredProfile = resolveDirectorTimingProfile(caps, restoredPatch.formData);
		expect(restoredProfile).toEqual(preSaveProfile);
		const restoredRail = deriveRailModel(restoredPatch.videoDirector!, caps, undefined, restoredProfile);
		expect(restoredRail.shots.map((s) => s.contributedFrames)).toEqual([81, 77, 81]);
		expect(restoredRail.totalFrames).toBe(preSaveRail.totalFrames);

		// The submission itself never carries the sibling value (it can't --
		// buildDirectorSubmission has no formData parameter at all) -- proving
		// the two pieces genuinely travel as siblings, not folded into one
		// payload.
		const submission = buildDirectorSubmission(restoredPatch.videoDirector!, caps);
		expect(submission).toHaveLength(1);
		expect(JSON.stringify(submission[0])).not.toContain('svi_motion_latent_count');
	});

	it('a restored session with no motion_latent_count field at all resolves to the capability default, not a stale document value', () => {
		const caps = resolveDirectorCapabilities(WAN_PRESET_RAW, 'video')!;
		const liveTab = tab({ videoDirector: wan808080Doc(), formData: {} });

		const saved = collectTabSessionData(liveTab, 'video');
		const restoredPatch = buildSessionRestoreTabPatch(saved.video!);

		const restoredProfile = resolveDirectorTimingProfile(caps, restoredPatch.formData);
		expect(restoredProfile).toEqual({ motionLatentCount: 1 }); // capability default
		const restoredRail = deriveRailModel(restoredPatch.videoDirector!, caps, undefined, restoredProfile);
		expect(restoredRail.shots.map((s) => s.contributedFrames)).toEqual([81, 80, 81]);
	});
});
