<script lang="ts">
	import type { Snippet } from 'svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import Icon from '$lib/components/Icon.svelte';

	let {
		icon,
		name,
		dense = false,
		muted = false,
		onOpen,
		ariaLabel,
		description = null,
		nameSuffix,
		topRight,
		footer
	}: {
		icon: string;
		name: string;
		dense?: boolean;
		muted?: boolean;
		onOpen: () => void;
		ariaLabel?: string;
		description?: string | null;
		nameSuffix?: Snippet;
		topRight?: Snippet;
		footer: Snippet;
	} = $props();

	function handleKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter' || event.key === ' ') {
			event.preventDefault();
			onOpen();
		}
	}
</script>

<div
	class="group flex min-w-0 flex-col rounded-lg border border-line-strong bg-surface-1 shadow-raised transition-colors hover:border-line-hover hover:shadow-floating {dense
		? 'gap-1.5 p-2.5'
		: 'gap-2 p-3'}"
	role="button"
	tabindex="0"
	data-library-card
	aria-label={ariaLabel ?? name}
	onclick={onOpen}
	onkeydown={handleKeydown}
>
	<div class="flex min-w-0 items-center gap-2.5">
		<span
			class="flex flex-shrink-0 items-center justify-center border border-line bg-surface-2 {muted
				? 'text-fg-disabled'
				: 'text-fg-muted'} {dense ? 'h-6 w-6 rounded' : 'h-[30px] w-[30px] rounded-md'}"
		>
			<Icon name={icon} className={dense ? 'h-3.5 w-3.5' : 'h-4 w-4'} />
		</span>
		<Tooltip text={name} wrapperClass="flex min-w-0 flex-1 items-baseline gap-1.5">
			<span class="min-w-0 flex-1 truncate text-sm font-semibold {muted ? 'text-fg-muted' : 'text-fg'}">{name}</span>
			{@render nameSuffix?.()}
		</Tooltip>
		{@render topRight?.()}
	</div>

	{#if !dense && description}
		<p class="line-clamp-2 text-xs leading-relaxed {muted ? 'text-fg-subtle' : 'text-fg-muted'}">{description}</p>
	{/if}

	<div class="mt-auto flex flex-wrap items-center gap-2 border-t border-line {dense ? 'pt-1.5' : 'pt-2'}">
		{@render footer()}
	</div>
</div>
