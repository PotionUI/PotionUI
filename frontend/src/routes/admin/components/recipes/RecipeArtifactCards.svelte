<script lang="ts">
	import type { RecipeArtifact } from '$lib/services/api/recipes';
	import { formatBytes } from '$lib/utils/format';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Badge } from '$lib/components/ui';
	import { DETAIL_INSET_CLASS } from '$lib/components/detail';

	let { artifacts }: { artifacts: RecipeArtifact[] } = $props();

	function sizeLabel(artifact: RecipeArtifact): string {
		return artifact.size_bytes != null ? formatBytes(artifact.size_bytes) : '—';
	}
</script>

<ul class="space-y-1.5" data-recipe-artifact-cards>
	{#each artifacts as artifact (artifact.id)}
		<li class="px-3 py-2.5 {DETAIL_INSET_CLASS}" data-recipe-artifact-card>
			<div class="flex items-center gap-1.5 min-w-0">
				<Tooltip text={artifact.display_name} wrapperClass="min-w-0 flex-1">
					<span class="block truncate text-sm text-fg">{artifact.display_name}</span>
				</Tooltip>
				{#if artifact.gated}
					<Badge variant="warning" size="sm" class="shrink-0">gated</Badge>
					{#if artifact.license_url}
						<a
							href={artifact.license_url}
							target="_blank"
							rel="noreferrer"
							class="text-xs text-signal hover:underline shrink-0"
						>
							licence
						</a>
					{/if}
				{/if}
			</div>
			<div class="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs tabular-nums text-fg-subtle">
				<span class="whitespace-nowrap">{artifact.model_type}</span>
				<span class="whitespace-nowrap">{sizeLabel(artifact)}</span>
				{#if artifact.required}
					<Badge variant="neutral" size="sm">required</Badge>
				{:else}
					<span class="whitespace-nowrap">optional</span>
				{/if}
			</div>
		</li>
	{/each}
</ul>
