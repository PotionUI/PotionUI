<script lang="ts">
	import Icon from '../Icon.svelte';
	import Tooltip from '../Tooltip.svelte';
	import {
		filterUsers,
		isAllMode,
		isUserSelected,
		toggleUserSelection,
		clearSelection,
		setAllMode,
		selectedCount,
		moveActiveIndex,
		type UserPickerUser,
		type UserPickerValue
	} from './userPicker';

	let {
		users = [],
		value = [],
		onChange,
		disabled = false,
		class: className = ''
	}: {
		users: UserPickerUser[];
		value: UserPickerValue;
		onChange: (value: UserPickerValue) => void;
		disabled?: boolean;
		class?: string;
	} = $props();

	let query = $state('');
	let activeIndex = $state(-1);
	let listEl: HTMLDivElement | undefined = $state();

	let allMode = $derived(isAllMode(value));
	let filtered = $derived(filterUsers(users, query));
	let count = $derived(selectedCount(value, users.length));

	$effect(() => {
		filtered;
		activeIndex = -1;
	});

	function toggleAll(enabled: boolean) {
		if (disabled) return;
		onChange(setAllMode(enabled));
	}

	function toggleUser(userId: string) {
		if (disabled || allMode) return;
		onChange(toggleUserSelection(value, userId));
	}

	function clear() {
		if (disabled) return;
		onChange(clearSelection());
	}

	function scrollActiveIntoView() {
		if (!listEl || activeIndex < 0) return;
		const rows = listEl.querySelectorAll<HTMLElement>('[data-user-row]');
		rows[activeIndex]?.scrollIntoView({ block: 'nearest' });
	}

	function handleSearchKeydown(e: KeyboardEvent) {
		if (disabled || allMode) return;
		if (e.key === 'Escape') {
			if (query) {
				e.preventDefault();
				query = '';
			}
			return;
		}
		if (e.key === 'ArrowDown') {
			e.preventDefault();
			activeIndex = moveActiveIndex(activeIndex, 1, filtered.length);
			scrollActiveIntoView();
		} else if (e.key === 'ArrowUp') {
			e.preventDefault();
			activeIndex = moveActiveIndex(activeIndex, -1, filtered.length);
			scrollActiveIntoView();
		} else if (e.key === ' ' && activeIndex >= 0) {
			e.preventDefault();
			toggleUser(filtered[activeIndex].id);
		}
	}
</script>

<div class="flex min-w-[24rem] max-w-full flex-col gap-2 {className}">
	<label
		class="flex items-center gap-2 rounded border border-line-strong bg-surface-2 px-2.5 py-1.5 {disabled
			? 'cursor-not-allowed opacity-50'
			: 'cursor-pointer'}"
	>
		<input
			type="checkbox"
			checked={allMode}
			{disabled}
			onchange={(e) => toggleAll(e.currentTarget.checked)}
			class="h-3.5 w-3.5 accent-signal"
		/>
		<span class="text-sm text-fg">All users</span>
	</label>

	<div class="relative">
		<Icon name="search" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-subtle" />
		<input
			type="text"
			class="input h-8 pl-8 text-sm"
			placeholder="Search users..."
			bind:value={query}
			disabled={disabled || allMode}
			onkeydown={handleSearchKeydown}
			aria-label="Search users"
		/>
	</div>

	<div
		bind:this={listEl}
		class="max-h-60 overflow-y-auto rounded border border-line bg-surface-2/40 {allMode ? 'pointer-events-none opacity-50' : ''}"
		aria-label="Users"
	>
		{#if filtered.length === 0}
			<p class="px-3 py-6 text-center text-xs text-fg-subtle">No users match</p>
		{:else}
			{#each filtered as u, i (u.id)}
				<label
					data-user-row
					class="flex items-center gap-2 px-2.5 py-1.5 text-sm {disabled
						? 'cursor-not-allowed'
						: 'cursor-pointer'} {i === activeIndex ? 'bg-surface-3' : 'hover:bg-surface-3/60'}"
				>
					<input
						type="checkbox"
						checked={isUserSelected(value, u.id)}
						disabled={disabled || allMode}
						onchange={() => toggleUser(u.id)}
						class="h-3.5 w-3.5 flex-shrink-0 accent-signal"
					/>
					<span class="min-w-0 flex-1 truncate text-fg">{u.username}</span>
					{#if u.email}
						<Tooltip text={u.email} wrapperClass="min-w-0 max-w-[9rem]">
							<span class="block truncate text-xs text-fg-muted">{u.email}</span>
						</Tooltip>
					{/if}
				</label>
			{/each}
		{/if}
	</div>

	<div class="flex items-center justify-between">
		<span class="font-mono text-2xs tabular-nums text-fg-subtle">{count} selected</span>
		{#if count > 0}
			<button
				type="button"
				class="text-xs text-fg-muted hover:text-fg hover:underline underline-offset-2 disabled:opacity-50"
				onclick={clear}
				{disabled}
			>
				Clear
			</button>
		{/if}
	</div>
</div>
