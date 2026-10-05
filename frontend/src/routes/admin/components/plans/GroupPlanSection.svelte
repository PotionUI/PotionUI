<script lang="ts">
	import { DetailSection } from '$lib/components/detail';
	import { Spinner } from '$lib/components/ui';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import { shortLabel } from '$lib/plans/format';
	import { formatImpactCell, impactNote } from '$lib/plans/usage';
	import { INHERIT_PLAN, planOptions } from '$lib/plans/options';
	import type { GroupImpact, LimitKindDescriptor, Plan } from '$lib/plans/types';

	let {
		plans,
		kinds,
		planId,
		defaultPlanName = null,
		impact = null,
		loadingImpact = false,
		onChange
	}: {
		plans: readonly Plan[];
		kinds: readonly LimitKindDescriptor[];
		planId: string | null;
		defaultPlanName?: string | null;
		impact?: GroupImpact | null;
		loadingImpact?: boolean;
		onChange: (planId: string | null) => void;
	} = $props();

	const options = $derived(planOptions(plans, kinds, { inheritLabel: defaultPlanName ? `Inherit (use default: ${defaultPlanName})` : 'Inherit (use default)' }));
	const impactKinds = $derived(impact ? impact.kinds : []);
	const memberCount = $derived(impact?.members.length ?? 0);
</script>

<DetailSection label="Plan">
	<div class="space-y-4" data-group-plan>
		<div class="flex items-start justify-between gap-6">
			<p class="max-w-md text-sm text-fg-muted">
				Members get this plan's limits. If someone is in several groups with plans, they get the most generous value for each limit. A personal
				override on the user wins over groups.
			</p>
			<div class="w-64 flex-shrink-0" data-group-plan-select>
				<CustomSelect
					size="sm"
					value={planId ?? INHERIT_PLAN}
					{options}
					on:change={(e) => onChange(e.detail === INHERIT_PLAN ? null : String(e.detail))}
				/>
			</div>
		</div>

		{#if loadingImpact}
			<div class="flex items-center gap-2 text-sm text-fg-muted"><Spinner size="sm" /> Checking what changes for members</div>
		{:else if impact && impact.members.length > 0 && impactKinds.length > 0}
			<div>
				<h4 class="mb-2 font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
					What changes for the {memberCount} {memberCount === 1 ? 'member' : 'members'}
				</h4>
				<div class="overflow-x-auto rounded-lg border border-line" data-plan-impact>
					<table class="w-full text-sm">
						<thead>
							<tr class="border-b border-line bg-surface-2 text-left font-mono text-2xs uppercase tracking-[0.07em] text-fg-subtle">
								<th class="px-3 py-2 font-normal">Member</th>
								{#each impactKinds as kind (kind.key)}
									<th class="px-3 py-2 font-normal">{shortLabel(kind)}</th>
								{/each}
								<th class="px-3 py-2 font-normal">Note</th>
							</tr>
						</thead>
						<tbody>
							{#each impact.members as row (row.user_id)}
								<tr class="border-b border-line last:border-b-0" data-impact-row={row.username}>
									<td class="px-3 py-2 text-fg">{row.username}</td>
									{#each impactKinds as kind (kind.key)}
										{@const cell = row.limits.find((c) => c.kind === kind.key)}
										<td class="px-3 py-2 font-mono text-xs tabular-nums {cell?.over_after ? 'text-warning' : 'text-fg'}" data-impact-cell={kind.key}>
											{cell ? formatImpactCell(kind, cell) : '-'}{#if cell?.over_after}<span class="ml-1.5 text-warning">over</span>{/if}
										</td>
									{/each}
									<td class="px-3 py-2 text-fg-muted">{impactNote(row)}</td>
								</tr>
							{/each}
						</tbody>
					</table>
				</div>
				<p class="mt-2 text-sm text-fg-subtle">
					Decided per limit. Nothing is deleted when a limit shrinks; users above it just cannot add more.
				</p>
			</div>
		{:else if impact}
			<p class="text-sm text-fg-subtle" data-plan-impact-empty>No members yet, so nothing changes.</p>
		{/if}
	</div>
</DetailSection>
