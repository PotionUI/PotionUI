<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import EditorOriginChip from '$lib/components/EditorOriginChip.svelte';
	import { Badge } from '$lib/components/ui';
	import tooltip from '$lib/actions/tooltip';
	import { describeOrigin } from '$lib/generation/origin';
	import { editorOriginOf } from '$lib/tool-tabs/editOrigin';
	import type { GenerationOrigin } from '$lib/types/history';

	let {
		generation,
		iconOnly = false,
		class: className = '',
		onopened
	}: {
		generation: { origin?: GenerationOrigin | null };
		iconOnly?: boolean;
		class?: string;
		onopened?: () => void;
	} = $props();

	let info = $derived(describeOrigin(generation.origin));
	let editor = $derived(editorOriginOf(generation));
</script>

{#if editor}
	<EditorOriginChip origin={editor} {iconOnly} {onopened} class={className} />
{:else if info}
	<span
		class="inline-flex max-w-full {className}"
		data-origin-chip={info.kind}
		role={iconOnly ? 'img' : undefined}
		aria-label={iconOnly ? info.label : undefined}
		use:tooltip={iconOnly ? info.label : null}
	>
		<Badge class="h-6 max-w-full gap-1.5">
			<Icon name={info.icon} className="h-3.5 w-3.5 flex-none" />
			{#if !iconOnly}<span class="min-w-0 truncate">{info.label}</span>{/if}
		</Badge>
	</span>
{/if}
