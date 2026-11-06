import { describe, it, expect } from 'vitest';
import { resolveConsoleSelection, type ConsoleSelection } from './consoleSelection';
import { deriveShotRail, type ShotRailModel } from './shotRailModel';
import { chainEdgeKeyframeId, resolveDirectorCapabilities } from '$lib/utils/videoDirector';
import type { VideoDirectorValue } from '$lib/types/videoDirector';

const H3_RAW = {
	preset_modes: ['video', 'refs'],
	segment_routing: true,
	modes: {
		t2v: {},
		i2v: {},
		flf: {},
		director: {
			keyframes: 'anywhere',
			audio: true,
			max_keyframes: 8,
			max_segments: 6,
			continuation: { source: 'tail_frames', overlap_frames: 17, stitch: true },
			max_overlap_frames: 34
		}
	},
	limits: { default_duration: 5, default_fps: 24, max_duration: 15 },
	preset_mode_overrides: {
		refs: {
			references: 'per_shot',
			reference_fields: ['references'],
			modes: { director: { keyframes: null, audio: false, continuation: null, max_overlap_frames: null } }
		}
	}
};

function chainDoc(): VideoDirectorValue {
	return {
		schema_version: 1,
		mode: 't2v',
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
					id: 'chain-1',
					prompt: '',
					prompt_segments: [],
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

function timelineRail(): ShotRailModel {
	return {
		durationSeconds: 5,
		ticks: [],
		lanes: {
			prompt: { beats: [{ id: 'seg-1', startPercent: 0, widthPercent: 100, text: '', global: false }], canAdd: true, addDisabledReason: null },
			keyframes: { marks: [{ id: 'kf-1', kind: 'free', atPercent: 40, thumbUrl: null, label: '', empty: false }], count: 1, cap: null, canAdd: true },
			audio: { clips: [{ id: 'au-1', startPercent: 0, widthPercent: 50, role: 'condition', filename: 'a.wav' }], canAdd: true }
		}
	};
}

describe('resolveConsoleSelection', () => {
	it('keeps a start keyframe selection while the H3 video rail shows it', () => {
		const caps = resolveDirectorCapabilities(H3_RAW, 'video')!;
		const rail = deriveShotRail(chainDoc(), caps, 'chain-1');
		const start: ConsoleSelection = { shotId: 'chain-1', kind: 'keyframe', id: chainEdgeKeyframeId('first', 'chain-1') };
		expect(rail.lanes.keyframes?.marks.some((m) => m.id === start.id)).toBe(true);
		expect(resolveConsoleSelection(start, 'chain-1', rail, true)).toBe(start);
	});

	it('falls back to the shot prompt beat when the H3 refs rail has no keyframe lane', () => {
		const caps = resolveDirectorCapabilities(H3_RAW, 'refs')!;
		const rail = deriveShotRail(chainDoc(), caps, 'chain-1');
		const start: ConsoleSelection = { shotId: 'chain-1', kind: 'keyframe', id: chainEdgeKeyframeId('first', 'chain-1') };
		expect(rail.lanes.keyframes).toBeNull();
		expect(resolveConsoleSelection(start, 'chain-1', rail, true)).toEqual({ shotId: 'chain-1', kind: 'beat', id: 'chain-1' });
	});

	it('defaults a chain shot to its beat and keeps that beat as is', () => {
		const caps = resolveDirectorCapabilities(H3_RAW, 'refs')!;
		const rail = deriveShotRail(chainDoc(), caps, 'chain-1');
		const beat = resolveConsoleSelection(null, 'chain-1', rail, true);
		expect(beat).toEqual({ shotId: 'chain-1', kind: 'beat', id: 'chain-1' });
		expect(resolveConsoleSelection(beat, 'chain-1', rail, true)).toBe(beat);
	});

	it('moves a selection from another shot onto the active shot', () => {
		const caps = resolveDirectorCapabilities(H3_RAW, 'video')!;
		const rail = deriveShotRail(chainDoc(), caps, 'chain-1');
		expect(resolveConsoleSelection({ shotId: 'chain-0', kind: 'beat', id: 'chain-0' }, 'chain-1', rail, true)).toEqual({
			shotId: 'chain-1',
			kind: 'beat',
			id: 'chain-1'
		});
	});

	it('keeps timeline beats, keyframes and audio that are on the rail', () => {
		const rail = timelineRail();
		for (const sel of [
			{ shotId: 'shot-1', kind: 'beat', id: 'seg-1' },
			{ shotId: 'shot-1', kind: 'keyframe', id: 'kf-1' },
			{ shotId: 'shot-1', kind: 'audio', id: 'au-1' }
		] as ConsoleSelection[]) {
			expect(resolveConsoleSelection(sel, 'shot-1', rail, false)).toBe(sel);
		}
	});

	it('clears a timeline selection whose object left the rail', () => {
		const rail = timelineRail();
		rail.lanes.audio = null;
		expect(resolveConsoleSelection({ shotId: 'shot-1', kind: 'audio', id: 'au-1' }, 'shot-1', rail, false)).toBeNull();
		expect(resolveConsoleSelection({ shotId: 'shot-1', kind: 'keyframe', id: 'kf-gone' }, 'shot-1', rail, false)).toBeNull();
		expect(resolveConsoleSelection({ shotId: 'shot-1', kind: 'beat', id: 'shot-1' }, 'shot-1', rail, false)).toBeNull();
	});
});
