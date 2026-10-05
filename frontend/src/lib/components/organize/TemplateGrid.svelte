<script lang="ts">
	import type { RuleTemplate } from '$lib/organize/templates';

	let {
		templates,
		onUse
	}: {
		templates: RuleTemplate[];
		onUse: (template: RuleTemplate) => void;
	} = $props();
</script>

<section class="space-y-2" aria-label="Start from a template">
	<h3 class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-muted">Start from a template</h3>
	<div class="grid grid-cols-1 gap-2 sm:grid-cols-2 lg:grid-cols-3">
		{#each templates as template (template.key)}
			<button
				type="button"
				class="rounded-lg border border-line bg-surface-1 p-3 text-left shadow-raised transition-colors hover:border-line-hover hover:bg-surface-2"
				data-testid="rule-template"
				onclick={() => onUse(template)}
			>
				<span class="block text-sm font-semibold text-fg">{template.title}</span>
				<span class="mt-1 block text-xs text-fg-muted">{template.description}</span>
			</button>
		{/each}
	</div>
</section>
