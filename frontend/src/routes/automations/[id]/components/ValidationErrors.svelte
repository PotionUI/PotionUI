<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import type { ValidationIssue } from '$lib/types/automations';

	let { issues }: { issues: ValidationIssue[] } = $props();
</script>

{#if issues.length > 0}
	<div class="border-t border-line bg-surface-1 max-h-40 overflow-y-auto flex-shrink-0">
		<ul class="divide-y divide-line">
			{#each issues as issue, i (issue.node_id ?? '' + i)}
				<li class="flex items-start gap-2 px-4 py-2 text-xs">
					<Icon
						name={issue.severity === 'error' ? 'error' : 'warning'}
						className="w-3.5 h-3.5 flex-shrink-0 mt-0.5 {issue.severity === 'error'
							? 'text-danger'
							: 'text-warning'}"
					/>
					<div class="min-w-0">
						<p class="{issue.severity === 'error' ? 'text-danger' : 'text-warning'}">
							{issue.message}
						</p>
						{#if issue.node_id}
							<p class="text-2xs font-mono text-fg-subtle">{issue.node_id}</p>
						{/if}
					</div>
				</li>
			{/each}
		</ul>
	</div>
{/if}
