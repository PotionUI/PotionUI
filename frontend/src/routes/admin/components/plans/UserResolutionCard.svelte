<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { Badge } from '$lib/components/ui';
	import type { UserPlanDetail } from '$lib/plans/types';

	let { detail }: { detail: UserPlanDetail } = $props();

	const steps = $derived(
		detail.resolution.map((step, index) => {
			const winning = (step.step === 'groups' ? 'groups' : step.step) === detail.decided_by;
			if (step.step === 'override') {
				return { index, winning, label: 'Personal override', plan: step.plan?.name ?? 'none' };
			}
			if (step.step === 'groups') {
				const names = (step.plans ?? []).map((p) => p.plan.name);
				return { index, winning, label: 'Most generous per limit across groups', plan: names.length ? [...new Set(names)].join(', ') : 'none' };
			}
			return { index, winning, label: 'Default plan (All users)', plan: step.plan?.name ?? 'none' };
		})
	);
</script>

<DetailSection label="Resolution">
	<ol class="space-y-3" data-resolution>
		{#each steps as step (step.index)}
			<li class="flex items-center justify-between gap-3 text-sm" data-resolution-step data-winning={step.winning}>
				<span class="flex min-w-0 gap-2.5 {step.winning ? 'text-signal' : 'text-fg-muted'}">
					<span class="font-mono text-xs tabular-nums text-fg-subtle">{step.index + 1}</span>
					<span>{step.label}</span>
				</span>
				<Badge size="sm" variant={step.winning ? 'signal' : 'neutral'} class="font-mono">{step.plan}</Badge>
			</li>
		{/each}
	</ol>
</DetailSection>
