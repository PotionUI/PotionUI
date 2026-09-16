<script lang="ts">
	import { onMount } from 'svelte';
	import { DetailSection } from '$lib/components/detail';
	import * as adminApi from '$lib/services/admin-api';
	import type { UserGroup } from '$lib/services/admin-api';

	let {
		settings,
		onSettingChange
	}: { settings: Record<string, any>; onSettingChange: (key: string, value: unknown) => void } = $props();

	let groups = $state<UserGroup[]>([]);

	onMount(async () => {
		try {
			const response = await adminApi.getUserGroups();
			if (response.success && response.data) {
				groups = response.data;
			}
		} catch {
			groups = [];
		}
	});

	let currentGroup = $derived(settings.external_login_default_group || '');
	let currentGroupKnown = $derived(groups.some((group) => group.id === currentGroup));

	function isEnabled(key: string): boolean {
		return settings[key] === 'true' || settings[key] === true;
	}
</script>

<DetailSection label="External Login" padded={false}>
	<div class="px-4 sm:px-5 divide-y divide-line">
		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="external-login-auto-create" class="block text-sm font-medium text-fg mb-1">
					Create accounts on first external login
				</label>
				<p class="text-sm text-fg-muted">
					An external login with no linked account creates a new PotionUI user.
				</p>
			</div>
			<input
				type="checkbox"
				id="external-login-auto-create"
				class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
				checked={isEnabled('external_login_auto_create')}
				onchange={(e) =>
					onSettingChange('external_login_auto_create', e.currentTarget.checked ? 'true' : 'false')}
			/>
		</div>

		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="external-login-link-by-email" class="block text-sm font-medium text-fg mb-1">
					Link by verified email
				</label>
				<p class="text-sm text-fg-muted">
					Attach an external identity to an existing account whose email matches, only when the
					provider says the email is verified.
				</p>
			</div>
			<input
				type="checkbox"
				id="external-login-link-by-email"
				class="w-4 h-4 mt-1 text-signal border-line-strong rounded focus:ring-signal flex-shrink-0"
				checked={isEnabled('external_login_link_by_email')}
				onchange={(e) =>
					onSettingChange('external_login_link_by_email', e.currentTarget.checked ? 'true' : 'false')}
			/>
		</div>

		<div class="py-4 flex items-start justify-between gap-6">
			<div>
				<label for="external-login-default-group" class="block text-sm font-medium text-fg mb-1">
					Default group
				</label>
				<p class="text-sm text-fg-muted">User group assigned to accounts created by an external login.</p>
			</div>
			<select
				id="external-login-default-group"
				class="input w-48 flex-shrink-0"
				value={currentGroup}
				onchange={(e) => onSettingChange('external_login_default_group', e.currentTarget.value)}
			>
				<option value="">None</option>
				{#each groups as group (group.id)}
					<option value={group.id}>{group.name}</option>
				{/each}
				{#if currentGroup && !currentGroupKnown}
					<option value={currentGroup}>{currentGroup}</option>
				{/if}
			</select>
		</div>
	</div>
</DetailSection>
