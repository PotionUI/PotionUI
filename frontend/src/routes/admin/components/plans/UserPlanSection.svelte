<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { Badge } from '$lib/components/ui';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { kindIcon } from '$lib/plans/format';
	import { describeSource } from '$lib/plans/usage';
	import { INHERIT_PLAN, planOptions } from '$lib/plans/options';
	import type { LimitKindDescriptor, Plan, UserPlanDetail } from '$lib/plans/types';
	import UsageBar from './UsageBar.svelte';

	let {
		plans,
		kinds,
		detail,
		overrideId,
		onChange
	}: {
		plans: readonly Plan[];
		kinds: readonly LimitKindDescriptor[];
		detail: UserPlanDetail | null;
		overrideId: string | null;
		onChange: (planId: string | null) => void;
	} = $props();

	const options = $derived(planOptions(plans, kinds, { inheritLabel: 'None (use groups)' }));
	const rows = $derived(
		(detail?.limits ?? []).flatMap((entry) => {
			const kind = kinds.find((k) => k.key === entry.kind);
			return kind ? [{ entry, kind }] : [];
		})
	);
</script>

<DetailSection label="Plan">
	<div class="space-y-4" data-user-plan>
		<div>
			<p class="font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">Effective</p>
			{#if detail?.plan}
				<p class="mt-1 text-sm font-semibold text-fg" data-effective-plan>
					{detail.plan.name}
					{#if detail.group}<span class="font-normal text-fg-muted">from group {detail.group.name}</span>{/if}
				</p>
			{/if}
		</div>

		{#if rows.length > 0}
			<ul class="divide-y divide-line">
				{#each rows as { entry, kind } (entry.kind)}
					<li class="flex items-center gap-4 py-3" data-effective-limit={entry.kind}>
						<span class="flex-shrink-0 text-signal"><Icon name={kindIcon(kind)} className="w-4 h-4" /></span>
						<div class="min-w-0 flex-1">
							<p class="text-sm font-semibold text-fg">{kind.short_label ?? kind.label}</p>
							<p class="text-xs text-fg-subtle" data-limit-source>{describeSource(entry)}</p>
						</div>
						<UsageBar {kind} used={entry.used ?? 0} limit={entry.limit} />
						{#if !entry.enforced}<Badge size="sm" variant="neutral" class="font-mono">exempt</Badge>{/if}
					</li>
				{/each}
			</ul>
		{:else}
			<p class="text-sm text-fg-muted" data-no-limits>No limits apply to this user.</p>
		{/if}

		<div class="flex items-start justify-between gap-6 border-t border-line pt-4">
			<div class="max-w-md">
				<p class="text-sm font-medium text-fg">Personal override</p>
				<p class="text-sm text-fg-muted">Replaces every group plan for this person, all limits. Pick Unlimited to lift all limits.</p>
			</div>
			<div class="w-64 flex-shrink-0" data-user-plan-select>
				<CustomSelect
					size="sm"
					value={overrideId ?? INHERIT_PLAN}
					{options}
					on:change={(e) => onChange(e.detail === INHERIT_PLAN ? null : String(e.detail))}
				/>
			</div>
		</div>
	</div>
</DetailSection>
