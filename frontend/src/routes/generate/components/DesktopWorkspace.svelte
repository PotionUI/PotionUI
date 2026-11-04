<script lang="ts">
	import SessionScope from '$lib/components/session/SessionScope.svelte';
	import ContextToolbar from './ContextToolbar.svelte';
	import type { GenerationTab } from '$lib/types/tabs';
	import type { PresetInfo } from '$lib/services/api/index';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import type { PresetModeChoice } from '$lib/utils/presetModeOptions';

	export let tab: GenerationTab;
	export let presets: PresetInfo[] = [];
	export let readiness: ReadinessReport | null = null;
	export let isLoading = false;
	export let isReloading = false;
	export let availableModes: PresetModeChoice[] = [];
	export let onPresetChange: (presetId: string) => void;
	export let onModeChange: (mode: string) => void;
	export let onVariantChange: (variant: string) => void;
	export let onReload: () => void;

	$: presetVersion = presets.find((preset) => preset.id === tab.selectedPreset)?.version;
	$: embedded = tab.embedded ?? null;
</script>

{#if embedded && embedded.persistence === 'local'}
	<div class="flex h-full flex-col">
		<div class="min-h-0 flex-1">
			<slot />
		</div>
	</div>
{:else}
	<SessionScope
		presetId={tab.selectedPreset}
		currentMode={tab.selectedMode}
		tabId={tab.id}
		{presetVersion}
		{availableModes}
		let:session
		let:actions
		let:fieldLabels
	>
		<div class="flex h-full flex-col">
			{#if !embedded}
				<ContextToolbar
					{tab}
					{presets}
					{readiness}
					{isLoading}
					{isReloading}
					{availableModes}
					{session}
					{actions}
					{fieldLabels}
					{onPresetChange}
					{onModeChange}
					{onVariantChange}
					{onReload}
				/>
			{/if}
			<div class="min-h-0 flex-1">
				<slot />
			</div>
		</div>
	</SessionScope>
{/if}
