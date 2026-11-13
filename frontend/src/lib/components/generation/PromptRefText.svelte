<script lang="ts">
	import PromptResourceChip from '$lib/components/PromptResourceChip.svelte';
	import { ensureKits } from '$lib/stores/kits';
	import { parseKitKey } from '$lib/kits/kitMarker';
	import { describeRefs, groupRefs, tokenizeRefs, type RefView } from './promptRefs';

	let { text, formData = null }: { text: string; formData?: Record<string, unknown> | null } = $props();

	let rendered = $derived.by(() => {
		const parts = tokenizeRefs(text);
		const views = describeRefs(parts, formData);
		let next = 0;
		return groupRefs(parts).map((node) => ({ node, view: node.type === 'ref' ? (views[next++] as RefView) : null }));
	});
	let kitIds = $derived(
		rendered.flatMap(({ view }) => (view?.type === 'kit' ? [parseKitKey(view.key)?.kitId ?? ''] : [])).filter(Boolean)
	);

	$effect(() => {
		if (kitIds.length) void ensureKits(kitIds);
	});

	const chipFrame =
		'inline-flex align-middle -my-px [&_.resource-chip]:mx-0 [&_.resource-chip]:py-0 [&_.resource-chip_.py-1]:py-0 [&_.resource-chip]:text-fg [&_.chip-label_.opacity-60]:opacity-80 [&_.chip-thumb]:inline-flex [&_.chip-thumb]:h-4 [&_.chip-thumb]:w-4 [&_.icon]:h-4 [&_.icon]:w-4';
</script>

{#each rendered as { node, view }}{#if node.type === 'text'}{node.text}{:else}<span class="whitespace-nowrap">{#if view?.type === 'missing'}<span
				class="inline-flex items-center rounded border border-line bg-surface-2 px-1.5 text-sm text-fg-muted"
				data-ref-chip="missing">missing reference</span
			>{:else if view?.type === 'kit'}<span class={chipFrame} data-ref-chip="kit"><PromptResourceChip field={view.field} itemKey={view.key} spec={null} position={null} /></span>{:else if view?.type === 'media'}<span class={chipFrame} data-ref-chip="media"><PromptResourceChip
					field={view.field}
					itemKey={view.key}
					spec={view.spec}
					position={view.position}
					item={view.item}
					disabled
				/></span>{/if}{node.trailing}</span>{/if}{/each}
