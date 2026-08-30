<script lang="ts">
	import StudioSheet from './StudioSheet.svelte';
	import PresetControls from '../PresetControls.svelte';
	import SessionPill from '$lib/components/session/SessionPill.svelte';
	import { tabsStore } from '$lib/stores/tabs';
	import type { Tab } from '$lib/types/tabs';
	import type { ReadinessReport } from '$lib/services/api/setup';

	export let tab: Tab;
	export let presets: any[];
	export let readiness: ReadinessReport | null;
	export let isLoading: boolean;
	export let isReloading: boolean;
	export let availableModes: Array<{ id: string; label: string; variants?: any[]; sourcePlugin?: string | null }>;
	export let onPresetChange: (presetId: string) => void;
	export let onModeChange: (mode: string) => void;
	export let onVariantChange: (variant: string) => void;
	export let onReload: () => void;
	export let onClose: () => void;

	$: tabs = $tabsStore.tabs;
	$: activeTabId = $tabsStore.activeTabId;
	$: presetVersion = presets.find((p: any) => p.id === tab.selectedPreset)?.version;

	// Cheap dirty signal for the tab chip strip — `savedSessionSignature ===
	// null` is sessionTabState.ts's own "historical restore is dirty" baseline.
	// A full sessionIsDirty() comparison needs each OTHER tab's live
	// prompt/form state recomputed, which isn't worth doing just to draw a dot.
	function isTabDirty(candidate: Tab): boolean {
		return !!candidate.selectedSessionId && candidate.savedSessionSignature === null;
	}
</script>

<StudioSheet maxHeight="88%" ariaLabel="Preset and session" on:close={onClose}>
	<svelte:fragment slot="header">
		<div class="flex items-baseline gap-2">
			<span class="text-sm font-semibold text-fg">Preset &amp; session</span>
			<span class="font-mono text-2xs uppercase tracking-wide text-fg-subtle">{tab.name}</span>
		</div>
	</svelte:fragment>

	<div class="flex flex-col gap-3 pb-4">
		<PresetControls
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
		/>

		<SessionPill
			presetId={tab.selectedPreset}
			currentMode={tab.selectedMode}
			tabId={tab.id}
			{presetVersion}
			availableModes={availableModes.map((m) => ({ id: m.id, variants: m.variants }))}
		/>

		<div>
			<p class="mb-1.5 font-mono text-2xs uppercase tracking-wide text-fg-subtle">Tabs</p>
			<div class="flex flex-wrap gap-1.5">
				{#each tabs as t (t.id)}
					<button
						type="button"
						class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-2xs font-medium {t.id === activeTabId
							? 'bg-accent text-accent-contrast'
							: 'bg-surface-2 text-fg-muted'}"
						on:click={() => tabsStore.setActiveTab(t.id)}
					>
						{#if isTabDirty(t)}
							<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-warning"></span>
						{/if}
						{t.name}
					</button>
				{/each}
				<button
					type="button"
					class="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded bg-surface-2 text-fg-subtle"
					on:click={() => tabsStore.addTab()}
					aria-label="New tab"
				>
					+
				</button>
			</div>
		</div>
	</div>

	<svelte:fragment slot="footer">
		<button
			type="button"
			class="h-11 w-full rounded-lg bg-accent text-sm font-semibold text-accent-contrast"
			on:click={onClose}
		>
			Done
		</button>
	</svelte:fragment>
</StudioSheet>
