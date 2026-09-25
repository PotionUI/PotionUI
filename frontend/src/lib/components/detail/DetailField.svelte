<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		label,
		id,
		help,
		error,
		wide = false,
		children
	}: {
		label: string;
		id?: string;
		help?: string;
		error?: string | null;
		wide?: boolean;
		children: Snippet;
	} = $props();
</script>

<div class="{wide ? '' : 'max-w-[40rem]'} min-w-0" data-detail-field>
	<label for={id} class="mb-1 block text-sm font-medium text-fg-muted">{label}</label>
	{@render children()}
	{#if error}
		<p class="mt-1 text-sm text-danger">{error}</p>
	{:else if help}
		<p class="mt-1 text-sm text-fg-subtle">{help}</p>
	{/if}
</div>
