<script lang="ts">
	import StudioSheet from '$lib/components/StudioSheet.svelte';
	import PresetControls from '../PresetControls.svelte';
	import SessionControl from '$lib/components/session/SessionControl.svelte';
	import type { SessionPanelActions, SessionPanelState } from '$lib/session/sessionDialogs';
	import type { FieldLabels } from '$lib/session/sessionDrawerModel';
	import FormulasButton from '$lib/components/formulas/FormulasButton.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { formAudienceStore } from '$lib/stores/formAudience';
	import { modeLabel } from '$lib/utils/modeLabel.svelte';
	import { tabsStore } from '$lib/stores/tabs';
	import { tabSessionDirty } from '$lib/stores/tabSessionDirty';
	import { tabShowsUnsavedChanges } from '$lib/tabs/tabDirty';
	import { isToolTab, type GenerationTab, type Tab } from '$lib/types/tabs';
	import type { ReadinessReport } from '$lib/services/api/setup';

	export let tab: GenerationTab;
	export let presets: any[];
	export let readiness: ReadinessReport | null;
	export let isLoading: boolean;
	export let isReloading: boolean;
	export let availableModes: Array<{ id: string; label: string; variants?: any[]; sourcePlugin?: string | null; description?: string | null; icon?: string | null; requirementsMissing?: string[] | null; install?: import('$lib/types/api').PresetModeInstall | null }>;
	export let onPresetChange: (presetId: string) => void;
	export let onModeChange: (mode: string) => void;
	export let onVariantChange: (variant: string) => void;
	export let onReload: () => void;
	export let onClose: () => void;
	export let onFormulaApplied: (() => void) | undefined = undefined;
	export let session: SessionPanelState;
	export let actions: SessionPanelActions;
	export let fieldLabels: FieldLabels = {};

	$: tabs = $tabsStore.tabs;
	$: activeTabId = $tabsStore.activeTabId;
	$: presetInfo = presets.find((p: any) => p.id === tab.selectedPreset);
	$: presetVersion = presetInfo?.version;
	$: currentModeLabel = modeLabel(tab.selectedMode, availableModes.find((m) => m.id === tab.selectedMode)?.label);
	$: advancedFields = $formAudienceStore === 'advanced';

	function tabUnsaved(t: Tab, live: Record<string, boolean>): boolean {
		return isToolTab(t) ? (t.dirty ?? 0) > 0 : tabShowsUnsavedChanges(t, live);
	}
</script>

<StudioSheet maxHeight="88%" ariaLabel="Preset and session" on:close={onClose}>
	<svelte:fragment slot="header">
		<div class="flex items-baseline gap-2">
			<span class="text-sm font-semibold text-fg">Preset &amp; session</span>
			<span class="font-mono text-sm uppercase tracking-wide text-fg-subtle">{tab.name}</span>
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

		<SessionControl {session} {actions} {fieldLabels} layout="sheet" />

		<div class="flex gap-1.5" data-testid="preset-session-sheet-tools">
			<FormulasButton
				{tab}
				layout="sheet"
				presetName={presetInfo?.name ?? ''}
				presetVersion={presetVersion ?? ''}
				modeLabel={currentModeLabel}
				onApplied={onFormulaApplied}
			/>
			<button
				type="button"
				class="flex h-[42px] flex-1 items-center justify-center gap-1.5 rounded border px-3 text-sm transition-colors {advancedFields
					? 'border-signal/50 bg-signal/10 text-signal'
					: 'border-line bg-surface-2 text-fg'}"
				aria-label="Field visibility: {advancedFields ? 'Advanced' : 'Simple'}"
				aria-pressed={advancedFields}
				on:click={() => formAudienceStore.toggle()}
			>
				<Icon name="list" className="h-4 w-4 flex-shrink-0" />
				<span>Fields: {advancedFields ? 'Advanced' : 'Simple'}</span>
			</button>
		</div>

		<div>
			<p class="mb-1.5 font-mono text-sm uppercase tracking-wide text-fg-subtle">Tabs</p>
			<div class="flex flex-wrap gap-1.5">
				{#each tabs as t (t.id)}
					<button
						type="button"
						class="inline-flex items-center gap-1.5 rounded px-3 py-1.5 text-sm font-medium {t.id === activeTabId
							? 'bg-accent text-accent-contrast'
							: 'bg-surface-2 text-fg-muted'}"
						on:click={() => tabsStore.setActiveTab(t.id)}
					>
						{#if tabUnsaved(t, $tabSessionDirty)}
							<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-warning-solid" aria-hidden="true"></span>
							{t.name}<span class="sr-only">, unsaved changes</span>
						{:else}
							{t.name}
						{/if}
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
