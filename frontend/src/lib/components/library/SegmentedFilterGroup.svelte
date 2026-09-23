<script lang="ts" generics="T extends string">
	let {
		label,
		options,
		value,
		onChange
	}: {
		label: string;
		options: readonly { value: T; label: string }[];
		value: T;
		onChange: (value: T) => void;
	} = $props();
</script>

<div class="flex flex-col gap-1.5">
	<span class="text-xs font-medium text-fg-muted">{label}</span>
	<div class="inline-flex gap-0.5 rounded border border-line-strong bg-surface-2 p-0.5">
		{#each options as option (option.value)}
			<button
				type="button"
				class="flex-1 rounded px-2 py-1 text-xs font-medium transition-colors {value === option.value
					? 'bg-surface-1 text-fg shadow-raised'
					: 'text-fg-muted hover:text-fg'}"
				onclick={() => onChange(option.value)}
			>
				{option.label}
			</button>
		{/each}
	</div>
</div>
