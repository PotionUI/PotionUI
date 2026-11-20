<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import Icon from '$lib/components/Icon.svelte';

	let {
		label,
		tooltip: tooltipText,
		icon,
		text,
		pressed = false,
		onclick
	}: {
		label: string;
		tooltip?: string;
		icon?: string;
		text?: string;
		pressed?: boolean;
		onclick: () => void;
	} = $props();
</script>

<button
	type="button"
	class="inline-flex h-6 min-w-6 flex-none items-center justify-center rounded-sm px-1 transition-colors duration-100 {pressed
		? 'bg-signal/10 text-signal'
		: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'} {text ? 'font-mono text-sm font-semibold' : ''}"
	aria-label={label}
	aria-pressed={pressed}
	use:tooltip={tooltipText ?? label}
	{onclick}
>
	{#if icon}
		<Icon name={icon} className="h-3.5 w-3.5" />
	{:else}
		{text}
	{/if}
</button>
