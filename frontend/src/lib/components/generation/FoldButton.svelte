<script lang="ts">
	let {
		label,
		expanded,
		controls,
		onToggle,
		class: className = ''
	}: {
		label: string;
		expanded: boolean;
		controls: string;
		onToggle: () => void;
		class?: string;
	} = $props();
</script>

<button
	type="button"
	class="min-h-6 rounded py-1 text-sm text-signal hover:underline {className}"
	aria-expanded={expanded}
	aria-controls={controls}
	onclick={onToggle}
>
	{label}
</button>
