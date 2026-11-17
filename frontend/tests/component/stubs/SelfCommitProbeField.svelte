<script lang="ts">
	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	export let onCommit: ((fieldName: string) => void) | undefined = undefined;
</script>

<button
	type="button"
	data-probe-bump
	on:click={() => {
		if (!name) return;
		onChange(name, (Number(value) || 0) + 1);
		onCommit?.(name);
	}}
>
	{config.title || name}: {value}
</button>
