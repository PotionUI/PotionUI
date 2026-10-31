<script lang="ts">
	import { onMount } from 'svelte';
	import { Alert, Badge, Spinner } from '$lib/components/ui';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { api } from '$lib/services/api/index';
	import type { RecipePlanResponse } from '$lib/services/api/recipeModes';
	import { getApiErrorMessage } from '$lib/utils/logger';
	import { formatBytesShort } from '$lib/utils/format';

	let { recipeId, recipeName, onClose }: { recipeId: string; recipeName: string; onClose: () => void } = $props();

	let plan = $state<RecipePlanResponse | null>(null);
	let loading = $state(true);
	let error = $state('');

	onMount(async () => {
		try {
			plan = await api.planRecipe(recipeId);
		} catch (err) {
			error = getApiErrorMessage(err, "Couldn't preview this recipe.");
		} finally {
			loading = false;
		}
	});
</script>

<BaseModal
	isOpen={true}
	title="Preview {recipeName}"
	subtitle="This is what installing would do. Nothing is downloaded or changed."
	size="md"
	compact
	on:close={onClose}
>
	<div class="space-y-4 p-5" data-recipe-plan-preview>
		{#if loading}
			<div class="flex justify-center py-8"><Spinner size="md" /></div>
		{:else if error}
			<Alert variant="danger" density="compact">{error}</Alert>
		{:else if plan}
			<ol class="divide-y divide-line rounded border border-line">
				{#each plan.steps as step, index (step.key)}
					<li class="flex items-start gap-3 px-3 py-2">
						<span class="mt-0.5 font-mono text-sm tabular-nums text-fg-subtle">{index + 1}</span>
						<div class="min-w-0 flex-1">
							<div class="text-base text-fg">{step.title}</div>
							{#if step.preview?.summary}
								<div class="text-sm text-fg-muted">{step.preview.summary}</div>
							{:else if step.preview?.error}
								<div class="text-sm text-danger">{step.preview.error}</div>
							{/if}
						</div>
						{#if step.onboarding_only}<Badge size="sm">First run only</Badge>{/if}
					</li>
				{/each}
			</ol>
			{#if plan.totals.download_bytes != null}
				<div class="flex items-center justify-between rounded bg-surface-2 px-4 py-3">
					<span class="font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">To download</span>
					<span class="font-mono text-base tabular-nums text-fg">
						{formatBytesShort(plan.totals.download_bytes)}{plan.totals.file_count != null
							? ` · ${plan.totals.file_count} ${plan.totals.file_count === 1 ? 'file' : 'files'}`
							: ''}
					</span>
				</div>
			{/if}
		{/if}
	</div>
</BaseModal>
