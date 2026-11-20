<script lang="ts">
	import SegmentedControl from '$lib/components/ui/SegmentedControl.svelte';
	import FieldShell from './FieldShell.svelte';

	export let name: string | null;
	export let config: any = {};
	export let value: any;
	export let onChange: (fieldName: string, value: any) => void;
	export let onCommit: ((fieldName: string) => void) | undefined = undefined;

	type Option = { label?: string; value: string | number; disabled?: boolean };

	$: label = config.title || name || '';
	$: description = config.description || '';
	$: tooltip = config.tooltip || config.configuration?.tooltip;
	$: options = (config.options || []) as Option[];
	$: fit = config.fit === true;
	$: items = options.map((option) => ({
		id: String(option.value),
		label: option.label ?? String(option.value),
		disabled: option.disabled || config.disabled === true
	}));
	$: selected = value === null || value === undefined ? '' : String(value);

	function select(id: string) {
		const option = options.find((candidate) => String(candidate.value) === id);
		if (option && name) {
			onChange(name, option.value);
			onCommit?.(name);
		}
	}
</script>

<FieldShell {name} {label} {description} {tooltip} labelId={name ? `${name}-label` : undefined}>
	<SegmentedControl
		variant="toggle"
		ariaLabel={label}
		{items}
		{selected}
		fullWidth={!fit}
		onSelect={select}
	/>
</FieldShell>
