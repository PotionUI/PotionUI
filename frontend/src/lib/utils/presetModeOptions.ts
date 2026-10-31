import type { PresetModeInstall, PresetModeVariant } from '$lib/types/api';
import { openModeInstall } from '$lib/stores/modeInstall';
import { formatBytesShort } from '$lib/utils/format';
import { modeLabel } from '$lib/utils/modeLabel.svelte';
import { hasIcon } from '$lib/utils/IconLibrary';
import { modeRequirementIssue } from '$lib/utils/modeRequirements';

export interface PresetModeChoice {
	id: string;
	label: string;
	variants?: PresetModeVariant[];
	sourcePlugin?: string | null;
	description?: string | null;
	icon?: string | null;
	requirementsMissing?: string[] | null;
	install?: PresetModeInstall | null;
}

export interface PresetModeOption {
	value: string;
	label: string;
	icon?: string;
	description?: string;
	marker?: string;
	markerLabel?: string;
	issue?: string;
	notice?: string;
	action?: { label: string; onclick: () => void };
}

export const MODE_NOT_SET_UP_NOTICE = 'Not set up yet - ask an admin';

export function modeInstallLabel(install: PresetModeInstall): string {
	return install.missing_bytes != null && install.missing_bytes > 0
		? `Install · ${formatBytesShort(install.missing_bytes)}`
		: 'Install';
}

function installOptions(
	mode: PresetModeChoice,
	admin: boolean
): Pick<PresetModeOption, 'issue' | 'notice' | 'action'> {
	const missing = (mode.requirementsMissing?.length ?? 0) > 0 || mode.install?.ready === false;
	if (!missing) return {};
	if (!admin) return { notice: MODE_NOT_SET_UP_NOTICE };
	const install = mode.install;
	if (install?.recipe_id) {
		return {
			action: {
				label: modeInstallLabel(install),
				onclick: () => openModeInstall(install.recipe_id, [mode.id])
			}
		};
	}
	return { issue: modeRequirementIssue(mode.requirementsMissing) };
}

export function presetModeOptions(modes: PresetModeChoice[], options: { admin?: boolean } = {}): PresetModeOption[] {
	const admin = options.admin ?? false;
	return modes.map((mode) => ({
		value: mode.id,
		label: modeLabel(mode.id, mode.label),
		icon: mode.icon && hasIcon(mode.icon) ? mode.icon : undefined,
		description: mode.description || undefined,
		marker: mode.sourcePlugin ? '•' : undefined,
		markerLabel: mode.sourcePlugin ? `contributed by ${mode.sourcePlugin}` : undefined,
		...installOptions(mode, admin)
	}));
}
