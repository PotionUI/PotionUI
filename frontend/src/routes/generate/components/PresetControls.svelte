<script lang="ts">
	import PresetHeader from '$lib/components/preset/PresetHeader.svelte';
	import FormulasButton from '$lib/components/formulas/FormulasButton.svelte';
	import type { Tab } from '$lib/types/tabs';
	import type { PresetInfo, PresetModeVariant } from '$lib/services/api/index';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import { modeLabel as resolveModeLabel } from '$lib/utils/modeLabel.svelte';

	export let tab: Tab;
	export let presets: PresetInfo[] = [];
	export let readiness: ReadinessReport | null = null;
	export let isLoading = false;
	export let isReloading = false;
	export let availableModes: Array<{
		id: string;
		label: string;
		variants?: PresetModeVariant[];
		sourcePlugin?: string | null;
		description?: string | null;
		icon?: string | null;
	}> = [];
	export let onPresetChange: (presetId: string) => void;
	export let onModeChange: (mode: string) => void;
	export let onVariantChange: (variant: string) => void;
	export let onReload: () => void;
	export let onFormulaApplied: (() => void) | undefined = undefined;

	$: presetInfo = presets.find((p) => p.id === tab.selectedPreset);
	$: modeLabel = resolveModeLabel(tab.selectedMode, availableModes.find((m) => m.id === tab.selectedMode)?.label);
</script>

<PresetHeader
	{presets}
	{readiness}
	{isLoading}
	{isReloading}
	selectedPreset={tab.selectedPreset || ''}
	selectedMode={tab.selectedMode ?? ''}
	{availableModes}
	selectedVariant={tab.selectedVariant ?? null}
	on:presetChange={(e) => onPresetChange(e.detail)}
	on:modeChange={(e) => onModeChange(e.detail)}
	on:variantChange={(e) => onVariantChange(e.detail)}
	on:reload={onReload}
>
	<svelte:fragment slot="mode-actions">
		<FormulasButton
			grouped
			{tab}
			presetName={presetInfo?.name ?? ''}
			presetVersion={presetInfo?.version ?? ''}
			{modeLabel}
			onApplied={onFormulaApplied}
		/>
	</svelte:fragment>
</PresetHeader>
