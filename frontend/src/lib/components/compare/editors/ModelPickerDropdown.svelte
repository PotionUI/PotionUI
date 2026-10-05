<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import ModelBrowserPanel from '$lib/components/form-fields/ModelBrowserPanel.svelte';

	let {
		modelType,
		presetId,
		label,
		disabled = false,
		excludeIds = new Set<string>(),
		onSelect
	}: {
		modelType: string;
		presetId: string;
		label: string;
		disabled?: boolean;
		excludeIds?: Set<string>;
		onSelect: (model: any) => void;
	} = $props();

	let open = $state(false);
	let trigger = $state<HTMLButtonElement>();
	let panel = $state<HTMLDivElement>();
	let search = $state('');
	let position = $state({ top: 0, left: 0, width: 320 });

	function toggle() {
		if (!open && trigger) {
			const rect = trigger.getBoundingClientRect();
			position = { top: rect.bottom + 4, left: rect.left, width: Math.max(rect.width, 320) };
		}
		open = !open;
	}

	function pick(model: any) {
		open = false;
		search = '';
		onSelect(model);
	}

	onMount(() => {
		const down = (event: PointerEvent) => {
			if (!open) return;
			const target = event.target as Node;
			if (trigger?.contains(target) || panel?.contains(target)) return;
			open = false;
		};
		const key = (event: KeyboardEvent) => {
			if (open && event.key === 'Escape') open = false;
		};
		window.addEventListener('pointerdown', down, true);
		window.addEventListener('keydown', key);
		return () => {
			window.removeEventListener('pointerdown', down, true);
			window.removeEventListener('keydown', key);
		};
	});
</script>

<button
	bind:this={trigger}
	type="button"
	{disabled}
	class="flex w-full items-center justify-center gap-1.5 rounded border border-dashed border-line px-2 py-2 text-sm text-fg-muted hover:border-line-hover hover:text-fg disabled:cursor-not-allowed disabled:opacity-50"
	aria-expanded={open}
	onclick={toggle}
>
	<Icon name="plus" className="h-3.5 w-3.5" />
	{label}
</button>

{#if open}
	<div
		bind:this={panel}
		use:portal
		use:overlayLayer
		class="fixed z-overlay max-h-[340px] overflow-y-auto rounded-lg border border-line-strong bg-surface-1 shadow-floating"
		style="top: {position.top}px; left: {position.left}px; width: {position.width}px"
	>
		<div class="sticky top-0 z-10 border-b border-line bg-surface-1 p-2">
			<input class="input" type="text" aria-label="Search models" placeholder="Search" bind:value={search} />
		</div>
		<ModelBrowserPanel {modelType} {presetId} searchQuery={search} {excludeIds} rowSize="md" onSelect={pick} />
	</div>
{/if}
