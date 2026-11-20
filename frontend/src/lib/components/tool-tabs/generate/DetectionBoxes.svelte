<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { kindLabel, type DetectionItem } from '$lib/tool-tabs/image/generate/detections';

	let {
		items,
		hover = null,
		ontoggle,
		onhover
	}: {
		items: readonly DetectionItem[];
		hover?: string | null;
		ontoggle: (id: string, on: boolean) => void;
		onhover?: (id: string | null) => void;
	} = $props();
</script>

<div class="pointer-events-none absolute inset-0" data-detection-boxes>
	{#each items as item (item.id)}
		<div
			class="absolute rounded-sm border-2 {item.kind === 'face' ? 'border-signal' : 'border-info'} {item.on ? '' : 'border-dashed opacity-50'} {hover === item.id ? 'ring-2 ring-signal' : ''}"
			style="left: {item.box.x * 100}%; top: {item.box.y * 100}%; width: {item.box.width * 100}%; height: {item.box.height * 100}%"
			data-detection-box={item.id}
			data-detection-on={item.on}
		>
			<button
				type="button"
				class="pointer-events-auto absolute -top-px left-0 inline-flex h-6 -translate-y-full items-center gap-1 rounded-t-sm px-1.5 font-mono text-sm {item.kind === 'face' ? 'bg-signal text-accent-contrast' : 'bg-info-solid text-white'}"
				aria-pressed={item.on}
				aria-label="{item.on ? 'Skip' : 'Fix'} {kindLabel(item.kind).toLowerCase()} {item.id}"
				onclick={() => ontoggle(item.id, !item.on)}
				onmouseenter={() => onhover?.(item.id)}
				onmouseleave={() => onhover?.(null)}
				data-detection-tag={item.id}
			>
				<Icon name={item.on ? 'check' : 'close'} className="h-3 w-3" />{kindLabel(item.kind)}
			</button>
		</div>
	{/each}
</div>
