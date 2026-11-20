<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import Icon from '$lib/components/Icon.svelte';
	import HeaderDropdown from './HeaderDropdown.svelte';

	let {
		options,
		value,
		dir,
		onchange,
		ondir,
		dirDisabled = false,
		label = 'Sort'
	}: {
		options: Array<{ value: string; label: string }>;
		value: string;
		dir: 'asc' | 'desc';
		onchange: (value: string) => void;
		ondir: (dir: 'asc' | 'desc') => void;
		dirDisabled?: boolean;
		label?: string;
	} = $props();

	const dirTooltip = $derived(
		dirDisabled
			? 'Manual order has no direction'
			: dir === 'desc'
				? 'Descending. Click for ascending'
				: 'Ascending. Click for descending'
	);
</script>

<div class="inline-flex flex-none items-center" data-header-sort>
	<HeaderDropdown {options} {value} {label} joined compact onchange={onchange} />
	<button
		type="button"
		class="inline-flex h-8 w-8 items-center justify-center rounded-r border border-l-0 border-line-strong bg-surface-2/50 transition-colors {dirDisabled
			? 'cursor-not-allowed text-fg-disabled'
			: 'text-fg hover:bg-surface-3/60'}"
		aria-label={dir === 'desc' ? 'Sort descending' : 'Sort ascending'}
		disabled={dirDisabled}
		use:tooltip={dirTooltip}
		onclick={() => ondir(dir === 'desc' ? 'asc' : 'desc')}
	>
		<Icon name={dir === 'desc' ? 'arrow-down' : 'arrow-up'} className="h-3.5 w-3.5" />
	</button>
</div>
