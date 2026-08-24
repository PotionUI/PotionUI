<script lang="ts">
	import FieldShell from './FieldShell.svelte';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: disabled = config.disabled ?? false;
	$: tooltip = config.tooltip || config.configuration?.tooltip;
	$: multiline = (config.input_type ?? config.configuration?.input_type) === 'textarea';
	$: rows = Number(config.rows ?? config.configuration?.rows) || 4;
	$: placeholder = config.placeholder ?? config.configuration?.placeholder ?? '';

	function handleInput(event: Event) {
		const target = event.target as HTMLInputElement | HTMLTextAreaElement;
		if (name) {
			onChange(name, target.value);
		}
	}
</script>

<FieldShell {name} {label} {description} {tooltip} labelFor={name || undefined} let:descriptionId>
	{#if multiline}
		<textarea
			id={name || undefined}
			{value}
			on:input={handleInput}
			{disabled}
			{rows}
			{placeholder}
			class="input textarea"
			aria-describedby={descriptionId}
		></textarea>
	{:else}
		<input
			type="text"
			id={name || undefined}
			{value}
			on:input={handleInput}
			{disabled}
			{placeholder}
			class="input"
			aria-describedby={descriptionId}
		/>
	{/if}
</FieldShell>

<style>
	.textarea {
		resize: vertical;
		min-height: 4.5rem;
		height: auto;
		line-height: 1.5;
	}
</style>
