<script lang="ts">
	import { Button, IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { suggestionKey } from '$lib/model-roots/logic';
	import type { ModelRoot } from '$lib/services/api/models';
	import type { FoldersController } from './controller.svelte';
	import { typeLabel } from './derive';

	let { ctrl, root }: { ctrl: FoldersController; root: ModelRoot } = $props();
	const again = $derived(ctrl.again[root.id]);
</script>

{#if again}
	<div class="space-y-2 rounded border border-line bg-surface-2 px-3 py-2.5" data-again-panel>
		<div class="flex items-center justify-between gap-2">
			<span class="font-mono text-[12px] uppercase tracking-[0.05em] text-fg-subtle">Found by detecting again</span>
			<Tooltip text="Dismiss" position="top"><IconButton icon="close" label="Dismiss" size="xs" onclick={() => ctrl.dismissAgain(root.id)} /></Tooltip>
		</div>
		{#if again.note}<p class="text-sm text-fg-muted">{again.note}</p>{/if}
		{#if again.missing.length}
			<ul class="space-y-1.5">
				{#each again.missing as s (suggestionKey(s))}
					{@const key = suggestionKey(s)}
					<li class="flex items-center gap-2.5">
						<input type="checkbox" id="again-{root.id}-{key}" class="h-4 w-4 rounded accent-signal-solid" bind:checked={again.ticks[key]} />
						<label for="again-{root.id}-{key}" class="min-w-0 flex-1 cursor-pointer">
							<span class="block text-sm text-fg">{typeLabel(s.model_type)}</span>
							<span class="block truncate font-mono text-[12px] text-fg-subtle">{s.subdir}</span>
						</label>
					</li>
				{/each}
			</ul>
			<Button
				variant="secondary"
				size="sm"
				icon="plus"
				loading={ctrl.isBusy(`addmissing:${root.id}`)}
				disabled={!again.missing.some((s) => again.ticks[suggestionKey(s)])}
				onclick={() => ctrl.addMissing(root)}
			>
				Add selected
			</Button>
		{/if}
	</div>
{/if}
