<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';

	export let icon: string;
	export let label: string;
	export let kbd: string | undefined = undefined;
	export let pressed: boolean | undefined = undefined;
	export let disabled: boolean = false;
	export let caption: string = '';
	export let position: 'top' | 'bottom' | 'left' | 'right' = 'bottom';
	export let compact: boolean = false;
	export let onClick: () => void;

	$: sizeClass = compact
		? 'w-8 h-8'
		: caption
			? 'min-w-14 h-14 md:min-w-0 md:w-9 md:h-9'
			: 'w-10 h-10 md:w-9 md:h-9';
	$: stateClass = pressed
		? 'border-signal/60 bg-signal/10 text-signal'
		: 'border-transparent text-fg-muted hover:bg-surface-3/50 hover:text-fg';
</script>

<Tooltip text={label} {kbd} kbdSize="md" {position} wrapperClass="inline-flex shrink-0">
	<button
		type="button"
		aria-label={label}
		aria-pressed={pressed}
		{disabled}
		class="inline-flex flex-col items-center justify-center gap-0.5 rounded border transition-colors touch-manipulation disabled:opacity-40 disabled:pointer-events-none {sizeClass} {stateClass}"
		on:click={onClick}
	>
		<svg class="w-5 h-5 md:w-[18px] md:h-[18px]" fill="none" viewBox="0 0 24 24" stroke="currentColor">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={icon} />
		</svg>
		{#if caption}
			<span class="text-xs leading-none md:hidden">{caption}</span>
		{/if}
	</button>
</Tooltip>
