<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';
	import { detailsPrefs, loadDetailsPrefs, setDetailsPref } from './detailsPrefs';

	let {
		id,
		title,
		icon,
		count,
		note,
		collapsible = true,
		strip = false,
		defaultOpen = true,
		headerRight,
		children
	}: {
		id: string;
		title: string;
		icon?: string;
		count?: number;
		note?: string;
		collapsible?: boolean;
		strip?: boolean;
		defaultOpen?: boolean;
		headerRight?: Snippet;
		children?: Snippet;
	} = $props();

	loadDetailsPrefs();

	const uid = $props.id();
	const bodyId = `${uid}-body`;

	let open = $derived(collapsible ? ($detailsPrefs[`section:${id}`] ?? defaultOpen) : true);
</script>

{#snippet headline()}
	<span class="flex min-w-0 items-center gap-2">
		{#if icon}
			<span aria-hidden="true" class="flex-shrink-0 text-fg-muted"><Icon name={icon} className="h-4 w-4" /></span>
		{/if}
		<h3 class="text-sm font-semibold uppercase tracking-wide text-fg-muted">
			{title}{#if count !== undefined}<span class="sr-only">, {count} {count === 1 ? 'item' : 'items'}</span>{/if}
		</h3>
		{#if count !== undefined}
			<span aria-hidden="true" class="flex flex-shrink-0 items-center" data-section-count>
				<Badge variant="neutral" size="sm" class="font-mono tabular-nums">{count}</Badge>
			</span>
		{/if}
		{#if note}
			<span class="truncate text-sm text-fg-subtle" data-section-note>{note}</span>
		{/if}
	</span>
{/snippet}

<section class={strip ? '' : 'mt-4 border-t border-line pt-4 first:mt-0 first:border-t-0 first:pt-0'} data-details-section={id} data-details-strip={strip || undefined}>
	{#if collapsible}
		<button
			type="button"
			class="flex w-full items-center justify-between gap-2 text-left {strip ? 'h-11 border-b border-line px-4 max-sm:h-12' : 'min-h-6 px-4 pb-2'}"
			aria-expanded={open}
			aria-controls={bodyId}
			onclick={() => setDetailsPref(`section:${id}`, !open)}
		>
			{@render headline()}
			<Icon
				name="chevron-down"
				className="h-3.5 w-3.5 shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}"
			/>
		</button>
	{:else}
		<div class="flex items-center justify-between gap-2 {strip ? 'h-11 border-b border-line px-4 max-sm:h-12' : 'min-h-6 px-4 pb-2'}">
			{@render headline()}
			{@render headerRight?.()}
		</div>
	{/if}
	{#if open}
		<div id={bodyId} class={strip ? 'px-1 pt-3' : 'px-1'}>
			{@render children?.()}
		</div>
	{/if}
</section>
