<script lang="ts">
	import SessionScope from '$lib/components/session/SessionScope.svelte';
	import StepEditBar from '$lib/components/elixirs/StepEditBar.svelte';
	import ContextToolbar from './ContextToolbar.svelte';
	import LongVideoNotice from './LongVideoNotice.svelte';
	import type { GenerationTab } from '$lib/types/tabs';
	import type { PresetInfo } from '$lib/services/api/index';
	import type { ReadinessReport } from '$lib/services/api/setup';
	import type { PresetModeChoice } from '$lib/utils/presetModeOptions';
	import type { StepBarModel } from '$lib/elixirs/stepBar';

	export let tab: GenerationTab;
	export let presets: PresetInfo[] = [];
	export let readiness: ReadinessReport | null = null;
	export let isLoading = false;
	export let isReloading = false;
	export let availableModes: PresetModeChoice[] = [];
	export let stepBar: StepBarModel | null = null;
	export let onPresetChange: (presetId: string) => void;
	export let onModeChange: (mode: string) => void;
	export let onVariantChange: (variant: string) => void;
	export let onReload: () => void;

	$: presetVersion = presets.find((preset) => preset.id === tab.selectedPreset)?.version;
	$: embedded = tab.embedded ?? null;
</script>

{#snippet stepLead()}
	{#if stepBar}
		<StepEditBar model={stepBar} part="lead" />
	{/if}
{/snippet}

{#snippet stepActions()}
	{#if stepBar}
		<StepEditBar model={stepBar} part="actions" />
	{/if}
{/snippet}

{#if embedded && embedded.persistence === 'local'}
	<div class="flex h-full flex-col">
		{#if stepBar}
			<ContextToolbar
				{tab}
				{presets}
				{readiness}
				{isLoading}
				{isReloading}
				{availableModes}
				{onPresetChange}
				{onModeChange}
				{onVariantChange}
				{onReload}
				leading={stepLead}
				trailing={stepActions}
			/>
		{/if}
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
			{#if !embedded || stepBar}
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
					leading={embedded ? stepLead : undefined}
					trailing={embedded ? stepActions : undefined}
				/>
			{/if}
			{#if !embedded}
				<LongVideoNotice {tab} modeNames={availableModes.map((mode) => mode.id)} class="mx-4 mt-2" />
			{/if}
			<div class="min-h-0 flex-1">
				<slot />
			</div>
		</div>
	</SessionScope>
{/if}
