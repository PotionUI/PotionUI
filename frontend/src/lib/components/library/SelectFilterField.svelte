<script lang="ts" generics="T extends string">
	let {
		label,
		value,
		options,
		onChange
	}: {
		label: string;
		value: T;
		options: readonly { value: T; label: string }[];
		onChange: (value: T) => void;
	} = $props();
</script>

<label class="flex flex-col gap-1.5">
	<span class="text-xs font-medium text-fg-muted">{label}</span>
	<select
		class="input w-full text-xs"
		{value}
		onchange={(event) => onChange((event.currentTarget as HTMLSelectElement).value as T)}
	>
		{#each options as option (option.value)}
			<option value={option.value}>{option.label}</option>
		{/each}
	</select>
</label>
