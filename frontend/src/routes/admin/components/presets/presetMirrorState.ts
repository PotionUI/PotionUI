import type { PresetInfo, PresetMirrorState } from '$lib/types/api';
import type { DetailHeaderChip } from '$lib/components/detail/detailHeaderChips';

export interface MirrorStateCopy {
	label: string;
	description: string;
}

const STATE_COPY: Record<Exclude<PresetMirrorState, 'ok'>, MirrorStateCopy> = {
	source_missing: {
		label: 'Source missing',
		description: 'The preset it is based on is gone, so users cannot open it until that preset is back.'
	},
	modes_missing: {
		label: 'No modes left',
		description: 'None of the modes it offers exist in its source any more. Pick new modes on the Display tab.'
	},
	id_collision: {
		label: 'ID taken',
		description: 'Another preset now uses the same ID, so users cannot open this one. Remove it and create it again.'
	}
};

export function mirrorStateCopy(state: PresetMirrorState | null | undefined): MirrorStateCopy | null {
	if (!state || state === 'ok') return null;
	return STATE_COPY[state] ?? null;
}

export function isUnavailableMirror(preset: Pick<PresetInfo, 'is_mirror' | 'mirror_state'> | null | undefined): boolean {
	return !!preset?.is_mirror && !!preset.mirror_state && preset.mirror_state !== 'ok';
}

export function mirrorSourceName(
	preset: Pick<PresetInfo, 'is_mirror' | 'source_preset_id'>,
	presetsById: ReadonlyMap<string, Pick<PresetInfo, 'name'>>
): string | null {
	if (!preset.is_mirror || !preset.source_preset_id) return null;
	return presetsById.get(preset.source_preset_id)?.name ?? preset.source_preset_id;
}

export function presetHeaderChips(
	preset: Pick<PresetInfo, 'engine' | 'version' | 'installed' | 'is_mirror' | 'mirror_state'>
): DetailHeaderChip[] {
	const chips: DetailHeaderChip[] = [];
	if (preset.engine) chips.push({ key: 'engine', label: preset.engine, tone: 'signal' });
	if (preset.is_mirror) chips.push({ key: 'mirror', label: 'Mirror', tone: 'neutral' });
	const state = mirrorStateCopy(preset.mirror_state);
	if (preset.is_mirror && state) {
		chips.push({ key: 'mirror-state', label: state.label, tone: 'warning' });
		return chips;
	}
	if (preset.version && !preset.is_mirror) chips.push({ key: 'version', label: `v${preset.version}`, tone: 'neutral' });
	chips.push(
		preset.installed
			? { key: 'installed', label: 'Installed', tone: 'success' }
			: { key: 'installed', label: 'Not installed', tone: 'neutral' }
	);
	return chips;
}

export function mirrorCountLabel(count: number | null | undefined): string | null {
	if (!count || count < 1) return null;
	return `${count} ${count === 1 ? 'mirror' : 'mirrors'}`;
}
