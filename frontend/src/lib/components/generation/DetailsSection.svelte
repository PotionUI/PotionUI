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
		collapsible = true,
		defaultOpen = true,
		headerRight,
		children
	}: {
		id: string;
		title: string;
		icon?: string;
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

<section class="border-t border-line pb-5 pt-5 first:border-t-0 first:pt-0" data-details-section={id}>
	{#if collapsible}
		<button
			type="button"
			class="flex w-full items-center justify-between gap-2 px-4 py-1.5 text-left transition-colors hover:bg-surface-2"
			aria-expanded={open}
			aria-controls={bodyId}
			onclick={() => setDetailsPref(`section:${id}`, !open)}
		>
			<span class="flex min-w-0 items-center gap-2">
				{#if icon}<Icon name={icon} className="h-4 w-4 shrink-0 text-fg-muted" />{/if}
				<h3 class="text-md font-semibold text-fg">{title}</h3>
			</span>
			<span class="flex shrink-0 items-center gap-2">
				{#if count !== undefined}<Badge variant="neutral" size="sm" class="font-mono">{count}</Badge>{/if}
				<Icon
					name="chevron-down"
					className="h-3.5 w-3.5 text-fg-subtle transition-transform {open ? 'rotate-180' : ''}"
				/>
			</span>
		</button>
	{:else}
		<div class="flex items-center justify-between gap-2 px-4 py-1.5">
			<span class="flex min-w-0 items-center gap-2">
				{#if icon}<Icon name={icon} className="h-4 w-4 shrink-0 text-fg-muted" />{/if}
				<h3 class="text-md font-semibold text-fg">{title}</h3>
			</span>
			<span class="flex shrink-0 items-center gap-2">
				{#if count !== undefined}<Badge variant="neutral" size="sm" class="font-mono">{count}</Badge>{/if}
				{@render headerRight?.()}
			</span>
		</div>
	{/if}
	{#if open}
		<div id={bodyId} class="px-1 pt-1">
			{@render children?.()}
		</div>
	{/if}
</section>
