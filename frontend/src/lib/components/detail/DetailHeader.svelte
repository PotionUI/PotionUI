<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge } from '$lib/components/ui';
	import { splitDetailHeaderChips, type DetailHeaderChip } from './detailHeaderChips';

	let {
		title,
		icon,
		backLabel,
		onBack,
		chips,
		chipItems,
		subtitle,
		enabledSwitch,
		titleEditor,
		actions
	}: {
		title: string;
		icon?: string;
		backLabel?: string;
		onBack?: () => void;
		chips?: Snippet;
		chipItems?: DetailHeaderChip[];
		subtitle?: Snippet;
		enabledSwitch?: Snippet;
		titleEditor?: Snippet;
		actions?: Snippet;
	} = $props();

	const showBack = $derived(Boolean(backLabel && onBack));
	const chipSplit = $derived(chipItems ? splitDetailHeaderChips(chipItems) : null);
</script>

<div class="flex min-h-header items-center max-sm:flex-wrap gap-x-3 gap-y-2 px-4 sm:px-6 py-2.5 border-b border-line bg-surface-1 flex-shrink-0">
	{#if showBack}
		<Tooltip text="Back to {backLabel}">
			<button
				type="button"
				class="flex items-center gap-1 text-xs font-medium text-fg-muted hover:text-fg"
				aria-label="Back to {backLabel}"
				data-detail-back
				onclick={onBack}
			>
				<Icon name="chevron-left" className="h-3.5 w-3.5" />
				{backLabel}
			</button>
		</Tooltip>
		<span class="h-5 w-px flex-shrink-0 bg-line-strong" aria-hidden="true"></span>
	{/if}
	{#if icon}
		<span
			class="flex-shrink-0 flex items-center justify-center w-[30px] h-[30px] rounded bg-surface-2 border border-line-strong text-fg-subtle"
		>
			<Icon name={icon} className="w-4 h-4" />
		</span>
	{/if}
	<div class="min-w-0 flex-1">
		<div class="flex items-center gap-2 min-w-0 flex-wrap">
			{#if titleEditor}
				{@render titleEditor()}
			{:else}
				<Tooltip text={title} wrapperClass="flex min-w-0 max-w-full">
					<h2 class="min-w-0 max-w-full text-base font-semibold text-fg truncate" data-detail-title>{title}</h2>
				</Tooltip>
			{/if}
			{@render chips?.()}
			{#if chipSplit}
				{#each chipSplit.visible as chip (chip.key)}
					<Badge size="sm" variant={chip.tone ?? 'neutral'}>{chip.label}</Badge>
				{/each}
				{#if chipSplit.overflow.length}
					<Tooltip text={chipSplit.overflow.map((chip) => chip.label).join(', ')}>
						<Badge size="sm" variant="neutral">+{chipSplit.overflow.length}</Badge>
					</Tooltip>
				{/if}
			{/if}
		</div>
		{#if subtitle}
			<div class="mt-0.5 min-w-0 font-mono text-xs text-fg-subtle flex flex-wrap items-center gap-x-1.5" data-detail-subtitle>
				{@render subtitle()}
			</div>
		{/if}
	</div>
	{#if enabledSwitch || actions}
		<div class="ml-auto flex items-center gap-2 flex-shrink-0 flex-wrap justify-end max-sm:basis-full" data-detail-actions>
			{@render enabledSwitch?.()}
			{@render actions?.()}
		</div>
	{/if}
</div>
