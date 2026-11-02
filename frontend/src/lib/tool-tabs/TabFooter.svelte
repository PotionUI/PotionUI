<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import { tabFooter } from './shortcuts';
	import type { EditorKind } from './toolIds';

	let { targets, count = 1 }: { targets: readonly EditorKind[]; count?: number } = $props();
</script>

{#if targets.length > 0}
	<div
		role="none"
		class="mt-1 flex items-start gap-2.5 border-t border-line px-2.5 pb-1.5 pt-2.5 text-[12px] leading-4 text-fg-subtle"
		data-tab-footer
	>
		<Icon name="external-link" className="mt-px h-3.5 w-3.5 shrink-0" />
		<span>{tabFooter(targets, count)}</span>
	</div>
{/if}
