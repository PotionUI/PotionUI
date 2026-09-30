<script lang="ts">
	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: current = typeof value === 'string' ? value : (value?.modelPath ?? '');
</script>

<select
	aria-label="Model"
	value={current}
	on:change={(event) => name && onChange(name, { modelPath: event.currentTarget.value, tagFilters: [] })}
>
	<option value="">none</option>
	<option value="model:a">A</option>
	<option value="model:b">B</option>
	<option value="model:c">C</option>
	<option value="model:d">D</option>
</select>
