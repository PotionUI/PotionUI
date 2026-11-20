<script lang="ts">
	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
</script>

<button type="button" data-probe-bump on:click={() => name && onChange(name, (Number(value) || 0) + 1)}>
	{config.title || name}: {value}
</button>
