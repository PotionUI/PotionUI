<script lang="ts">
	let {
		label,
		value,
		hint,
		hero = false,
		mono = false,
		warning = false,
		class: className = ''
	}: {
		label: string;
		value: string;
		hint?: string;
		hero?: boolean;
		mono?: boolean;
		warning?: boolean;
		class?: string;
	} = $props();
</script>

<div class="bg-surface-1 border border-line rounded-lg shadow-raised p-4 {className}">
	<div class="text-xs font-medium text-fg-subtle uppercase tracking-wide">{label}</div>
	<div
		class="{hero ? 'mt-2 text-4xl' : 'mt-1.5 text-2xl'} font-semibold {warning ? 'text-warning' : 'text-fg'} {mono
			? 'font-mono tabular-nums'
			: ''}"
	>
		{value}
	</div>
	{#if hint}
		<div class="mt-1 text-xs text-fg-muted">{hint}</div>
	{/if}
</div>
