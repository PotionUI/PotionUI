<script lang="ts">
	import PresetHeader from '$lib/components/preset/PresetHeader.svelte';
	import type { GenerationTab } from '$lib/types/tabs';
	import type { PresetInfo, PresetModeVariant } from '$lib/services/api/index';
	import type { PresetModeInstall } from '$lib/types/api';
	import type { ReadinessReport } from '$lib/services/api/setup';

	export let tab: GenerationTab;
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
		requirementsMissing?: string[] | null;
		install?: PresetModeInstall | null;
	}> = [];
	export let onPresetChange: (presetId: string) => void;
	export let onModeChange: (mode: string) => void;
	export let onVariantChange: (variant: string) => void;
	export let onReload: () => void;
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
/>
