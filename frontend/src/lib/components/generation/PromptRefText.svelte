<script lang="ts">
	import PromptResourceChip from '$lib/components/PromptResourceChip.svelte';
	import { ensureKits } from '$lib/stores/kits';
	import { parseKitKey } from '$lib/kits/kitMarker';
	import { describeRefs, tokenizeRefs, type RefView } from './promptRefs';

	let { text, formData = null }: { text: string; formData?: Record<string, unknown> | null } = $props();

	let rendered = $derived.by(() => {
		const parts = tokenizeRefs(text);
		const views = describeRefs(parts, formData);
		let next = 0;
		return parts.map((part) => ({ part, view: part.type === 'ref' ? (views[next++] as RefView) : null }));
	});
	let kitIds = $derived(
		rendered.flatMap(({ view }) => (view?.type === 'kit' ? [parseKitKey(view.key)?.kitId ?? ''] : [])).filter(Boolean)
	);

	$effect(() => {
		if (kitIds.length) void ensureKits(kitIds);
	});
</script>

{#each rendered as { part, view }}{#if part.type === 'text'}{part.text}{:else if view?.type === 'missing'}<span
			class="mx-1 inline-flex items-center rounded border border-line bg-surface-2 px-1.5 py-0.5 text-sm text-fg-muted"
			data-ref-chip="missing">missing reference</span
		>{:else if view?.type === 'kit'}<span data-ref-chip="kit"><PromptResourceChip field={view.field} itemKey={view.key} spec={null} position={null} disabled /></span>{:else if view?.type === 'media'}<span data-ref-chip="media"><PromptResourceChip
				field={view.field}
				itemKey={view.key}
				spec={view.spec}
				position={view.position}
				item={view.item}
				disabled
			/></span>{/if}{/each}
