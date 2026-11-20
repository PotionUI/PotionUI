<script lang="ts">
	import IconButton from '$lib/components/ui/IconButton.svelte';
	import HeaderSegmented from './HeaderSegmented.svelte';

	let {
		size,
		onsize,
		grouped,
		ongroup,
		onrefresh
	}: {
		size?: string;
		onsize?: (size: string) => void;
		grouped?: boolean;
		ongroup?: () => void;
		onrefresh?: () => void;
	} = $props();

	const sizes = [
		{ value: 'small', label: 'S', mono: true, tooltip: 'Small tiles' },
		{ value: 'medium', label: 'M', mono: true, tooltip: 'Medium tiles' },
		{ value: 'large', label: 'L', mono: true, tooltip: 'Large tiles' }
	];
</script>

{#if onsize && size !== undefined}
	<HeaderSegmented label="Tile size" items={sizes} value={size} onchange={onsize} />
{/if}
{#if ongroup && grouped !== undefined}
	<IconButton
		icon="calendar"
		label="Group by date"
		tooltip={grouped
			? 'Grouped by date. Click for one continuous wall'
			: 'One continuous wall. Click to group by date'}
		active={grouped}
		ariaPressed={grouped}
		onclick={ongroup}
	/>
{/if}
{#if onrefresh}
	<IconButton icon="refresh" label="Refresh" onclick={onrefresh} />
{/if}
