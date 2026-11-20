<script lang="ts">
	import HeaderDropdown, { type HeaderOption } from './HeaderDropdown.svelte';

	let {
		label,
		options,
		value,
		onchange,
		active = false
	}: {
		label: string;
		options: HeaderOption[];
		value: string;
		onchange: (value: string) => void;
		active?: boolean;
	} = $props();
</script>

<HeaderDropdown {label} {options} {value} {onchange} {active} />
