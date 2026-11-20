<script lang="ts">
	import tooltip from '$lib/actions/tooltip';
	import Icon from '$lib/components/Icon.svelte';

	type Item = {
		value: string;
		label?: string;
		icon?: string;
		mono?: boolean;
		count?: number;
		tooltip?: string;
		ariaLabel?: string;
	};

	let {
		items,
		value,
		onchange,
		label
	}: {
		items: Item[];
		value: string;
		onchange: (value: string) => void;
		label: string;
	} = $props();
</script>

<div
	class="inline-flex h-8 flex-none items-center gap-0.5 rounded bg-surface-2/50 p-0.5"
	role="radiogroup"
	aria-label={label}
>
	{#each items as item (item.value)}
		<button
			type="button"
			role="radio"
			aria-checked={value === item.value}
			aria-label={item.ariaLabel ?? item.tooltip ?? item.label}
			use:tooltip={item.tooltip ?? null}
			class="inline-flex h-7 min-w-7 items-center justify-center gap-1.5 whitespace-nowrap rounded-sm px-2 text-sm transition-colors duration-100 {item.mono
				? 'font-mono'
				: ''} {value === item.value
				? 'bg-signal/10 text-signal'
				: 'text-fg-muted hover:bg-surface-3/50 hover:text-fg'}"
			onclick={() => onchange(item.value)}
		>
			{#if item.icon}
				<Icon name={item.icon} className="h-3.5 w-3.5" />
			{/if}
			{#if item.label}{item.label}{/if}
			{#if item.count !== undefined}
				<span class="font-mono tabular-nums">{item.count}</span>
			{/if}
		</button>
	{/each}
</div>
