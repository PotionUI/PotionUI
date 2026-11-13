<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { detailsPrefs, loadDetailsPrefs, setDetailsPref } from './detailsPrefs';

	let {
		id,
		title,
		count,
		collapsible = true,
		defaultOpen = true,
		headerRight,
		children
	}: {
		id: string;
		title: string;
		count?: number;
		collapsible?: boolean;
		defaultOpen?: boolean;
		headerRight?: Snippet;
		children?: Snippet;
	} = $props();

	loadDetailsPrefs();

	const uid = $props.id();
	const bodyId = `${uid}-body`;

	let open = $derived(collapsible ? ($detailsPrefs[`section:${id}`] ?? defaultOpen) : true);
</script>

<section class="mx-4 mt-4 border-t border-line-strong pt-4" data-details-section={id}>
	{#if collapsible}
		<button
			type="button"
			class="flex min-h-6 w-full items-center justify-between gap-2 pb-2 text-left"
			aria-expanded={open}
			aria-controls={bodyId}
			onclick={() => setDetailsPref(`section:${id}`, !open)}
		>
			<h3 class="text-sm font-semibold uppercase tracking-wide text-fg-muted">
				{title}{#if count !== undefined}<span class="ml-1.5 font-normal tabular-nums">{count}</span>{/if}
			</h3>
			<Icon
				name="chevron-down"
				className="h-3.5 w-3.5 shrink-0 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}"
			/>
		</button>
	{:else}
		<div class="flex min-h-6 items-center justify-between gap-2 pb-2">
			<h3 class="text-sm font-semibold uppercase tracking-wide text-fg-muted">
				{title}{#if count !== undefined}<span class="ml-1.5 font-normal tabular-nums">{count}</span>{/if}
			</h3>
			{@render headerRight?.()}
		</div>
	{/if}
	{#if open}
		<div id={bodyId} class="-mx-3">
			{@render children?.()}
		</div>
	{/if}
</section>
