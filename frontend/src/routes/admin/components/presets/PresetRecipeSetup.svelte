<script lang="ts">
	import RecipeRunProgress from '$lib/components/recipes/RecipeRunProgress.svelte';
	import { adminRecipeRunActions } from '$lib/components/recipes/runActions';
	import type { RecipeRunSession } from '$lib/components/recipes/recipeRunSession.svelte';
	import { Alert, Badge, Button } from '$lib/components/ui';
	import { formatBytes } from '$lib/utils/format';
	import { runStatusLabel } from '$lib/utils/setupRunDisplay';
	import type { PresetRecipeLink } from '$lib/types/api';

	let {
		recipes,
		session
	}: {
		recipes: PresetRecipeLink[];
		session: RecipeRunSession;
	} = $props();
</script>

<section data-preset-recipe-setup>
	<div class="rounded-lg border border-line bg-surface-1 divide-y divide-line">
		{#each recipes as recipe (recipe.id)}
			<div class="flex flex-wrap items-center gap-3 px-4 py-3">
				<div class="min-w-0 flex-1">
					<a
						class="block truncate text-sm font-medium text-fg hover:text-signal"
						href="/admin?tab=recipes&id={encodeURIComponent(recipe.id)}"
					>
						{recipe.name}
					</a>
					<p class="mt-0.5 text-xs text-fg-muted">
						Installs the preset and downloads its models{#if recipe.total_download_bytes != null}
							<span class="font-mono tabular-nums"> · ~{formatBytes(recipe.total_download_bytes)}</span>
						{/if}
					</p>
				</div>
				{#if recipe.readiness === 'installed'}
					<Badge variant="success" size="sm" dot>ran before</Badge>
				{/if}
			</div>
		{/each}
	</div>

	{#if session.conflict}
		<div class="mt-3" data-recipe-run-conflict>
			<Alert variant="warning" density="compact" title="Another recipe is running">
				{session.conflict.activeRun.recipeName} is still running ({runStatusLabel(
					session.conflict.activeRun.status
				).toLowerCase()}).
				{#snippet actions()}
					<div class="flex items-center gap-2">
						<Button
							variant="secondary"
							size="sm"
							href="/admin?tab=recipes&id={encodeURIComponent(session.conflict!.activeRun.recipeId)}"
						>
							Open it
						</Button>
						<Button variant="secondary" size="sm" onclick={() => session.cancelConflict()}>Cancel it</Button>
					</div>
				{/snippet}
			</Alert>
		</div>
	{:else if session.error}
		<div class="mt-3">
			<Alert variant="danger" density="compact" title="Couldn't start this recipe">{session.error}</Alert>
		</div>
	{/if}

	{#if session.run}
		<div class="mt-3 rounded-lg border border-line bg-surface-1 px-4 py-4">
			<RecipeRunProgress
				run={session.run}
				title="Setting up"
				actions={adminRecipeRunActions}
				onRunUpdated={session.adopt}
			/>
		</div>
	{/if}
</section>
