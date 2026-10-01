import type { DirectorCapabilities } from '$lib/types/videoDirector';
import type { MusicDirectorCapabilities } from '$lib/types/musicDirector';
import type { SegmentJoin } from '$lib/utils/richSegments';
import { resolveDirectorCapabilities } from '$lib/utils/videoDirector';
import { resolveMusicDirectorCapabilities } from '$lib/utils/musicDirector';
import { isPromptlessMode } from '$lib/utils/promptlessMode';

export interface RequestContext {
	numPrompts: number;
	segmentJoin: SegmentJoin;
	promptRelayActive: boolean;
	promptlessActive: boolean;
	videoDirectorCaps: DirectorCapabilities | null;
	videoDirectorActive: boolean;
	musicDirectorCaps: MusicDirectorCapabilities | null;
	musicDirectorActive: boolean;
}

type PresetVars = Record<string, any> | null | undefined;

function modeEnabled(caps: { presetModes: string[] | null } | null, mode: string | null | undefined): boolean {
	return !!caps && !!mode && (caps.presetModes === null || caps.presetModes.includes(mode));
}

export function resolveRequestContext(presetVars: PresetVars, mode: string | null | undefined): RequestContext {
	const vars = presetVars ?? undefined;
	const relayModes: string[] = vars?.prompt_relay_modes || [];
	const videoDirectorCaps = resolveDirectorCapabilities(vars?.video_director, mode);
	const musicDirectorCaps = resolveMusicDirectorCapabilities(vars?.music_director, mode);
	return {
		numPrompts: vars?.num_prompts || 1,
		segmentJoin: vars?.prompt?.segment_join === 'paragraph' ? 'paragraph' : 'space',
		promptRelayActive: !!mode && relayModes.includes(mode),
		promptlessActive: isPromptlessMode(vars ?? {}, mode),
		videoDirectorCaps,
		videoDirectorActive: modeEnabled(videoDirectorCaps, mode),
		musicDirectorCaps,
		musicDirectorActive: modeEnabled(musicDirectorCaps, mode)
	};
}

function contextKey(presetId: string, presetVars: PresetVars, mode: string | null | undefined): string {
	const vars = presetVars ?? undefined;
	return JSON.stringify([
		presetId,
		mode ?? '',
		vars?.video_director ?? null,
		vars?.music_director ?? null,
		vars?.num_prompts ?? null,
		vars?.prompt?.segment_join ?? null,
		vars?.prompt_relay_modes ?? null,
		vars?.promptless_modes ?? null
	]);
}

export function createRequestContextCache() {
	let cached: { key: string; context: RequestContext } | null = null;
	return (presetId: string, presetVars: PresetVars, mode: string | null | undefined): RequestContext => {
		const key = contextKey(presetId, presetVars, mode);
		if (cached && cached.key === key) return cached.context;
		const context = resolveRequestContext(presetVars, mode);
		cached = { key, context };
		return context;
	};
}
