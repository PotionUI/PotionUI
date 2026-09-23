<script lang="ts">
	import FilterPopoverFrame from '$lib/components/library/FilterPopoverFrame.svelte';
	import SegmentedFilterGroup from '$lib/components/library/SegmentedFilterGroup.svelte';
	import type { User } from '$lib/stores/auth';
	import {
		GENERATION_STATUS_OPTIONS,
		clearAllGenerationsFilters,
		type GenerationsFilters,
		type GenerationStatusFilter
	} from './generationsFilters';

	let {
		filters,
		users,
		onChange,
		onClose
	}: {
		filters: GenerationsFilters;
		users: readonly User[];
		onChange: (filters: GenerationsFilters) => void;
		onClose: () => void;
	} = $props();

	function set(patch: Partial<GenerationsFilters>) {
		onChange({ ...filters, ...patch });
	}
</script>

<FilterPopoverFrame
	label="Generation filters"
	width="w-[34rem]"
	onClearAll={() => onChange(clearAllGenerationsFilters(filters))}
	{onClose}
>
	<div class="col-span-2">
		<SegmentedFilterGroup
			label="Status"
			options={GENERATION_STATUS_OPTIONS}
			value={filters.status}
			onChange={(status: GenerationStatusFilter) => set({ status })}
		/>
	</div>

	<label class="col-span-2 flex flex-col gap-1.5">
		<span class="text-xs font-medium text-fg-muted">User</span>
		<select
			class="input"
			value={filters.userId}
			onchange={(event) => set({ userId: (event.currentTarget as HTMLSelectElement).value })}
		>
			<option value="">All users</option>
			{#each users as user (user.id)}
				<option value={user.id}>{user.username}</option>
			{/each}
		</select>
	</label>

	<label class="flex flex-col gap-1.5">
		<span class="text-xs font-medium text-fg-muted">Created from</span>
		<input
			type="date"
			class="input"
			value={filters.createdFrom}
			onchange={(event) => set({ createdFrom: (event.currentTarget as HTMLInputElement).value })}
		/>
	</label>

	<label class="flex flex-col gap-1.5">
		<span class="text-xs font-medium text-fg-muted">Created to</span>
		<input
			type="date"
			class="input"
			value={filters.createdTo}
			onchange={(event) => set({ createdTo: (event.currentTarget as HTMLInputElement).value })}
		/>
	</label>
</FilterPopoverFrame>
