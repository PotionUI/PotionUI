<script lang="ts">
	import Button from '$lib/components/ui/Button.svelte';

	let {
		label,
		icon,
		onclick,
		primary = false,
		compact = false,
		loading = false,
		disabled = false
	}: {
		label: string;
		icon: string;
		onclick: () => void;
		primary?: boolean;
		compact?: boolean;
		loading?: boolean;
		disabled?: boolean;
	} = $props();
</script>

<Button
	variant={primary ? 'primary' : 'secondary'}
	size="sm"
	{icon}
	{loading}
	{disabled}
	ariaLabel={label}
	class={compact ? 'h-8 !px-2' : 'h-8'}
	{onclick}
>
	{#if !compact}{label}{/if}
</Button>
