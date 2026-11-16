<script lang="ts">
	import SliderField from '$lib/components/form-fields/SliderField.svelte';
	import { SegmentedControl } from '$lib/components/ui';
	import { MASK_BRUSH, MASK_TOOLS } from '$lib/tool-tabs/image/mask';

	let {
		brush = $bindable<number>(MASK_BRUSH.default),
		erase = $bindable<boolean>(false)
	}: {
		brush?: number;
		erase?: boolean;
	} = $props();

	const items = MASK_TOOLS.map(({ id, label, icon }) => ({ id, label, icon }));
	let selected = $derived(MASK_TOOLS.find((tool) => tool.erase === erase)?.id ?? MASK_TOOLS[0].id);
</script>

<SegmentedControl
	variant="toggle"
	fullWidth
	ariaLabel="Mask brush"
	{items}
	{selected}
	onSelect={(id) => (erase = MASK_TOOLS.find((tool) => tool.id === id)?.erase ?? false)}
/>
<SliderField
	name="brush"
	config={{ title: 'Brush size', min: MASK_BRUSH.min, max: MASK_BRUSH.max, step: MASK_BRUSH.step, default: MASK_BRUSH.default, unit: 'px' }}
	value={brush}
	onChange={(_name, value) => (brush = Number(value))}
/>
