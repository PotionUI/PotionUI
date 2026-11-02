<script lang="ts">
	let {
		percent,
		label = 'Saving',
		size = 'sm',
		class: className = ''
	}: {
		percent: number;
		label?: string;
		size?: 'sm' | 'md';
		class?: string;
	} = $props();

	let value = $derived(Math.max(0, Math.min(100, Math.round(percent))));
</script>

<div
	class="relative overflow-hidden rounded-sm bg-surface-3 {size === 'sm' ? 'h-1.5 w-[120px]' : 'h-2 w-full'} {className}"
	class:md={size === 'md'}
	role="progressbar"
	aria-label={label}
	aria-valuemin={0}
	aria-valuemax={100}
	aria-valuenow={value}
	data-tick-progress
>
	<div class="absolute inset-y-0 left-0 bg-signal transition-[width] duration-200" style="width: {value}%"></div>
	<div class="ticks pointer-events-none absolute inset-0"></div>
</div>

<style>
	.ticks {
		background: repeating-linear-gradient(90deg, transparent 0 7px, rgb(var(--canvas) / 0.6) 7px 8px);
	}

	.md .ticks {
		background: repeating-linear-gradient(90deg, transparent 0 9px, rgb(var(--canvas) / 0.6) 9px 10px);
	}
</style>
