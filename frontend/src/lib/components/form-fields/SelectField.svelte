<script lang="ts">
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import FieldShell from './FieldShell.svelte';
	import Icon from '../Icon.svelte';
	import { copyText } from '$lib/utils/clipboard';
	import { toasts } from '$lib/stores/toast';
	import { formatSelectOptions } from './selectOptions';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;

	$: label = config.title || name || '';
	$: description = config.description || '';
	let options: any[] = [];
	$: options = config.options || [];
	$: disabled = config.disabled ?? false;
	$: tooltip = config.tooltip || config.configuration?.tooltip;
	// SelectField's own `config` (the field's `configuration` block, or the raw
	// field config as a fallback) - distinct from the outer field config above.
	$: selectConfig = config.configuration || config;

	// Get allow_empty configuration
	$: allowEmpty = selectConfig?.allow_empty || false;

	function handleChange(newValue: any) {
		if (name) {
			// Convert empty string to null for backend
			const valueToSend = newValue === '' ? null : newValue;
			onChange(name, valueToSend);
		}
	}

	// Find the selected option's example
	$: selectedOption = options.find((opt) => opt.value === value);
	$: example = selectedOption?.example;

	$: formattedOptions = formatSelectOptions(options, allowEmpty);

	let copied = false;
	async function copyExample() {
		if (!example) return;
		const ok = await copyText(example);
		if (ok) {
			copied = true;
			setTimeout(() => (copied = false), 1500);
		} else {
			toasts.error('Could not copy');
		}
	}
</script>

<FieldShell
	{name}
	{label}
	{description}
	{tooltip}
	labelId={name ? `${name}-label` : undefined}
	let:descriptionId
>
	<div aria-labelledby={name ? `${name}-label` : undefined} aria-describedby={descriptionId}>
		<CustomSelect
			{value}
			options={formattedOptions}
			on:change={(e) => handleChange(e.detail)}
			{disabled}
			searchable={options.length > 10}
			placeholder="Select an option..."
		/>
	</div>
	{#if example}
		<div class="mt-2 flex items-center justify-between gap-2 px-2.5 py-1.5 bg-surface-2/50 border border-line-strong rounded">
			<span class="min-w-0 truncate font-mono text-xs text-fg-muted">{example}</span>
			<button
				type="button"
				on:click={copyExample}
				class="shrink-0 text-fg-subtle hover:text-fg-muted transition-colors"
				title="Copy to clipboard"
			>
				<Icon name={copied ? 'check' : 'copy'} className="w-3.5 h-3.5" />
			</button>
		</div>
	{/if}
</FieldShell>
