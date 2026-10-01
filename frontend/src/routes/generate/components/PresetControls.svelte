<script lang="ts">
	import PresetHeader from '$lib/components/preset/PresetHeader.svelte';
	import FormulasButton from '$lib/components/formulas/FormulasButton.svelte';
	import type { Tab } from '$lib/types/tabs';
	import type { PresetInfo, PresetModeVariant } from '$lib/services/api/index';
	import type { ReadinessReport } from '$lib/services/api/setup';

	// The preset/mode/variant selector, wired the same way at every mount site
	// (mobile Panel 0, the desktop no-selection placeholder, and the desktop
	// GenerationPanels left pane).
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
	}> = [];
	export let onPresetChange: (presetId: string) => void;
	export let onModeChange: (mode: string) => void;
	export let onVariantChange: (variant: string) => void;
	export let onReload: () => void;
	export let onFormulaApplied: (() => void) | undefined = undefined;

	$: presetInfo = presets.find((p) => p.id === tab.selectedPreset);
	$: modeLabel = availableModes.find((m) => m.id === tab.selectedMode)?.label ?? '';
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
			{tab}
			presetName={presetInfo?.name ?? ''}
			presetVersion={presetInfo?.version ?? ''}
			{modeLabel}
			onApplied={onFormulaApplied}
		/>
	</svelte:fragment>
</PresetHeader>
