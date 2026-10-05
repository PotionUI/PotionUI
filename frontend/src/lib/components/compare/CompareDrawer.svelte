<script lang="ts">
	import { onMount } from 'svelte';
	import DrawerShell from '$lib/components/drawer/DrawerShell.svelte';
	import DrawerHeader from '$lib/components/drawer/DrawerHeader.svelte';
	import StudioSheet from '../../../routes/generate/components/studio/StudioSheet.svelte';
	import { Button, Switch } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import AxisCard from './AxisCard.svelte';
	import { tabsStore } from '$lib/stores/tabs';
	import { isMobile } from '$lib/stores/viewport';
	import { limits, limitsMeta, refreshLimits } from '$lib/plans/store';
	import { DEFAULT_CONTACT_LINE } from '$lib/plans/refusal';
	import {
		clearAxes,
		closeCompareDrawer,
		effectiveLockSeed,
		getCandidates,
		getCompare,
		getCompareSummary,
		isCompareDrawerOpen,
		seedAxisActive,
		setCompare,
		swapAxes,
		turnOffCompare
	} from '$lib/generation/compare/compareStore.svelte';
	import { effectiveAxes } from '$lib/generation/compare/axisValues';

	let {
		tabId,
		canGenerate,
		generateDisabledReason = undefined,
		isGenerating,
		onGenerate
	}: {
		tabId: string;
		canGenerate: boolean;
		generateDisabledReason?: string;
		isGenerating: boolean;
		onGenerate: () => void;
	} = $props();

	let open = $derived(isCompareDrawerOpen(tabId));
	let tab = $derived($tabsStore.tabs.find((t) => t.id === tabId));
	let formData = $derived((tab?.formData ?? {}) as Record<string, unknown>);
	let config = $derived(getCompare(tabId));
	let candidates = $derived(getCandidates(tabId, formData));
	let summary = $derived.by(() => {
		void $tabsStore;
		return getCompareSummary(tabId, $limits, $limitsMeta?.contactLine ?? DEFAULT_CONTACT_LINE);
	});
	let axes = $derived(effectiveAxes(config));
	let lockOn = $derived(effectiveLockSeed(config));
	let seedIsAxis = $derived(seedAxisActive(config));
	let seedValue = $derived(formData.seed);
	let quantity = $derived(Number(formData.quantity ?? 1));
	let contactLine = $derived($limitsMeta?.contactLine ?? DEFAULT_CONTACT_LINE);
	let generateBlockedReason = $derived(
		summary.disabledReason ?? (!canGenerate ? (generateDisabledReason ?? 'Cannot generate yet') : null)
	);
	let generateLabel = $derived(`Generate ${summary.count}`);

	onMount(() => {
		void refreshLimits();
	});

	function toggleLock(next: boolean) {
		setCompare(tabId, { lockSeed: next });
	}
</script>

{#snippet sectionLabel(text: string)}
	<div class="mb-2 mt-4 flex items-center gap-3 first:mt-0">
		<span class="font-mono text-[10px] font-semibold uppercase tracking-wider text-fg">{text}</span>
		<span class="h-px flex-1 bg-line"></span>
	</div>
{/snippet}

{#snippet body()}
	{@render sectionLabel('X axis · columns')}
	<AxisCard {tabId} slot="x" {candidates} axis={config.x} otherField={config.y?.field ?? null} />

	<div class="my-3 flex items-center gap-3">
		<span class="h-px flex-1 bg-line"></span>
		<Button variant="ghost" size="sm" icon="refresh" onclick={() => swapAxes(tabId)}>Swap X and Y</Button>
		<span class="h-px flex-1 bg-line"></span>
	</div>

	{@render sectionLabel('Y axis · rows')}
	<AxisCard {tabId} slot="y" {candidates} axis={config.y} otherField={config.x?.field ?? null} />

	{@render sectionLabel('Options')}
	<div class="rounded-lg border border-line bg-surface-2 p-3">
		<div class="flex items-start justify-between gap-3">
			<div class="min-w-0">
				<div class="text-sm font-medium text-fg">Lock seed</div>
				<p class="mt-0.5 text-xs text-fg-muted">
					{#if seedIsAxis}
						Lock seed is off while seed is an axis.
					{:else if lockOn}
						Every cell uses seed <span class="font-mono tabular-nums">{seedValue ?? ''}</span>, so only the axes differ.
					{:else}
						Each cell gets its own random seed, saved with the cell.
					{/if}
				</p>
			</div>
			<Switch label="Lock seed" checked={lockOn} disabled={seedIsAxis} onchange={toggleLock} size="sm" />
		</div>
	</div>
	<div class="mt-3 flex items-start gap-2 rounded-lg border border-line bg-surface-2 p-3 text-xs text-fg-muted">
		<Icon name="information-circle" className="mt-0.5 h-4 w-4 flex-shrink-0 text-fg-subtle" />
		<p>
			Everything else comes from the form. Prompt, steps and the rest are the same in all cells.
			{#if quantity > 1}
				Quantity counts as 1 per cell.
			{/if}
		</p>
	</div>
{/snippet}

{#snippet summaryBlock()}
	<div class="min-w-0">
		{#if summary.count === 0}
			<div class="font-mono text-sm text-fg-subtle">No cells yet</div>
		{:else if axes.rows}
			<div class="font-mono text-sm tabular-nums text-fg" data-testid="compare-summary">
				<span class="text-signal">{axes.cols?.values.length} × {axes.rows.values.length}</span>
				= {summary.count} generations
			</div>
		{:else}
			<div class="font-mono text-sm tabular-nums text-fg" data-testid="compare-summary">
				<span class="text-signal">{summary.count}</span> generations
			</div>
		{/if}
		{#if summary.shortfall}
			<div class="mt-1 flex items-start gap-2 text-xs text-warning" data-testid="compare-shortfall">
				<Icon name="warning" className="mt-0.5 h-4 w-4 flex-shrink-0" />
				<div>
					<div class="font-semibold">{summary.shortfall.needed} needed, {summary.shortfall.remaining} left today</div>
					<div class="text-fg-muted">{contactLine}</div>
				</div>
			</div>
		{:else if summary.overCap}
			<div class="mt-1 text-xs text-danger" data-testid="compare-overcap">{summary.disabledReason}</div>
		{:else if summary.count > 0}
			<div class="mt-0.5 text-xs text-fg-muted" data-testid="compare-estimate">
				{summary.estimate}{summary.estimate === '-' ? '' : ' on this backend'}
			</div>
		{/if}
	</div>
{/snippet}

{#if open}
	{#if $isMobile}
		<StudioSheet ariaLabel="Compare" on:close={closeCompareDrawer}>
			<svelte:fragment slot="header">
				<div class="flex items-baseline gap-2 pb-2">
					<h2 class="text-md font-semibold text-fg">Compare</h2>
					<span class="font-mono text-xs tabular-nums text-fg-subtle">{summary.count}</span>
				</div>
			</svelte:fragment>
			<div class="pb-4">
				{@render body()}
			</div>
			<svelte:fragment slot="footer">
				<div class="flex items-start justify-between gap-3 pb-3">
					{@render summaryBlock()}
					<div class="flex flex-shrink-0 items-center">
						<Button variant="ghost" size="sm" onclick={() => clearAxes(tabId)}>Clear</Button>
						<Button variant="ghost" size="sm" onclick={() => turnOffCompare(tabId)}>Turn off</Button>
					</div>
				</div>
				<Button
					variant="primary"
					size="lg"
					class="w-full"
					disabled={generateBlockedReason !== null || isGenerating}
					title={generateBlockedReason ?? undefined}
					onclick={onGenerate}
				>
					{generateLabel}
				</Button>
			</svelte:fragment>
		</StudioSheet>
	{:else}
		<DrawerShell label="Compare" keepOpenKey="compare-drawer" onClose={closeCompareDrawer}>
			{#snippet header({ keepOpen, setKeepOpen })}
				<DrawerHeader
					title="Compare"
					count={summary.count}
					closeLabel="Close compare"
					{keepOpen}
					onKeepOpenChange={setKeepOpen}
					onClose={closeCompareDrawer}
				/>
			{/snippet}
			{#snippet children()}
				<div class="drawer-scroll min-h-0 flex-1 overflow-y-auto px-4 py-4">
					{@render body()}
				</div>
				<div class="flex flex-shrink-0 items-start justify-between gap-3 border-t border-line px-4 py-3">
					{@render summaryBlock()}
					<div class="flex flex-shrink-0 items-center">
						<Button variant="ghost" size="sm" onclick={() => clearAxes(tabId)}>Clear</Button>
						<Button variant="ghost" size="sm" onclick={() => turnOffCompare(tabId)}>Turn off</Button>
					</div>
				</div>
			{/snippet}
		</DrawerShell>
	{/if}
{/if}
