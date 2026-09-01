<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { toggleOpen, sectionBoxClass, sectionBodyClass } from './detailSection';

	let {
		label,
		headerExtra,
		footer,
		collapsible = false,
		open = $bindable(true),
		padded = true,
		children
	}: {
		label: string;
		headerExtra?: Snippet;
		footer?: Snippet;
		collapsible?: boolean;
		open?: boolean;
		padded?: boolean;
		children?: Snippet;
	} = $props();

	const showBody = $derived(!collapsible || open);

	function handleToggle() {
		open = toggleOpen(open);
	}
</script>

<section class={sectionBoxClass(padded)}>
	<div
		class="px-4 sm:px-5 py-3 flex items-center justify-between gap-2 {showBody ? 'border-b border-line' : ''}"
	>
		<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted truncate">{label}</h3>
		<div class="flex items-center gap-2 flex-shrink-0">
			{@render headerExtra?.()}
			{#if collapsible}
				<button
					type="button"
					class="inline-flex items-center justify-center w-6 h-6 rounded text-fg-muted hover:text-fg hover:bg-surface-2"
					onclick={handleToggle}
					aria-expanded={open}
					aria-label={open ? 'Collapse' : 'Expand'}
				>
					<Icon
						name="chevron-down"
						className="w-3.5 h-3.5 transition-transform {open ? '' : '-rotate-90'}"
					/>
				</button>
			{/if}
		</div>
	</div>
	{#if showBody}
		<div class={sectionBodyClass(padded)}>
			{@render children?.()}
		</div>
	{/if}
	{#if footer}
		<div class="px-4 sm:px-5 py-3 border-t border-line flex items-center justify-end gap-2 flex-wrap">
			{@render footer()}
		</div>
	{/if}
</section>
