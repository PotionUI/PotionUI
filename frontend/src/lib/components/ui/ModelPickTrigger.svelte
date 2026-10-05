<script lang="ts">
	import Icon from '../Icon.svelte';
	import IconButton from './IconButton.svelte';

	let {
		id,
		value = null,
		placeholder = 'All models',
		allowClear = true,
		disabled = false,
		clearLabel = 'Clear model',
		onopen,
		onclear,
		class: className = ''
	}: {
		id?: string;
		value?: string | null;
		placeholder?: string;
		allowClear?: boolean;
		disabled?: boolean;
		clearLabel?: string;
		onopen: () => void;
		onclear: () => void;
		class?: string;
	} = $props();

	let picked = $derived(!!value);
	let showClear = $derived(picked && allowClear);

	function clear(event: MouseEvent) {
		event.stopPropagation();
		onclear();
	}
</script>

<div class="input flex w-full items-center gap-1 {className}">
	<button
		{id}
		type="button"
		class="flex min-w-0 flex-1 items-center gap-2 rounded text-left {picked ? 'text-fg' : 'text-fg-subtle'}"
		{disabled}
		onclick={onopen}
	>
		<span class="min-w-0 flex-1 truncate">{picked ? value : placeholder}</span>
		<Icon name="chevron-down" className="h-3 w-3 flex-shrink-0 text-fg-subtle" />
	</button>
	{#if showClear}
		<IconButton icon="close" label={clearLabel} size="xs" {disabled} onclick={clear} />
	{/if}
</div>
